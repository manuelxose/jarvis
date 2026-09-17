from pathlib import Path
import importlib.util
import unittest

from voice.input_device_selection import (
    DetectionStats,
    filter_input_candidates,
    native_chunk_size,
    resample_to_16khz,
    select_device,
)


class WakeWordDiagnosticTests(unittest.TestCase):
    def test_device_selection_skips_silent_open_device(self) -> None:
        rms_by_device = {17: 0.02, 20: 0.01, 45: 12.0, 1: 20.0}

        selected, rms = select_device(
            [17, 20, 45, 1],
            rms_by_device.get,
        )

        self.assertEqual(1, selected)
        self.assertEqual(20.0, rms)

    def test_device_selection_keeps_first_safe_candidate_when_probe_is_silent(self) -> None:
        selected, rms = select_device([6, 1], {6: 0.1, 1: 0.5}.get)

        self.assertEqual(6, selected)
        self.assertEqual(0.1, rms)

    def test_detection_stats_keeps_peak_and_success_after_multiple_detections(self) -> None:
        stats = DetectionStats()

        self.assertTrue(stats.observe(0.032, 0.03))
        self.assertTrue(stats.observe(0.060, 0.03))
        stats.observe(0.010, 0.03)

        self.assertEqual(2, stats.detections)
        self.assertEqual(0.060, stats.peak_score)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy dependency not installed")
    def test_native_44100_chunk_is_resampled_to_16khz(self) -> None:
        import numpy as np

        native = np.arange(native_chunk_size(44100), dtype=np.int16)

        converted = resample_to_16khz(native, 44100)

        self.assertEqual(1280, converted.size)
        self.assertEqual(np.int16, converted.dtype)

    def test_candidate_filter_rejects_stale_stereo_mix_index(self) -> None:
        devices = [
            {"index": 0, "name": "Asignador de sonido Microsoft - Input", "maxInputChannels": 2},
            {"index": 17, "name": "Mezcla est?reo (Realtek)", "maxInputChannels": 2},
            {"index": 1, "name": "Micrófonos Intel", "maxInputChannels": 4},
        ]

        self.assertEqual([1], filter_input_candidates(devices, [17, 1]))

    def test_native_chunk_size_keeps_80ms_model_frames(self) -> None:
        self.assertEqual(1280, native_chunk_size(16000))
        self.assertEqual(3528, native_chunk_size(44100))
        self.assertEqual(3840, native_chunk_size(48000))

    def test_diagnostic_uses_signal_aware_selection(self) -> None:
        source = (Path(__file__).parents[1] / "diagnostico_wakeword.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("select_device", source)
        self.assertIn("filter_input_candidates", source)
        self.assertIn("probe_input_rms", source)
        self.assertIn("probe_candidates", source)
        self.assertIn("capture_rms < 3.0", source)
        self.assertIn("peak_rms", source)
        self.assertIn("JARVIS_DIAG_DEVICE", source)
        self.assertIn("JARVIS_DIAG_THRESHOLD", source)
        self.assertIn("resample_to_16khz", source)
        self.assertIn("DetectionStats", source)
        self.assertNotIn("audioop", source)
        self.assertNotIn("voice.audio_utils", source)
        self.assertNotIn("selected_device = candidate", source)


if __name__ == "__main__":
    unittest.main()
