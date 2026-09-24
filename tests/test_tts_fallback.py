import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts.fallback import TTSChain
from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext


async def _chunks(*items):
    for item in items:
        yield item


class ConfigErrorTTS:
    name = "bad-config"

    async def synthesize(self, text, context):
        raise ProviderConfigError("missing key", provider=self.name)
        yield  # pragma: no cover


class TransientFailTTS:
    name = "flaky"

    def __init__(self, fail_times=1):
        self.attempts = 0
        self.fail_times = fail_times

    async def synthesize(self, text, context):
        self.attempts += 1
        async for _ in text:
            pass
        if self.attempts <= self.fail_times:
            raise ProviderUnavailable("timeout", provider=self.name)
        yield b"audio-from-flaky"


class PartialFailureTTS:
    name = "partial"

    async def synthesize(self, text, context):
        async for _ in text:
            yield b"first-chunk"
            raise ProviderUnavailable("connection lost", provider=self.name)


class ImmediateTTS:
    name = "immediate"

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text, context):
        self.calls += 1
        async for chunk in text:
            yield chunk.encode("utf-8")


async def _collect(chain, context, *texts):
    return [chunk async for chunk in chain.synthesize(_chunks(*texts), context)]


class TTSChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_providers_raises_unavailable(self):
        chain = TTSChain([])
        context = TurnContext.fresh("t")
        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, "hola")

    async def test_falls_back_to_next_provider_before_first_byte(self):
        primary = TransientFailTTS(fail_times=99)
        fallback = ImmediateTTS()
        chain = TTSChain([primary, fallback], retries=0)
        context = TurnContext.fresh("t")

        audio = await _collect(chain, context, "hola")

        self.assertEqual([b"hola"], audio)
        self.assertEqual(1, fallback.calls)

    async def test_config_error_skips_to_next_provider_without_retry(self):
        primary = ConfigErrorTTS()
        fallback = ImmediateTTS()
        chain = TTSChain([primary, fallback], retries=3)
        context = TurnContext.fresh("t")

        audio = await _collect(chain, context, "hola")

        self.assertEqual([b"hola"], audio)

    async def test_retries_transient_failure_before_falling_back(self):
        primary = TransientFailTTS(fail_times=1)
        chain = TTSChain([primary], retries=1, backoff_seconds=0.0)
        context = TurnContext.fresh("t")

        audio = await _collect(chain, context, "hola")

        self.assertEqual([b"audio-from-flaky"], audio)
        self.assertEqual(2, primary.attempts)

    async def test_mid_stream_failure_after_first_byte_is_not_retried(self):
        chain = TTSChain([PartialFailureTTS(), ImmediateTTS()])
        context = TurnContext.fresh("t")

        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, "hola")

    async def test_all_providers_failing_raises_unavailable(self):
        chain = TTSChain(
            [TransientFailTTS(fail_times=99), TransientFailTTS(fail_times=99)], retries=0
        )
        context = TurnContext.fresh("t")

        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, "hola")

    async def test_open_circuit_skips_provider_without_calling_it(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure("immediate")
        primary = ImmediateTTS()
        fallback = ImmediateTTS()
        fallback.name = "fallback"
        chain = TTSChain([primary, fallback], breaker=breaker)
        context = TurnContext.fresh("t")

        await _collect(chain, context, "hola")

        self.assertEqual(0, primary.calls)
        self.assertEqual(1, fallback.calls)

    async def test_successful_provider_replays_chunks_consumed_before_a_prior_failure(self):
        primary = TransientFailTTS(fail_times=99)
        fallback = ImmediateTTS()
        chain = TTSChain([primary, fallback], retries=0)
        context = TurnContext.fresh("t")

        audio = await _collect(chain, context, "uno", "dos")

        self.assertEqual([b"uno", b"dos"], audio)


if __name__ == "__main__":
    unittest.main()
