import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from jarvis import voice_profile as vp


def _wav(path: Path, seconds: float, *, rate: int = 24000, amp: float = 0.3, channels: int = 1) -> Path:
    frames = int(seconds * rate)
    samples = [max(-32768, min(32767, int(amp * 32767 * math.sin(2 * math.pi * 220 * i / rate)))) for i in range(frames)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", s) * channels for s in samples))
    return path


class ValidateReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_clean_sample_passes(self):
        report = vp.validate_reference(_wav(self.tmp / "a.wav", 8))
        self.assertEqual(report.errors, [])
        self.assertAlmostEqual(report.duration_s, 8, places=1)

    def test_too_short_too_quiet_clipped_low_rate(self):
        self.assertTrue(any("short" in e for e in vp.validate_reference(_wav(self.tmp / "s.wav", 1)).errors))
        self.assertTrue(any("quiet" in e for e in vp.validate_reference(_wav(self.tmp / "q.wav", 8, amp=0.001)).errors))
        self.assertTrue(any("clip" in e for e in vp.validate_reference(_wav(self.tmp / "c.wav", 8, amp=1.5)).errors))
        self.assertTrue(any("rate" in e for e in vp.validate_reference(_wav(self.tmp / "r.wav", 8, rate=8000)).errors))

    def test_window_selects_segment(self):
        report = vp.validate_reference(_wav(self.tmp / "long.wav", 40), start_s=5, duration_s=10)
        self.assertEqual(report.errors, [])
        self.assertAlmostEqual(report.duration_s, 10, places=1)
        self.assertTrue(any("long" in e for e in vp.validate_reference(self.tmp / "long.wav").errors))


class EnrollTest(unittest.TestCase):
    def test_enroll_status_delete_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        src = _wav(tmp / "me.wav", 30, rate=48000, channels=2)
        profile = tmp / "profile"
        meta = vp.enroll(src, profile, ref_text="hola que tal", start_s=2, duration_s=9, icl=True)
        self.assertEqual(meta["mode"], "icl")
        with self.assertRaises(ValueError):
            vp.enroll(src, tmp / "other", start_s=2, duration_s=9, icl=True)  # ICL needs the transcript
        with wave.open(str(profile / "reference.wav")) as w:
            self.assertEqual((w.getnchannels(), round(w.getnframes() / w.getframerate())), (1, 9))
        self.assertEqual(json.loads((profile / "profile.json").read_text())["ref_text"], "hola que tal")
        self.assertTrue(vp.status(profile)["enrolled"])
        # Re-enrolling replaces the reference and drops the stale cached prompt.
        (profile / "prompt.pt").write_bytes(b"stale")
        self.assertEqual(vp.enroll(src, profile, start_s=0, duration_s=6)["mode"], "xvec")
        self.assertFalse((profile / "prompt.pt").exists())
        vp.delete(profile)
        self.assertFalse(profile.exists())
        self.assertFalse(vp.status(profile)["enrolled"])

    def test_invalid_sample_is_rejected_without_touching_profile(self):
        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            vp.enroll(_wav(tmp / "bad.wav", 1), tmp / "profile")
        self.assertFalse((tmp / "profile").exists())


if __name__ == "__main__":
    unittest.main()
