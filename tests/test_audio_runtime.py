import logging
from pathlib import Path
import unittest
from io import StringIO

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
