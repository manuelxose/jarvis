"""Triple-clap gesture detection with cheap, local signal processing.

A clap is a loud broadband transient: a sharp attack (well above the tracked
noise floor within ~20 ms), a fast decay (back under -12 dB of its peak within
~120 ms) and a large share of its energy above 1.5 kHz. Speech and music fail
at least one of those tests (sustained vowels/notes, low spectral centroid).

The gesture is ``claps_required`` claps (default two) inside a configurable
window, preceded by quiet: speech or music just before the first clap vetoes
it. The first accepted clap raises ``on_candidate`` (the sentinel starts
speculative warm-up); ``on_candidate_expired`` fires when no confirming clap
arrives in time. With two claps the gesture confirms on the second clap
(optionally after ``confirm_quiet_seconds``); with three it waits for a
possible 4th clap so rhythms are rejected. After a gesture the detector is in
cooldown, so extra claps never restart the activation. ``numpy`` is lazy-imported so
the contract tier stays importable without it.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

_HOP_SECONDS = 0.01
_HF_CUTOFF_HZ = 1500.0


@dataclass(frozen=True)
class ClapTuning:
    """Detector parameters; every field is owner-configurable."""

    sample_rate: int = 16000
    claps_required: int = 2  # 2 (default) or 3
    # 0..1: higher accepts quieter/softer claps (and more false positives).
    sensitivity: float = 0.5
    # Absolute floor so distant taps and keyboard clicks never count.
    min_peak_dbfs: float = -32.0
    window_seconds: float = 2.0  # first to third clap
    min_gap_seconds: float = 0.12  # closer onsets are the same clap's reverb
    max_gap_seconds: float = 0.9  # also the first clap's candidate lifetime
    max_gap_ratio: float = 2.5  # spacing regularity (longest / shortest gap)
    quiet_before_seconds: float = 0.8
    quiet_after_seconds: float = 0.35  # three-clap mode only
    # Two-clap mode: wait this long after the 2nd clap for a rhythm-breaking 3rd
    # transient before confirming (0 = confirm on the 2nd clap, lowest latency).
    confirm_quiet_seconds: float = 0.0
    max_decay_seconds: float = 0.12
    min_hf_ratio: float = 0.2
    confidence_threshold: float = 0.55
    cooldown_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.sample_rate < 8000:
            raise ValueError("sample_rate must be at least 8000")
        if not 0.0 <= self.sensitivity <= 1.0:
            raise ValueError("sensitivity must be within 0..1")
        if not 0.0 < self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be within (0, 1]")
        if not 0 < self.min_gap_seconds < self.max_gap_seconds:
            raise ValueError("gaps must satisfy 0 < min_gap < max_gap")
        if self.window_seconds < 2 * self.min_gap_seconds:
            raise ValueError("window_seconds is too short for three claps")
        if self.min_peak_dbfs >= 0:
            raise ValueError("min_peak_dbfs must be negative")
        if self.claps_required not in (2, 3):
            raise ValueError("claps_required must be 2 or 3")

    @property
    def onset_ratio(self) -> float:
        # sensitivity 0 -> 16x (24 dB) over the floor, 1 -> 4x (12 dB)
        return 16.0 * (0.25 ** self.sensitivity)


@dataclass(frozen=True)
class Clap:
    time: float
    peak_dbfs: float
    confidence: float
    features: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ClapGesture:
    time: float  # stream time of the confirmation
    confidence: float
    claps: tuple[Clap, ...]

    @property
    def latency_seconds(self) -> float:
        """Last clap to confirmation (the gesture's detection latency)."""
        return self.time - self.claps[-1].time


def _dbfs(rms: float) -> float:
    return 20.0 * math.log10(max(rms, 1e-9))


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


class ClapDetector:
    """Streaming detector: feed mono PCM, get a :class:`ClapGesture` or None."""

    def __init__(self, tuning: ClapTuning | None = None) -> None:
        import numpy as np  # noqa: PLC0415

        self._np = np
        self.tuning = tuning or ClapTuning()
        self._hop = max(1, int(self.tuning.sample_rate * _HOP_SECONDS))
        self._pending = np.zeros(0, dtype=np.float32)
        self._hops = 0
        self._floor = 1e-3
        self._recent_rms: deque[float] = deque(maxlen=3)
        # ~60 ms of samples so the spectral test sees the whole attack
        self._ring: deque[Any] = deque(maxlen=6)
        self._event: Optional[dict[str, Any]] = None
        self._claps: list[Clap] = []
        self._last_onset = -1e9
        self._suspended_until = -1e9
        self._suspended = False
        self.rejected: list[dict[str, float]] = []  # recent non-clap transients (diagnostics)
        # Called from the audio thread; keep them cheap (schedule work elsewhere).
        self.on_candidate: Optional[Callable[[Clap], None]] = None
        self.on_candidate_expired: Optional[Callable[[str], None]] = None
        self.accepted: list[Clap] = []  # recent single claps (diagnostics, calibration)

    # -- control ---------------------------------------------------------
    @property
    def now(self) -> float:
        return self._hops * _HOP_SECONDS

    def suspend(self) -> None:
        """Ignore everything (startup music, Jarvis speaking) until resume()."""
        self._suspended = True
        self._clear("suspended")
        self._event = None

    def resume(self, *, cooldown: bool = True) -> None:
        self._suspended = False
        self._clear("resumed")
        self._event = None
        if cooldown:
            self._suspended_until = self.now + self.tuning.cooldown_seconds

    # -- streaming -------------------------------------------------------
    def feed(self, pcm: Any) -> Optional[ClapGesture]:
        """Consume int16 bytes or a float/int16 array; return a gesture if confirmed."""
        np = self._np
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            samples = np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32) / 32768.0
        else:
            samples = np.asarray(pcm)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            if samples.dtype == np.int16:
                samples = samples.astype(np.float32) / 32768.0
            else:
                samples = samples.astype(np.float32, copy=False)
        if self._pending.size:
            samples = np.concatenate([self._pending, samples])
        usable = samples.size - samples.size % self._hop
        self._pending = samples[usable:].copy()
        if not usable:
            return None
        hops = samples[:usable].reshape(-1, self._hop)
        levels = np.sqrt(np.mean(hops * hops, axis=1))
        gesture: Optional[ClapGesture] = None
        for hop, level in zip(hops, levels):
            found = self._step(hop, float(level))
            gesture = gesture or found
        return gesture

    def _step(self, hop: Any, level: float) -> Optional[ClapGesture]:
        self._hops += 1
        self._ring.append(hop)
        now = self.now
        if self._suspended or now < self._suspended_until:
            self._track_floor(level)
            self._recent_rms.append(level)
            return None

        gesture = None
        if self._event is not None:
            self._continue_event(level, now)
        elif self._is_onset(level, now):
            self._event = {
                "start": now,
                "peak": level,
                "peak_hop": self._hops,
                "floor": self._floor,
                "rise": level / max(max(self._recent_rms, default=self._floor), 1e-6),
                "hf": self._hf_ratio(),
            }
        else:
            self._track_floor(level)
            gesture = self._maybe_confirm(now)
        self._recent_rms.append(level)
        return gesture

    def _track_floor(self, level: float) -> None:
        # Fast down, slow up: the floor follows quiet quickly and music slowly.
        rate = 0.3 if level < self._floor else 0.02
        self._floor = max(1e-4, self._floor + rate * (level - self._floor))

    def _is_onset(self, level: float, now: float) -> bool:
        if _dbfs(level) < self.tuning.min_peak_dbfs:
            return False
        if level < self._floor * self.tuning.onset_ratio:
            return False
        previous = max(self._recent_rms, default=self._floor)
        # Sharp attack: >= ~8 dB within 30 ms. Speech ramps up far more slowly.
        return level >= 2.5 * previous

    def _continue_event(self, level: float, now: float) -> None:
        event = self._event
        assert event is not None
        if level > event["peak"] and self._hops - event["peak_hop"] <= 2:
            event["peak"] = level
            event["peak_hop"] = self._hops
            event["hf"] = max(event["hf"], self._hf_ratio())
            return
        duration = now - event["start"]
        if level < event["peak"] * 0.25:
            self._event = None
            self._finish_event(event, duration)
        elif duration > self.tuning.max_decay_seconds:
            # Sustained sound: not a clap. It still counts as a transient that
            # breaks any sequence in progress (music, a loud word).
            self._event = None
            self._reject(event, duration, "sustained")

    def _finish_event(self, event: dict[str, Any], decay: float) -> None:
        tuning = self.tuning
        snr = event["peak"] / max(event["floor"], 1e-6)
        rise_score = _clamp01((math.log10(event["rise"]) - math.log10(2.5)) / (math.log10(20) - math.log10(2.5)))
        decay_score = _clamp01(1.0 - decay / tuning.max_decay_seconds)
        hf_score = _clamp01((event["hf"] - tuning.min_hf_ratio * 0.6) / (1.0 - tuning.min_hf_ratio * 0.6))
        snr_score = _clamp01(math.log10(snr / tuning.onset_ratio + 1e-9) / 1.0 + 0.5)
        confidence = (rise_score * decay_score * hf_score * snr_score) ** 0.25
        features = {
            "peak_dbfs": round(_dbfs(event["peak"]), 1),
            "snr_db": round(_dbfs(snr), 1),
            "rise": round(event["rise"], 2),
            "decay_s": round(decay, 3),
            "hf_ratio": round(event["hf"], 3),
        }
        if event["hf"] < tuning.min_hf_ratio or confidence < tuning.confidence_threshold:
            self._reject(event, decay, "shape", features)
            return
        start = event["start"]
        if start - self._last_onset < tuning.min_gap_seconds:
            return  # reverb / double hit of the same clap
        self._last_onset = start
        if self._claps and start - self._claps[-1].time > tuning.max_gap_seconds:
            self._clear("timeout")
        clap = Clap(start, features["peak_dbfs"], round(confidence, 3), features)
        self.accepted.append(clap)
        del self.accepted[:-20]
        if not self._claps and self._onset_before(start):
            return  # a hit inside music/speech never starts a candidate
        self._claps.append(clap)
        if len(self._claps) == 1 and self.on_candidate is not None:
            self.on_candidate(clap)
        if len(self._claps) > tuning.claps_required:
            # One more than required: a rhythm (music, applause), not the gesture.
            self._clear("rhythm")
            self._suspended_until = self.now + tuning.quiet_before_seconds

    def _clear(self, reason: str) -> None:
        pending = bool(self._claps)
        self._claps.clear()
        if pending and self.on_candidate_expired is not None:
            self.on_candidate_expired(reason)

    def _reject(self, event: dict[str, Any], duration: float, reason: str, features: dict | None = None) -> None:
        start = event["start"]
        self.rejected.append({"time": round(start, 3), "reason": reason, "duration": round(duration, 3), **(features or {})})
        del self.rejected[:-20]
        self._clear("interrupted")
        self._last_onset = start

    def _maybe_confirm(self, now: float) -> Optional[ClapGesture]:
        tuning = self.tuning
        if len(self._claps) == 1 and now - self._claps[0].time > tuning.max_gap_seconds:
            self._clear("timeout")  # the confirming clap never came
            return None
        if len(self._claps) != tuning.claps_required:
            return None
        claps = tuple(self._claps)
        gaps = [b.time - a.time for a, b in zip(claps, claps[1:])]
        if tuning.claps_required == 2:
            wait = tuning.confirm_quiet_seconds
        else:
            # Wait long enough for a 4th clap at the owner's own tempo to show up.
            wait = max(tuning.quiet_after_seconds, 1.25 * max(gaps))
        if now - claps[-1].time < wait:
            return None
        self._claps.clear()
        span = claps[-1].time - claps[0].time
        if span > tuning.window_seconds or max(gaps) / max(min(gaps), 1e-3) > tuning.max_gap_ratio:
            self._notify_expired("irregular")
            return None
        if self._onset_before(claps[0].time):
            self._notify_expired("not quiet before")
            return None
        confidence = sum(c.confidence for c in claps) / len(claps)
        self._suspended_until = now + tuning.cooldown_seconds
        return ClapGesture(round(now, 3), round(confidence, 3), claps)

    def _notify_expired(self, reason: str) -> None:
        if self.on_candidate_expired is not None:
            self.on_candidate_expired(reason)

    def _onset_before(self, first: float) -> bool:
        # Sustained sound or a clap-like-but-failing hit (a snare) shortly before
        # the first clap means the claps are part of music or speech. A short
        # low-frequency thump (knock, footstep, speaker pop) does not count.
        window = self.tuning.quiet_before_seconds
        return any(
            first - window <= r["time"] < first
            and (r["reason"] == "sustained" or r.get("hf_ratio", 1.0) >= self.tuning.min_hf_ratio * 0.5)
            for r in self.rejected
        )

    def _hf_ratio(self) -> float:
        np = self._np
        frame = np.concatenate(list(self._ring)[-3:])
        spectrum = np.abs(np.fft.rfft(frame * np.hanning(frame.size))) ** 2
        freqs = np.fft.rfftfreq(frame.size, 1.0 / self.tuning.sample_rate)
        total = float(spectrum[freqs > 80].sum())
        return float(spectrum[freqs >= _HF_CUTOFF_HZ].sum()) / total if total > 0 else 0.0


# -- calibration -------------------------------------------------------------

def calibration_path(base: Path) -> Path:
    return base / "clap_calibration.json"


def load_calibration(tuning: ClapTuning, path: Path) -> ClapTuning:
    """Apply a saved calibration (min_peak_dbfs / sensitivity) over *tuning*."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return tuning
    updates = {k: float(data[k]) for k in ("min_peak_dbfs", "sensitivity", "min_hf_ratio") if isinstance(data.get(k), (int, float))}
    if all(isinstance(data.get(k), (int, float)) for k in ("noise_p99_dbfs", "softest_clap_dbfs")):
        # Re-derive with the current rule so older calibration files get stricter too.
        updates["min_peak_dbfs"] = min_peak_for(float(data["noise_p99_dbfs"]), float(data["softest_clap_dbfs"]))
    try:
        return replace(tuning, **updates)
    except ValueError:
        return tuning


def min_peak_for(noise_p99_dbfs: float, softest_clap_dbfs: float) -> float:
    """Loudness gate: 9 dB under the softest clap, never within 12 dB of the room.

    With two claps, loudness is the strongest separator from keyboard clicks and
    other short transients (their shape can be clap-like); the owner's claps are
    far louder than typing at the microphone.
    """
    return round(min(max(noise_p99_dbfs + 12.0, softest_clap_dbfs - 9.0), -3.0), 1)


def calibrate(noise: Any, claps: Any, tuning: ClapTuning | None = None) -> dict[str, Any]:
    """Derive owner-specific thresholds from an ambient and a clapping recording.

    *noise* and *claps* are float32 mono arrays at ``tuning.sample_rate``.
    The minimum peak is set 9 dB under the softest detected clap (and at least
    12 dB over the room's loud tail), so the owner's claps pass and quieter
    transients such as typing do not.
    """
    import numpy as np  # noqa: PLC0415

    tuning = tuning or ClapTuning()
    hop = int(tuning.sample_rate * _HOP_SECONDS)
    usable = noise[: noise.size - noise.size % hop].reshape(-1, hop)
    noise_levels = np.sqrt(np.mean(usable * usable, axis=1)) if usable.size else np.array([1e-4])
    noise_p99 = _dbfs(float(np.percentile(noise_levels, 99)))

    permissive = replace(tuning, min_peak_dbfs=-60.0, sensitivity=1.0, confidence_threshold=0.3, cooldown_seconds=0.5, min_hf_ratio=0.1)
    detector = ClapDetector(permissive)
    peaks: list[float] = []
    hf: list[float] = []
    gestures = 0
    step = hop * 10
    tail = np.zeros(int(tuning.sample_rate * 0.6), dtype=np.float32)
    for chunk in [claps[i:i + step] for i in range(0, claps.size, step)] + [tail]:
        seen = len(detector.accepted)
        gestures += detector.feed(chunk) is not None
        peaks.extend(c.peak_dbfs for c in detector.accepted[seen:])
        hf.extend(c.features["hf_ratio"] for c in detector.accepted[seen:])
        del detector.accepted[:]
    if not peaks:
        return {"ok": False, "reason": "no claps detected", "noise_p99_dbfs": round(noise_p99, 1)}
    softest = min(peaks)
    return {
        "ok": softest > noise_p99 + 15.0,
        "noise_p99_dbfs": round(noise_p99, 1),
        "softest_clap_dbfs": round(softest, 1),
        "min_peak_dbfs": min_peak_for(noise_p99, softest),
        # Mics with DSP noise suppression dull the clap's top end; speech stays < 0.1.
        "min_hf_ratio": round(min(tuning.min_hf_ratio, max(0.15, 0.75 * min(hf))), 3),
        "sensitivity": tuning.sensitivity,
        "gestures_seen": gestures,
    }


def tuning_dict(tuning: ClapTuning) -> dict[str, Any]:
    return asdict(tuning)
