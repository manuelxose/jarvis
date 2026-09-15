"""Validated, immutable runtime configuration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Union


_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class RuntimeSettings:
    command_deadline_ms: int = 500


@dataclass(frozen=True)
class ProviderSettings:
    fast_model_api_key: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class MemorySettings:
    pass


@dataclass(frozen=True)
class SecuritySettings:
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    runtime: RuntimeSettings
    providers: ProviderSettings
    memory: MemorySettings
    security: SecuritySettings

    def public_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "runtime": {"command_deadline_ms": self.runtime.command_deadline_ms},
            "providers": {
                "fast_model_api_key": (
                    "<redacted>" if self.providers.fast_model_api_key is not None else None
                )
            },
            "memory": {},
            "security": {},
        }


def load_config(
    path: Union[str, Path], environ: Optional[Mapping[str, str]] = None
) -> RuntimeConfig:
    """Load configuration from *path*, expanding provider secrets from *environ*."""
    with Path(path).open(encoding="utf-8") as config_file:
        document = json.load(config_file)
    if not isinstance(document, dict):
        raise ValueError("configuration must be a JSON object")

    runtime_data = _section(document, "runtime", required=True)
    providers_data = _section(document, "providers")
    _section(document, "memory")
    _section(document, "security")

    deadline = runtime_data.get("command_deadline_ms", 500)
    if isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0:
        raise ValueError("runtime.command_deadline_ms must be a positive integer")

    api_key = providers_data.get("fast_model_api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("providers.fast_model_api_key must be a string")

    return RuntimeConfig(
        runtime=RuntimeSettings(command_deadline_ms=deadline),
        providers=ProviderSettings(
            fast_model_api_key=_resolve_secret(
                api_key, os.environ if environ is None else environ
            )
        ),
        memory=MemorySettings(),
        security=SecuritySettings(),
    )


def _section(
    document: Mapping[str, Any], name: str, required: bool = False
) -> Mapping[str, Any]:
    if name not in document:
        if required:
            raise ValueError("configuration requires a runtime object")
        return {}
    section = document[name]
    if not isinstance(section, dict):
        raise ValueError("configuration section {!r} must be an object".format(name))
    return section


def _resolve_secret(value: Optional[str], environ: Mapping[str, str]) -> Optional[str]:
    if value is None:
        return None
    match = _ENV_VALUE.fullmatch(value)
    if not match:
        return value
    name = match.group(1)
    if name not in environ:
        raise ValueError("environment variable {!r} is required".format(name))
    return environ[name]
