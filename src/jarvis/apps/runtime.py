"""Foundation runtime composition without hardware or provider adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import RuntimeConfig
from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.lifecycle import Supervisor
from jarvis.core.state import RuntimeState
from jarvis.observability.tracing import InteractionTrace


@dataclass(frozen=True)
class _HealthComponent:
    name: str
    report: HealthReport
    required: bool = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthReport:
        return self.report


@dataclass
class FoundationRuntime:
    """Compose the foundation checks and expose their supervisor diagnostics."""

    config: RuntimeConfig
    supervisor: Supervisor
    trace: InteractionTrace

    @property
    def state(self) -> RuntimeState:
        return self.supervisor.state

    async def check(self) -> dict[str, object]:
        await self.supervisor.start()
        return self.diagnostics()

    async def stop(self) -> None:
        await self.supervisor.stop()

    def diagnostics(self) -> dict[str, object]:
        components = {
            name: {
                "status": report.status.value,
                "detail": report.detail,
                "required": report.required,
                "retryable": report.retryable,
            }
            for name, report in self.supervisor.health_snapshot().items()
        }
        return {
            "state": self.state.value,
            "components": components,
            "trace": self.trace.as_dict(),
        }


def create_foundation_runtime(config: RuntimeConfig) -> FoundationRuntime:
    """Create health-only foundation composition until real adapters are supplied."""
    storage_path = Path.cwd()
    storage_status = (
        HealthStatus.HEALTHY
        if storage_path.is_dir() and os.access(storage_path, os.W_OK)
        else HealthStatus.FAILED
    )
    storage_detail = (
        "storage path available"
        if storage_status is HealthStatus.HEALTHY
        else "storage path is unavailable"
    )
    unavailable = "unavailable: adapter is not configured in the foundation runtime"
    components = (
        _HealthComponent(
            "configuration",
            HealthReport("configuration", HealthStatus.HEALTHY, "configuration loaded"),
            required=True,
        ),
        _HealthComponent(
            "storage path",
            HealthReport("storage path", storage_status, storage_detail, required=True),
            required=True,
        ),
        *(
            _HealthComponent(
                name,
                HealthReport(name, HealthStatus.DEGRADED, unavailable, required=False),
            )
            for name in (
                "audio input",
                "audio output",
                "STT",
                "TTS",
                "fast model",
                "Hermes",
                "network",
            )
        ),
    )
    return FoundationRuntime(config, Supervisor(components), InteractionTrace("foundation-startup"))
