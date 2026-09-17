"""Contract tests for the real audio playback renderer.

The ``sounddevice``/``numpy`` dependencies are absent on CI hosts, so these
tests fake them in ``sys.modules`` and drive the decode → device-write path
deterministically. No external audio dependency is imported at module load.
"""

import io
import math
import struct
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.output import (
    AudioOutputQueue,
    _silent_render,
    decode_wav,
    make_sounddevice_render,
)
from jarvis.application.runtime import (
    _audio_output_available,
    _availability_report,
    build_runtime,
)
from jarvis.config import (
    MemorySettings,
    ProviderSettings,
    RuntimeConfig,
    RuntimeSettings,
    SecuritySettings,
)
from jarvis.core.contracts import HealthStatus
from jarvis.core.errors import ProviderUnavailable
from jarvis.core.turn import TurnContext

RATE = 22050


class _FakePortAudioError(Exception):
    """Stand-in for ``sounddevice.PortAudioError``."""


class _PortAudioMissingFinder:
    """Make ``import sounddevice`` raise OSError, as it does without PortAudio."""

    def find_spec(self, name, path=None, target=None):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return None


def make_sine_wav(
    seconds: float = 1.0, rate: int = RATE, frequency: float = 440.0
) -> tuple[bytes, int]:
    """Return a 1-channel int16 WAV blob of a sine wave plus its frame count."""
    nframes = int(rate * seconds)
    frames = bytearray()
    for i in range(nframes):
        sample = int(32000 * math.sin(2 * math.pi * frequency * i / rate))
        frames += struct.pack("<h", sample)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return buffer.getvalue(), nframes


def make_fake_sounddevice(recorder: dict) -> types.SimpleNamespace:
    """A sounddevice stand-in whose ``play`` records its arguments."""

    def play(samples, samplerate=None, device=None, blocking=None):
        recorder["play_calls"] = recorder.get("play_calls", 0) + 1
        recorder["samplerate"] = samplerate
        recorder["device"] = device
        recorder["blocking"] = blocking

    return types.SimpleNamespace(play=play)


def make_fake_numpy(recorder: dict) -> types.SimpleNamespace:
    """A numpy stand-in whose ``frombuffer`` records the decoded sample count."""

    class Samples:
        def __init__(self, frames: bytes) -> None:
            self.frames = frames

        def reshape(self, *shape) -> "Samples":
            recorder["reshape"] = shape
            return self

    def frombuffer(frames, dtype=None):
        recorder["dtype"] = dtype
        recorder["frames_len"] = len(frames)
        recorder["sample_count"] = len(frames) // 2  # int16 = 2 bytes/sample
        return Samples(frames)

    return types.SimpleNamespace(int16="int16", frombuffer=frombuffer)


class DecodeWavTests(unittest.TestCase):
    def test_decode_wav_extracts_stream_metadata(self):
        blob, nframes = make_sine_wav()
        frames, rate, channels, sampwidth = decode_wav(blob)

        self.assertEqual(RATE, rate)
        self.assertEqual(1, channels)
        self.assertEqual(2, sampwidth)
        self.assertEqual(nframes * sampwidth * channels, len(frames))


class RendererGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_raises_provider_unavailable_when_sounddevice_missing(self):
        render = make_sounddevice_render()
        blob, _ = make_sine_wav()

        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            with self.assertRaises(ProviderUnavailable) as raised:
                await render("turn", blob)

        self.assertEqual("audio", raised.exception.provider)


class RendererFullPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_plays_wav_through_queue_with_selected_device(self):
        recorder: dict = {}
        fake_sd = make_fake_sounddevice(recorder)
        fake_np = make_fake_numpy(recorder)
        blob, nframes = make_sine_wav()

        queue = AudioOutputQueue(render=make_sounddevice_render(device=7))
        context = TurnContext.fresh("s03")

        async def audio():
            yield blob

        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}):
            await queue.play(audio(), context)

        state = queue.state()
        self.assertEqual(1, state["played"])
        self.assertEqual(0, state["render_errors"])
        self.assertEqual(0, state["render_timeouts"])
        self.assertEqual(1, recorder["play_calls"])
        self.assertEqual(RATE, recorder["samplerate"])
        self.assertEqual(7, recorder["device"])
        self.assertTrue(recorder["blocking"])
        self.assertEqual(nframes, recorder["sample_count"])
        self.assertEqual(nframes * 2, recorder["frames_len"])
        self.assertEqual((-1, 1), recorder["reshape"])


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_runtime_wires_sounddevice_renderer(self):
        recorder: dict = {}
        fake_sd = make_fake_sounddevice(recorder)
        fake_np = make_fake_numpy(recorder)

        with tempfile.TemporaryDirectory() as tmp:
            config = RuntimeConfig(
                runtime=RuntimeSettings(),
                providers=ProviderSettings(),
                memory=MemorySettings(db_path=str(Path(tmp) / "memory.db")),
                security=SecuritySettings(),
            )
            with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}):
                runtime = build_runtime(config, use_fakes=False)
                self.assertIsNot(runtime.components.audio_output._render, _silent_render)

                blob, _ = make_sine_wav()
                context = TurnContext.fresh("s03")

                async def audio():
                    yield blob

                await runtime.components.audio_output.play(audio(), context)

        state = runtime.components.audio_output.state()
        self.assertEqual(1, state["played"])
        self.assertEqual(0, state["render_errors"])
        self.assertEqual(1, recorder["play_calls"])


class PortAudioGuardTests(unittest.IsolatedAsyncioTestCase):
    """A sounddevice install without the PortAudio library raises OSError at import."""

    async def test_render_maps_portaudio_import_oserror_to_provider_unavailable(self):
        render = make_sounddevice_render()
        blob, _ = make_sine_wav()
        finder = _PortAudioMissingFinder()
        saved = sys.modules.pop("sounddevice", None)

        with mock.patch.dict(sys.modules):
            sys.meta_path.insert(0, finder)
            try:
                with self.assertRaises(ProviderUnavailable) as raised:
                    await render("turn", blob)
            finally:
                sys.meta_path.remove(finder)
                if saved is not None:
                    sys.modules["sounddevice"] = saved

        self.assertEqual("audio", raised.exception.provider)


class DeviceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_portaudio_error_maps_to_provider_unavailable(self):
        render = make_sounddevice_render()

        def play(*_: object, **__: object) -> None:
            raise _FakePortAudioError("device unplugged")

        fake_sd = types.SimpleNamespace(play=play, PortAudioError=_FakePortAudioError)
        fake_np = make_fake_numpy({})
        blob, _ = make_sine_wav()

        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}):
            with self.assertRaises(ProviderUnavailable) as raised:
                await render("turn", blob)

        self.assertEqual("audio", raised.exception.provider)

    async def test_device_failure_is_contained_by_the_queue(self):
        def play(*_: object, **__: object) -> None:
            raise _FakePortAudioError("device unplugged")

        fake_sd = types.SimpleNamespace(play=play, PortAudioError=_FakePortAudioError)
        fake_np = make_fake_numpy({})
        blob, _ = make_sine_wav()
        queue = AudioOutputQueue(render=make_sounddevice_render())
        context = TurnContext.fresh("s03")

        async def audio():
            yield blob
            yield blob

        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}):
            await queue.play(audio(), context)

        state = queue.state()
        self.assertEqual(2, state["played"])
        self.assertEqual(2, state["render_errors"])
        self.assertEqual(0, state["pending"])


class AudioOutputProbeTests(unittest.TestCase):
    """The doctor probe must not report a static HEALTHY for audio output."""

    def test_probe_reports_unavailable_without_sounddevice(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            self.assertFalse(_audio_output_available())

    def test_probe_reports_unavailable_when_no_output_device(self):
        fake_sd = types.SimpleNamespace(
            PortAudioError=_FakePortAudioError,
            query_devices=mock.Mock(side_effect=_FakePortAudioError("no output device")),
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            self.assertFalse(_audio_output_available())

    def test_probe_reports_available_with_output_device(self):
        fake_sd = types.SimpleNamespace(
            PortAudioError=_FakePortAudioError,
            query_devices=lambda **_: [{"name": "Speakers"}],
        )
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
            self.assertTrue(_audio_output_available())

    def test_health_report_degrades_when_output_probe_fails(self):
        with mock.patch.dict(sys.modules, {"sounddevice": None}):
            report = _availability_report(
                "audio output",
                _audio_output_available(),
                detail="no PortAudio output device available",
            )

        self.assertEqual(HealthStatus.DEGRADED, report.status)
        self.assertEqual("no PortAudio output device available", report.detail)


if __name__ == "__main__":
    unittest.main()
