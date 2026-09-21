import subprocess
import unittest
from unittest.mock import call, patch

from legacy.voice.directshow_audio import (
    DirectShowCaptureError,
    capture_first_available,
    capture_pcm16,
    choose_audio_device,
    list_audio_devices,
    parse_audio_devices,
)


class DirectShowAudioTests(unittest.TestCase):
    def test_parse_audio_devices_ignores_video_and_alternative_names(self):
        output = '''
[dshow @ 000001] DirectShow video devices
[dshow @ 000001]  "USB Camera"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Headset Microphone (G435 Bluetooth Gaming Headset)"
[dshow @ 000001]     Alternative name "@device_cm_{abc}"
[dshow @ 000001]  "Microphone (Realtek(R) Audio)"
'''

        self.assertEqual(
            [
                "Headset Microphone (G435 Bluetooth Gaming Headset)",
                "Microphone (Realtek(R) Audio)",
            ],
            parse_audio_devices(output),
        )

    def test_choose_audio_device_prefers_the_portaudio_endpoint_name(self):
        devices = [
            "Microphone (Realtek(R) Audio)",
            "Headset Microphone (G435 Bluetooth Gaming Headset)",
        ]

        self.assertEqual(
            "Headset Microphone (G435 Bluetooth Gaming Headset)",
            choose_audio_device(devices, "G435 Bluetooth Gaming Headset"),
        )

    def test_choose_audio_device_keeps_first_when_no_name_matches(self):
        devices = ["Microphone (Realtek(R) Audio)", "Headset Microphone (G435)"]

        self.assertEqual("Microphone (Realtek(R) Audio)", choose_audio_device(devices, None))

    @patch("legacy.voice.directshow_audio.subprocess.run")
    def test_capture_pcm16_returns_ffmpeg_pcm_for_the_selected_device(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"\x01\x00\x02\x00", b"")

        audio = capture_pcm16("Headset Microphone (G435)", 0.8, ffmpeg="ffmpeg.exe")

        self.assertEqual(b"\x01\x00\x02\x00", audio)
        self.assertIn("audio=Headset Microphone (G435)", run.call_args.args[0])

    @patch("legacy.voice.directshow_audio.capture_pcm16")
    @patch("legacy.voice.directshow_audio.list_audio_devices")
    def test_capture_first_available_retries_the_other_directshow_input(self, list_devices, capture):
        list_devices.return_value = ["Microphone (Realtek)", "Headset Microphone (G435)"]
        capture.side_effect = [DirectShowCaptureError("busy"), b"\x01\x00"]

        device, audio = capture_first_available("G435 Bluetooth Gaming Headset", 0.8)

        self.assertEqual("Microphone (Realtek)", device)
        self.assertEqual(b"\x01\x00", audio)
        self.assertEqual(
            ["Headset Microphone (G435)", "Microphone (Realtek)"],
            [call.args[0] for call in capture.call_args_list],
        )

    @patch("legacy.voice.directshow_audio.subprocess.run")
    def test_list_audio_devices_reports_ffmpeg_failure_detail(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 1, "", "Unknown input format: 'dshow'"
        )

        with self.assertRaisesRegex(DirectShowCaptureError, "Unknown input format"):
            list_audio_devices(ffmpeg="ffmpeg.exe")
