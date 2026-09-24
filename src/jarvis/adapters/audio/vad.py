"""Energy-based voice activity detection (no external dependencies)."""

from __future__ import annotations

import array
import math
import sys



def rms_int16(audio: bytes) -> float:
    """Root-mean-square amplitude of 16-bit little-endian PCM bytes."""
    usable = len(audio) - (len(audio) % 2)
    if usable < 2:
        return 0.0
    samples = array.array("h", audio[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return (total / len(samples)) ** 0.5


class EnergyVAD:
    """Speech detection by comparing frame RMS against a configurable floor."""

    def __init__(self, threshold: float = 300.0, sample_width: int = 2) -> None:
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("threshold must be a non-negative finite number")
        if sample_width != 2:
            raise ValueError("EnergyVAD supports 16-bit PCM only")
        self.threshold = threshold
        self.sample_width = sample_width

    def is_speech(self, audio: bytes) -> bool:
        return rms_int16(audio) > self.threshold
