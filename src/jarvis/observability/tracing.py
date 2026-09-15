"""Monotonic timing for one interaction trace."""

from __future__ import annotations

import time
from typing import Any


_STAGES = frozenset(
    {
        "speech_start",
        "speech_end",
        "transcript_ready",
        "model_start",
        "first_token",
        "response_ready",
        "synthesis_start",
        "playback_start",
        "playback_end",
    }
)


class InteractionTrace:
    """Collect allowed interaction stages as milliseconds relative to creation."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._started_at = time.perf_counter()
        self._stages_ms: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        """Record an allowed stage using the monotonic clock."""
        if stage not in _STAGES:
            raise ValueError(f"Unsupported trace stage: {stage}")
        self._stages_ms[stage] = round((time.perf_counter() - self._started_at) * 1000, 3)

    def elapsed_ms(self, start: str, end: str) -> float | None:
        """Return elapsed milliseconds when both stages have been recorded."""
        if start not in self._stages_ms or end not in self._stages_ms:
            return None
        return max(0.0, self._stages_ms[end] - self._stages_ms[start])

    def as_dict(self) -> dict[str, Any]:
        """Return scalar trace data suitable for JSON serialization."""
        result: dict[str, Any] = {"trace_id": self.trace_id, "stages_ms": dict(self._stages_ms)}
        first_audio = self.elapsed_ms("speech_end", "playback_start")
        if first_audio is not None:
            result["speech_end_to_first_audio_ms"] = first_audio
        return result
