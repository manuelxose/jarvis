import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, document):
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def test_loads_defaults_and_resolves_secret(self):
        self.write_config(
            {
                "runtime": {},
                "providers": {"fast_model_api_key": "${JARVIS_TEST_TOKEN}"},
                "memory": {},
                "security": {},
            }
        )

        config = load_config(self.path, {"JARVIS_TEST_TOKEN": "secret"})

        self.assertEqual(config.runtime.command_deadline_ms, 500)
        self.assertEqual(config.providers.fast_model_api_key, "secret")
        self.assertNotIn("secret", repr(config))
        self.assertNotIn("secret", json.dumps(config.public_dict()))

    def test_rejects_missing_runtime_section(self):
        self.write_config({"providers": {}, "memory": {}, "security": {}})

        with self.assertRaises(ValueError):
            load_config(self.path, {})

    def test_rejects_non_positive_command_deadline(self):
        self.write_config(
            {
                "runtime": {"command_deadline_ms": 0},
                "providers": {},
                "memory": {},
                "security": {},
            }
        )

        with self.assertRaises(ValueError):
            load_config(self.path, {})

    def test_uses_the_supplied_environment_mapping(self):
        self.write_config(
            {
                "runtime": {},
                "providers": {"fast_model_api_key": "${PATH}"},
                "memory": {},
                "security": {},
            }
        )

        with self.assertRaises(ValueError):
            load_config(self.path, {})

    def test_rejects_negative_command_deadline(self):
        self.write_config({"runtime": {"command_deadline_ms": -1}})
        with self.assertRaisesRegex(ValueError, "positive integer"):
            load_config(self.path, {})

    def test_accepts_sapi_stt_provider(self):
        self.write_config({"runtime": {}, "stt": {"provider": "sapi"}})
        self.assertEqual(load_config(self.path, {}).stt.provider, "sapi")

    def test_accepts_sapi_tts_provider(self):
        self.write_config({"runtime": {}, "tts": {"provider": "sapi"}})
        self.assertEqual(load_config(self.path, {}).tts.provider, "sapi")

    def test_normalizes_provider_case(self):
        self.write_config({"runtime": {}, "stt": {"provider": " WhIsPeR "}})
        self.assertEqual(load_config(self.path, {}).stt.provider, "whisper")

    def test_rejects_cloud_stt_provider(self):
        self.write_config({"runtime": {}, "stt": {"provider": "cloud"}})
        with self.assertRaisesRegex(ValueError, "out of scope"):
            load_config(self.path, {})

    def test_rejects_cloud_tts_provider(self):
        self.write_config({"runtime": {}, "tts": {"provider": "cloud"}})
        with self.assertRaisesRegex(ValueError, "out of scope"):
            load_config(self.path, {})

    def test_rejects_unknown_stt_provider(self):
        self.write_config({"runtime": {}, "stt": {"provider": "google"}})
        with self.assertRaisesRegex(ValueError, "must be one of"):
            load_config(self.path, {})

    def test_rejects_unknown_tts_provider(self):
        self.write_config({"runtime": {}, "tts": {"provider": "elevenlabs"}})
        with self.assertRaisesRegex(ValueError, "must be one of"):
            load_config(self.path, {})


if __name__ == "__main__":
    unittest.main()
