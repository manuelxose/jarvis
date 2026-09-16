import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.activation import ActivationManager, ActivationMode, ConversationWindow
from jarvis.adapters.audio.vad import EnergyVAD, rms_int16


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

    def test_continuous_mode_never_requires_wake_word(self):
        manager = ActivationManager(mode="continuous")
        self.assertFalse(manager.wake_word_required())

    def test_matches_wake_word(self):
        manager = ActivationManager(wake_word="jarvis")
        self.assertTrue(manager.matches_wake_word("Jarvis, abre spotify"))
        self.assertFalse(manager.matches_wake_word("hola"))


if __name__ == "__main__":
    unittest.main()
