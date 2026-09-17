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


class FormatCaptureBackendTests(unittest.TestCase):
    def test_renders_backend_device_rate_channels(self):
        capture = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.WASAPI,
            device_index=2,
            sample_rate=16000,
            channels=1,
        )
        rendered = audio_utils.format_capture_backend(capture)
        self.assertIn("backend=wasapi", rendered)
        self.assertIn("device_index=2", rendered)
        self.assertIn("sample_rate=16000", rendered)
        self.assertIn("channels=1", rendered)
        self.assertNotIn("fallback_reason", rendered)

    def test_includes_fallback_reason_when_present(self):
        capture = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.PYAUDIO,
            device_index=None,
            sample_rate=16000,
            channels=1,
            fallback_reason="WASAPI hostapi not found",
        )
        rendered = audio_utils.format_capture_backend(capture)
        self.assertIn("backend=pyaudio", rendered)
        self.assertIn("fallback_reason='WASAPI hostapi not found'", rendered)


class NormalizeBackendTests(unittest.TestCase):
    def test_capture_backend_is_passed_through(self):
        capture = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.WASAPI,
            device_index=4,
            sample_rate=48000,
            channels=2,
        )
        self.assertIs(capture, audio_utils._normalize_backend(capture, 9, 16000, 1))

    def test_backend_enum_builds_descriptor_with_given_device(self):
        capture = audio_utils._normalize_backend(
            audio_utils.Backend.WASAPI, 6, 32000, 1
        )
        self.assertIs(audio_utils.Backend.WASAPI, capture.backend)
        self.assertEqual(6, capture.device_index)
        self.assertEqual(32000, capture.sample_rate)
        self.assertEqual(1, capture.channels)

    def test_none_auto_resolves(self):
        fake_sd = make_sounddevice(
            [{"name": "Windows WASAPI", "default_input_device": 7}]
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            capture = audio_utils._normalize_backend(None, 7, 16000, 1)

        self.assertIs(audio_utils.Backend.WASAPI, capture.backend)
        self.assertEqual(7, capture.device_index)


class _FakeSoundDeviceStream:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def read(self, frames):
        return (b"", False)


class OpenInputStreamDispatchTests(unittest.TestCase):
    def test_wasapi_backend_opens_sounddevice_stream(self):
        fake_stream = _FakeSoundDeviceStream()
        created = {}

        def fake_input_stream(**kwargs):
            created.update(kwargs)
            return fake_stream

        fake_sd = types.SimpleNamespace(InputStream=fake_input_stream)
        capture = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.WASAPI,
            device_index=3,
            sample_rate=16000,
            channels=1,
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            handle = audio_utils._open_input_stream(capture, frames_per_buffer=480)

        self.assertTrue(fake_stream.started)
        self.assertEqual(16000, created["samplerate"])
        self.assertEqual(1, created["channels"])
        self.assertEqual("int16", created["dtype"])
        self.assertEqual(3, created["device"])

        handle.close()
        self.assertTrue(fake_stream.stopped)
        self.assertTrue(fake_stream.closed)

    def test_pyaudio_backend_opens_pyaudio_stream(self):
        created = {}
        opened = {}

        class _FakePyAudioStream:
            def __init__(self):
                self.stopped = False
                self.closed = False

            def is_active(self):
                return True

            def stop_stream(self):
                self.stopped = True

            def close(self):
                self.closed = True

            def read(self, frames, exception_on_overflow=False):
                return b"\x00\x00"

        class _FakePyAudio:
            def __init__(self):
                self.terminated = False
                opened["pa"] = self

            def open(self, **kwargs):
                created.update(kwargs)
                stream = _FakePyAudioStream()
                opened["stream"] = stream
                return stream

            def terminate(self):
                self.terminated = True

        fake_pa = types.SimpleNamespace(PyAudio=_FakePyAudio, paInt16="int16")
        capture = audio_utils.CaptureBackend(
            backend=audio_utils.Backend.PYAUDIO,
            device_index=5,
            sample_rate=16000,
            channels=1,
        )
        with mock.patch.dict(sys.modules, {"pyaudio": fake_pa}):
            handle = audio_utils._open_input_stream(capture, frames_per_buffer=512)

        self.assertEqual(5, created["input_device_index"])
        self.assertEqual(16000, created["rate"])
        self.assertEqual("int16", created["format"])

        handle.close()
        self.assertTrue(opened["stream"].stopped)
        self.assertTrue(opened["stream"].closed)
        self.assertTrue(opened["pa"].terminated)


if __name__ == "__main__":
    unittest.main()
