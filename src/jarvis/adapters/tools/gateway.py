"""Tool Gateway: the single controlled path from intents to system actions.

Every tool declares its name, description, input schema, risk class, and
reversibility. The gateway validates arguments, applies an allowlist, enforces
permission policy, and gates destructive/externally-visible actions behind
trace-bound, expiring confirmation.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionDenied,
)


class Risk(str, enum.Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    CONFIRM_REQUIRED = "confirm_required"
    HIGH_RISK = "high_risk"


class Tool:
    """Base class for executable system tools."""

    name: str = ""
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    risk: Risk = Risk.REVERSIBLE
    timeout_seconds: float = 30.0

    @property
    def reversible(self) -> bool:
        return self.risk in (Risk.READ_ONLY, Risk.REVERSIBLE)

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        raise NotImplementedError


Confirmer = Callable[[str, Mapping[str, Any], TurnContext], Awaitable[bool]]


class ToolGateway:
    """Validate, authorize, and execute tools with a permission policy."""

    def __init__(
        self,
        tools: list[Tool],
        *,
        allowlist: Optional[frozenset[str]] = None,
        confirmer: Optional[Confirmer] = None,
        confirmation_timeout_seconds: float = 30.0,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._allowlist = allowlist
        self._confirmer = confirmer
        self._confirmation_timeout_seconds = confirmation_timeout_seconds
        self._approved: dict[str, dict[str, float]] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    async def execute(
        self, name: str, arguments: Mapping[str, Any], context: TurnContext
    ) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"tool not registered: {name}")
        if self._allowlist is not None and name not in self._allowlist:
            raise ToolPermissionDenied(f"tool {name} is not allowlisted")
        self._validate(tool, arguments)

        if tool.risk in (Risk.CONFIRM_REQUIRED, Risk.HIGH_RISK):
            if not self._is_approved(name, context.trace_id):
                if self._confirmer is None:
                    raise ToolPermissionDenied(
                        f"tool {name} requires confirmation but no confirmer is configured"
                    )
                approved = await self._confirmer(name, arguments, context)
                if not approved:
                    raise ToolPermissionDenied(f"tool {name} was declined")
                self._approve(name, context.trace_id)

        try:
            return await asyncio.wait_for(
                tool.execute(arguments, context), timeout=tool.timeout_seconds
            )
        except asyncio.TimeoutError as error:
            raise ToolExecutionError(f"tool {name} timed out") from error
        except (ToolNotFoundError, ToolPermissionDenied):
            raise
        except Exception as error:
            raise ToolExecutionError(f"tool {name} failed: {error}") from error

    def _validate(self, tool: Tool, arguments: Mapping[str, Any]) -> None:
        required = {
            key
            for key, spec in tool.input_schema.items()
            if isinstance(spec, dict) and spec.get("required")
        }
        missing = required - set(arguments)
        if missing:
            raise ToolExecutionError(
                f"tool {tool.name} missing required arguments: {sorted(missing)}"
            )

    def _is_approved(self, name: str, trace_id: str) -> bool:
        approvals = self._approved.get(trace_id, {})
        approved_at = approvals.get(name)
        if approved_at is None:
            return False
        if time.time() - approved_at > self._confirmation_timeout_seconds:
            approvals.pop(name, None)
            return False
        return True

    def _approve(self, name: str, trace_id: str) -> None:
        self._approved.setdefault(trace_id, {})[name] = time.time()
