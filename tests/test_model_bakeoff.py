"""Deterministic coverage for the secret-safe provider bake-off harness.

Uses fake providers and layered-config seams so the timing, fallback,
validation, and non-disclosure contracts are proven offline with no cloud
endpoint, credentials, or local Ollama service.
"""

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import model_bakeoff as mb  # noqa: E402

from jarvis.adapters.models.ollama import OllamaProvider  # noqa: E402
from jarvis.core.errors import ProviderUnavailable  # noqa: E402
from jarvis.core.turn import TurnContext  # noqa: E402


class _FakeOllama(OllamaProvider):
    """An ``OllamaProvider`` subclass that streams fixed tokens without a server."""

    def __init__(self, tokens=("local",), name="ollama"):
        super().__init__(
            base_url="http://localhost:11434", model="mistral:7b-instruct", name=name
        )
        self.tokens = list(tokens)
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        for token in self.tokens:
            yield token


class _StreamingProvider:
    """Yields fixed tokens, optionally with a delay and a post-token failure."""

    def __init__(self, tokens=("respuesta",), *, delay=0.0, fail_after=None,
                 name="model", kind="fake"):
        self.tokens = list(tokens)
        self.delay = delay
        self.fail_after = fail_after
        self.name = name
        self.kind = kind
        self.base_url = None
        self.model = "fake-model"
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        for index, token in enumerate(self.tokens):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield token
            if self.fail_after is not None and index == self.fail_after:
                raise ProviderUnavailable("mid-stream failure", provider=self.name)


class _FailingPreToken:
    """Always fails before emitting any token (transient)."""

    def __init__(self, name="cloud"):
        self.name = name
        self.kind = "openai_compat"
        self.base_url = "https://api.example.com/v1"
        self.model = "gpt-4o-mini"
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        raise ProviderUnavailable("pre-token failure", provider=self.name)
        yield  # pragma: no cover


class _SecretProvider:
    """A provider carrying a credential, prompt, and response as sentinels."""

    name = "secret-cloud"
    kind = "openai_compat"
    base_url = "https://api.example.com/v1"
    model = "gpt-4o-mini"
    api_key = "sk-sentinel-secret"
    prompt = "SENTINEL_PROMPT"
    last_response = "SENTINEL_RESPONSE"


class RecordSchemaTests(unittest.TestCase):
    def test_validate_quality_accepts_bounded_vocabulary(self):
        for valid in mb.QUALITY_VALUES:
            self.assertEqual(mb.validate_quality(valid), valid)

    def test_validate_quality_rejects_invalid_category(self):
        with self.assertRaises(ValueError):
            mb.validate_quality("bogus")

    def test_record_excludes_credentials_prompt_and_response(self):
        result = mb._StreamResult(first_token_ms=10, total_ms=20, tokens=1, error=None)
        entry = mb._entry_for(
            "cloud_chain",
            [_SecretProvider()],
            result,
            selected=["secret-cloud"],
            quality="accepted",
        )
        record = mb.build_record([entry])
        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("sk-sentinel-secret", serialized)
        self.assertNotIn("SENTINEL_PROMPT", serialized)
        self.assertNotIn("SENTINEL_RESPONSE", serialized)
        self.assertNotIn("api_key", serialized)

    def test_record_keys_are_whitelisted(self):
        entry = mb._case_entry(
            case="cloud_chain",
            providers=[mb._provider_identity(_SecretProvider(), "primary")],
            route="secret-cloud",
            fallback="not_used",
            first_token_ms=1,
            total_ms=2,
            quality="accepted",
            outcome=mb.OUTCOME_OK,
        )
        with patch.object(mb.platform, "system", return_value="Windows"):
            record = mb.build_record([entry])
        self.assertEqual({"schema", "platform", "cases"}, set(record.keys()))
        self.assertEqual("Windows", record["platform"])
        self.assertEqual(set(mb._CASE_KEYS), set(entry.keys()))
        self.assertEqual(
            set(mb._PROVIDER_IDENTITY_KEYS), set(entry["providers"][0].keys())
        )

    def test_provider_identity_reports_role_name_kind_url_model_only(self):
        identity = mb._provider_identity(_SecretProvider(), "primary")
        self.assertEqual(
            {
                "role": "primary",
                "name": "secret-cloud",
                "kind": "openai_compat",
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o-mini",
            },
            identity,
        )


class BakeoffAsyncTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _context() -> TurnContext:
        return TurnContext.fresh("test", timeout_seconds=10.0)

    async def test_first_token_precedes_total_time(self):
        provider = _StreamingProvider(tokens=["a", "b"], delay=0.05)
        result = await mb._stream_measure(provider, "p", self._context(), sink=None)
        self.assertEqual(2, result.tokens)
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.first_token_ms)
        self.assertIsNotNone(result.total_ms)
        self.assertGreaterEqual(result.first_token_ms, 0)
        self.assertLess(result.first_token_ms, result.total_ms)

    async def test_forced_pre_token_handoff_routes_to_fallback(self):
        failing = _FailingPreToken(name="cloud")
        fallback = _StreamingProvider(tokens=["local"], name="ollama", kind="ollama")
        entry = await mb._run_chain_case(
            "forced_fallback",
            [failing, fallback],
            "p",
            self._context(),
            sink=None,
            quality="accepted",
            retries=0,
            backoff_seconds=0.0,
        )
        self.assertEqual(mb.OUTCOME_OK, entry["outcome"])
        self.assertEqual("ollama", entry["route"])
        self.assertEqual("used", entry["fallback"])
        self.assertEqual(1, fallback.calls)
        self.assertEqual(1, failing.calls)

    async def test_post_token_failure_commits_without_fallback_duplicate(self):
        partial = _StreamingProvider(tokens=["a", "b"], fail_after=1, name="cloud")
        fallback = _StreamingProvider(tokens=["local"], name="ollama", kind="ollama")
        collected = []
        entry = await mb._run_chain_case(
            "cloud_chain",
            [partial, fallback],
            "p",
            self._context(),
            sink=collected.append,
            quality="accepted",
            retries=0,
            backoff_seconds=0.0,
        )
        self.assertEqual(mb.OUTCOME_PARTIAL, entry["outcome"])
        self.assertEqual("cloud", entry["route"])
        self.assertEqual("not_used", entry["fallback"])
        self.assertEqual(["a", "b"], collected)
        self.assertEqual(0, fallback.calls)
        self.assertEqual(1, partial.calls)

    async def test_missing_local_yields_missing_local_outcomes(self):
        cloud = _StreamingProvider(tokens=["nube"], name="cloud", kind="openai_compat")
        entries = await mb._run_cases([cloud], "p", "accepted", stream=None)
        by_case = {entry["case"]: entry for entry in entries}
        self.assertEqual(3, len(entries))
        self.assertEqual(mb.OUTCOME_OK, by_case["cloud_chain"]["outcome"])
        self.assertEqual(mb.OUTCOME_MISSING_LOCAL, by_case["local_baseline"]["outcome"])
        self.assertEqual(mb.OUTCOME_MISSING_LOCAL, by_case["forced_fallback"]["outcome"])

    async def test_no_token_is_recorded_as_no_token(self):
        empty = _StreamingProvider(tokens=[], name="cloud")
        entry = await mb._run_local_case(
            "local_baseline", empty, "p", self._context(), sink=None, quality="accepted"
        )
        self.assertEqual(mb.OUTCOME_NO_TOKEN, entry["outcome"])
        self.assertEqual("none", entry["route"])
        self.assertIsNone(entry["first_token_ms"])
        self.assertIsNone(entry["total_ms"])

    async def test_forced_fallback_with_only_local_records_unavailable(self):
        # No cloud provider: the forced case has nothing to fall back to.
        local = _FakeOllama(tokens=["local"])
        entries = await mb._run_cases([local], "p", "accepted", stream=None)
        by_case = {entry["case"]: entry for entry in entries}
        self.assertEqual(mb.OUTCOME_UNAVAILABLE, by_case["forced_fallback"]["outcome"])


class ConfigSeamTests(unittest.TestCase):
    def test_resolve_providers_orders_cloud_before_local_and_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "config.json"
            base.write_text(
                json.dumps(
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
                ),
                encoding="utf-8",
            )
            local = Path(tmp) / "config.local.json"
            local.write_text(
                json.dumps(
                    {
                        "models": {
                            "providers": [
                                {
                                    "name": "openai",
                                    "kind": "openai_compat",
                                    "base_url": "https://api.example.com/v1",
                                    "api_key": "${OPENAI_API_KEY}",
                                    "model": "gpt-4o-mini",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = mb.load_config(base, environ={"OPENAI_API_KEY": "sk-sentinel-secret"})
            providers = mb._resolve_providers(config)
        self.assertEqual(["openai", "ollama"], [p.name for p in providers])
        identities = [
            mb._provider_identity(p, "primary" if index == 0 else "fallback")
            for index, p in enumerate(providers)
        ]
        serialized = json.dumps(identities, sort_keys=True)
        self.assertNotIn("sk-sentinel-secret", serialized)
        self.assertNotIn("api_key", serialized)


class CliTests(unittest.TestCase):
    def _fake_providers(self):
        cloud = _StreamingProvider(tokens=["nube", " ok"], name="openai", kind="openai_compat")
        local = _FakeOllama(tokens=["local"])
        return cloud, local

    def test_main_emits_json_record_and_exit_zero(self):
        cloud, local = self._fake_providers()
        with patch.object(mb, "load_config", return_value=object()), \
                patch.object(mb, "_resolve_providers", return_value=[cloud, local]):
            out = io.StringIO()
            err = io.StringIO()
            code = mb.main(["--json", "--quality", "accepted"], stdout=out, stderr=err)
        self.assertEqual(0, code)
        record = json.loads(out.getvalue())
        self.assertEqual(mb.RECORD_SCHEMA, record["schema"])
        self.assertEqual(3, len(record["cases"]))
        by_case = {entry["case"]: entry for entry in record["cases"]}
        self.assertEqual("openai", by_case["cloud_chain"]["route"])
        self.assertEqual("not_used", by_case["cloud_chain"]["fallback"])
        self.assertEqual("ollama", by_case["forced_fallback"]["route"])
        self.assertEqual("used", by_case["forced_fallback"]["fallback"])
        self.assertEqual("accepted", by_case["cloud_chain"]["quality"])

    def test_main_rejects_invalid_quality_with_exit_two(self):
        out = io.StringIO()
        err = io.StringIO()
        code = mb.main(["--quality", "bogus"], stdout=out, stderr=err)
        self.assertEqual(2, code)
        self.assertIn("quality must be one of", err.getvalue())

    def test_main_writes_output_record_file(self):
        cloud, local = self._fake_providers()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(mb, "load_config", return_value=object()), \
                patch.object(mb, "_resolve_providers", return_value=[cloud, local]):
            out_path = Path(tmp) / "record.json"
            out = io.StringIO()
            err = io.StringIO()
            code = mb.main(
                ["--json", "--quality", "accepted", "--output", str(out_path)],
                stdout=out,
                stderr=err,
            )
            self.assertEqual(0, code)
            self.assertTrue(out_path.is_file())
            written = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(mb.RECORD_SCHEMA, written["schema"])


if __name__ == "__main__":
    unittest.main()
