"""scripts/clap_eval.py: WAV fallback, config-matched tuning, synthetic corpus, error paths."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np

_spec = importlib.util.spec_from_file_location("clap_eval", ROOT / "scripts" / "clap_eval.py")
clap_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clap_eval)

try:
    import soundfile  # noqa: F401

    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False


def write_wav(path, audio, rate=16000, channels=1):
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    if channels > 1:
        pcm = np.repeat(pcm[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = clap_eval.main(argv)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, lines


class ClapEvalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        # clap_tuning reads the saved calibration: point it at an empty temp dir.
        patcher = mock.patch("jarvis.apps.daemon._data_dir", return_value=self.dir / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        rng = np.random.default_rng(3)
        self.two = self.dir / "two_claps.wav"
        write_wav(self.two, clap_eval.claps(rng, [0.8, 1.2], 3.0))
        self.speech = self.dir / "speech.wav"
        write_wav(self.speech, clap_eval.speech_like(rng, 8.0))

    def test_synthetic_corpus_has_no_false_activations_and_detects_positives(self):
        code, lines = run_main(["--synthetic"])
        self.assertEqual(code, 0)
        summary = lines[-1]
        self.assertTrue(summary["summary"])
        self.assertEqual((summary["false_activations"], summary["missed_positives"]), (0, 0))
        cases = {(r["file"], r["claps_required"]): r for r in lines[:-1]}
        for name in ("speech_like", "music_like_120bpm", "music_like_90bpm", "tts_like", "typing_knocks"):
            for mode in (2, 3):
                self.assertEqual(cases[(name, mode)]["activations"], 0, (name, mode))
        for key in (("two_claps", 2), ("three_claps", 3)):
            self.assertEqual(cases[key]["activations"], 1)
            self.assertLess(cases[key]["latency_ms"][0], 600)

    def test_synthetic_run_fails_when_detector_is_made_trigger_happy(self):
        with mock.patch.object(clap_eval, "ClapDetector") as detector:
            detector.return_value.feed.side_effect = lambda block: None
            detector.return_value.accepted = detector.return_value.rejected = []
            code, lines = run_main(["--synthetic"])
        self.assertEqual(code, 1)
        self.assertEqual(lines[-1]["missed_positives"], 2)

    def test_wav_files_without_soundfile_speech_negative_and_clap_positive(self):
        tuning = clap_eval.ClapTuning()
        speech = clap_eval.evaluate(str(self.speech), tuning)
        self.assertEqual(speech["activations"], 0)
        positive = clap_eval.evaluate(str(self.two), tuning)
        self.assertEqual(positive["activations"], 1)
        self.assertLess(positive["latency_ms"][0], 300)
        code, lines = run_main([str(self.speech)])
        self.assertEqual((code, lines[-1]["ok"]), (0, True))
        code, _ = run_main([str(self.two)])
        self.assertEqual(code, 1)

    def test_stereo_and_other_sample_rates_are_downmixed_and_resampled(self):
        rng = np.random.default_rng(5)
        path = self.dir / "stereo48k.wav"
        write_wav(path, clap_eval.claps(rng, [0.8, 1.2], 3.0), rate=16000, channels=2)
        self.assertEqual(clap_eval.evaluate(str(path), clap_eval.ClapTuning())["activations"], 1)
        write_wav(path, np.repeat(clap_eval.claps(rng, [0.8, 1.2], 3.0), 3), rate=48000)
        audio = clap_eval.load_audio_mono(str(path), clap_eval.RATE)
        self.assertAlmostEqual(audio.size / clap_eval.RATE, 3.0, delta=0.05)

    def test_empty_and_very_short_audio_have_no_activations(self):
        for size in (0, 50):
            path = self.dir / f"short{size}.wav"
            write_wav(path, np.zeros(size, dtype=np.float32), rate=8000)
            self.assertEqual(clap_eval.evaluate(str(path), clap_eval.ClapTuning())["activations"], 0)

    def test_config_claps_required_is_honoured(self):
        config = self.dir / "config.json"
        config.write_text(json.dumps({"runtime": {}, "claps": {"claps_required": 3}}), encoding="utf-8")
        code, lines = run_main([str(self.two), "--config", str(config)])
        self.assertEqual(code, 0)
        self.assertEqual(lines[0]["activations"], 0)
        self.assertEqual(lines[0]["claps_required"], 3)
        # explicit flag overrides the config
        code, lines = run_main([str(self.two), "--config", str(config), "--claps-required", "2"])
        self.assertEqual((code, lines[0]["activations"]), (1, 1))

    def test_saved_calibration_is_applied_like_the_daemon(self):
        (self.dir / "data").mkdir()
        (self.dir / "data" / "clap_calibration.json").write_text(json.dumps({"min_peak_dbfs": -3.0}), encoding="utf-8")
        config = self.dir / "config.json"
        config.write_text(json.dumps({"runtime": {}}), encoding="utf-8")
        code, lines = run_main([str(self.two), "--config", str(config)])
        self.assertEqual((code, lines[0]["activations"]), (0, 0))  # claps peak below the calibrated floor

    def test_invalid_config_is_a_clean_error(self):
        code, lines = run_main([str(self.two), "--config", str(self.dir / "missing.json")])
        self.assertEqual(code, 2)
        self.assertIn("invalid configuration", lines[0]["error"])

    def test_unreadable_file_reports_error_line_and_fails(self):
        garbage = self.dir / "garbage.wav"
        garbage.write_bytes(b"not a wav")
        code, lines = run_main([str(self.dir / "absent.wav"), str(garbage), str(self.speech)])
        self.assertEqual(code, 1)
        self.assertIn("error", lines[0])
        self.assertIn("error", lines[1])
        self.assertEqual(lines[2]["activations"], 0)  # later files are still evaluated
        self.assertEqual(lines[-1]["errors"], 2)

    @unittest.skipIf(HAS_SOUNDFILE, "soundfile installed: non-WAV formats are readable")
    def test_non_wav_without_soundfile_explains_itself(self):
        flac = self.dir / "recording.flac"
        flac.write_bytes(b"fLaC")
        code, lines = run_main([str(flac)])
        self.assertEqual(code, 1)
        self.assertIn("soundfile", lines[0]["error"])

    def test_no_arguments_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            clap_eval.main([])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
