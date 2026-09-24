import asyncio
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from jarvis.adapters.audio.output import decode_wav
from jarvis.adapters.tts.fallback import TTSChain
from jarvis.adapters.tts.qwen_clone import QwenCloneTTS
from jarvis.core.contracts import HealthStatus, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled

# Fake worker speaking the qwen_worker protocol. Mode comes from argv[1].
_FAKE = textwrap.dedent(
    """
    import base64, json, sys, threading, time, queue
    mode = sys.argv[1]
    marker = sys.argv[2]
    def emit(e):
        sys.stdout.write(json.dumps(e) + "\\n"); sys.stdout.flush()
    if mode == "fatal":
        emit({"type": "fatal", "code": "low_vram", "detail": "only 100 MiB"}); sys.exit(3)
    if mode == "slow_start":
        time.sleep(5)
    emit({"type": "ready", "profile": None if mode == "noprofile" else "owner", "mode": "xvec"})
    cancelled = set(); requests = queue.Queue()
    def reader():
        for line in sys.stdin:
            m = json.loads(line)
            if m["op"] == "cancel": cancelled.add(m["id"])
            else: requests.put(m)
        requests.put({"op": "exit"})
    threading.Thread(target=reader, daemon=True).start()
    while True:
        m = requests.get()
        if m["op"] == "exit": break
        rid = m["id"]
        if mode == "crash_once":
            import os
            if not os.path.exists(marker):
                open(marker, "w").close(); sys.exit(1)
        if mode == "noprofile":
            emit({"type": "error", "id": rid, "code": "no_profile", "detail": "none"}); continue
        pcm = m["text"].encode()[:8].ljust(8, b"_")
        was = False
        for i in range(4 if mode != "slow" else 50):
            if rid in cancelled: was = True; break
            emit({"type": "audio", "id": rid, "rate": 24000, "pcm": base64.b64encode(pcm).decode()})
            time.sleep(0.01 if mode != "slow" else 0.05)
        emit({"type": "done", "id": rid, "cancelled": was, "ttfa_ms": 1, "audio_s": 0.1, "gen_ms": 2})
    """
)


async def _text(*chunks):
    for chunk in chunks:
        yield chunk


def _pcm(chunk: bytes) -> bytes:
    return decode_wav(chunk)[0]


class QwenCloneTTSTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.script = self.tmp / "fake_worker.py"
        self.script.write_text(_FAKE)

    def _tts(self, mode, **kwargs):
        tts = QwenCloneTTS([sys.executable, str(self.script), mode, str(self.tmp / "marker")], **kwargs)
        self.addAsyncCleanup(tts.stop)
        return tts

    async def _ready(self, tts):
        await tts.start()
        await asyncio.wait_for(tts.wait_ready(), 10)

    async def test_warming_worker_is_not_retryable_and_turn_waits_for_it(self):
        tts = self._tts("slow_start", warmup_wait_seconds=10)
        await tts.start()
        report = await tts.health()
        self.assertFalse(report.retryable)  # the supervisor must not kill a loading model
        chunks = [c async for c in tts.synthesize(_text("hola"), TurnContext.fresh("t"))]
        self.assertEqual(_pcm(chunks[0]), b"hola____")  # waited for the owner's voice

    async def test_warmup_wait_is_bounded_then_falls_back(self):
        tts = self._tts("slow_start", warmup_wait_seconds=0.1)
        await tts.start()
        with self.assertRaises(ProviderError):
            async for _ in tts.synthesize(_text("hola"), TurnContext.fresh("t")):
                pass

    async def test_streams_wav_chunks_per_segment(self):
        tts = self._tts("ok")
        await self._ready(tts)
        ctx = TurnContext.fresh("t")
        chunks = [c async for c in tts.synthesize(_text("hola", "adios"), ctx)]
        self.assertEqual([_pcm(c) for c in chunks], [b"hola____"] * 4 + [b"adios___"] * 4)
        self.assertEqual(decode_wav(chunks[0])[1], 24000)
        self.assertEqual((await tts.health()).status, HealthStatus.HEALTHY)

    async def test_cancel_stops_worker_and_next_turn_gets_no_stale_audio(self):
        tts = self._tts("slow")
        await self._ready(tts)
        ctx = TurnContext.fresh("t")
        got = []
        with self.assertRaises(TurnCancelled):
            async for chunk in tts.synthesize(_text("viejo"), ctx):
                got.append(chunk)
                if len(got) == 2:
                    ctx.cancellation.cancel()
        self.assertEqual(len(got), 2)
        fresh = TurnContext.fresh("t")
        stream = tts.synthesize(_text("nuevo"), fresh)
        first = await anext(stream)
        self.assertEqual(_pcm(first), b"nuevo___")
        await stream.aclose()

    async def test_missing_profile_is_config_error_and_chain_falls_back(self):
        tts = self._tts("noprofile")
        await self._ready(tts)
        with self.assertRaises(ProviderConfigError):
            [c async for c in tts.synthesize(_text("hola"), TurnContext.fresh("t"))]
        health = await tts.health()
        self.assertEqual(health.status, HealthStatus.DEGRADED)
        self.assertIn("profile", health.detail)

        class Sapi:
            name = "sapi"

            async def synthesize(self, text, context):
                async for chunk in text:
                    yield b"sapi:" + chunk.encode()

        selected = []
        chain = TTSChain([tts, Sapi()], on_select=selected.append)
        out = [c async for c in chain.synthesize(_text("hola"), TurnContext.fresh("t"))]
        self.assertEqual(out, [b"sapi:hola"])

    async def test_not_ready_fails_fast_instead_of_blocking(self):
        tts = self._tts("slow_start")
        await tts.start()
        started = asyncio.get_running_loop().time()
        with self.assertRaises(ProviderError) as raised:
            [c async for c in tts.synthesize(_text("hola"), TurnContext.fresh("t"))]
        self.assertFalse(raised.exception.transient)  # chain fails over without retry
        self.assertLess(asyncio.get_running_loop().time() - started, 1.0)
        self.assertIn("warming", (await tts.health()).detail)

    async def test_crash_is_reported_then_worker_restarts(self):
        tts = self._tts("crash_once", restart_backoff_seconds=0.01)
        await self._ready(tts)
        with self.assertRaises(ProviderUnavailable):
            [c async for c in tts.synthesize(_text("hola"), TurnContext.fresh("t"))]
        await asyncio.wait_for(tts.wait_ready(), 10)
        chunks = [c async for c in tts.synthesize(_text("hola"), TurnContext.fresh("t"))]
        self.assertEqual(len(chunks), 4)
        self.assertEqual(tts.state()["restarts"], 1)

    async def test_fatal_startup_is_degraded_not_crash(self):
        tts = self._tts("fatal", restart_max=0)
        await tts.start()
        with self.assertRaises(ProviderUnavailable):
            await asyncio.wait_for(tts.wait_ready(), 10)
        health = await tts.health()
        self.assertEqual(health.status, HealthStatus.DEGRADED)
        self.assertIn("low_vram", health.detail)

    async def test_repeated_start_stop(self):
        tts = self._tts("ok")
        for _ in range(3):
            await self._ready(tts)
            await tts.stop()
            self.assertIsNone(tts.state()["pid"])


if __name__ == "__main__":
    unittest.main()


class GenerationBudgetTests(unittest.TestCase):
    def test_short_text_cannot_babble_but_long_text_is_never_cut(self):
        from jarvis.adapters.tts.qwen_worker import max_tokens_for

        self.assertLess(max_tokens_for("Listo, señor.") / 12, 4.6)  # measured runaway: 4.6 s
        confirmation = (
            "Atención: voy a apagarme por completo; para volver a usarme tendrás que arrancarme "
            "a mano o reiniciar la sesión de Windows. ¿Confirmas? Di «confirmo» o «no»."
        )
        self.assertGreater(max_tokens_for(confirmation) / 12, 12.7)  # longest real take: 12.7 s
