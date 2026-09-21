import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.activation import ActivationManager, ActivationMode, ConversationWindow
from jarvis.adapters.audio.input import MicCapture, list_devices
from jarvis.adapters.audio.vad import EnergyVAD, rms_int16
from jarvis.core.errors import ProviderUnavailable
from jarvis.core.turn import TurnContext


def pcm(*samples):
    return struct.pack("<%dh" % len(samples), *samples)


class VADTests(unittest.TestCase):
    def test_rms_of_silence_and_signal(self):
        self.assertLess(rms_int16(pcm(0, 0, 0, 0)), 1.0)
        self.assertGreater(rms_int16(pcm(1000, 1000, 1000, 1000)), 900.0)

    def test_energy_vad_threshold(self):
        vad = EnergyVAD(threshold=500.0)
        self.assertFalse(vad.is_speech(pcm(0, 0, 0, 0)))
        self.assertTrue(vad.is_speech(pcm(2000, 2000, 2000, 2000)))

    def test_vad_ignores_incomplete_samples_and_rejects_invalid_configuration(self):
        self.assertEqual(0.0, rms_int16(b"\x01"))
        for threshold in (-1.0, float("nan"), float("inf")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                EnergyVAD(threshold=threshold)
        with self.assertRaises(ValueError):
            EnergyVAD(sample_width=1)


class ActivationTests(unittest.TestCase):
    def test_conversation_window_expires(self):
        window = ConversationWindow(timeout_seconds=0.01)
        window.note_activity()
        self.assertTrue(window.active)
        import time

        time.sleep(0.02)
        self.assertFalse(window.active)

    def test_wake_word_required_by_default(self):
        manager = ActivationManager(mode="wake_word")
        self.assertTrue(manager.wake_word_required())
        manager.note_turn_complete()
        self.assertFalse(manager.wake_word_required())

    def test_non_wake_modes_never_require_wake_word(self):
        for mode in (
            ActivationMode.CONTINUOUS.value,
            ActivationMode.PUSH_TO_TALK.value,
            ActivationMode.MANUAL.value,
        ):
            with self.subTest(mode=mode):
                self.assertFalse(ActivationManager(mode=mode).wake_word_required())

    def test_matches_wake_word(self):
        manager = ActivationManager(wake_word="jarvis")
        self.assertTrue(manager.matches_wake_word("Jarvis, abre spotify"))
        self.assertTrue(manager.matches_wake_word("Járvis, abre spotify"))
        self.assertFalse(manager.matches_wake_word("hola"))

    def test_extracts_command_only_after_leading_wake_word(self):
        manager = ActivationManager(wake_word="jarvis")

        self.assertEqual("abre Spotify", manager.command_after_wake_word("Jarvis, abre Spotify"))
        self.assertEqual("", manager.command_after_wake_word("¡Járvis!"))
        self.assertIsNone(manager.command_after_wake_word("hablé con Jarvis"))
        self.assertIsNone(manager.command_after_wake_word("jarvisito abre Spotify"))

    def test_tolerates_a_single_character_stt_slip_on_the_wake_word(self):
        manager = ActivationManager(wake_word="jarvis")

        self.assertEqual("abre Spotify", manager.command_after_wake_word("¡Carvis! abre Spotify"))

    def test_rejects_a_similar_length_word_that_is_not_a_close_match(self):
        manager = ActivationManager(wake_word="jarvis")

        self.assertIsNone(manager.command_after_wake_word("Javi, ¿qué hora es?"))

    def test_rejects_a_heavily_garbled_wake_word_attempt(self):
        manager = ActivationManager(wake_word="jarvis")

        self.assertIsNone(manager.command_after_wake_word("¡Görais! qué hora es"))

    def test_activation_rejects_empty_or_negative_timing_configuration(self):
        with self.assertRaises(ValueError):
            ActivationManager(wake_word="   ")
        with self.assertRaises(ValueError):
            ActivationManager(cooldown_seconds=-1)
        with self.assertRaises(ValueError):
            ConversationWindow(timeout_seconds=-1)

    def test_cooldown_starts_after_activation(self):
        manager = ActivationManager(cooldown_seconds=10)
        self.assertFalse(manager.in_cooldown())
        manager.note_activation()
        self.assertTrue(manager.in_cooldown())


class _Frames:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio

    def tobytes(self) -> bytes:
        return self.audio


class _InputStream:
    instances: list["_InputStream"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.requested_frames = 0
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def read(self, frames: int):
        self.requested_frames = frames
        return _Frames(b"pcm"), False


class MicCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_uses_selected_device_and_closes_on_cancellation(self):
        _InputStream.instances.clear()
        sounddevice = types.SimpleNamespace(InputStream=_InputStream)
        with mock.patch.dict(sys.modules, {"sounddevice": sounddevice}):
            capture = MicCapture(sample_rate=8000, channels=2, device="Microphone (USB)")
            context = TurnContext.fresh("audio")
            frames = capture.capture(context)
            self.assertEqual(b"pcm", await anext(frames))
            context.cancellation.cancel()
            with self.assertRaises(StopAsyncIteration):
                await anext(frames)

        stream = _InputStream.instances[0]
        self.assertEqual(800, stream.requested_frames)
        self.assertEqual("Microphone (USB)", stream.kwargs["device"])
        self.assertTrue(stream.closed)

    async def test_capture_translates_device_open_errors(self):
        def broken_stream(**_: object):
            raise OSError("device unavailable")

        sounddevice = types.SimpleNamespace(InputStream=broken_stream)
        with mock.patch.dict(sys.modules, {"sounddevice": sounddevice}):
            with self.assertRaises(ProviderUnavailable) as raised:
                await anext(MicCapture().capture(TurnContext.fresh("audio")))
        self.assertEqual("audio", raised.exception.provider)

    async def test_capture_reports_missing_optional_dependency(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaises(ProviderUnavailable):
                await anext(MicCapture().capture(TurnContext.fresh("audio")))

    def test_list_devices_handles_unavailable_and_malformed_device_data(self):
        unavailable = types.SimpleNamespace(query_devices=mock.Mock(side_effect=OSError("PortAudio unavailable")))
        with mock.patch.dict(sys.modules, {"sounddevice": unavailable}):
            self.assertEqual([], list_devices())

        malformed_response = types.SimpleNamespace(query_devices=lambda: None)
        with mock.patch.dict(sys.modules, {"sounddevice": malformed_response}):
            self.assertEqual([], list_devices())

        sounddevice = types.SimpleNamespace(
            query_devices=lambda: [
                {"name": "USB Mic", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "bad", "max_input_channels": object()},
                "malformed",
            ]
        )
        with mock.patch.dict(sys.modules, {"sounddevice": sounddevice}):
            self.assertEqual(
                [{"index": 0, "name": "USB Mic", "max_input_channels": 1, "max_output_channels": 0}],
                list_devices(),
            )

    def test_capture_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            MicCapture(sample_rate=0)
        with self.assertRaises(ValueError):
            MicCapture(channels=0)


if __name__ == "__main__":
    unittest.main()
