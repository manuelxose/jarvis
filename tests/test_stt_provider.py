import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.stt import (
    SapiSTT,
    WhisperSTT,
    resolve_stt,
    sapi_stt_available,
    stt_available,
    stt_provider,
    whisper_available,
)
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable


async def empty_audio():
    if False:
        yield b""


async def collect_transcripts(adapter):
    return [transcript async for transcript in adapter.transcribe(empty_audio(), None)]


def config(provider="whisper"):
    return SimpleNamespace(
        stt=SimpleNamespace(provider=provider, model="base", language="en", device="cuda"),
        audio=SimpleNamespace(sample_rate=22050),
    )


class _FakeArray:
    def astype(self, _dtype):
        return self

    def __truediv__(self, _divisor):
        return self


class WhisperModelConstructionTests(unittest.TestCase):
    def test_transcribe_loads_the_model_from_local_cache_only(self):
        recorded_kwargs = {}

        class FakeSegment(SimpleNamespace):
            text = "hola"

        class FakeWhisperModel:
            def __init__(self, model_size, **kwargs):
                recorded_kwargs["model_size"] = model_size
                recorded_kwargs.update(kwargs)

            def transcribe(self, audio_array, **kwargs):
                return [FakeSegment()], None

        fake_whisper_module = types.ModuleType("faster_whisper")
        fake_whisper_module.WhisperModel = FakeWhisperModel

        fake_numpy_module = types.ModuleType("numpy")
        fake_numpy_module.int16 = "int16"
        fake_numpy_module.float32 = "float32"
        fake_numpy_module.frombuffer = lambda *_args, **_kwargs: _FakeArray()

        with mock.patch.dict(
            sys.modules,
            {"faster_whisper": fake_whisper_module, "numpy": fake_numpy_module},
        ):
            asyncio.run(collect_transcripts(WhisperSTT()))

        self.assertIs(True, recorded_kwargs.get("local_files_only"))


class STTProviderDependencyTests(unittest.TestCase):
    def test_whisper_transcribe_raises_typed_error_without_dependency(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaises(ProviderUnavailable) as raised:
                asyncio.run(collect_transcripts(WhisperSTT()))
        self.assertEqual("stt", raised.exception.provider)

    def test_sapi_transcribe_raises_typed_error_without_dependency(self):
        with mock.patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}):
            with self.assertRaises(ProviderUnavailable) as raised:
                asyncio.run(collect_transcripts(SapiSTT()))
        self.assertEqual("stt", raised.exception.provider)

    def test_availability_is_false_when_optional_dependencies_are_absent(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            self.assertFalse(whisper_available())
        with mock.patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}):
            self.assertFalse(sapi_stt_available())

    def test_availability_is_true_when_optional_dependencies_are_present(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": types.ModuleType("faster_whisper")}):
            self.assertTrue(whisper_available())
        comtypes = types.ModuleType("comtypes")
        comtypes.__path__ = []
        with mock.patch.dict(
            sys.modules,
            {"comtypes": comtypes, "comtypes.client": types.ModuleType("comtypes.client")},
        ):
            self.assertTrue(sapi_stt_available())


class STTProviderResolutionTests(unittest.TestCase):
    def test_resolve_whisper_uses_configured_settings(self):
        adapter = resolve_stt(config())
        self.assertIsInstance(adapter, WhisperSTT)
        self.assertEqual("base", adapter.model_size)
        self.assertEqual("en", adapter.language)
        self.assertEqual("cuda", adapter.device)
        self.assertEqual(22050, adapter.sample_rate)
        self.assertEqual("whisper", stt_provider(config(" WhIsPeR ")))

    def test_resolve_sapi_when_configured(self):
        configured = config("sapi")
        self.assertIsInstance(resolve_stt(configured), SapiSTT)
        self.assertEqual("sapi", stt_provider(configured))
        with mock.patch("jarvis.adapters.stt.resolve.sapi_stt_available", return_value=True):
            self.assertTrue(stt_available(configured))

    def test_cloud_and_unknown_providers_raise_typed_error(self):
        for provider in ("cloud", "remote"):
            with self.subTest(provider=provider), self.assertRaises(ProviderConfigError) as raised:
                resolve_stt(config(provider))
            self.assertEqual("stt", raised.exception.provider)


if __name__ == "__main__":
    unittest.main()
