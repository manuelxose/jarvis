"""openWakeWord wake-word detector adapter (lazy-imported)."""

from __future__ import annotations

import math
from typing import Any

from jarvis.core.errors import ProviderUnavailable


def openwakeword_available() -> bool:
    """Return whether the optional openWakeWord runtime dependencies import."""
    try:
        import openwakeword  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


class OpenWakeWordDetector:
    """Detect a configured wake word in int16 PCM audio frames."""

    def __init__(
        self,
        *,
        model_name: str = "hey_jarvis",
        threshold: float = 0.3,
        min_frames: int = 2,
        sample_rate: int = 16000,
        inference_framework: str = "onnx",
    ) -> None:
        if not 0 < threshold < 1 or not math.isfinite(threshold):
            raise ValueError("threshold must be finite and between 0 and 1")
        if min_frames < 1:
            raise ValueError("min_frames must be at least 1")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        model_name = model_name.strip()
        if not model_name:
            raise ValueError("model_name must not be empty")

        self.model_name = model_name
        self.threshold = threshold
        self.min_frames = min_frames
        self.sample_rate = sample_rate
        self.inference_framework = inference_framework
        self._model: Any | None = None
        self._numpy: Any | None = None
        self._streak = 0
        self.last_score = 0.0

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            import numpy as np  # noqa: PLC0415
            from openwakeword.model import Model  # noqa: PLC0415
        except ImportError as error:
            raise ProviderUnavailable(
                "openwakeword is not installed; wake-word detection unavailable", provider="wake_word"
            ) from error

        self._numpy = np
        try:
            self._model = Model(
                wakeword_models=[self.model_name],
                inference_framework=self.inference_framework,
            )
        except Exception as error:
            # A missing/bundled-absent model or an onnxruntime load failure must
            # degrade as a typed unavailable error, not crash the voice loop.
            raise ProviderUnavailable(
                f"wake-word model {self.model_name!r} could not be loaded: {error}",
                provider="wake_word",
            ) from error

    def detected(self, audio: bytes) -> bool:
        """Return whether the latest PCM frame crosses the configured threshold.

        ``min_frames`` consecutive frames above the threshold are required before a
        hit is returned, so a transient noise spike cannot arm a turn.
        """
        if len(audio) < 2:
            return False

        self._ensure_model()
        frame = self._numpy.frombuffer(audio, dtype=self._numpy.int16)
        scores = self._model.predict(frame)
        score = scores.get(self.model_name)
        if score is None:
            score = max(scores.values())
        score_value = float(score)
        self.last_score = score_value
        if score_value >= self.threshold:
            self._streak += 1
            if self._streak >= self.min_frames:
                self._streak = 0
                return True
            return False
        self._streak = 0
        return False
