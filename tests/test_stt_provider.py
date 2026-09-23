import asyncio
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.stt import (
    AlibabaQwenSTT,
    SapiSTT,
    WhisperSTT,
    alibaba_qwen_stt_available,
    resolve_stt,
    sapi_stt_available,
    stt_available,
    stt_provider,
    whisper_available,
)
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext


async def empty_audio():
    if False:
        yield b""


async def one_frame(frame=b"\x00\x01"):
    yield frame


async def collect_transcripts(adapter, audio=None, context=None):
    return [
        transcript
        async for transcript in adapter.transcribe(audio or empty_audio(), context or TurnContext.fresh("t"))
    ]


def config(provider="whisper", *, voice="", api_key=None, workspace_id="ws-1"):
    return SimpleNamespace(
        stt=SimpleNamespace(provider=provider, model="base", language="en", device="cuda", api_key=api_key),
        audio=SimpleNamespace(sample_rate=22050),
        alibaba=SimpleNamespace(
            region="singapore",
            workspace_id=workspace_id,
            stt_model="qwen3-asr-flash-realtime",
            tts_model="qwen3-tts-flash-realtime",
        ),
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

    def test_model_is_constructed_once_and_reused_across_calls(self):
        construction_count = 0

        class FakeSegment(SimpleNamespace):
            text = "hola"

        class FakeWhisperModel:
            def __init__(self, model_size, **kwargs):
                nonlocal construction_count
                construction_count += 1

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
            adapter = WhisperSTT()
            asyncio.run(collect_transcripts(adapter))
            asyncio.run(collect_transcripts(adapter))

        self.assertEqual(1, construction_count)


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

    def test_resolve_alibaba_qwen_uses_configured_settings(self):
        configured = config("alibaba_qwen", api_key="secret")
        adapter = resolve_stt(configured)
        self.assertIsInstance(adapter, AlibabaQwenSTT)
        self.assertEqual("secret", adapter.api_key)
        self.assertEqual("qwen3-asr-flash-realtime", adapter.model)
        self.assertEqual("alibaba_qwen", stt_provider(configured))

    def test_cloud_and_unknown_providers_raise_typed_error(self):
        for provider in ("cloud", "remote"):
            with self.subTest(provider=provider), self.assertRaises(ProviderConfigError) as raised:
                resolve_stt(config(provider))
            self.assertEqual("stt", raised.exception.provider)


def _fake_dashscope_modules():
    """A fake ``dashscope`` package exercising the real threading callback bridge."""
    fake_dashscope = types.ModuleType("dashscope")
    fake_dashscope.api_key = None
    fake_dashscope.base_websocket_api_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    def set_region(region, workspace_id=None):
        if not workspace_id:
            raise ValueError("workspace_id is required")
        fake_dashscope.base_websocket_api_url = f"wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/inference"

    fake_dashscope.set_region = set_region

    class RecognitionCallback:
        def on_open(self):
            pass

        def on_complete(self):
            pass

        def on_error(self, result):
            pass

        def on_close(self):
            pass

        def on_event(self, result):
            pass

    class FakeRecognitionResult(SimpleNamespace):
        def get_sentence(self):
            return self.sentence

    class Recognition:
        def __init__(self, model, callback, format, sample_rate, workspace=None, **kwargs):
            self.model = model
            self.callback = callback
            self.format = format
            self.sample_rate = sample_rate
            self.workspace = workspace
            self.kwargs = kwargs
            self.sent_frames = []
            self.stopped = False

        def start(self):
            self.callback.on_open()

        def send_audio_frame(self, buffer):
            self.sent_frames.append(buffer)

            def _deliver():
                time.sleep(0.01)
                self.callback.on_event(
                    FakeRecognitionResult(sentence={"text": "hola", "end_time": 500})
                )
                self.callback.on_complete()

            threading.Thread(target=_deliver, daemon=True).start()

        def stop(self):
            self.stopped = True
            self.callback.on_close()

    asr_module = types.ModuleType("dashscope.audio.asr")
    asr_module.Recognition = Recognition
    asr_module.RecognitionCallback = RecognitionCallback

    audio_module = types.ModuleType("dashscope.audio")
    audio_module.asr = asr_module

    return fake_dashscope, audio_module, asr_module


class AlibabaQwenSTTTests(unittest.TestCase):
    def test_available_requires_dependency_key_and_workspace(self):
        fake_dashscope, audio_module, asr_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {"dashscope": fake_dashscope, "dashscope.audio": audio_module, "dashscope.audio.asr": asr_module},
        ):
            self.assertFalse(alibaba_qwen_stt_available(config("alibaba_qwen", api_key=None)))
            self.assertFalse(alibaba_qwen_stt_available(config("alibaba_qwen", api_key="k", workspace_id="")))
            self.assertTrue(alibaba_qwen_stt_available(config("alibaba_qwen", api_key="k")))
        with mock.patch.dict(sys.modules, {"dashscope": None}):
            self.assertFalse(alibaba_qwen_stt_available(config("alibaba_qwen", api_key="k")))

    def test_transcribe_raises_typed_config_error_without_key(self):
        fake_dashscope, audio_module, asr_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {"dashscope": fake_dashscope, "dashscope.audio": audio_module, "dashscope.audio.asr": asr_module},
        ):
            adapter = AlibabaQwenSTT.from_config(config("alibaba_qwen", api_key=None))
            with self.assertRaises(ProviderConfigError):
                asyncio.run(collect_transcripts(adapter, one_frame()))

    def test_transcribe_raises_typed_config_error_without_workspace_id(self):
        fake_dashscope, audio_module, asr_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {"dashscope": fake_dashscope, "dashscope.audio": audio_module, "dashscope.audio.asr": asr_module},
        ):
            adapter = AlibabaQwenSTT.from_config(config("alibaba_qwen", api_key="secret", workspace_id=""))
            with self.assertRaises(ProviderConfigError):
                asyncio.run(collect_transcripts(adapter, one_frame()))

    def test_transcribe_streams_frames_and_yields_a_final_transcript(self):
        fake_dashscope, audio_module, asr_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {"dashscope": fake_dashscope, "dashscope.audio": audio_module, "dashscope.audio.asr": asr_module},
        ):
            adapter = AlibabaQwenSTT.from_config(config("alibaba_qwen", api_key="secret"))
            transcripts = asyncio.run(collect_transcripts(adapter, one_frame(b"\x01\x02")))

        self.assertEqual(1, len(transcripts))
        self.assertEqual("hola", transcripts[0].text)
        self.assertTrue(transcripts[0].is_final)
        self.assertEqual(
            "wss://ws-1.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference",
            fake_dashscope.base_websocket_api_url,
        )


if __name__ == "__main__":
    unittest.main()
