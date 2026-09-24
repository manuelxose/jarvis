"""Clap detector (two- and three-clap modes): synthetic positives and speech/music/noise negatives."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from jarvis.adapters.audio.claps import ClapDetector, ClapTuning, calibrate, load_calibration

RATE = 16000
RNG = np.random.default_rng(7)
THREE = ClapTuning(claps_required=3)


def silence(seconds, level=0.001):
    return (RNG.standard_normal(int(RATE * seconds)) * level).astype(np.float32)


def clap(amplitude=0.5, tau=0.012):
    t = np.arange(int(RATE * 0.15)) / RATE
    burst = RNG.standard_normal(t.size) * np.exp(-t / tau)
    burst = np.diff(burst, prepend=0.0)  # tilt the spectrum up like a real clap
    return (amplitude * burst / np.abs(burst).max()).astype(np.float32)


def claps(times, total, amplitude=0.5, floor=0.001):
    signal = silence(total, floor)
    for at in times:
        c = clap(amplitude)
        start = int(at * RATE)
        signal[start:start + c.size] += c[: signal.size - start]
    return signal


def speech_like(seconds):
    """Voiced syllables: 110-220 Hz harmonics with 4-6 Hz syllable envelope and plosives."""
    t = np.arange(int(RATE * seconds)) / RATE
    f0 = 140 + 30 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12))
    envelope = np.clip(np.sin(2 * np.pi * 4.5 * t), 0, None) ** 0.6
    signal = 0.25 * voiced * envelope
    for at in np.arange(0.2, seconds, 0.45):  # "p"/"t" bursts at syllable starts
        start = int(at * RATE)
        burst = RNG.standard_normal(160) * 0.15
        signal[start:start + 160] += burst[: signal.size - start]
    return signal.astype(np.float32) + silence(seconds)


def music_like(seconds, bpm=120):
    """Sustained chord plus a snare-like noise hit on every beat."""
    t = np.arange(int(RATE * seconds)) / RATE
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440)) * 0.05
    signal = chord.astype(np.float32)
    for at in np.arange(0.1, seconds, 60 / bpm):
        c = clap(0.4, tau=0.03)
        start = int(at * RATE)
        signal[start:start + c.size] += c[: signal.size - start]
    return signal


def run(detector, signal, chunk=320):
    gestures = []
    for start in range(0, signal.size, chunk):
        g = detector.feed(signal[start:start + chunk])
        if g is not None:
            gestures.append(g)
    return gestures


class ClapDetectorTests(unittest.TestCase):
    """Three-clap mode (claps_required=3)."""

    def test_three_claps_are_detected_with_low_latency(self):
        gestures = run(ClapDetector(THREE), claps([0.8, 1.2, 1.6], 3.0))
        self.assertEqual(len(gestures), 1)
        self.assertGreaterEqual(gestures[0].confidence, 0.55)
        self.assertLess(gestures[0].latency_seconds, 0.6)
        self.assertEqual(len(gestures[0].claps), 3)

    def test_int16_bytes_input_is_supported(self):
        pcm = (claps([0.8, 1.2, 1.6], 3.0) * 32767).astype("<i2").tobytes()
        detector = ClapDetector(THREE)
        found = [g for s in range(0, len(pcm), 640) if (g := detector.feed(pcm[s:s + 640]))]
        self.assertEqual(len(found), 1)

    def test_one_or_two_claps_do_not_activate(self):
        self.assertEqual(run(ClapDetector(THREE), claps([0.8], 3.0)), [])
        self.assertEqual(run(ClapDetector(THREE), claps([0.8, 1.2], 3.0)), [])

    def test_four_claps_are_a_rhythm_not_the_gesture(self):
        self.assertEqual(run(ClapDetector(THREE), claps([0.8, 1.2, 1.6, 2.0], 4.0)), [])

    def test_claps_too_slow_are_rejected(self):
        self.assertEqual(run(ClapDetector(THREE), claps([0.5, 1.6, 2.7], 4.0)), [])

    def test_irregular_spacing_is_rejected(self):
        self.assertEqual(run(ClapDetector(THREE), claps([0.5, 0.65, 1.3], 3.0)), [])

    def test_speech_never_activates(self):
        self.assertEqual(run(ClapDetector(THREE), speech_like(20.0)), [])

    def test_music_with_drums_never_activates(self):
        self.assertEqual(run(ClapDetector(THREE), music_like(20.0)), [])
        self.assertEqual(run(ClapDetector(THREE), music_like(20.0, bpm=170)), [])

    def test_steady_tone_and_quiet_room_never_activate(self):
        t = np.arange(RATE * 5) / RATE
        tone = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        self.assertEqual(run(ClapDetector(THREE), tone), [])
        self.assertEqual(run(ClapDetector(THREE), silence(10, 0.003)), [])

    def test_quiet_taps_below_min_peak_are_ignored(self):
        self.assertEqual(run(ClapDetector(THREE), claps([0.8, 1.2, 1.6], 3.0, amplitude=0.01)), [])

    def test_low_thump_before_claps_does_not_veto(self):
        signal = claps([1.0, 1.4, 1.8], 3.0)
        t = np.arange(int(RATE * 0.06)) / RATE
        thump = (0.3 * np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.015)).astype(np.float32)
        start = int(0.6 * RATE)
        signal[start:start + thump.size] += thump
        self.assertEqual(len(run(ClapDetector(THREE), signal)), 1)

    def test_cooldown_blocks_immediate_retrigger(self):
        signal = claps([0.8, 1.2, 1.6, 2.6, 3.0, 3.4], 4.5)
        self.assertEqual(len(run(ClapDetector(THREE), signal)), 1)
        fast = ClapDetector(ClapTuning(claps_required=3, cooldown_seconds=0.3))
        self.assertEqual(len(run(fast, signal)), 2)

    def test_suspend_ignores_claps_until_resume(self):
        detector = ClapDetector(THREE)
        detector.suspend()
        self.assertEqual(run(detector, claps([0.8, 1.2, 1.6], 3.0)), [])
        detector.resume(cooldown=False)
        self.assertEqual(len(run(detector, claps([0.8, 1.2, 1.6], 3.0))), 1)

    def test_invalid_tuning_is_rejected(self):
        with self.assertRaises(ValueError):
            ClapTuning(sensitivity=2)
        with self.assertRaises(ValueError):
            ClapTuning(min_gap_seconds=1.0, max_gap_seconds=0.5)

    def test_calibration_separates_room_from_claps(self):
        result = calibrate(silence(3, 0.004), claps([0.8, 1.2, 1.6], 3.0, amplitude=0.2))
        self.assertTrue(result["ok"], result)
        self.assertLess(result["min_peak_dbfs"], result["softest_clap_dbfs"])
        self.assertGreater(result["min_peak_dbfs"], result["noise_p99_dbfs"])
        self.assertLessEqual(result["min_hf_ratio"], ClapTuning().min_hf_ratio)

    def test_calibration_file_overrides_tuning(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"min_peak_dbfs": -40.0, "sensitivity": 0.8}))
            tuning = load_calibration(ClapTuning(), path)
            self.assertEqual((tuning.min_peak_dbfs, tuning.sensitivity), (-40.0, 0.8))
            # An older file (midpoint rule) is re-derived with the stricter rule.
            path.write_text(json.dumps({"min_peak_dbfs": -37.1, "noise_p99_dbfs": -65.7, "softest_clap_dbfs": -8.4}))
            self.assertEqual(load_calibration(ClapTuning(), path).min_peak_dbfs, -20.4)
            self.assertEqual(load_calibration(ClapTuning(), Path(tmp) / "missing.json"), ClapTuning())


class TwoClapTests(unittest.TestCase):
    """Default mode: exactly two claps; the first one only starts a candidate."""

    def detector(self, **kwargs):
        detector = ClapDetector(ClapTuning(**kwargs))
        self.events = []
        detector.on_candidate = lambda clap: self.events.append(("candidate", round(clap.time, 1)))
        detector.on_candidate_expired = lambda reason: self.events.append(("expired", reason))
        return detector

    def test_two_claps_confirm_right_after_the_second(self):
        gestures = run(self.detector(), claps([1.0, 1.4], 3.0))
        self.assertEqual(len(gestures), 1)
        self.assertEqual(len(gestures[0].claps), 2)
        self.assertLess(gestures[0].time - 1.4, 0.12)  # confirmed ~one decay after the 2nd clap
        self.assertEqual(self.events, [("candidate", 1.0)])

    def test_single_clap_never_activates_and_candidate_expires(self):
        self.assertEqual(run(self.detector(), claps([1.0], 3.0)), [])
        self.assertEqual(self.events, [("candidate", 1.0), ("expired", "timeout")])

    def test_third_clap_does_not_restart_activation(self):
        gestures = run(self.detector(), claps([1.0, 1.4, 1.8], 4.0))
        self.assertEqual(len(gestures), 1)  # 3rd clap lands in the cooldown
        self.assertEqual([e for e in self.events if e[0] == "candidate"], [("candidate", 1.0)])

    def test_claps_too_far_apart_do_not_activate(self):
        self.assertEqual(run(self.detector(), claps([1.0, 2.2], 4.0)), [])

    def test_speech_music_and_tones_never_activate(self):
        self.assertEqual(run(self.detector(), speech_like(20.0)), [])
        self.assertEqual(run(self.detector(), music_like(20.0)), [])
        self.assertEqual(run(self.detector(), music_like(20.0, bpm=170)), [])
        t = np.arange(RATE * 5) / RATE
        self.assertEqual(run(self.detector(), (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)), [])

    def test_music_hits_do_not_even_start_speculation(self):
        run(self.detector(), music_like(10.0))
        self.assertLessEqual(len([e for e in self.events if e[0] == "candidate"]), 1)

    def test_rhythm_guard_rejects_three_quick_claps_when_configured(self):
        self.assertEqual(run(self.detector(confirm_quiet_seconds=0.5), claps([1.0, 1.3, 1.6], 3.0)), [])
        self.assertEqual(len(run(self.detector(confirm_quiet_seconds=0.5), claps([1.0, 1.4], 3.0))), 1)

    def test_cooldown_then_new_activation(self):
        signal = claps([1.0, 1.4, 7.0, 7.4], 8.5)
        self.assertEqual(len(run(self.detector(cooldown_seconds=5.0), signal)), 2)

    def test_dull_mic_clap_is_not_penalised_twice(self):
        """Real clap on the owner's mic: loud, 60 ms room tail, HF share right at the gate."""

        detector = ClapDetector(ClapTuning(min_hf_ratio=0.2))
        accepted = []
        detector._claps_accepted = accepted
        event = {"start": 1.0, "peak": 0.5, "floor": 0.001, "rise": 104.77, "hf": 0.201}
        detector._finish_event(event, 0.06)
        self.assertEqual(len(detector.accepted), 1)  # was rejected at confidence 0.46

    def test_softer_second_clap_still_confirms_but_cannot_start_alone(self):
        signal = claps([1.0], 3.0, amplitude=0.5)  # first clap about -16 dBFS
        soft = clap(0.2)  # second clap about -24 dBFS, under the -20 dBFS gate
        start = int(1.4 * RATE)
        signal[start:start + soft.size] += soft
        self.assertEqual(len(run(self.detector(min_peak_dbfs=-20.0), signal)), 1)
        self.assertEqual(run(self.detector(min_peak_dbfs=-20.0), claps([1.0, 1.4], 3.0, amplitude=0.2)), [])

    def test_claps_required_is_validated(self):
        with self.assertRaises(ValueError):
            ClapTuning(claps_required=1)


if __name__ == "__main__":
    unittest.main()
