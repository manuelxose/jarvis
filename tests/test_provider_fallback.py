import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import FailingModel, ScriptedModel
from jarvis.adapters.models.fallback import ProviderChain
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext


class ConfigErrorModel:
    name = "bad-config"

    async def generate(self, prompt, context):
        raise ProviderConfigError("missing key", provider=self.name)
        yield  # pragma: no cover


class ProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_after_transient_failure(self):
        chain = ProviderChain(
            [FailingModel(failures=1), ScriptedModel(default="respuesta de reserva")],
            retries=0,
            backoff_seconds=0,
        )
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertIn("reserva", " ".join(tokens))

    async def test_config_error_skips_to_next_provider_without_retry(self):
        chain = ProviderChain(
            [ConfigErrorModel(), ScriptedModel(default="ok")],
            retries=2,
            backoff_seconds=0,
        )
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["ok "], tokens)

    async def test_all_failures_raise(self):
        chain = ProviderChain([FailingModel(failures=10)], retries=0, backoff_seconds=0)
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass

    async def test_no_providers_raises(self):
        chain = ProviderChain([])
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass


if __name__ == "__main__":
    unittest.main()
