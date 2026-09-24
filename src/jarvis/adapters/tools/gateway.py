"""Tool Gateway: the single controlled path from intents to system actions.

Every tool declares its name, description, input schema, risk class, and
reversibility. The gateway validates arguments, applies an allowlist, enforces
permission policy, and gates destructive/externally-visible actions behind
trace-bound, expiring confirmation.

Risk policy (M007):

* LOW (``READ_ONLY``/``REVERSIBLE``): runs automatically.
* ``MEDIUM``: runs automatically when every path it touches is inside an
  owner-authorized scope (or the owner listed the tool as trusted); otherwise
  it needs confirmation. Agent-originated calls (Hermes, documents, web
  content) never benefit from the trusted list.
* ``HIGH_RISK``: explicit confirmation immediately before *every* execution;
  approvals are never cached and cannot be pre-authorized by configuration.

Mutating tools run on their own execution token, shielded from the turn:
barge-in cancels narration, not a half-written file. ``cancel_operations``
cancels execution explicitly. Every decision is written to the audit log with
secrets and content redacted.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.turn import CancellationToken
from jarvis.core.errors import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionDenied,
)


logger = logging.getLogger("jarvis.tools")


class Risk(str, enum.Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    MEDIUM = "medium"
    CONFIRM_REQUIRED = "confirm_required"
    HIGH_RISK = "high_risk"


_LOW = (Risk.READ_ONLY, Risk.REVERSIBLE)
_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


@dataclass(frozen=True)
class ToolResult:
    """Structured tool outcome; ``str()`` is the short spoken form."""

    say: str
    data: Any = None
    ok: bool = True

    def __str__(self) -> str:
        return self.say


class Tool:
    """Base class for executable system tools."""

    name: str = ""
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    risk: Risk = Risk.REVERSIBLE
    timeout_seconds: float = 30.0
    # Strict tools reject arguments their schema does not declare.
    strict: bool = False

    @property
    def reversible(self) -> bool:
        return self.risk in (Risk.READ_ONLY, Risk.REVERSIBLE)

    def risk_for(self, arguments: Mapping[str, Any]) -> Risk:
        """Risk of this specific call (e.g. a permanent delete is higher)."""
        return self.risk

    def scope_paths(self, arguments: Mapping[str, Any]) -> list[str]:
        """Filesystem paths this call would modify (checked against scopes)."""
        return []

    def describe(self, arguments: Mapping[str, Any]) -> str:
        """Plain-Spanish explanation spoken before a confirmation."""
        return f"ejecutar {self.name}"

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> Any:
        raise NotImplementedError


Confirmer = Callable[[str, Mapping[str, Any], TurnContext], Awaitable[bool]]

_SECRET_KEY = re.compile(r"pass|token|secret|api_?key|credential|auth|cookie", re.IGNORECASE)
_BULK_KEYS = {"content", "text", "data"}


def redact(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Audit-safe argument summary: no secrets, no file contents."""
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if _SECRET_KEY.search(str(key)):
            safe[key] = "<redacted>"
        elif key in _BULK_KEYS and isinstance(value, str):
            safe[key] = f"<{len(value)} chars>"
        elif isinstance(value, str):
            safe[key] = value if len(value) <= 200 else value[:200] + "..."
        elif isinstance(value, (list, tuple)):
            safe[key] = [redact({"v": v})["v"] for v in list(value)[:20]]
        else:
            safe[key] = value if isinstance(value, (int, float, bool)) or value is None else str(value)[:200]
    return safe


class AuditLog:
    """Append-only JSON-lines operation log (one line per tool decision)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, **record: Any) -> None:
        record = {"ts": round(time.time(), 3), **record}
        logger.info("tool %s", record)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.warning("audit log unavailable at %s", self.path, exc_info=True)


def within(path: str | Path, scopes: Iterable[Path]) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for scope in scopes:
        try:
            resolved.relative_to(scope)
            return True
        except ValueError:
            continue
    return False


class ToolGateway:
    """Validate, authorize, and execute tools with a permission policy."""

    def __init__(
        self,
        tools: list[Tool],
        *,
        allowlist: Optional[frozenset[str]] = None,
        confirmer: Optional[Confirmer] = None,
        confirmation_timeout_seconds: float = 30.0,
        authorized_scopes: Iterable[str | Path] = (),
        trusted: Iterable[str] = (),
        audit: Optional[AuditLog] = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._allowlist = allowlist
        self._confirmer = confirmer
        self._confirmation_timeout_seconds = confirmation_timeout_seconds
        self._approved: dict[str, dict[str, float]] = {}
        self.scopes = tuple(Path(p).expanduser().resolve() for p in authorized_scopes)
        self._trusted = frozenset(trusted)
        self._audit = audit or AuditLog(None)
        self._operations: dict[asyncio.Task[Any], TurnContext] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Add a tool; the orchestrator needs no change for new capabilities."""
        self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    def describe(self, name: str, arguments: Mapping[str, Any]) -> str:
        tool = self._tools.get(name)
        return tool.describe(arguments) if tool is not None else f"ejecutar {name}"

    @property
    def running_operations(self) -> int:
        return sum(1 for task in self._operations if not task.done())

    def cancel_operations(self) -> int:
        """Explicitly cancel running mutating operations (not just narration)."""
        count = 0
        for task, exec_context in list(self._operations.items()):
            if not task.done():
                exec_context.cancellation.cancel()
                task.cancel()
                count += 1
        return count

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: TurnContext,
        *,
        origin: str = "owner",
    ) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"tool not registered: {name}")
        if self._allowlist is not None and name not in self._allowlist:
            self._audit.write(tool=name, origin=origin, decision="denied", reason="not allowlisted", trace=context.trace_id)
            raise ToolPermissionDenied(f"tool {name} is not allowlisted")
        self._validate(tool, arguments)
        risk = tool.risk_for(arguments)
        record = {"tool": name, "risk": risk.value, "origin": origin, "trace": context.trace_id, "args": redact(arguments)}

        decision = "auto"
        if risk is Risk.HIGH_RISK or (risk is Risk.MEDIUM and not self._medium_authorized(tool, arguments, origin)):
            # Never cached: the owner confirms each high-risk action right before it runs.
            decision = await self._confirm(tool, arguments, context, record)
        elif risk is Risk.CONFIRM_REQUIRED:
            if not self._is_approved(name, context.trace_id):
                decision = await self._confirm(tool, arguments, context, record)
                self._approve(name, context.trace_id)

        started = time.monotonic()
        try:
            result = await self._run(tool, arguments, context, risk)
        except asyncio.TimeoutError as error:
            self._audit.write(**record, decision=decision, outcome="timeout", ms=_ms(started))
            raise ToolExecutionError(f"tool {name} timed out") from error
        except asyncio.CancelledError:
            self._audit.write(**record, decision=decision, outcome="cancelled", ms=_ms(started))
            raise
        except (ToolNotFoundError, ToolPermissionDenied):
            raise
        except Exception as error:
            self._audit.write(**record, decision=decision, outcome="error", error=str(error)[:300], ms=_ms(started))
            raise ToolExecutionError(f"tool {name} failed: {error}") from error
        ok = getattr(result, "ok", True)
        self._audit.write(**record, decision=decision, outcome="ok" if ok else "failed", ms=_ms(started))
        return result

    def _medium_authorized(self, tool: Tool, arguments: Mapping[str, Any], origin: str) -> bool:
        if origin == "owner" and tool.name in self._trusted:
            return True
        paths = tool.scope_paths(arguments)
        return all(within(path, self.scopes) for path in paths)

    async def _confirm(self, tool: Tool, arguments: Mapping[str, Any], context: TurnContext, record: dict) -> str:
        if self._confirmer is None:
            self._audit.write(**record, decision="denied", reason="no confirmer")
            raise ToolPermissionDenied(
                f"tool {tool.name} requires confirmation but no confirmer is configured"
            )
        approved = await self._confirmer(tool.name, arguments, context)
        if not approved:
            self._audit.write(**record, decision="declined")
            raise ToolPermissionDenied(f"tool {tool.name} was declined")
        return "confirmed"

    async def _run(self, tool: Tool, arguments: Mapping[str, Any], context: TurnContext, risk: Risk) -> Any:
        if risk is Risk.READ_ONLY:
            return await asyncio.wait_for(tool.execute(arguments, context), timeout=tool.timeout_seconds)
        # Own cancellation token: stopping the narration must not abort a write.
        exec_context = TurnContext(
            context.trace_id, context.conversation_id, time.monotonic() + tool.timeout_seconds, CancellationToken()
        )
        task = asyncio.ensure_future(asyncio.wait_for(tool.execute(arguments, exec_context), timeout=tool.timeout_seconds))
        self._operations[task] = exec_context
        task.add_done_callback(lambda t: self._operations.pop(t, None))
        return await asyncio.shield(task)

    def _validate(self, tool: Tool, arguments: Mapping[str, Any]) -> None:
        if not isinstance(arguments, Mapping):
            raise ToolExecutionError(f"tool {tool.name} arguments must be an object")
        schema = tool.input_schema
        required = {
            key
            for key, spec in schema.items()
            if isinstance(spec, dict) and spec.get("required")
        }
        missing = required - set(arguments)
        if missing:
            raise ToolExecutionError(
                f"tool {tool.name} missing required arguments: {sorted(missing)}"
            )
        if tool.strict:
            unknown = set(arguments) - set(schema)
            if unknown:
                raise ToolExecutionError(f"tool {tool.name} got unknown arguments: {sorted(unknown)}")
        for key, value in arguments.items():
            spec = schema.get(key)
            if not isinstance(spec, dict) or value is None:
                continue
            expected = spec.get("type")
            if expected and (not isinstance(value, _TYPES[expected]) or (expected in ("integer", "number") and isinstance(value, bool))):
                raise ToolExecutionError(f"tool {tool.name}: {key} must be {expected}")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolExecutionError(f"tool {tool.name}: {key} must be one of {list(spec['enum'])}")
            if isinstance(value, str) and len(value) > spec.get("max_length", 100_000):
                raise ToolExecutionError(f"tool {tool.name}: {key} is too long")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value < spec.get("min", float("-inf")) or value > spec.get("max", float("inf")):
                    raise ToolExecutionError(f"tool {tool.name}: {key} out of range")

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


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)
