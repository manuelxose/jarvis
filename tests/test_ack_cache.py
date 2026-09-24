import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts.ack_cache import AckAudioCache, bytes_to_stream, cache_key


class CacheKeyTests(unittest.TestCase):
    def test_same_text_produces_same_key(self):
        self.assertEqual(cache_key("Hecho."), cache_key("Hecho."))

    def test_different_text_produces_different_key(self):
        self.assertNotEqual(cache_key("Hecho."), cache_key("Siguiente."))


class AckAudioCacheTests(unittest.TestCase):
    def test_returns_cached_bytes_for_known_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            (cache_dir / f"{cache_key('Hecho.')}.wav").write_bytes(b"cached-audio")
            cache = AckAudioCache(cache_dir)
            self.assertEqual(b"cached-audio", cache.get("Hecho."))

    def test_returns_none_for_unknown_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = AckAudioCache(Path(tmp))
            self.assertIsNone(cache.get("no existe"))

    def test_returns_none_for_empty_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = AckAudioCache(Path(tmp))
            self.assertIsNone(cache.get(""))
            self.assertIsNone(cache.get("   "))

    def test_caches_in_memory_after_first_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            path = cache_dir / f"{cache_key('Hecho.')}.wav"
            path.write_bytes(b"first")
            cache = AckAudioCache(cache_dir)
            self.assertEqual(b"first", cache.get("Hecho."))
            path.write_bytes(b"second")
            self.assertEqual(b"first", cache.get("Hecho."))


class BytesToStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_yields_the_bytes_once(self):
        collected = [chunk async for chunk in bytes_to_stream(b"abc")]
        self.assertEqual([b"abc"], collected)


if __name__ == "__main__":
    unittest.main()
