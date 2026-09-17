"""Contract tests for the unified capture backend selection in ``voice.audio_utils``.

Slice S01 goal: unify Windows capture backend selection around WASAPI
(``sounddevice``) with an explicit PyAudio fallback, a single capture descriptor
shared by microphone health checks and recording, and monotonic timing hooks.

The heavy audio dependencies (``numpy``, ``pyaudio``, ``sounddevice``,
``soundfile``, ``webrtcvad``) are absent on CI hosts, so ``voice.audio_utils``
is imported once with lightweight stubs, which are removed immediately after
import. Each test then controls backend availability with
``mock.patch.dict(sys.modules, ...)`` — the same pattern used by
``tests/test_audio_renderer.py``.

The backend resolver must lazy-import ``sounddevice``/``pyaudio`` inside its
resolution path (as ``MicCapture`` and ``make_sounddevice_render`` already do),
so the fakes injected here are exactly what it observes.

These tests are written "tests first": they encode the capture backend contract
and are RED until the S01 implementation lands in ``voice.audio_utils.py``.
"""

import sys
import types
import unittest
from unittest import mock

# Stub the heavy deps just long enough to import the module, then remove the
# stubs so the rest of the environment (and the lazy backend resolver) sees the
# real, dependency-free world. ``mock.patch.dict`` is deliberately NOT used here
# because it snapshots and restores the *entire* ``sys.modules`` on exit, which
# would also evict the freshly imported ``voice.audio_utils`` module.
_STUBS = ("numpy", "pyaudio", "sounddevice", "soundfile", "webrtcvad")
_added = [name for name in _STUBS if name not in sys.modules]
for _name in _added:
    sys.modules[_name] = types.ModuleType(_name)
try:
    import voice.audio_utils as audio_utils
finally:
    for _name in _added:
        sys.modules.pop(_name, None)


def make_sounddevice(hostapis):
    """Return a ``sounddevice`` stand-in exposing the WASAPI hostapi probe."""
    return types.SimpleNamespace(query_hostapis=lambda: hostapis)


class BackendEnumTests(unittest.TestCase):
    def test_wasapi_and_pyaudio_are_the_only_backends(self):
        self.assertEqual("wasapi", audio_utils.Backend.WASAPI.value)
        self.assertEqual("pyaudio", audio_utils.Backend.PYAUDIO.value)


class CaptureBackendDescriptorTests(unittest.TestCase):
    def test_descriptor_carries_backend_device_rate_channels(self):
        backend = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.WASAPI,
            device_index=2,
            sample_rate=16000,
            channels=1,
        )
        self.assertIs(audio_utils.Backend.WASAPI, backend.backend)
        self.assertEqual(2, backend.device_index)
        self.assertEqual(16000, backend.sample_rate)
        self.assertEqual(1, backend.channels)
        self.assertIsNone(backend.device_name)
        self.assertIsNone(backend.fallback_reason)

    def test_descriptor_is_immutable(self):
        backend = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.WASAPI,
            device_index=2,
            sample_rate=16000,
            channels=1,
        )
        with self.assertRaises(AttributeError):
            backend.device_index = 9


class ResolveCaptureBackendTests(unittest.TestCase):
    def test_prefers_wasapi_when_hostapi_reports_wasapi(self):
        fake_sd = make_sounddevice(
            [
                {"name": "MME"},
                {"name": "Windows WASAPI", "default_input_device": 2},
            ]
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            resolved = audio_utils.resolve_capture_backend(sample_rate=16000, channels=1)

        self.assertIsInstance(resolved, audio_utils.CaptureBackend)
        self.assertIs(audio_utils.Backend.WASAPI, resolved.backend)
        self.assertEqual(2, resolved.device_index)
        self.assertEqual(16000, resolved.sample_rate)
        self.assertEqual(1, resolved.channels)
        self.assertIsNone(resolved.fallback_reason)

    def test_wasapi_honors_preferred_device_index(self):
        fake_sd = make_sounddevice(
            [{"name": "Windows WASAPI", "default_input_device": 2}]
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            resolved = audio_utils.resolve_capture_backend(
                preferred_index=7, sample_rate=48000, channels=2
            )

        self.assertIs(audio_utils.Backend.WASAPI, resolved.backend)
        self.assertEqual(7, resolved.device_index)
        self.assertEqual(48000, resolved.sample_rate)
        self.assertEqual(2, resolved.channels)

    def test_falls_back_to_pyaudio_when_sounddevice_missing(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            resolved = audio_utils.resolve_capture_backend()

        self.assertIsInstance(resolved, audio_utils.CaptureBackend)
        self.assertIs(audio_utils.Backend.PYAUDIO, resolved.backend)
        self.assertTrue(resolved.fallback_reason)
        self.assertIn("wasapi", resolved.fallback_reason.lower())

    def test_falls_back_to_pyaudio_when_no_wasapi_hostapi(self):
        fake_sd = make_sounddevice([{"name": "ALSA"}, {"name": "PipeWire"}])
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            resolved = audio_utils.resolve_capture_backend()

        self.assertIsInstance(resolved, audio_utils.CaptureBackend)
        self.assertIs(audio_utils.Backend.PYAUDIO, resolved.backend)
        self.assertTrue(resolved.fallback_reason)

    def test_resolution_is_deterministic_for_shared_capture_selection(self):
        fake_sd = make_sounddevice(
            [{"name": "Windows WASAPI", "default_input_device": 3}]
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            first = audio_utils.resolve_capture_backend(
                preferred_index=3, sample_rate=48000, channels=2
            )
            second = audio_utils.resolve_capture_backend(
                preferred_index=3, sample_rate=48000, channels=2
            )

        self.assertEqual(first, second)
        self.assertEqual(3, first.device_index)
        self.assertEqual(48000, first.sample_rate)
        self.assertEqual(2, first.channels)


class MonotonicTimerTests(unittest.TestCase):
    def test_elapsed_is_non_negative_float(self):
        timer = audio_utils.MonotonicTimer()
        elapsed = timer.elapsed()
        self.assertIsInstance(elapsed, float)
        self.assertGreaterEqual(elapsed, 0.0)

    def test_elapsed_uses_monotonic_clock(self):
        # ``voice.audio_utils`` imports ``time`` at module top, so its
        # ``time.monotonic`` is the stdlib ``time.monotonic``.
        clock = iter([10.0, 10.25, 10.5])
        with mock.patch("time.monotonic", side_effect=lambda: next(clock)):
            timer = audio_utils.MonotonicTimer()
            first = timer.elapsed()
            second = timer.elapsed()

        self.assertAlmostEqual(0.25, first, places=6)
        self.assertAlmostEqual(0.5, second, places=6)
        self.assertGreaterEqual(second, first)

    def test_reset_rebaselines_the_clock(self):
        clock = iter([0.0, 1.0, 1.0, 1.5])
        with mock.patch("time.monotonic", side_effect=lambda: next(clock)):
            timer = audio_utils.MonotonicTimer()
            self.assertAlmostEqual(1.0, timer.elapsed(), places=6)
            timer.reset()
            self.assertAlmostEqual(0.5, timer.elapsed(), places=6)


if __name__ == "__main__":
    unittest.main()
