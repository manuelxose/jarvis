import asyncio
import base64
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts import (
    AlibabaQwenTTS,
    LocalTTS,
    Pyttsx3TTS,
    alibaba_qwen_tts_available,
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


async def collect_audio(adapter, text=None, context=None):
    context = context or TurnContext.fresh("test")
    return [audio async for audio in adapter.synthesize(text or empty_text(), context)]


def config(provider="local", *, voice="", api_key=None, workspace_id="ws-1"):
    return SimpleNamespace(
        tts=SimpleNamespace(provider=provider, language="en", voice=voice, api_key=api_key),
        alibaba=SimpleNamespace(
            region="singapore",
            workspace_id=workspace_id,
            stt_model="qwen3-asr-flash-realtime",
            tts_model="qwen3-tts-flash-realtime",
        ),
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


class LocalTTSConstructionTests(unittest.TestCase):
    def test_engine_is_constructed_once_and_reused_across_turns(self):
        construction_count = 0

        class FakeEngine:
            def __init__(self, _model_name):
                nonlocal construction_count
                construction_count += 1

            def tts(self, *, text, language):
                return [0.0]

        fake_tts_module = types.ModuleType("TTS")
        fake_tts_api_module = types.ModuleType("TTS.api")
        fake_tts_api_module.TTS = FakeEngine

        fake_numpy_module = types.ModuleType("numpy")
        fake_numpy_module.asarray = lambda value: value

        fake_soundfile_module = types.ModuleType("soundfile")
        fake_soundfile_module.write = lambda *args, **kwargs: None

        with mock.patch.dict(
            sys.modules,
            {
                "TTS": fake_tts_module,
                "TTS.api": fake_tts_api_module,
                "numpy": fake_numpy_module,
                "soundfile": fake_soundfile_module,
            },
        ):
            adapter = LocalTTS()
            asyncio.run(collect_audio(adapter, one_chunk("hola")))
            asyncio.run(collect_audio(adapter, one_chunk("adios")))

        self.assertEqual(1, construction_count)


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

    def test_resolve_alibaba_qwen_uses_configured_voice_and_key(self):
        configured = config("alibaba_qwen", voice="cloned-voice-id", api_key="secret")
        adapter = resolve_tts(configured)
        self.assertIsInstance(adapter, AlibabaQwenTTS)
        self.assertEqual("cloned-voice-id", adapter.voice_id)
        self.assertEqual("secret", adapter.api_key)
        self.assertEqual("qwen3-tts-flash-realtime", adapter.model)
        self.assertEqual("alibaba_qwen", tts_provider(configured))

    def test_cloud_and_unknown_providers_raise_typed_error(self):
        for provider in ("cloud", "remote"):
            with self.subTest(provider=provider), self.assertRaises(ProviderConfigError) as raised:
                resolve_tts(config(provider))
            self.assertEqual("tts", raised.exception.provider)


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

    class QwenTtsRealtimeCallback:
        def on_open(self):
            pass

        def on_close(self, close_status_code, close_msg):
            pass

        def on_event(self, message):
            pass

    class QwenTtsRealtime:
        def __init__(self, model, headers=None, callback=None, workspace=None, url=None, additional_params=None):
            self.model = model
            self.callback = callback
            self.workspace = workspace
            self.url = url
            self.appended = []
            self.session_config = None
            self.closed = False

        def connect(self):
            self.callback.on_open()

        def update_session(self, voice, audio_format=None, language_type=None, **kwargs):
            self.session_config = {"voice": voice, "audio_format": audio_format, "language_type": language_type}

        def append_text(self, text):
            self.appended.append(text)

        def finish(self):
            def _deliver():
                time.sleep(0.01)
                delta = base64.b64encode(b"AUDIO").decode("ascii")
                self.callback.on_event({"type": "response.audio.delta", "delta": delta})
                self.callback.on_event({"type": "response.done"})
                self.callback.on_close(1000, "done")

            threading.Thread(target=_deliver, daemon=True).start()

        def close(self):
            self.closed = True

    qwen_module = types.ModuleType("dashscope.audio.qwen_tts_realtime")
    qwen_module.QwenTtsRealtime = QwenTtsRealtime
    qwen_module.QwenTtsRealtimeCallback = QwenTtsRealtimeCallback

    audio_module = types.ModuleType("dashscope.audio")
    audio_module.qwen_tts_realtime = qwen_module

    return fake_dashscope, audio_module, qwen_module


class AlibabaQwenTTSTests(unittest.TestCase):
    def test_available_requires_dependency_key_voice_and_workspace(self):
        fake_dashscope, audio_module, qwen_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": audio_module,
                "dashscope.audio.qwen_tts_realtime": qwen_module,
            },
        ):
            self.assertFalse(alibaba_qwen_tts_available(config("alibaba_qwen", voice="v", api_key=None)))
            self.assertFalse(alibaba_qwen_tts_available(config("alibaba_qwen", voice="", api_key="k")))
            self.assertFalse(
                alibaba_qwen_tts_available(config("alibaba_qwen", voice="v", api_key="k", workspace_id=""))
            )
            self.assertTrue(alibaba_qwen_tts_available(config("alibaba_qwen", voice="v", api_key="k")))
        with mock.patch.dict(sys.modules, {"dashscope": None}):
            self.assertFalse(alibaba_qwen_tts_available(config("alibaba_qwen", voice="v", api_key="k")))

    def test_synthesize_raises_typed_config_error_without_key_or_voice(self):
        fake_dashscope, audio_module, qwen_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": audio_module,
                "dashscope.audio.qwen_tts_realtime": qwen_module,
            },
        ):
            adapter = AlibabaQwenTTS.from_config(config("alibaba_qwen", voice="voice", api_key=None))
            with self.assertRaises(ProviderConfigError):
                asyncio.run(collect_audio(adapter, one_chunk()))

            adapter = AlibabaQwenTTS.from_config(config("alibaba_qwen", voice="", api_key="secret"))
            with self.assertRaises(ProviderConfigError):
                asyncio.run(collect_audio(adapter, one_chunk()))

    def test_synthesize_raises_typed_config_error_without_workspace_id(self):
        fake_dashscope, audio_module, qwen_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": audio_module,
                "dashscope.audio.qwen_tts_realtime": qwen_module,
            },
        ):
            adapter = AlibabaQwenTTS.from_config(
                config("alibaba_qwen", voice="voice", api_key="secret", workspace_id="")
            )
            with self.assertRaises(ProviderConfigError):
                asyncio.run(collect_audio(adapter, one_chunk()))

    def test_synthesize_streams_audio_deltas_and_closes_cleanly(self):
        fake_dashscope, audio_module, qwen_module = _fake_dashscope_modules()
        with mock.patch.dict(
            sys.modules,
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": audio_module,
                "dashscope.audio.qwen_tts_realtime": qwen_module,
            },
        ):
            adapter = AlibabaQwenTTS.from_config(
                config("alibaba_qwen", voice="cloned-voice-id", api_key="secret")
            )
            audio = asyncio.run(collect_audio(adapter, one_chunk("hola mundo")))

        self.assertEqual([b"AUDIO"], audio)
        self.assertEqual(
            "wss://ws-1.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/inference",
            fake_dashscope.base_websocket_api_url,
        )


if __name__ == "__main__":
    unittest.main()
