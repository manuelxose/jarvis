"""Selection must skip inputs that open but capture only silence."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class LiveMicrophoneSelectionTests(unittest.TestCase):
    def test_silent_default_does_not_hide_a_live_microphone(self):
        dependencies = {
            name: types.ModuleType(name)
            for name in ("numpy", "pyaudio", "sounddevice", "soundfile", "webrtcvad")
        }
        source = Path(__file__).parents[1] / "voice" / "audio_utils.py"
        spec = importlib.util.spec_from_file_location("audio_utils_selection_test", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)

        devices = [
            {"name": "Realtek HD Audio Mic input", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 16000},
            {"name": "Varios micrófonos 1", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 16000},
        ]
        hostapis = [{"name": "Windows WASAPI", "default_input_device": 0}]
        with (
            patch.object(module.sys, "platform", "win32"),
            patch.object(module.sd, "query_hostapis", return_value=hostapis, create=True),
            patch.object(module.sd, "query_devices", return_value=devices, create=True),
            patch.object(module, "_probe_native_rms", side_effect=[0.0, 12.0]) as probe,
        ):
            selected = module.resolve_capture_backend(verify=True)
            self.assertEqual(1, selected.device_index)
            self.assertEqual(2, probe.call_count)

            probe.reset_mock()
            self.assertEqual(1, module.resolve_capture_backend(preferred_index=1).device_index)
            probe.assert_not_called()
