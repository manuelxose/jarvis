from pathlib import Path
import importlib.util
import unittest

from legacy.voice.input_device_selection import (
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

    def test_shared_input_selector_rejects_primary_capture_controller(self) -> None:
        source = (Path(__file__).parents[1] / "legacy" / "voice" / "audio_utils.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'if any(hint in name for hint in _BAD_INPUT_HINTS):\n'
            '        return float("-inf")',
            source,
        )
        self.assertIn('if score == float("-inf"):\n            continue', source)

    def test_stt_capture_uses_the_device_native_rate(self) -> None:
        source = (Path(__file__).parents[1] / "legacy" / "voice" / "audio_utils.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('capture_rate = int(device_info.get("defaultSampleRate", sample_rate))', source)
        self.assertIn("rate=capture_rate", source)
        self.assertIn("_resample_pcm16", source)
        self.assertIn("_wasapi_default_input", source)
        self.assertIn("sd.InputStream", source)

    def test_raw_audio_diagnostic_bypasses_stt_and_resampling(self) -> None:
        diagnostic = Path(__file__).parents[1] / "legacy" / "diagnostico_audio_raw.py"
        self.assertTrue(diagnostic.exists(), "falta el diagnóstico de audio crudo")
        source = diagnostic.read_text(
            encoding="utf-8"
        )

        self.assertIn("default_sample_rate", source)
        self.assertIn("diagnostico_audio_raw.wav", source)
        self.assertIn("time.monotonic", source)
        self.assertIn("JARVIS_DIAG_CHANNELS", source)
        self.assertIn('== "max"', source)
        self.assertNotIn("STTService", source)
        self.assertNotIn("resample", source)

    def test_wasapi_audio_diagnostic_uses_the_wasapi_default_input(self) -> None:
        diagnostic = Path(__file__).parents[1] / "legacy" / "diagnostico_audio_wasapi.py"
        self.assertTrue(diagnostic.exists(), "falta el diagnóstico WASAPI")
        source = diagnostic.read_text(encoding="utf-8")

        self.assertIn("Windows WASAPI", source)
        self.assertIn("default_input_device", source)
        self.assertIn("max_input_channels", source)
        self.assertIn("_capture_callback_audio", source)
        self.assertIn("pyaudio", source)
        self.assertIn("input_device_index=device_index", source)
        self.assertIn("diagnostico_audio_wasapi.wav", source)

    def test_native_chunk_size_keeps_80ms_model_frames(self) -> None:
        self.assertEqual(1280, native_chunk_size(16000))
        self.assertEqual(3528, native_chunk_size(44100))
        self.assertEqual(3840, native_chunk_size(48000))

    def test_diagnostic_uses_signal_aware_selection(self) -> None:
        source = (Path(__file__).parents[1] / "legacy" / "diagnostico_wakeword.py").read_text(
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
        self.assertIn("peak={detection_stats.peak_score:.3f}", source)
        self.assertIn("PREFERRED_DEVICES = []", source)
        self.assertNotIn("audioop", source)
        self.assertNotIn("voice.audio_utils", source)
        self.assertNotIn("selected_device = candidate", source)
        self.assertNotIn("peak={peak_score:.3f}", source)

    def test_speech_diagnostic_transcribes_a_normal_phrase(self) -> None:
        diagnostic = Path(__file__).parents[1] / "legacy" / "diagnostico_stt.py"
        self.assertTrue(diagnostic.exists(), "falta el diagnóstico de transcripción")
        source = diagnostic.read_text(
            encoding="utf-8"
        )

        self.assertIn("resolve_input_device", source)
        self.assertIn("STTService", source)
        self.assertIn("capture_from_mic", source)
        self.assertIn("transcribe_audio(audio_data)", source)
        self.assertIn("TRANSCRIPCION", source)
        self.assertIn("logging.basicConfig", source)
        self.assertIn("JARVIS_DIAG_STT_DISABLE_VAD", source)
        self.assertIn("diagnostico_stt.wav", source)
        self.assertIn("wave.open", source)
        stt_source = (Path(__file__).parents[1] / "legacy" / "voice" / "stt.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("rms=%.0f", stt_source)

    def test_whisper_vad_is_disabled_after_capture_vad(self) -> None:
        config = (Path(__file__).parents[1] / "legacy" / "config.yaml").read_text(encoding="utf-8")

        self.assertIn('model: "small"', config)
        self.assertIn("whisper_vad_filter: false", config)
        self.assertIn("auto_select_input: true", config)


if __name__ == "__main__":
    unittest.main()
