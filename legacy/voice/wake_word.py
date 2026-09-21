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

from legacy.voice.audio_utils import format_audio_device, play_beep


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
        hard_trigger_hits: int = 1,
        soft_trigger_hits: int = 4,
        soft_trigger_ratio: float = 0.6,
        voice_rms_for_soft_trigger: float = 30.0,
        score_log_interval_seconds: float = 5.0,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.callback = callback
        self.chunk_size = chunk_size
        self.cooldown_seconds = cooldown_seconds
        self.input_device_index = input_device_index
        self.hard_trigger_hits = max(1, int(hard_trigger_hits))
        self.soft_trigger_hits = max(2, int(soft_trigger_hits))
        self.soft_trigger_ratio = max(0.1, min(1.0, float(soft_trigger_ratio)))
        self.voice_rms_for_soft_trigger = max(0.0, float(voice_rms_for_soft_trigger))
        self.score_log_interval_seconds = max(1.0, float(score_log_interval_seconds))

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
        frame_seconds = self.chunk_size / float(self.sample_rate)
        silence_rms_threshold = 2.0
        silence_failover_seconds = 8.0
        candidate_cycle: list[int | None] = []

        def _list_input_devices() -> list[int]:
            result: list[int] = []
            try:
                for idx in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(idx)
                    if int(info.get("maxInputChannels", 0)) > 0:
                        result.append(idx)
            except Exception as device_error:
                LOGGER.debug("Could not enumerate input devices: %s", device_error)
            return result

        def _next_device(current: int | None) -> int | None:
            nonlocal candidate_cycle
            if not candidate_cycle:
                available = _list_input_devices()
                candidate_cycle = [idx for idx in available if idx != current]
                if current is not None:
                    candidate_cycle.append(None)
            if not candidate_cycle:
                return current
            return candidate_cycle.pop(0)

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
                    no_signal_seconds = 0.0
                    hard_hits = 0
                    soft_hits = 0
                    peak_score = 0.0
                    score_log_deadline = time.monotonic() + self.score_log_interval_seconds
                    while not self._stop_event.is_set():
                        frame_bytes = stream.read(self.chunk_size, exception_on_overflow=False)
                        audio_frame = np.frombuffer(frame_bytes, dtype=np.int16)
                        rms = 0.0

                        if audio_frame.size:
                            rms = float(np.sqrt(np.mean(audio_frame.astype(np.float32) ** 2)))
                            if rms <= silence_rms_threshold:
                                no_signal_seconds += frame_seconds
                            else:
                                no_signal_seconds = 0.0

                            if no_signal_seconds >= silence_failover_seconds:
                                next_device = _next_device(active_device)
                                if next_device != active_device:
                                    LOGGER.warning(
                                        "Wake listener sin senal util en %s (rms<=%.2f durante %.1fs). "
                                        "Cambiando a %s.",
                                        format_audio_device(active_device),
                                        silence_rms_threshold,
                                        no_signal_seconds,
                                        format_audio_device(next_device),
                                    )
                                    active_device = next_device
                                    hard_hits = 0
                                    soft_hits = 0
                                    peak_score = 0.0
                                    break
                                no_signal_seconds = 0.0

                        scores = self._model.predict(audio_frame)

                        if not scores:
                            continue

                        score = scores.get(self.model_name)
                        if score is None:
                            score = max(scores.values())
                        score = float(score)
                        if score > peak_score:
                            peak_score = score

                        now = time.monotonic()
                        if now >= score_log_deadline:
                            LOGGER.info(
                                "Wake score peak=%.3f current=%.3f threshold=%.3f soft_threshold=%.3f rms=%.1f",
                                peak_score,
                                score,
                                self.threshold,
                                max(0.02, self.threshold * self.soft_trigger_ratio),
                                rms,
                            )
                            peak_score = 0.0
                            score_log_deadline = now + self.score_log_interval_seconds

                        hard_threshold = max(0.02, float(self.threshold))
                        soft_threshold = max(0.02, hard_threshold * self.soft_trigger_ratio)
                        if score >= hard_threshold:
                            hard_hits += 1
                        else:
                            hard_hits = 0

                        if score >= soft_threshold and rms >= self.voice_rms_for_soft_trigger:
                            soft_hits += 1
                        else:
                            soft_hits = 0

                        reason = None
                        if hard_hits >= self.hard_trigger_hits:
                            reason = "hard"
                        elif soft_hits >= self.soft_trigger_hits:
                            reason = "soft"

                        if reason and (now - last_trigger_time) >= self.cooldown_seconds:
                            last_trigger_time = now
                            hard_hits = 0
                            soft_hits = 0
                            play_beep()
                            self._wake_event.set()
                            LOGGER.info(
                                "Wake word detected (%s, score=%.3f, rms=%.1f, hard_threshold=%.3f, soft_threshold=%.3f)",
                                reason,
                                score,
                                rms,
                                hard_threshold,
                                soft_threshold,
                            )
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
