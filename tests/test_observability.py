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
            trace.mark("playback_start_ms")

        self.assertEqual(trace.elapsed_ms("speech_end", "playback_start_ms"), 30.0)
        self.assertIsNone(trace.elapsed_ms("missing", "playback_start_ms"))
        self.assertEqual(
            trace.as_dict(),
            {
                "trace_id": "trace-123",
                "speech_end": 20.0,
                "playback_start_ms": 50.0,
                "speech_end_to_first_audio_ms": 30.0,
            },
        )

    def test_accepts_the_complete_required_stage_allowlist(self):
        required_stages = {
            "wake_ms",
            "speech_start",
            "speech_end",
            "vad_finalize_ms",
            "stt_first_partial_ms",
            "stt_final_ms",
            "routing_ms",
            "memory_lookup_ms",
            "provider_selection_ms",
            "agent_first_token_ms",
            "agent_total_ms",
            "tts_first_audio_ms",
            "tts_total_ms",
            "playback_start_ms",
            "total_request_ms",
        }
        trace = InteractionTrace("trace-allowlist")

        for stage in required_stages:
            trace.mark(stage)

        self.assertEqual(set(trace.as_dict()) - {"trace_id", "speech_end_to_first_audio_ms"}, required_stages)

    def test_explicit_timestamps_and_playback_alias_preserve_flat_metrics(self):
        with patch("jarvis.observability.tracing.time.perf_counter", return_value=0.0):
            trace = InteractionTrace("trace-explicit")
            trace.mark("speech_end", at=0.0)
            trace.mark("playback_start", 0.125)
        self.assertEqual(trace.elapsed_ms("speech_end", "playback_start"), 125.0)
        self.assertEqual(trace.as_dict()["playback_start_ms"], 125.0)
        self.assertEqual(trace.as_dict()["speech_end_to_first_audio_ms"], 125.0)
        self.assertTrue(all(isinstance(value, (float, str)) for value in trace.as_dict().values()))

    def test_rejects_unknown_stages(self):
        with self.assertRaisesRegex(ValueError, "Unsupported trace stage"):
            InteractionTrace("trace-unknown").mark("unknown_stage")

    def test_omits_first_audio_latency_without_both_prerequisites(self):
        for stage in ("speech_end", "playback_start_ms"):
            trace = InteractionTrace(f"trace-{stage}")
            trace.mark(stage)
            self.assertNotIn("speech_end_to_first_audio_ms", trace.as_dict())


class LoggingTests(unittest.TestCase):
    def test_redacts_dictionary_messages_and_format_arguments(self):
        stream = io.StringIO()
        logger = configure_logging(logging.getLogger("jarvis.test_structured"), stream=stream)
        secret = {"fast_model_api_key": "model-secret", "Authorization": "Bearer auth-secret"}
        logger.info(secret)
        logger.info("details=%s", secret)
        logger.info("key=%(fast_model_api_key)s auth=%(Authorization)s", secret)
        logger.info("Authorization: %s; api_key=%s", "Bearer positional-secret", "api-secret")
        logger.info("API-Key: %s", "hyphen-secret")
        for value in ("model-secret", "auth-secret", "positional-secret", "api-secret", "hyphen-secret"):
            self.assertNotIn(value, stream.getvalue())
        self.assertEqual(len(stream.getvalue().splitlines()), 5)
        for line in stream.getvalue().splitlines():
            self.assertIn("<redacted>", json.loads(line)["message"])
        self.assertEqual(secret["fast_model_api_key"], "model-secret")

    def test_config_secret_field_is_redacted_in_extras(self):
        stream = io.StringIO()
        logger = configure_logging(logging.getLogger("jarvis.test_config_secret"), stream=stream)
        logger.info("config", extra={"fast_model_api_key": "config-secret"})
        self.assertEqual(json.loads(stream.getvalue())["fast_model_api_key"], "<redacted>")

    def test_descendant_records_do_not_reach_parent_or_root_handlers(self):
        parent = logging.getLogger("jarvis.logging_ancestors")
        boundary = logging.getLogger(f"{parent.name}.boundary")
        root = logging.getLogger()
        stream = io.StringIO()
        leaked = []

        class Capture(logging.Handler):
            def emit(self, record):
                leaked.append(record)

        for logger in (parent, boundary, root):
            self.addCleanup(setattr, logger, "handlers", logger.handlers[:])
            self.addCleanup(setattr, logger, "propagate", logger.propagate)
            self.addCleanup(setattr, logger, "level", logger.level)
        parent.addHandler(Capture())
        root.addHandler(Capture())
        configure_logging(boundary, stream=stream)
        child = logging.getLogger(f"{boundary.name}.child")
        child.info({"token": "raw-child-secret"})
        self.assertEqual(leaked, [])
        self.assertNotIn("raw-child-secret", stream.getvalue())

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

    def test_logging_does_not_propagate_raw_secrets_to_parent_handler(self):
        parent = logging.getLogger("jarvis.test_observability.boundary")
        child = logging.getLogger(f"{parent.name}.child")
        parent_stream = io.StringIO()
        parent_handler = logging.StreamHandler(parent_stream)
        parent_handler.setFormatter(logging.Formatter("%(api_key)s"))

        previous_parent_handlers = parent.handlers[:]
        previous_parent_propagate = parent.propagate
        previous_child_handlers = child.handlers[:]
        previous_child_propagate = child.propagate

        def restore_loggers():
            parent.handlers[:] = previous_parent_handlers
            parent.propagate = previous_parent_propagate
            child.handlers[:] = previous_child_handlers
            child.propagate = previous_child_propagate

        self.addCleanup(restore_loggers)
        parent.handlers.clear()
        child.handlers.clear()
        parent.propagate = False
        child.propagate = True
        parent.addHandler(parent_handler)
        configure_logging(child, stream=io.StringIO())

        child.info("request complete", extra={"api_key": "parent-visible-secret"})

        self.assertEqual(parent_stream.getvalue(), "")
        self.assertNotIn("parent-visible-secret", parent_stream.getvalue())


if __name__ == "__main__":
    unittest.main()
