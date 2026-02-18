from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import openwakeword
import numpy as np
import pyaudio
from openwakeword.model import Model
from openwakeword.utils import download_models

from voice.audio_utils import format_audio_device, play_beep


LOGGER = logging.getLogger(__name__)


class WakeWordListener:
    """Background wake-word listener using openwakeword."""

    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.35,
        sample_rate: int = 16000,
        callback: Callable[[], None] | None = None,
        chunk_size: int = 1280,
        cooldown_seconds: float = 1.5,
        input_device_index: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.callback = callback
        self.chunk_size = chunk_size
        self.cooldown_seconds = cooldown_seconds
        self.input_device_index = input_device_index

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._model = self._build_model()

    def _build_model(self) -> Model:
        # Ensure ONNX wake word assets are available locally (no tflite-runtime needed).
        try:
            download_models(model_names=[self.model_name])
        except Exception as download_error:
            LOGGER.warning("Could not pre-download wake-word ONNX assets: %s", download_error)

        try:
            return Model(wakeword_models=[self.model_name], inference_framework="onnx")
        except Exception:
            LOGGER.warning(
                "Could not load wake model '%s' directly. Falling back to default model list.",
                self.model_name,
            )
            try:
                download_models(model_names=list(openwakeword.MODELS.keys()))
            except Exception as download_error:
                LOGGER.warning("Could not pre-download fallback ONNX assets: %s", download_error)
            return Model(inference_framework="onnx")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        LOGGER.info("Wake word listener started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        LOGGER.info("Wake word listener stopped.")

    def wait_for_wake_word(self, timeout: float | None = None) -> bool:
        """Block until wake word event is raised."""
        triggered = self._wake_event.wait(timeout=timeout)
        if triggered:
            self._wake_event.clear()
        return triggered

    def _run_loop(self) -> None:
        pa = pyaudio.PyAudio()
        active_device = self.input_device_index

        last_trigger_time = 0.0
        try:
            while not self._stop_event.is_set():
                stream = None
                try:
                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=self.sample_rate,
                        input=True,
                        frames_per_buffer=self.chunk_size,
                        input_device_index=active_device,
                    )
                    LOGGER.info("Wake listener using %s", format_audio_device(active_device))
                except Exception as open_error:
                    LOGGER.error("Wake listener could not open input stream: %s", open_error)
                    if active_device is not None:
                        LOGGER.warning("Retrying wake listener with default input device.")
                        active_device = None
                    time.sleep(1.0)
                    continue

                try:
                    while not self._stop_event.is_set():
                        frame_bytes = stream.read(self.chunk_size, exception_on_overflow=False)
                        audio_frame = np.frombuffer(frame_bytes, dtype=np.int16)
                        scores = self._model.predict(audio_frame)

                        if not scores:
                            continue

                        score = scores.get(self.model_name)
                        if score is None:
                            score = max(scores.values())

                        now = time.monotonic()
                        if score >= self.threshold and (now - last_trigger_time) >= self.cooldown_seconds:
                            last_trigger_time = now
                            play_beep()
                            self._wake_event.set()
                            LOGGER.info("Wake word detected (score=%.3f)", score)
                            if self.callback:
                                try:
                                    self.callback()
                                except Exception as callback_error:
                                    LOGGER.exception("Wake callback failed: %s", callback_error)
                except Exception as listen_error:
                    LOGGER.warning("Wake listener stream error. Reopening input stream: %s", listen_error)
                    time.sleep(0.3)
                finally:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
        finally:
            pa.terminate()
