import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts import (
    LocalTTS,
    Pyttsx3TTS,
    local_tts_available,
    pyttsx3_available,
    resolve_tts,
    tts_available,
    tts_provider,
)
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable


async def empty_text():
    if False:
        yield ""


async def collect_audio(adapter):
    return [audio async for audio in adapter.synthesize(empty_text(), None)]


def config(provider="local"):
    return SimpleNamespace(tts=SimpleNamespace(provider=provider, language="en"))


class TTSProviderDependencyTests(unittest.TestCase):
    def test_local_synthesize_raises_typed_error_without_dependency(self):
        with mock.patch.dict(sys.modules, {"TTS": None, "TTS.api": None}):
            with self.assertRaises(ProviderUnavailable) as raised:
                asyncio.run(collect_audio(LocalTTS()))
        self.assertEqual("tts", raised.exception.provider)

    def test_pyttsx3_synthesize_raises_typed_error_without_dependency(self):
        with mock.patch.dict(sys.modules, {"pyttsx3": None}):
            with self.assertRaises(ProviderUnavailable) as raised:
                asyncio.run(collect_audio(Pyttsx3TTS()))
        self.assertEqual("tts", raised.exception.provider)

    def test_availability_is_false_when_optional_dependencies_are_absent(self):
        with mock.patch.dict(sys.modules, {"TTS": None}):
            self.assertFalse(local_tts_available())
        with mock.patch.dict(sys.modules, {"pyttsx3": None}):
            self.assertFalse(pyttsx3_available())

    def test_availability_is_true_when_optional_dependencies_are_present(self):
        with mock.patch.dict(sys.modules, {"TTS": types.ModuleType("TTS")}):
            self.assertTrue(local_tts_available())
        with mock.patch.dict(sys.modules, {"pyttsx3": types.ModuleType("pyttsx3")}):
            self.assertTrue(pyttsx3_available())


class TTSProviderResolutionTests(unittest.TestCase):
    def test_resolve_local_uses_configured_language(self):
        adapter = resolve_tts(config())
        self.assertIsInstance(adapter, LocalTTS)
        self.assertEqual("en", adapter.language)
        self.assertEqual("local", tts_provider(config(" LoCaL ")))

    def test_resolve_sapi_when_configured(self):
        configured = config("sapi")
        self.assertIsInstance(resolve_tts(configured), Pyttsx3TTS)
        self.assertEqual("sapi", tts_provider(configured))
        with mock.patch("jarvis.adapters.tts.resolve.pyttsx3_available", return_value=True):
            self.assertTrue(tts_available(configured))

    def test_cloud_and_unknown_providers_raise_typed_error(self):
        for provider in ("cloud", "remote"):
            with self.subTest(provider=provider), self.assertRaises(ProviderConfigError) as raised:
                resolve_tts(config(provider))
            self.assertEqual("tts", raised.exception.provider)


if __name__ == "__main__":
    unittest.main()
