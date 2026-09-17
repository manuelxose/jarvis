from __future__ import annotations

from collections.abc import Callable, Iterable
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass
class DetectionStats:
    """Monotonic diagnostic state; a detection must survive later frames."""

    peak_score: float = 0.0
    detections: int = 0

    def observe(self, score: float, threshold: float) -> bool:
        self.peak_score = max(self.peak_score, score)
        if score >= threshold:
            self.detections += 1
            return True
        return False


def resample_to_16khz(samples, source_rate: int):
    """Resample one native-rate PCM16 chunk to OpenWakeWord's 16 kHz input."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.int16)
    if source_rate == 16000 or samples.size == 0:
        return samples
    output_size = max(1, round(samples.size * 16000 / source_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(output_size, dtype=np.float64) * source_rate / 16000
    target_positions = np.minimum(target_positions, samples.size - 1)
    return np.rint(np.interp(target_positions, source_positions, samples)).astype(np.int16)


_UNSAFE_INPUT_HINTS = (
    "stereo mix",
    "mezcla stereo",
    "mezcla estéreo",
    "mezcla",
    "output",
    "mapper",
    "primary sound capture",
    "controlador primario",
    "microsoft sound mapper",
    "asignador de sonido",
)


def native_chunk_size(sample_rate: int) -> int:
    """Return one 80 ms capture chunk for OpenWakeWord's 16 kHz model frame."""
    return round(sample_rate * 1280 / 16000)


def filter_input_candidates(
    devices: Iterable[Mapping[str, object]],
    preferred: Iterable[int | None],
) -> list[int]:
    valid: list[int] = []
    for device in devices:
        index = int(device["index"])
        name = str(device.get("name", "")).casefold()
        if int(device.get("maxInputChannels", 0)) < 1:
            continue
        if any(hint in name for hint in _UNSAFE_INPUT_HINTS):
            continue
        valid.append(index)

    ordered: list[int] = []
    for index in [candidate for candidate in preferred if candidate is not None] + valid:
        if index in valid and index not in ordered:
            ordered.append(index)
    return ordered


def select_device(
    candidates: Iterable[int | None],
    probe: Callable[[int | None], float],
    minimum_rms: float = 3.0,
) -> tuple[int | None, float]:
    """Select the loudest candidate with signal, or the first safe candidate."""
    fallback: tuple[int | None, float] | None = None
    best_signal: tuple[int | None, float] | None = None
    for candidate in candidates:
        rms = float(probe(candidate))
        if fallback is None:
            fallback = (candidate, rms)
        if rms >= minimum_rms and (best_signal is None or rms > best_signal[1]):
            best_signal = (candidate, rms)
    if best_signal is not None:
        return best_signal
    return fallback if fallback is not None else (None, 0.0)
