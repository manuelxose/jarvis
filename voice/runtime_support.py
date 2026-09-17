from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Iterator


@dataclass(frozen=True)
class CaptureBackend:
    """Resolved microphone path shared by health checks and real capture."""

    name: str
    device_index: int | None
    device_name: str
    sample_rate: int
    fallback_reason: str | None = None

    @property
    def is_wasapi(self) -> bool:
        return self.name.upper() == "WASAPI"

    def describe(self) -> str:
        fallback = f" fallback_reason={self.fallback_reason!r}" if self.fallback_reason else ""
        return (
            f"backend={self.name} device={self.device_name!r} "
            f"index={self.device_index} sample_rate={self.sample_rate}{fallback}"
        )


@contextmanager
def timed_phase(logger, phase: str) -> Iterator[None]:
    """Log one phase duration from a monotonic clock."""
    started = time.monotonic()
    try:
        yield
    finally:
        logger.info("timing phase=%s duration_seconds=%.3f", phase, time.monotonic() - started)
