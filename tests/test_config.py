import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.config import load_config


# Committed, git-tracked configuration documents. These are read directly (never
# via .gitignore or a gitignored config.local.json) to lock the secret-free
# template contract in the repository itself.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMITTED_BASE = _REPO_ROOT / "config.json"
_COMMITTED_EXAMPLE = _REPO_ROOT / "config.local.example.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_config(self, document):
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def write_local(self, document):
        (self.path.parent / "config.local.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    def test_loads_defaults_and_resolves_secret(self):
        self.write_config(
            {
                "runtime": {},
                "stt": {"api_key": "${JARVIS_TEST_TOKEN}"},
            }
        )

        config = load_config(self.path, {"JARVIS_TEST_TOKEN": "secret"})

        self.assertEqual(config.runtime.command_deadline_ms, 500)
        self.assertEqual(config.stt.api_key, "secret")
        self.assertNotIn("secret", repr(config))
        self.assertNotIn("secret", json.dumps(config.public_dict()))

    def test_rejects_missing_runtime_section(self):
        self.write_config({"memory": {}})

        with self.assertRaises(ValueError):
            load_config(self.path, {})

    def test_rejects_non_positive_command_deadline(self):
        self.write_config(
            {
                "runtime": {"command_deadline_ms": 0},
            }
        )

        with self.assertRaises(ValueError):
            load_config(self.path, {})

    def test_uses_the_supplied_environment_mapping(self):
        self.write_config(
            {
                "runtime": {},
                "stt": {"api_key": "${PATH}"},
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

    def test_accepts_alibaba_qwen_tts_provider(self):
        self.write_config({"runtime": {}, "tts": {"provider": "alibaba_qwen"}})
        self.assertEqual(load_config(self.path, {}).tts.provider, "alibaba_qwen")

    def test_accepts_alibaba_qwen_stt_provider(self):
        self.write_config({"runtime": {}, "stt": {"provider": "alibaba_qwen"}})
        self.assertEqual(load_config(self.path, {}).stt.provider, "alibaba_qwen")

    def test_alibaba_settings_default_and_override(self):
        self.write_config({"runtime": {}})
        config = load_config(self.path, {})
        self.assertEqual("singapore", config.alibaba.region)
        self.assertIsNone(config.alibaba.workspace_id)
        self.assertEqual("qwen3-asr-flash-realtime", config.alibaba.stt_model)
        self.assertEqual("qwen3-tts-flash-realtime", config.alibaba.tts_model)

        self.write_config(
            {
                "runtime": {},
                "alibaba": {"region": "beijing", "workspace_id": "ws-1", "tts_model": "qwen-audio-3.0-tts-flash"},
            }
        )
        overridden = load_config(self.path, {})
        self.assertEqual("beijing", overridden.alibaba.region)
        self.assertEqual("ws-1", overridden.alibaba.workspace_id)
        self.assertEqual("qwen-audio-3.0-tts-flash", overridden.alibaba.tts_model)

    def test_rejects_unknown_alibaba_region(self):
        self.write_config({"runtime": {}, "alibaba": {"region": "mars"}})
        with self.assertRaisesRegex(ValueError, "alibaba"):
            load_config(self.path, {})

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
        self.write_config({"runtime": {}, "tts": {"provider": "azure"}})
        with self.assertRaisesRegex(ValueError, "must be one of"):
            load_config(self.path, {})

    def test_merges_sibling_local_override_and_prepends_cloud_before_ollama(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [
                        {
                            "name": "ollama",
                            "kind": "ollama",
                            "base_url": "http://localhost:11434",
                            "model": "mistral:7b-instruct",
                        }
                    ]
                },
            }
        )
        self.write_local(
            {
                "models": {
                    "providers": [
                        {
                            "name": "openai",
                            "kind": "openai_compat",
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "${JARVIS_TEST_TOKEN}",
                            "model": "gpt-4o-mini",
                        }
                    ]
                }
            }
        )

        config = load_config(self.path, {"JARVIS_TEST_TOKEN": "secret"})

        self.assertEqual(
            ["openai", "ollama"], [p.name for p in config.models.providers]
        )
        self.assertEqual("openai_compat", config.models.providers[0].kind)
        self.assertEqual("secret", config.models.providers[0].api_key)
        self.assertEqual("ollama", config.models.providers[1].kind)

    def test_local_override_merges_named_provider_fields_in_place(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [
                        {
                            "name": "ollama",
                            "kind": "ollama",
                            "base_url": "http://localhost:11434",
                            "model": "mistral:7b-instruct",
                        },
                        {
                            "name": "local2",
                            "kind": "ollama",
                            "base_url": "http://localhost:11435",
                            "model": "tiny",
                        },
                    ]
                },
            }
        )
        self.write_local(
            {"models": {"providers": [{"name": "ollama", "model": "llama3.2"}]}}
        )

        config = load_config(self.path, {})

        self.assertEqual(
            ["ollama", "local2"], [p.name for p in config.models.providers]
        )
        self.assertEqual("llama3.2", config.models.providers[0].model)
        # Untouched base fields are preserved by the per-field merge.
        self.assertEqual(
            "http://localhost:11434", config.models.providers[0].base_url
        )

    def test_absent_local_override_uses_base_only(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [
                        {
                            "name": "ollama",
                            "kind": "ollama",
                            "base_url": "http://localhost:11434",
                            "model": "mistral:7b-instruct",
                        }
                    ]
                },
            }
        )

        config = load_config(self.path, {})

        self.assertEqual(["ollama"], [p.name for p in config.models.providers])

    def test_redacts_api_key_from_public_dict_and_repr(self):
        self.write_config({"runtime": {}})
        self.write_local(
            {
                "models": {
                    "providers": [
                        {
                            "name": "openai",
                            "kind": "openai_compat",
                            "base_url": "https://api.openai.com/v1",
                            "api_key": "${JARVIS_TEST_TOKEN}",
                            "model": "gpt-4o-mini",
                        }
                    ]
                }
            }
        )

        config = load_config(self.path, {"JARVIS_TEST_TOKEN": "sk-super-secret"})

        public = json.dumps(config.public_dict())
        self.assertNotIn("sk-super-secret", public)
        self.assertNotIn("sk-super-secret", repr(config))
        self.assertEqual(
            "<redacted>", config.public_dict()["models"]["providers"][0]["api_key"]
        )

    def test_rejects_unknown_provider_kind(self):
        self.write_config(
            {"runtime": {}, "models": {"providers": [{"name": "x", "kind": "watson"}]}}
        )
        with self.assertRaisesRegex(ValueError, "kind"):
            load_config(self.path, {})

    def test_normalizes_provider_kind_case(self):
        self.write_config(
            {"runtime": {}, "models": {"providers": [{"name": "o", "kind": "Ollama"}]}}
        )
        self.assertEqual("ollama", load_config(self.path, {}).models.providers[0].kind)

    def test_rejects_timeout_above_ceiling(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [{"name": "x", "kind": "ollama", "timeout_seconds": 9999}]
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            load_config(self.path, {})

    def test_accepts_bounded_timeout(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [{"name": "x", "kind": "ollama", "timeout_seconds": 60}]
                },
            }
        )
        self.assertEqual(60.0, load_config(self.path, {}).models.providers[0].timeout_seconds)

    def test_rejects_non_object_provider_entry(self):
        self.write_config({"runtime": {}, "models": {"providers": [123]}})
        with self.assertRaisesRegex(ValueError, "must be an object"):
            load_config(self.path, {})

    def test_rejects_duplicate_provider_names(self):
        self.write_config(
            {
                "runtime": {},
                "models": {
                    "providers": [
                        {"name": "dup", "kind": "ollama"},
                        {"name": "dup", "kind": "ollama"},
                    ]
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_config(self.path, {})

    # -- Committed template contract (secret-free local override) ----------

    def test_committed_base_and_example_parse_as_json_objects(self):
        base = _read_json(_COMMITTED_BASE)
        example = _read_json(_COMMITTED_EXAMPLE)

        self.assertIsInstance(base, dict)
        self.assertIsInstance(example, dict)
        self.assertIn("models", base)
        self.assertIn("models", example)
        self.assertIsInstance(base["models"]["providers"], list)
        self.assertIsInstance(example["models"]["providers"], list)

    def test_committed_base_config_is_secret_free(self):
        raw = _COMMITTED_BASE.read_text(encoding="utf-8")
        base = _read_json(_COMMITTED_BASE)

        # The committed base must never carry a credential field or env reference.
        self.assertNotIn("api_key", raw)
        self.assertNotIn("${", raw)
        for provider in base["models"]["providers"]:
            self.assertNotIn("api_key", provider)

    def test_committed_example_locks_env_placeholder(self):
        raw = _COMMITTED_EXAMPLE.read_text(encoding="utf-8")
        example = _read_json(_COMMITTED_EXAMPLE)

        # The checked-in example must reference the environment variable rather
        # than embed any credential literal.
        self.assertIn("${DEEPSEEK_API_KEY}", raw)
        provider = example["models"]["providers"][0]
        self.assertEqual("${DEEPSEEK_API_KEY}", provider["api_key"])
        # No other secret-looking literal leaks into the committed template.
        self.assertNotIn("sk-", raw.lower())

    def test_committed_example_over_base_merges_cloud_before_ollama_and_redacts(self):
        base = _read_json(_COMMITTED_BASE)
        example = _read_json(_COMMITTED_EXAMPLE)
        self.write_config(base)
        self.write_local(example)

        sentinel = "sk-test-sentinel-abc123"
        config = load_config(self.path, {"DEEPSEEK_API_KEY": sentinel})

        # Cloud-before-Ollama merge contract from the committed documents.
        self.assertEqual(["deepseek", "ollama"], [p.name for p in config.models.providers])
        self.assertEqual("openai_compat", config.models.providers[0].kind)
        self.assertEqual("ollama", config.models.providers[1].kind)
        # The key resolves onto the runtime object ...
        self.assertEqual(sentinel, config.models.providers[0].api_key)
        # ... but never appears in public serialization or repr.
        self.assertNotIn(sentinel, json.dumps(config.public_dict()))
        self.assertNotIn(sentinel, repr(config))
        self.assertEqual(
            "<redacted>", config.public_dict()["models"]["providers"][0]["api_key"]
        )


if __name__ == "__main__":
    unittest.main()
