import io
import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability import InteractionTrace, configure_logging


class InteractionTraceTests(unittest.TestCase):
    def test_marks_stages_as_relative_monotonic_timestamps(self):
        with patch("jarvis.observability.tracing.time.perf_counter", side_effect=(10.0, 10.02, 10.05)):
            trace = InteractionTrace("trace-123")
            trace.mark("speech_end")
            trace.mark("playback_start")

        self.assertEqual(trace.elapsed_ms("speech_end", "playback_start"), 30.0)
        self.assertIsNone(trace.elapsed_ms("missing", "playback_start"))
        self.assertEqual(
            trace.as_dict(),
            {
                "trace_id": "trace-123",
                "stages_ms": {"speech_end": 20.0, "playback_start": 50.0},
                "speech_end_to_first_audio_ms": 30.0,
            },
        )


class LoggingTests(unittest.TestCase):
    def test_logging_recursively_redacts_secret_fields(self):
        stream = io.StringIO()
        logger = logging.getLogger("jarvis.test_observability")
        logger.handlers.clear()
        logger.propagate = False
        configure_logging(logger, stream=stream)

        logger.info(
            "request complete",
            extra={
                "api_key": "api-secret",
                "details": {
                    "Authorization": "Bearer secret",
                    "items": [{"token": "token-secret"}, {"password": "password-secret"}],
                },
            },
        )

        record = json.loads(stream.getvalue())
        self.assertEqual(record["api_key"], "<redacted>")
        self.assertEqual(record["details"]["Authorization"], "<redacted>")
        self.assertEqual(record["details"]["items"][0]["token"], "<redacted>")
        self.assertEqual(record["details"]["items"][1]["password"], "<redacted>")
        self.assertNotIn("api-secret", stream.getvalue())
        self.assertNotIn("Bearer secret", stream.getvalue())
        self.assertNotIn("token-secret", stream.getvalue())
        self.assertNotIn("password-secret", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
