"""Regression tests for non-blocking startup and single-capture wake flow.

Pins the M003/S02 contract for the *legacy* runtime:

1. Readiness is not blocked by optional XTTS cache pre-generation.
2. TTS cache work is deferrable (background) and serializable (synchronous)
   so it can be kept behind active STT.
3. An inline wake command is extracted from a single utterance, so a wake
   with an inline command never triggers a second capture/transcription.

The legacy services (``main``, ``voice.tts``) pull in heavy optional third
party dependencies (colorlog, Coqui TTS, numpy, pyaudio, openwakeword,
faster-whisper, ...) that are not installed in the test environment.  We stub
those modules in ``sys.modules`` before importing ``main``, then fake the
legacy services themselves with ``unittest.mock`` (see AGENTS.md: "Use
mocks/fakes around legacy services").
"""

import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cache import audio_cache
from cache.audio_cache import (
    COMMON_RESPONSES,
    AudioCache,
    pregenerate_common_responses,
)


# ---------------------------------------------------------------------------
# Stub heavy optional dependencies so the legacy modules can be imported.
# ---------------------------------------------------------------------------

def _legacy_dependency_stubs() -> dict[str, types.ModuleType]:
    stubs: dict[str, types.ModuleType] = {}
    for name in (
        "colorlog",
        "TTS",
        "TTS.api",
        "numpy",
        "psutil",
        "pyautogui",
        "pyaudio",
        "sounddevice",
        "soundfile",
        "webrtcvad",
        "faster_whisper",
        "openwakeword",
        "openwakeword.model",
        "openwakeword.utils",
    ):
        stubs[name] = types.ModuleType(name)

    stubs["colorlog"].ColoredFormatter = mock.Mock
    stubs["colorlog"].StreamHandler = mock.Mock
    # A fresh mock engine per construction, so each TTSService gets its own
    # inspectable engine (a string spec would reject ``tts_to_file``).
    stubs["TTS.api"].TTS = lambda *args, **kwargs: mock.Mock()
    stubs["numpy"].int16 = object()
    stubs["faster_whisper"].WhisperModel = mock.Mock
    stubs["openwakeword.model"].Model = mock.Mock
    stubs["openwakeword.utils"].download_models = mock.Mock
    return stubs


with mock.patch.dict(sys.modules, _legacy_dependency_stubs()):
    import main  # noqa: E402
    import voice.tts as legacy_tts  # noqa: E402
    import voice.stt as legacy_stt  # noqa: E402
    from voice.stt import SttActivityGate  # noqa: E402


def _runtime_config() -> dict:
    """Minimal but complete legacy config used by ``build_runtime_components``."""
    return {
        "llm": {
            "base_url": "http://localhost:11434",
            "model": "mistral:7b-instruct",
            "temperature": 0.7,
            "timeout": 30,
            "max_history_messages": 10,
        },
        "tts": {
            "model": "tts_models/multilingual/multi-dataset/xtts_v2",
            "language": "es",
            "cache_enabled": True,
            "auto_accept_cpml": True,
            "speaker_wav_dir": "voice_samples/",
        },
        "stt": {"model": "base", "language": "es"},
        "audio": {
            "sample_rate": 16000,
            "channels": 1,
            "input_device": None,
            "auto_select_input": True,
        },
        "ais": {"host": "localhost", "port": 10110, "enabled": True},
        "trading": {"mt4_log_path": "", "enabled": True},
        "jarvis": {"user_name": "Manuel"},
        "wake_word": {
            "model": "hey_jarvis",
            "threshold": 0.35,
            "cooldown_seconds": 1.5,
            "hard_trigger_hits": 1,
            "soft_trigger_hits": 4,
            "soft_trigger_ratio": 0.6,
            "voice_rms_for_soft_trigger": 30.0,
            "score_log_interval_seconds": 5.0,
            "openwakeword_enabled": True,
            "stt_fallback_enabled": True,
            "stt_fallback_max_record_seconds": 2.2,
            "stt_fallback_probe_interval_seconds": 1.0,
            "continuous_listen_when_disabled": True,
            "continuous_require_keyword": False,
        },
    }


class CachePregenerationDeferralTests(unittest.TestCase):
    """Cache pre-generation is deferrable (background) and serializable (sync)."""

    def _cache(self, tmp: str) -> AudioCache:
        return AudioCache(Path(tmp) / "cache" / "responses")

    def test_background_generation_returns_without_waiting_for_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            entered = threading.Event()
            release = threading.Event()
            calls: list[str] = []

            def blocking_synth(phrase: str, path: Path) -> None:
                calls.append(phrase)
                entered.set()
                release.wait(timeout=5)

            thread = pregenerate_common_responses(
                synthesizer=blocking_synth,
                cache=cache,
                background=True,
            )

            # The caller returns immediately with a live daemon thread while
            # synthesis is still blocked: readiness is never gated on cache work.
            self.assertIsNotNone(thread)
            self.assertTrue(thread.daemon)
            self.assertTrue(entered.wait(timeout=5), "worker never started")
            self.assertTrue(thread.is_alive(), "worker finished before release")
            self.assertFalse(release.is_set(), "caller blocked on synthesis")

            release.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(COMMON_RESPONSES), len(calls))

    def test_synchronous_generation_serializes_until_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            calls: list[str] = []

            def synth(phrase: str, path: Path) -> None:
                calls.append(phrase)

            result = pregenerate_common_responses(
                synthesizer=synth,
                cache=cache,
                background=False,
            )

            # Synchronous mode lets a caller serialize cache work behind STT.
            self.assertIsNone(result)
            self.assertEqual(len(COMMON_RESPONSES), len(calls))

    def test_already_cached_phrases_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            cache.cache_audio("Entendido", b"RIFFfake")
            calls: list[str] = []

            def synth(phrase: str, path: Path) -> None:
                calls.append(phrase)

            pregenerate_common_responses(
                synthesizer=synth,
                cache=cache,
                background=False,
            )

            self.assertNotIn("Entendido", calls)
            self.assertEqual(len(COMMON_RESPONSES) - 1, len(calls))

    def test_failed_synthesis_does_not_crash_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)

            def failing_synth(phrase: str, path: Path) -> None:
                raise RuntimeError("synthesis unavailable")

            # Synchronous mode swallows per-phrase errors.
            pregenerate_common_responses(
                synthesizer=failing_synth,
                cache=cache,
                background=False,
            )

            # Background mode completes without propagating the error.
            thread = pregenerate_common_responses(
                synthesizer=failing_synth,
                cache=cache,
                background=True,
            )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_background_generation_pauses_while_stt_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            busy = threading.Event()
            busy.set()
            calls: list[str] = []

            def synth(phrase: str, path: Path) -> None:
                calls.append(phrase)

            thread = pregenerate_common_responses(
                synthesizer=synth,
                cache=cache,
                background=True,
                is_busy=busy.is_set,
            )

            # While STT is "busy", the worker must not synthesize anything.
            time.sleep(0.15)
            self.assertEqual([], calls)
            self.assertTrue(thread.is_alive())

            busy.clear()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(COMMON_RESPONSES), len(calls))


class ReadinessTests(unittest.TestCase):
    """Startup reaches ready without synchronously generating the TTS cache."""

    def test_build_runtime_components_defers_cache_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            config = _runtime_config()

            patches = mock.patch.multiple(
                main,
                MemoryStore=mock.Mock(),
                OllamaClient=mock.Mock(),
                STTService=mock.Mock(),
                PCController=mock.Mock(),
                AISMonitor=mock.Mock(),
                TradingMonitor=mock.Mock(),
                WebSearch=mock.Mock(),
                ActionRouter=mock.Mock(),
                build_system_prompt=mock.Mock(return_value="system prompt"),
                WakeWordListener=mock.Mock(),
                resolve_input_device=mock.Mock(return_value=0),
                resolve_capture_backend=mock.Mock(
                    return_value=SimpleNamespace(device_index=0)
                ),
                check_microphone_capture=mock.Mock(return_value=(True, 0.05, "ok")),
                format_audio_device=mock.Mock(return_value="device-0"),
                format_capture_backend=mock.Mock(return_value="backend"),
            )
            with mock.patch.object(main, "TTSService") as tts_service_cls, patches:
                components = main.build_runtime_components(base_dir, config)

            # Readiness: the component map is returned with the expected keys.
            for key in ("tts", "audio_cache", "stt", "router", "wake_listener"):
                self.assertIn(key, components)

            # Cache pre-generation is deferred, never run synchronously.
            tts_service_cls.return_value.pregenerate_common_cache.assert_called_once_with(
                background=True
            )

    def test_build_runtime_components_wires_shared_stt_activity_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            config = _runtime_config()

            patches = mock.patch.multiple(
                main,
                MemoryStore=mock.Mock(),
                OllamaClient=mock.Mock(),
                PCController=mock.Mock(),
                AISMonitor=mock.Mock(),
                TradingMonitor=mock.Mock(),
                WebSearch=mock.Mock(),
                ActionRouter=mock.Mock(),
                build_system_prompt=mock.Mock(return_value="system prompt"),
                WakeWordListener=mock.Mock(),
                resolve_input_device=mock.Mock(return_value=0),
                resolve_capture_backend=mock.Mock(
                    return_value=SimpleNamespace(device_index=0)
                ),
                check_microphone_capture=mock.Mock(return_value=(True, 0.05, "ok")),
                format_audio_device=mock.Mock(return_value="device-0"),
                format_capture_backend=mock.Mock(return_value="backend"),
            )
            with mock.patch.object(main, "TTSService") as tts_cls, mock.patch.object(
                main, "STTService"
            ) as stt_cls, patches:
                main.build_runtime_components(base_dir, config)

            tts_gate = tts_cls.call_args.kwargs.get("stt_activity_gate")
            stt_gate = stt_cls.call_args.kwargs.get("activity_gate")
            self.assertIsNotNone(tts_gate)
            self.assertIs(tts_gate, stt_gate)


class TTSServiceCacheDeferralTests(unittest.TestCase):
    """``TTSService`` forwards cache pre-generation in a deferrable way."""

    def _make_tts(self, tmp: str) -> legacy_tts.TTSService:
        base_dir = Path(tmp)
        samples = base_dir / "voice_samples"
        samples.mkdir()
        (samples / "sample1.wav").write_bytes(b"RIFFfake")
        cache = AudioCache(base_dir / "cache" / "responses")
        return legacy_tts.TTSService(
            tts_config={"model": "tts_models/x", "language": "es"},
            cache=cache,
            base_dir=base_dir,
        )

    def test_pregenerate_common_cache_defers_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tts = self._make_tts(tmp)
            with mock.patch.object(
                legacy_tts, "pregenerate_common_responses"
            ) as pregen:
                tts.pregenerate_common_cache()
            pregen.assert_called_once()
            self.assertIs(pregen.call_args.kwargs.get("background"), True)

    def test_pregenerate_common_cache_can_serialize_in_foreground(self):
        with tempfile.TemporaryDirectory() as tmp:
            tts = self._make_tts(tmp)
            result = tts.pregenerate_common_cache(background=False)
            self.assertIsNone(result)
            # Foreground generation actually drove the synthesizer once per phrase.
            self.assertEqual(len(COMMON_RESPONSES), tts.engine.tts_to_file.call_count)

    def test_pregenerate_common_cache_passes_gate_as_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tts = self._make_tts(tmp)
            gate = SttActivityGate()
            tts.stt_activity_gate = gate
            with mock.patch.object(
                legacy_tts, "pregenerate_common_responses"
            ) as pregen:
                tts.pregenerate_common_cache()
            is_busy = pregen.call_args.kwargs.get("is_busy")
            self.assertIsNotNone(is_busy)
            self.assertFalse(is_busy())
            gate.__enter__()
            try:
                self.assertTrue(is_busy())
            finally:
                gate.__exit__(None, None, None)
            self.assertFalse(is_busy())


class InlineWakeCommandTests(unittest.TestCase):
    """An inline wake command is resolved from one utterance, no second capture."""

    def test_inline_command_extracted_from_single_utterance(self):
        detected, command = main._extract_command_from_wake("jarvis abre la calculadora")
        self.assertTrue(detected)
        self.assertEqual("abre la calculadora", command)

    def test_bare_wake_word_yields_empty_command_for_follow_up(self):
        detected, command = main._extract_command_from_wake("jarvis")
        self.assertTrue(detected)
        self.assertEqual("", command)

    def test_non_wake_utterance_is_not_detected(self):
        detected, command = main._extract_command_from_wake("hola mundo")
        self.assertFalse(detected)
        self.assertEqual("", command)

    def test_empty_utterance_is_not_detected(self):
        self.assertEqual((False, ""), main._extract_command_from_wake(""))

    def test_wake_aliases_are_recognized(self):
        for alias in ("jarbis", "yarvis", "jarviss", "jervis", "harvis", "jarbisz"):
            with self.subTest(alias=alias):
                detected, command = main._extract_command_from_wake(
                    f"{alias} enciende la luz"
                )
                self.assertTrue(detected)
                self.assertEqual("enciende la luz", command)

    def test_accented_and_uppercase_wake_is_normalized(self):
        detected, command = main._extract_command_from_wake("J\u00c1RVIS qu\u00e9 hora es")
        self.assertTrue(detected)
        self.assertEqual("que hora es", command)

    def test_fuzzy_wake_token_matching_jarvis_prefix(self):
        detected, command = main._extract_command_from_wake("jarvix reproduce musica")
        self.assertTrue(detected)
        self.assertEqual("reproduce musica", command)


class _FakeAudio:
    size = 0


class SttActivityGateTests(unittest.TestCase):
    def test_idle_by_default(self):
        gate = SttActivityGate()
        self.assertFalse(gate.active)
        self.assertTrue(gate.wait_until_idle(timeout=0))

    def test_enter_exit_toggles_active_and_wait(self):
        gate = SttActivityGate()
        gate.__enter__()
        self.assertTrue(gate.active)
        self.assertFalse(gate.wait_until_idle(timeout=0.05))
        gate.__exit__(None, None, None)
        self.assertFalse(gate.active)
        self.assertTrue(gate.wait_until_idle(timeout=0.05))

    def test_nested_entries_keep_active_until_last_exit(self):
        gate = SttActivityGate()
        gate.__enter__()
        gate.__enter__()
        gate.__exit__(None, None, None)
        self.assertTrue(gate.active)
        gate.__exit__(None, None, None)
        self.assertFalse(gate.active)


class SttServiceGateTests(unittest.TestCase):
    def _service(self, gate: SttActivityGate) -> legacy_stt.STTService:
        return legacy_stt.STTService(
            stt_config={"model": "base", "language": "es"},
            audio_config={
                "sample_rate": 16000,
                "channels": 1,
                "input_device": None,
                "capture_backend": None,
            },
            activity_gate=gate,
        )

    def test_transcribe_from_mic_marks_gate_active(self):
        gate = SttActivityGate()
        observed = {"active_during_capture": False}
        service = self._service(gate)

        def capture(*args, **kwargs):
            observed["active_during_capture"] = gate.active
            return _FakeAudio()

        with mock.patch.object(
            legacy_stt, "record_until_silence", side_effect=capture
        ), mock.patch.object(service, "transcribe_audio", return_value="hola"):
            result = service.transcribe_from_mic()

        self.assertEqual("hola", result)
        self.assertTrue(observed["active_during_capture"])
        self.assertFalse(gate.active)

    def test_transcribe_for_wake_marks_gate_active(self):
        gate = SttActivityGate()
        observed = {"active_during_capture": False}
        service = self._service(gate)

        def capture(*args, **kwargs):
            observed["active_during_capture"] = gate.active
            return _FakeAudio()

        with mock.patch.object(
            legacy_stt, "record_until_silence", side_effect=capture
        ), mock.patch.object(service, "transcribe_audio", return_value="jarvis"):
            result = service.transcribe_for_wake()

        self.assertEqual("jarvis", result)
        self.assertTrue(observed["active_during_capture"])
        self.assertFalse(gate.active)


if __name__ == "__main__":
    unittest.main()
