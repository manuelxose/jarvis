import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.wake import OpenWakeWordDetector, openwakeword_available
from jarvis.adapters.fakes import ScriptedWakeDetector
from jarvis.core.errors import ProviderUnavailable


def pcm(*samples: int) -> bytes:
    return struct.pack("<%dh" % len(samples), *samples)


def fake_dependencies(score_for_frame):
    """Return minimal optional-runtime modules and their inspectable model mock."""
    numpy = types.ModuleType("numpy")
    numpy.int16 = object()
    numpy.frombuffer = mock.Mock(
        side_effect=lambda audio, dtype: struct.unpack("<%dh" % (len(audio) // 2), audio)
    )
    model = types.SimpleNamespace(
        predict=mock.Mock(side_effect=lambda frame: {"hey_jarvis": score_for_frame(frame)})
    )
    model_class = mock.Mock(return_value=model)
    openwakeword = types.ModuleType("openwakeword")
    openwakeword.__path__ = []
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = model_class
    return {
        "numpy": numpy,
        "openwakeword": openwakeword,
        "openwakeword.model": model_module,
    }, model_class, model


class OpenWakeWordDetectorTests(unittest.TestCase):
    def test_silence_and_low_amplitude_frames_do_not_detect(self):
        modules, _, _ = fake_dependencies(
            lambda frame: 0.9 if any(abs(sample) > 1000 for sample in frame) else 0.1
        )
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector()
            self.assertFalse(detector.detected(pcm(0, 0, 0, 0)))
            self.assertFalse(detector.detected(pcm(10, -10, 15, -15)))

    def test_synthetic_jarvis_frame_detects_above_threshold(self):
        modules, _, model = fake_dependencies(
            lambda frame: 0.9 if any(abs(sample) > 1000 for sample in frame) else 0.1
        )
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector()
            self.assertFalse(detector.detected(pcm(0, 1400, -1800, 900)))
            self.assertTrue(detector.detected(pcm(0, 1400, -1800, 900)))
        self.assertEqual(2, model.predict.call_count)

    def test_single_high_frame_does_not_trigger_without_streak(self):
        modules, _, model = fake_dependencies(lambda _frame: 0.9)
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector()
            self.assertFalse(detector.detected(pcm(1, 2)))
            self.assertEqual(0.9, detector.last_score)
        self.assertEqual(1, model.predict.call_count)

    def test_streak_resets_on_a_low_frame(self):
        modules, _, _ = fake_dependencies(
            lambda frame: 0.9 if any(abs(sample) > 1000 for sample in frame) else 0.1
        )
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector()
            self.assertFalse(detector.detected(pcm(1400, -1800, 900, 0)))
            self.assertFalse(detector.detected(pcm(10, -10)))
            self.assertFalse(detector.detected(pcm(1400, -1800, 900, 0)))

    def test_empty_and_undersized_frames_skip_model_initialization(self):
        modules, model_class, model = fake_dependencies(lambda _frame: 0.9)
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector()
            self.assertFalse(detector.detected(b""))
            self.assertFalse(detector.detected(b"\x00"))
        model_class.assert_not_called()
        model.predict.assert_not_called()

    def test_missing_optional_dependencies_raise_typed_error(self):
        with mock.patch.dict(
            sys.modules,
            {"numpy": None, "openwakeword": None, "openwakeword.model": None},
        ):
            with self.assertRaises(ProviderUnavailable) as raised:
                OpenWakeWordDetector().detected(pcm(1, 2))
        self.assertEqual("wake_word", raised.exception.provider)

    def test_model_load_failure_raises_typed_error(self):
        numpy = types.ModuleType("numpy")
        numpy.int16 = object()
        openwakeword = types.ModuleType("openwakeword")
        openwakeword.__path__ = []
        model_module = types.ModuleType("openwakeword.model")
        model_module.Model = mock.Mock(side_effect=OSError("Load model failed: file doesn't exist"))
        with mock.patch.dict(
            sys.modules,
            {"numpy": numpy, "openwakeword": openwakeword, "openwakeword.model": model_module},
        ):
            with self.assertRaises(ProviderUnavailable) as raised:
                OpenWakeWordDetector().detected(pcm(1, 2))
        self.assertEqual("wake_word", raised.exception.provider)

    def test_constructor_rejects_invalid_configuration(self):
        for threshold in (0, 1, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                OpenWakeWordDetector(threshold=threshold)
        with self.assertRaises(ValueError):
            OpenWakeWordDetector(min_frames=0)
        with self.assertRaises(ValueError):
            OpenWakeWordDetector(sample_rate=0)
        with self.assertRaises(ValueError):
            OpenWakeWordDetector(model_name="   ")

    def test_model_is_cached_across_frames(self):
        modules, model_class, model = fake_dependencies(lambda _frame: 0.9)
        with mock.patch.dict(sys.modules, modules):
            detector = OpenWakeWordDetector(min_frames=1)
            self.assertTrue(detector.detected(pcm(1200, -1200)))
            self.assertTrue(detector.detected(pcm(1400, -1400)))
        model_class.assert_called_once_with(
            wakeword_models=["hey_jarvis"], inference_framework="onnx"
        )
        self.assertEqual(2, model.predict.call_count)


class OpenWakeWordAvailabilityTests(unittest.TestCase):
    def test_reports_false_when_optional_dependencies_are_absent(self):
        with mock.patch.dict(sys.modules, {"openwakeword": None, "numpy": None}):
            self.assertFalse(openwakeword_available())

    def test_reports_true_when_optional_dependencies_are_present(self):
        modules, _, _ = fake_dependencies(lambda _frame: 0.0)
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(openwakeword_available())


class ScriptedWakeDetectorTests(unittest.TestCase):
    def test_returns_scripted_sequence_then_false(self):
        detector = ScriptedWakeDetector([True, False, True])
        self.assertTrue(detector.detected(b"first"))
        self.assertFalse(detector.detected(b"second"))
        self.assertTrue(detector.detected(b"third"))
        self.assertFalse(detector.detected(b"exhausted"))


if __name__ == "__main__":
    unittest.main()
