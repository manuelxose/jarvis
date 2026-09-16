"""Monotonic timing for one interaction trace."""

from __future__ import annotations

import time
from typing import Any


_STAGES = frozenset(
    {
        "wake_ms",
        "speech_start",
        "speech_end",
        "vad_finalize_ms",
        "stt_first_partial_ms",
        "stt_final_ms",
        "routing_ms",
        "memory_lookup_ms",
        "provider_selection_ms",
        "agent_first_token_ms",
        "agent_total_ms",
        "tts_first_audio_ms",
        "tts_total_ms",
        "playback_start_ms",
        "total_request_ms",
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
        first_audio = self.elapsed_ms("speech_end", "playback_start_ms")
        if first_audio is not None:
            result["speech_end_to_first_audio_ms"] = first_audio
        return result
