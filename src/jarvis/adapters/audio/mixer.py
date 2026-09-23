"""Startup music + activation chime through one output stream with gain ramps.

Music is decoded with ``soundfile`` (libsndfile >= 1.1 reads MP3/FLAC/WAV) and
played from a PortAudio callback, so volume changes (ducking under Jarvis's
voice, fade-out) are sample-accurate linear ramps rather than audible steps.
The stream runs at the music's native rate, so nothing is resampled. The
render step is a pure function of the mixer state and is unit-tested without
an audio device. ``numpy``/``sounddevice``/``soundfile`` are lazy-imported.
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.mixer")

# Most music is 44.1 kHz; matching it avoids reopening the stream mid-chime.
DEFAULT_RATE = 44100


def synth_chime(rate: int = DEFAULT_RATE, channels: int = 2) -> Any:
    """A short rising two-tone 'power up' cue (no asset file needed)."""
    import numpy as np  # noqa: PLC0415

    t = np.arange(int(rate * 0.42)) / rate
    sweep = np.sin(2 * np.pi * (520 * t + 900 * t * t))  # 520 -> ~1270 Hz
    ping = np.sin(2 * np.pi * 1568 * t) * (t > 0.16)
    envelope = np.minimum(t / 0.01, 1.0) * np.exp(-t / 0.16)
    tone = (0.45 * sweep + 0.3 * ping) * envelope
    return np.repeat(tone.astype(np.float32)[:, None], channels, axis=1)


class Mixer:
    """Two voices (music, one-shot sound effects) with ramped music gain."""

    def __init__(self, device: int | str | None = None) -> None:
        self._device = device
        self._lock = threading.Lock()
        self._stream: Any = None
        self.rate = DEFAULT_RATE
        self.channels = 2
        self._music: Any = None
        self._pos = 0
        self._sfx: list[list[Any]] = []  # [buffer, position]
        self._gain = 0.0
        self._target = 0.0
        self._step = 0.0  # gain change per frame while ramping
        self._stop_at_silence = False
        self.level = 0.0  # RMS of the last rendered block (for visualizers)

    # -- state ------------------------------------------------------------
    @property
    def music_playing(self) -> bool:
        return self._music is not None and self._pos < len(self._music)

    @property
    def gain(self) -> float:
        return self._gain

    # -- control ------------------------------------------------------------
    def load(self, path: str | Path) -> None:
        """Decode *path*; raises FileNotFoundError / RuntimeError on bad media."""
        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"music file not found: {path}")
        try:
            data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        except Exception as error:  # libsndfile raises its own error types
            raise RuntimeError(f"cannot decode {path.name}: {error}") from error
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        if rate != self.rate:
            self._close_stream()  # outside the lock: abort() waits for the callback
            self.rate = int(rate)
        with self._lock:
            self._music, self._pos = data[:, :2].copy(), 0

    def play_sfx(self, samples: Any = None) -> None:
        with self._lock:
            self._sfx.append([samples if samples is not None else synth_chime(self.rate), 0])
        self._ensure_stream()

    def play_music(self, volume: float, fade_seconds: float = 1.5) -> None:
        if self._music is None:
            raise RuntimeError("no music loaded")
        with self._lock:
            self._pos, self._gain, self._stop_at_silence = 0, 0.0, False
        self.ramp(volume, fade_seconds)
        self._ensure_stream()

    def ramp(self, volume: float, seconds: float) -> None:
        """Move the music gain linearly to *volume* over *seconds*."""
        volume = min(max(float(volume), 0.0), 1.0)
        with self._lock:
            self._target = volume
            frames = max(1.0, seconds * self.rate)
            self._step = (volume - self._gain) / frames

    def fade_out(self, seconds: float = 2.0) -> None:
        self.ramp(0.0, seconds)
        with self._lock:
            self._stop_at_silence = True

    def stop(self) -> None:
        with self._lock:
            self._music, self._sfx, self._gain, self._target = None, [], 0.0, 0.0
        self._close_stream()
        self.level = 0.0

    # -- rendering ------------------------------------------------------------
    def render(self, frames: int) -> Any:
        """Mix the next *frames* frames (called from the audio callback)."""
        import numpy as np  # noqa: PLC0415

        out = np.zeros((frames, self.channels), dtype=np.float32)
        with self._lock:
            if self._music is not None:
                chunk = self._music[self._pos:self._pos + frames]
                n = len(chunk)
                if n:
                    gains = self._gain + self._step * np.arange(1, n + 1, dtype=np.float32)
                    if self._step > 0:
                        gains = np.minimum(gains, self._target)
                    elif self._step < 0:
                        gains = np.maximum(gains, self._target)
                    out[:n] += chunk * gains[:, None]
                    self._gain = float(gains[-1])
                    self._pos += n
                if self._stop_at_silence and self._gain <= 1e-4:
                    self._music = None
            for voice in self._sfx:
                buffer, position = voice
                chunk = buffer[position:position + frames]
                out[: len(chunk)] += chunk
                voice[1] = position + len(chunk)
            self._sfx = [v for v in self._sfx if v[1] < len(v[0])]
        np.clip(out, -1.0, 1.0, out=out)
        self.level = float(math.sqrt(float(np.mean(out * out)))) if frames else 0.0
        return out

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        outdata[:] = self.render(frames)

    def _ensure_stream(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            import sounddevice as sd  # noqa: PLC0415

            self._stream = sd.OutputStream(
                samplerate=self.rate,
                channels=self.channels,
                dtype="float32",
                device=self._device,
                callback=self._callback,
                latency="low",
            )
            self._stream.start()

    def _close_stream(self) -> None:
        # Never call with self._lock held: abort() waits for the running callback.
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:  # noqa: BLE001 - device already gone
                logger.debug("mixer stream close failed", exc_info=True)
