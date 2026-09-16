import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import FailingModel, ScriptedModel
from jarvis.adapters.models.fallback import ProviderChain
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnContext


class ConfigErrorModel:
    name = "bad-config"

    async def generate(self, prompt, context):
        raise ProviderConfigError("missing key", provider=self.name)
        yield  # pragma: no cover


class NonRetryableModel:
    name = "invalid-request"

    def __init__(self):
        self.attempts = 0

    async def generate(self, prompt, context):
        self.attempts += 1
        raise ProviderError("invalid request", transient=False, provider=self.name)
        yield  # pragma: no cover


class ImmediateModel:
    name = "immediate"

    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        yield "ok "


class PartialFailureModel:
    name = "partial-failure"

    async def generate(self, prompt, context):
        yield "partial "
        raise ProviderUnavailable("connection lost", provider=self.name)


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

    async def test_retries_transient_errors_with_bounded_exponential_backoff(self):
        model = FailingModel(failures=4)
        chain = ProviderChain([model], retries=4, backoff_seconds=0.25)
        with patch("jarvis.adapters.models.fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["respuesta de reserva"], tokens)
        self.assertEqual(5, model.attempts)
        sleep.assert_has_awaits([call(0.25), call(0.5), call(1.0), call(1.0)])

    async def test_non_retryable_provider_error_falls_back_without_delay(self):
        model = NonRetryableModel()
        chain = ProviderChain([model, ImmediateModel()], retries=5)
        with patch("jarvis.adapters.models.fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["ok "], tokens)
        self.assertEqual(1, model.attempts)
        sleep.assert_not_awaited()

    async def test_partial_stream_failure_does_not_replay_via_fallback(self):
        fallback = ImmediateModel()
        chain = ProviderChain([PartialFailureModel(), fallback], retries=2)
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass
        self.assertEqual(0, fallback.calls)

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
