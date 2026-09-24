"""Versioned voice cache and cache-aware fast-command acknowledgements."""

import asyncio
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.output import AudioOutputQueue
from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway, ToolResult
from jarvis.adapters.tts.voice_cache import VoiceCache, profile_version
from jarvis.application.routing import Router
from jarvis.application.turn_manager import TurnManager, _stable_ack
from jarvis.adapters.fakes import ScriptedModel


def wav(n=100):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x01\x00" * n)
    return buffer.getvalue()


ID = {"provider": "qwen_clone", "profile": "owner", "profile_version": "v1", "model": "m", "format": "wav"}


class VoiceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_exact_text(self):
        cache = VoiceCache(self.dir, ID)
        cache.put("Hecho.", wav())
        self.assertEqual(cache.get("Hecho."), wav())
        self.assertEqual(cache.get(" Hecho. "), wav())  # surrounding whitespace only
        self.assertIsNone(cache.get("hecho."))  # exact text, case matters
        self.assertEqual(VoiceCache(self.dir, ID).get("Hecho."), wav())  # persisted

    def test_entries_written_by_another_process_are_seen(self):
        import os
        import time

        daemon_view = VoiceCache(self.dir, ID)  # opened before the recording
        self.assertIsNone(daemon_view.get("Hola."))
        VoiceCache(self.dir, ID).put("Hola.", wav())  # e.g. `jarvis welcome record`
        index = self.dir / "index.json"
        os.utime(index, (time.time() + 5, time.time() + 5))  # coarse filesystem clocks
        self.assertEqual(daemon_view.get("Hola."), wav())

    def test_new_voice_profile_invalidates_old_audio(self):
        VoiceCache(self.dir, ID).put("Hecho.", wav())
        reenrolled = VoiceCache(self.dir, {**ID, "profile_version": "v2"})
        self.assertIsNone(reenrolled.get("Hecho."))
        self.assertEqual(reenrolled.stats()["entries"], 0)  # purged from disk
        self.assertEqual(list(self.dir.glob("*.wav")), [])

    def test_settings_and_format_are_part_of_the_key(self):
        a = VoiceCache(self.dir, ID)
        self.assertNotEqual(a.key("Hecho."), VoiceCache(self.dir / "x", {**ID, "model": "other"}).key("Hecho."))
        self.assertNotEqual(a.key("Hecho."), VoiceCache(self.dir / "y", {**ID, "format": "mp3"}).key("Hecho."))

    def test_lru_bound(self):
        cache = VoiceCache(self.dir, ID, max_entries=2)
        cache.put("uno", wav())
        cache.put("dos", wav())
        cache.get("uno")  # recently used
        cache.put("tres", wav())
        self.assertIsNotNone(cache.get("uno"))
        self.assertIsNone(cache.get("dos"))
        self.assertEqual(cache.stats()["entries"], 2)

    def test_profile_version_tracks_profile_files(self):
        profile = self.dir / "profile"
        profile.mkdir()
        self.assertEqual(profile_version(profile), "")
        (profile / "profile.json").write_text("{}")
        first = profile_version(profile)
        (profile / "reference.wav").write_bytes(b"x" * 10)
        self.assertNotEqual(first, profile_version(profile))

    def test_stable_ack_rules(self):
        self.assertTrue(_stable_ack("He subido el volumen."))
        for bad in ("Son las 09:15.", "CPU al 12 por ciento.", "No he podido abrir X.", "tool x failed: boom", ""):
            self.assertFalse(_stable_ack(bad), bad)


class FakeClone:
    name = "qwen_clone"
    ready = True

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text, context):
        self.calls += 1
        async for _ in text:
            yield wav(50)
            yield wav(50)


class Done(Tool):
    name, risk, input_schema = "repeat", Risk.READ_ONLY, {}

    def __init__(self, ok=True):
        self.ok = ok

    async def execute(self, arguments, context):
        return ToolResult("Hecho.", ok=self.ok)


class AckCachingTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_ack_is_live_and_recorded_then_served_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, clone, played = VoiceCache(Path(tmp), ID), FakeClone(), []

            async def render(turn_id, chunk):
                played.append(chunk)

            manager = TurnManager(router=Router(), tools=ToolGateway([Done()]).execute, model=ScriptedModel(),
                                  tts=clone, audio=AudioOutputQueue(render=render), ack_cache=cache)
            await manager.handle("repite")
            self.assertEqual((clone.calls, len(played)), (1, 2))
            self.assertIsNotNone(cache.get("Hecho."))
            await manager.handle("repite")
            self.assertEqual(clone.calls, 1)  # cache hit bypasses synthesis
            self.assertEqual(len(played), 3)  # one joined WAV

    async def test_failed_operation_never_plays_or_records_success_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, clone = VoiceCache(Path(tmp), ID), FakeClone()
            cache.put("Hecho.", wav())  # a success recording exists
            manager = TurnManager(router=Router(), tools=ToolGateway([Done(ok=False)]).execute, model=ScriptedModel(),
                                  tts=clone, audio=AudioOutputQueue(), ack_cache=cache)
            await manager.handle("repite")
            self.assertEqual(clone.calls, 1)  # spoken live, not from the cache

    async def test_fallback_voice_audio_is_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, clone = VoiceCache(Path(tmp), ID), FakeClone()
            clone.ready = False  # still loading: SAPI would speak
            manager = TurnManager(router=Router(), tools=ToolGateway([Done()]).execute, model=ScriptedModel(),
                                  tts=clone, audio=AudioOutputQueue(), ack_cache=cache)
            await manager.handle("repite")
            self.assertIsNone(cache.get("Hecho."))


if __name__ == "__main__":
    unittest.main()
