import logging
import importlib.util
from pathlib import Path
import unittest
from io import StringIO
from unittest.mock import patch

from voice.runtime_support import CaptureBackend, timed_phase


class AudioRuntimeTests(unittest.TestCase):
    def test_wasapi_backend_description_contains_real_endpoint(self):
        backend = CaptureBackend("WASAPI", 12, "G435 Bluetooth Gaming Headset", 16000)
        self.assertTrue(backend.is_wasapi)
        self.assertIn("backend=WASAPI", backend.describe())
        self.assertIn("index=12", backend.describe())
        self.assertIn("sample_rate=16000", backend.describe())

    def test_fallback_description_requires_reason(self):
        backend = CaptureBackend("PyAudio", 6, "fallback", 16000, "WASAPI open failed")
        self.assertFalse(backend.is_wasapi)
        self.assertIn("fallback_reason='WASAPI open failed'", backend.describe())

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("numpy", "pyaudio", "sounddevice")),
        "audio runtime dependencies are not installed",
    )
    def test_wasapi_auto_selection_uses_any_input_when_default_is_missing(self):
        from voice.audio_utils import resolve_capture_backend

        devices = [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "default_samplerate": 48000},
            {"name": "USB Microphone", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 48000},
        ]

        def query_devices(index=None):
            return devices if index is None else devices[index]

        with (
            patch("voice.audio_utils.sys.platform", "win32"),
            patch("voice.audio_utils.sd.query_hostapis", return_value=[
                {"name": "Windows WASAPI", "default_input_device": -1},
            ]),
            patch("voice.audio_utils.sd.query_devices", side_effect=query_devices),
        ):
            backend = resolve_capture_backend(preferred_index=None)

        self.assertTrue(backend.is_wasapi)
        self.assertEqual(1, backend.device_index)
        self.assertEqual("USB Microphone", backend.device_name)

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("numpy", "pyaudio", "sounddevice")),
        "audio runtime dependencies are not installed",
    )
    def test_wdm_ks_is_used_when_wasapi_has_no_input(self):
        from voice.audio_utils import resolve_capture_backend

        devices = [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "default_samplerate": 48000},
            {"name": "USB Microphone", "hostapi": 1, "max_input_channels": 1, "default_samplerate": 48000},
            {"name": "Studio Microphone", "hostapi": 1, "max_input_channels": 4, "default_samplerate": 48000},
        ]

        def query_devices(index=None):
            return devices if index is None else devices[index]

        with (
            patch("voice.audio_utils.sys.platform", "win32"),
            patch("voice.audio_utils.sd.query_hostapis", return_value=[
                {"name": "Windows WASAPI", "default_input_device": -1},
                {"name": "Windows WDM-KS", "default_input_device": 1},
            ]),
            patch("voice.audio_utils.sd.query_devices", side_effect=query_devices),
        ):
            backend = resolve_capture_backend(preferred_index=None)

        self.assertEqual("WDM-KS", backend.name)
        self.assertTrue(backend.is_wasapi)
        self.assertEqual(1, backend.device_index)

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("numpy", "pyaudio", "sounddevice")),
        "audio runtime dependencies are not installed",
    )
    def test_native_resolution_skips_an_endpoint_that_fails_to_start(self):
        from voice.audio_utils import resolve_capture_backend

        devices = [
            {"name": "Broken headset mic", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 16000},
            {"name": "Working USB Microphone", "hostapi": 1, "max_input_channels": 1, "default_samplerate": 16000},
        ]

        def query_devices(index=None):
            return devices if index is None else devices[index]

        with (
            patch("voice.audio_utils.sys.platform", "win32"),
            patch("voice.audio_utils.sd.query_hostapis", return_value=[
                {"name": "Windows WASAPI", "default_input_device": 0},
                {"name": "Windows WDM-KS", "default_input_device": 1},
            ]),
            patch("voice.audio_utils.sd.query_devices", side_effect=query_devices),
            patch("voice.audio_utils._probe_native_rms", side_effect=[-1.0, 12.0]),
        ):
            backend = resolve_capture_backend(preferred_index=None, verify=True)

        self.assertEqual("WDM-KS", backend.name)
        self.assertEqual(1, backend.device_index)

    def test_wdm_ks_uses_callback_capture_path(self):
        backend = CaptureBackend("WDM-KS", 1, "USB Microphone", 16000)

        self.assertTrue(backend.requires_callback)
        source = Path("voice/audio_utils.py").read_text(encoding="utf-8")
        self.assertIn("callback=", source)
        self.assertIn("backend.requires_callback", source)

    def test_main_passes_resolved_microphone_to_wake_listener(self):
        source = Path("main.py").read_text(encoding="utf-8")

        self.assertIn("input_device_index=resolved_input_device", source)
        self.assertNotIn(
            "input_device_index=None if capture_backend.is_wasapi else resolved_input_device",
            source,
        )

    def test_stt_loads_the_cached_model_without_huggingface_network(self):
        source = Path("voice/stt.py").read_text(encoding="utf-8")
        config = Path("config.yaml").read_text(encoding="utf-8")

        self.assertIn("local_files_only", source)
        self.assertIn("local_files_only: true", config)

    def test_stt_capture_is_bounded_for_short_voice_commands(self):
        config = Path("config.yaml").read_text(encoding="utf-8")
        self.assertIn("silence_duration: 0.45", config)
        self.assertIn("max_record_seconds: 3", config)

    def test_recording_uses_resolved_backend_not_stale_variable(self):
        source = Path("voice/audio_utils.py").read_text()
        self.assertNotIn("wasapi_input", source)
        self.assertIn("backend.is_wasapi and stream is not None", source)

    def test_timing_uses_observable_phase_name(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("audio-runtime-test")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            with timed_phase(logger, "microphone_capture"):
                pass
        finally:
            logger.removeHandler(handler)
        self.assertIn("timing phase=microphone_capture duration_seconds=", stream.getvalue())
