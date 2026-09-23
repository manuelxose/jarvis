import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.stt.fallback import STTChain
from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.core.contracts import Transcript
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext


async def _chunks(*items):
    for item in items:
        yield item


class ConfigErrorSTT:
    name = "bad-config"

    async def transcribe(self, audio, context):
        raise ProviderConfigError("missing key", provider=self.name)
        yield  # pragma: no cover


class TransientFailSTT:
    name = "flaky"

    def __init__(self, fail_times=1):
        self.attempts = 0
        self.fail_times = fail_times

    async def transcribe(self, audio, context):
        self.attempts += 1
        async for _ in audio:
            pass
        if self.attempts <= self.fail_times:
            raise ProviderUnavailable("timeout", provider=self.name)
        yield Transcript("desde flaky", is_final=True)


class PartialFailureSTT:
    name = "partial"

    async def transcribe(self, audio, context):
        async for _ in audio:
            yield Transcript("parcial", is_final=False)
            raise ProviderUnavailable("connection lost", provider=self.name)


class ImmediateSTT:
    name = "immediate"

    def __init__(self):
        self.calls = 0

    async def transcribe(self, audio, context):
        self.calls += 1
        async for chunk in audio:
            yield Transcript(chunk.decode("utf-8"), is_final=True)


async def _collect(chain, context, *frames):
    return [transcript async for transcript in chain.transcribe(_chunks(*frames), context)]


class STTChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_providers_raises_unavailable(self):
        chain = STTChain([])
        context = TurnContext.fresh("t")
        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, b"hola")

    async def test_falls_back_to_next_provider_before_first_transcript(self):
        primary = TransientFailSTT(fail_times=99)
        fallback = ImmediateSTT()
        chain = STTChain([primary, fallback], retries=0)
        context = TurnContext.fresh("t")

        transcripts = await _collect(chain, context, b"hola")

        self.assertEqual(["hola"], [t.text for t in transcripts])
        self.assertEqual(1, fallback.calls)

    async def test_config_error_skips_to_next_provider_without_retry(self):
        primary = ConfigErrorSTT()
        fallback = ImmediateSTT()
        chain = STTChain([primary, fallback], retries=3)
        context = TurnContext.fresh("t")

        transcripts = await _collect(chain, context, b"hola")

        self.assertEqual(["hola"], [t.text for t in transcripts])

    async def test_retries_transient_failure_before_falling_back(self):
        primary = TransientFailSTT(fail_times=1)
        chain = STTChain([primary], retries=1, backoff_seconds=0.0)
        context = TurnContext.fresh("t")

        transcripts = await _collect(chain, context, b"hola")

        self.assertEqual(["desde flaky"], [t.text for t in transcripts])
        self.assertEqual(2, primary.attempts)

    async def test_mid_stream_failure_after_first_transcript_is_not_retried(self):
        chain = STTChain([PartialFailureSTT(), ImmediateSTT()])
        context = TurnContext.fresh("t")

        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, b"hola")

    async def test_all_providers_failing_raises_unavailable(self):
        chain = STTChain(
            [TransientFailSTT(fail_times=99), TransientFailSTT(fail_times=99)], retries=0
        )
        context = TurnContext.fresh("t")

        with self.assertRaises(ProviderUnavailable):
            await _collect(chain, context, b"hola")

    async def test_open_circuit_skips_provider_without_calling_it(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure("immediate")
        primary = ImmediateSTT()
        fallback = ImmediateSTT()
        fallback.name = "fallback"
        chain = STTChain([primary, fallback], breaker=breaker)
        context = TurnContext.fresh("t")

        await _collect(chain, context, b"hola")

        self.assertEqual(0, primary.calls)
        self.assertEqual(1, fallback.calls)

    async def test_successful_provider_replays_chunks_consumed_before_a_prior_failure(self):
        primary = TransientFailSTT(fail_times=99)
        fallback = ImmediateSTT()
        chain = STTChain([primary, fallback], retries=0)
        context = TurnContext.fresh("t")

        transcripts = await _collect(chain, context, b"uno", b"dos")

        self.assertEqual(["uno", "dos"], [t.text for t in transcripts])


if __name__ == "__main__":
    unittest.main()
