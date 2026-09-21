import asyncio
import sys
import types
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts import (
    ElevenLabsTTS,
    LocalTTS,
    Pyttsx3TTS,
    elevenlabs_available,
    local_tts_available,
    pyttsx3_available,
    resolve_tts,
    tts_available,
    tts_provider,
)
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext


async def empty_text():
    if False:
        yield ""


async def one_chunk(text="hola"):
    yield text


async def collect_audio(adapter, text=None):
    context = TurnContext.fresh("test")
    return [audio async for audio in adapter.synthesize(text or empty_text(), context)]


def config(provider="local", *, voice="", api_key=None):
    return SimpleNamespace(
        tts=SimpleNamespace(provider=provider, language="en", voice=voice, api_key=api_key)
    )


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

    def test_resolve_elevenlabs_uses_configured_voice_and_key(self):
        configured = config("elevenlabs", voice="cloned-voice-id", api_key="secret")
        adapter = resolve_tts(configured)
        self.assertIsInstance(adapter, ElevenLabsTTS)
        self.assertEqual("cloned-voice-id", adapter.voice_id)
        self.assertEqual("secret", adapter.api_key)
        self.assertEqual("elevenlabs", tts_provider(configured))

    def test_elevenlabs_available_requires_key_and_voice(self):
        self.assertFalse(elevenlabs_available(api_key=None, voice_id="voice"))
        self.assertFalse(elevenlabs_available(api_key="secret", voice_id=""))
        self.assertTrue(elevenlabs_available(api_key="secret", voice_id="voice"))


class ElevenLabsTTSTests(unittest.TestCase):
    def test_synthesize_raises_typed_config_error_without_key_or_voice(self):
        with self.assertRaises(ProviderConfigError) as raised:
            asyncio.run(collect_audio(ElevenLabsTTS(api_key=None, voice_id="voice"), one_chunk()))
        self.assertEqual("tts", raised.exception.provider)

        with self.assertRaises(ProviderConfigError):
            asyncio.run(collect_audio(ElevenLabsTTS(api_key="secret", voice_id=""), one_chunk()))

    def test_synthesize_posts_to_the_voice_endpoint_and_yields_the_wav_bytes(self):
        adapter = ElevenLabsTTS(api_key="secret", voice_id="cloned-voice-id")
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"RIFF....WAVEfmt "
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=fake_response) as urlopen:
            audio = asyncio.run(collect_audio(adapter, one_chunk("hola mundo")))

        self.assertEqual([b"RIFF....WAVEfmt "], audio)
        request = urlopen.call_args.args[0]
        self.assertIn("cloned-voice-id", request.full_url)
        self.assertIn("output_format=wav_16000", request.full_url)
        self.assertEqual("secret", request.get_header("Xi-api-key"))

    def test_synthesize_maps_auth_errors_to_typed_config_error(self):
        adapter = ElevenLabsTTS(api_key="bad-key", voice_id="cloned-voice-id")
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        error.read = lambda *_a, **_k: b"invalid api key"

        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderConfigError) as raised:
                asyncio.run(collect_audio(adapter, one_chunk()))
        self.assertEqual("tts", raised.exception.provider)

    def test_synthesize_maps_network_errors_to_typed_unavailable_error(self):
        adapter = ElevenLabsTTS(api_key="secret", voice_id="cloned-voice-id")
        error = urllib.error.URLError("timed out")

        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderUnavailable) as raised:
                asyncio.run(collect_audio(adapter, one_chunk()))
        self.assertEqual("tts", raised.exception.provider)


if __name__ == "__main__":
    unittest.main()
