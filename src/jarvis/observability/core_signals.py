"""Session-sequenced visual-core telemetry (``core.signal`` / ``core.snapshot``).

Producers report *measured* playback and microphone facts here; the UI
visual-core machine consumes them. ``seq`` allocation and ``hub.publish`` share
one lock so cross-thread delivery order equals ``seq`` order. Every method is
a no-op returning ``False`` once :meth:`CoreSignals.close` was called, and a
failing producer can never break the voice pipeline (the hub swallows errors).
"""

from __future__ import annotations

import math
import sys
import threading
import time
import uuid
from array import array
from typing import Any, Callable, Iterable, Optional

from jarvis.observability.event_hub import EventHub, hub as default_hub

MAX_FENCES = 128
MIC_MIN_INTERVAL_S = 0.05
_OUTCOMES = ("ended", "cancelled", "failed")
CANDIDATE_MAX_MS = 2000.0
MAX_APPS = 32
_STARTUP_PHASES = ("idle", "acknowledged", "initializing", "announcing", "ready", "degraded", "failed", "cancelled")
_STARTUP_TERMINAL = ("ready", "degraded", "failed")
_APP_STATUS = {
    "launched": "ready",
    "opened": "ready",
    "already_running": "ready",
    "failed": "failed",
    "timeout": "failed",
    "pending": "pending",
}


def pcm_envelope(frames: bytes) -> tuple[float, float]:
    """Return (rms, abs-peak) of int16 little-endian PCM, each normalised to 0..1."""
    usable = len(frames) - (len(frames) % 2)
    if usable <= 0:
        return 0.0, 0.0
    samples = array("h")
    samples.frombytes(bytes(frames[:usable]))
    if sys.byteorder == "big":
        samples.byteswap()
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
    peak = max(max(samples), -min(samples)) / 32768.0
    return min(1.0, rms), min(1.0, peak)


def _level(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))


def _age(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


class CoreSignals:
    def __init__(
        self,
        hub: EventHub = default_hub,
        clock: Callable[[], float] = time.monotonic,
        session: Optional[str] = None,
    ) -> None:
        self._hub = hub
        self._clock = clock
        self.session = session or uuid.uuid4().hex
        self.generation = 1
        self._seq = 0
        self._lock = threading.RLock()
        self._closed = False
        self._turn: Optional[str] = None
        self._implicit = False
        self._playback: Optional[dict[str, str]] = None
        self._latest_playback: dict[str, str] = {}
        self._fenced: list[str] = []
        self._cancelled = False
        self._turn_ended = False
        self._overflow = False
        self._failed_turn = False
        self._listening = False
        self._output: Optional[tuple[float, float, float, float]] = None
        self._mic: Optional[tuple[float, float, float, float]] = None
        self._last_mic_emit: Optional[float] = None
        self._startup = "idle"
        self._partial = False
        self._apps: Optional[list[dict[str, str]]] = None
        self._candidate: Optional[tuple[str, float]] = None
        self._stopped = False
        self._cleaned_up = False
        self.dropped_late = 0
        self.throttled = 0
        self.rejected = 0

    # -- lifecycle ---------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._closed = True

    def advance_generation(self) -> int:
        """Start a new runtime generation: fence live playback and clear per-turn state so late audio from the old one is rejected."""
        with self._lock:
            if self._playback is not None:
                self._fence(self._playback["id"])
            self.generation += 1
            self._turn, self._implicit, self._playback = None, False, None
            self._output = self._mic = self._last_mic_emit = None
            self._cancelled = self._turn_ended = self._failed_turn = self._listening = False
            return self.generation

    def begin_activation(self) -> int:
        """Start a new session activation: new generation plus a clean startup/stop state, published so the UI re-establishes."""
        with self._lock:
            generation = self.advance_generation()
            self._stopped = self._cleaned_up = self._partial = False
            self._startup, self._apps, self._candidate = "idle", None, None
            self._seq += 1  # the clap candidate already used the last seq; a same-watermark higher-generation snapshot is rejected as stale
            self.publish_snapshot()
            return generation

    def _emit(self, kind: str, age_ms: float = 0.0, **fields: Any) -> bool:
        if self._closed:
            return False
        self._seq += 1
        self._hub.publish(
            "core.signal", session=self.session, generation=self.generation, seq=self._seq, age_ms=age_ms, kind=kind, **fields
        )
        return True

    def _fence(self, playback_id: str) -> None:
        if playback_id in self._fenced:
            return
        if len(self._fenced) >= MAX_FENCES:
            self._overflow = True
            return
        self._fenced.append(playback_id)

    # -- turns -------------------------------------------------------------------
    def turn_start(self, turn: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            # The UI machine treats an ended/cancelled/superseded turn as terminal for its generation.
            if self._turn_ended or self._cancelled or (self._turn is not None and self._turn != turn):
                self.generation += 1
            self._turn, self._implicit = turn, False
            self._cancelled = self._turn_ended = self._failed_turn = False
            return self._emit("turn-start", turn=turn)

    def turn_end(self, turn: str, ok: bool) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._turn == turn:
                self._turn, self._implicit = None, False
            self._turn_ended, self._failed_turn = True, not ok
            return self._emit("turn-end", turn=turn, ok=bool(ok))

    def cancel_turn(self, turn: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            if self._playback is not None:
                self._fence(self._playback["id"])
                self._playback = None
            self._output = None
            if self._turn == turn:
                self._turn, self._implicit = None, False
            self._cancelled = True
            return self._emit("cancel-turn", turn=turn)

    # -- playback ----------------------------------------------------------------
    def output(self, turn: str, level: float, peak: float, age_ms: float = 0.0, latency_ms: Optional[float] = None) -> bool:
        lv, pk = _level(level), _level(peak)
        with self._lock:
            if self._closed:
                return False
            if lv is None or pk is None:
                self.rejected += 1
                return False
            if self._playback is None:
                previous = self._latest_playback.get(turn)
                if previous is not None and previous in self._fenced:
                    self.dropped_late += 1
                    return False
                if self._turn is None:
                    self.turn_start(turn)
                    self._implicit = True
                playback_id = f"{turn}:1"
                self._playback = {"id": playback_id, "turn": turn}
                self._latest_playback[turn] = playback_id
                if len(self._latest_playback) > MAX_FENCES:
                    self._latest_playback.pop(next(iter(self._latest_playback)))
                self._emit("playback-start", turn=turn, playback=playback_id, confirmed=True, latency_ms=latency_ms)
            elif self._playback["turn"] != turn:
                self.dropped_late += 1
                return False
            age = _age(age_ms)
            self._output = (lv, pk, age, self._clock())
            return self._emit("output", age, turn=turn, playback=self._playback["id"], level=lv, peak=pk)

    def playback_end(self, turn: str, outcome: str) -> bool:
        if outcome not in _OUTCOMES:
            return False
        with self._lock:
            if self._closed or self._playback is None:
                return False
            playback = self._playback
            self._playback, self._output = None, None
            self._fence(playback["id"])
            self._emit("playback-end", turn=playback["turn"], playback=playback["id"], outcome=outcome)
            if self._implicit and self._turn == playback["turn"]:
                self.turn_end(playback["turn"], outcome != "failed")
            return True

    # -- capture / microphone ----------------------------------------------------
    def capture(self, active: bool) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._listening = bool(active)
            return self._emit("capture", active=bool(active))

    def microphone(self, level: float, peak: float, age_ms: float = 0.0) -> bool:
        lv, pk = _level(level), _level(peak)
        with self._lock:
            if self._closed:
                return False
            if lv is None or pk is None:
                self.rejected += 1
                return False
            now = self._clock()
            if self._last_mic_emit is not None and now - self._last_mic_emit < MIC_MIN_INTERVAL_S:
                self.throttled += 1
                return False
            self._last_mic_emit = now
            age = _age(age_ms)
            self._mic = (lv, pk, age, now)
            return self._emit("microphone", age, level=lv, peak=pk)

    # -- startup lifecycle -------------------------------------------------------
    def _fence_playback(self) -> None:
        if self._playback is not None:
            self._fence(self._playback["id"])
            self._playback = None
        self._output = self._mic = None

    def candidate(self, candidate_id: str, active: bool, remaining_ms: float) -> bool:
        with self._lock:
            if self._closed:
                return False
            remaining = _age(remaining_ms)
            remaining = min(CANDIDATE_MAX_MS, remaining)
            if active:
                self._candidate = (str(candidate_id), self._clock() + remaining / 1000.0)
            else:
                self._candidate = None
            return self._emit("candidate", candidate=str(candidate_id), active=bool(active), remaining_ms=remaining)

    def startup(self, phase: str, partial: bool = False) -> bool:
        with self._lock:
            if self._closed:
                return False
            if phase not in _STARTUP_PHASES:
                self.rejected += 1
                return False
            self._startup = phase
            self._partial = self._partial or bool(partial) or phase == "degraded"
            self._candidate = None
            if phase == "cancelled":
                self._stopped, self._cleaned_up = True, False
                self._fence_playback()
            elif phase == "failed":
                self._output = self._mic = None
            return self._emit("startup", phase=phase, partial=self._partial)

    def stop(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._stopped, self._cleaned_up = True, False
            self._fence_playback()
            self._turn, self._implicit, self._candidate = None, False, None
            return self._emit("stop")

    def cleaned_up(self) -> bool:
        with self._lock:
            if self._closed or not self._stopped:
                return False
            self._cleaned_up = True
            if self._startup not in _STARTUP_TERMINAL and self._startup != "cancelled":
                self._startup = "idle"
            return self.publish_snapshot()

    def apps(self, results: Iterable[tuple[str, str]]) -> bool:
        with self._lock:
            if self._closed:
                return False
            out: list[dict[str, str]] = []
            seen: set[str] = set()
            for item in results:
                try:
                    app_id, raw = item
                except (TypeError, ValueError):
                    continue
                status = _APP_STATUS.get(raw) if isinstance(raw, str) else None
                if status is None or not isinstance(app_id, str) or not app_id or app_id in seen:
                    continue
                seen.add(app_id)
                out.append({"id": app_id, "status": status})
                if len(out) >= MAX_APPS:
                    break
            self._apps = out
            return self.publish_snapshot()

    # -- snapshot ----------------------------------------------------------------
    def _sample(self, entry: Optional[tuple[float, float, float, float]], now: float) -> Optional[dict[str, float]]:
        if entry is None:
            return None
        level, peak, age, at = entry
        return {"level": level, "peak": peak, "age_ms": age + max(0.0, now - at) * 1000.0}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            candidate = (
                {"id": self._candidate[0], "remaining_ms": (self._candidate[1] - now) * 1000.0}
                if self._candidate is not None and self._candidate[1] > now
                else None
            )
            playback = (
                {"id": self._playback["id"], "turn": self._playback["turn"], "confirmed": True} if self._playback else None
            )
            return {
                "session": self.session,
                "replaces_session": None,
                "generation": self.generation,
                "watermark": self._seq,
                "age_ms": 0,
                "facts": {
                    "turn": self._turn,
                    "thinking": self._turn is not None and self._playback is None,
                    "executing": None,
                    "listening": self._listening,
                    "generated": False,
                    "playback": playback,
                    "startup": self._startup,
                    "partial": self._partial,
                    "apps": [dict(a) for a in self._apps] if self._apps is not None else None,
                    "failed_turn": self._failed_turn,
                    "persistent_error": self._startup == "failed",
                },
                "fences": {
                    "cancelled": self._cancelled,
                    "stopped": self._stopped,
                    "cleaned_up": self._cleaned_up,
                    "turn_ended": self._turn_ended,
                    "overflow": self._overflow,
                    "playbacks": list(self._fenced),
                    "commands": [],
                    "candidates": [],
                },
                "output": self._sample(self._output, now),
                "microphone": self._sample(self._mic, now),
                "candidate": candidate,
                "success_remaining_ms": 0,
                "error_remaining_ms": 0,
            }

    def publish_snapshot(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            return self._hub.publish("core.snapshot", **self.snapshot()) is not None
