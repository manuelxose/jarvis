"""Contract tests for the real audio playback renderer.

The ``sounddevice``/``numpy`` dependencies are absent on CI hosts, so these
tests fake them in ``sys.modules`` and drive the decode → device-write path
deterministically. No external audio dependency is imported at module load.
"""

import asyncio
import io
import math
import struct
import sys
import tempfile
import time
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
from jarvis.core.turn import TurnCancelled, TurnContext

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


def make_fake_sounddevice(recorder: dict, write=None) -> types.SimpleNamespace:
    """A sounddevice stand-in whose ``OutputStream`` records opens and writes."""

    class OutputStream:
        def __init__(self, samplerate=None, channels=None, dtype=None, device=None):
            recorder["opens"] = recorder.get("opens", 0) + 1
            recorder["samplerate"] = samplerate
            recorder["device"] = device
            recorder["dtype_out"] = dtype
            self.active = False

        def start(self):
            self.active = True

        def write(self, samples):
            if write is not None:
                write(samples)
            recorder["play_calls"] = recorder.get("play_calls", 0) + 1
            recorder["written"] = recorder.get("written", 0) + len(samples)

        def abort(self):
            recorder["aborts"] = recorder.get("aborts", 0) + 1
            self.active = False

        def close(self):
            recorder["closes"] = recorder.get("closes", 0) + 1

    return types.SimpleNamespace(OutputStream=OutputStream, PortAudioError=_FakePortAudioError)


def make_fake_numpy(recorder: dict) -> types.SimpleNamespace:
    """A numpy stand-in whose ``frombuffer`` records the decoded sample count."""

    class Samples:
        def __init__(self, frames: bytes) -> None:
            self.frames = frames

        def reshape(self, *shape) -> "Samples":
            recorder["reshape"] = shape
            return self

        def __len__(self) -> int:
            return len(self.frames) // 2

        def __getitem__(self, index: slice) -> "Samples":
            return Samples(self.frames[index.start * 2:index.stop * 2])

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
        self.assertEqual(1, recorder["opens"])
        self.assertEqual(RATE, recorder["samplerate"])
        self.assertEqual(7, recorder["device"])
        self.assertEqual(nframes, recorder["written"])
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
        self.assertEqual(1, recorder["opens"])


class PersistentStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_share_one_open_stream(self):
        recorder: dict = {}
        blob, nframes = make_sine_wav(0.2)
        queue = AudioOutputQueue(render=make_sounddevice_render())

        async def audio():
            for _ in range(3):
                yield blob

        with mock.patch.dict(sys.modules, {"sounddevice": make_fake_sounddevice(recorder), "numpy": make_fake_numpy({})}):
            await queue.play(audio(), TurnContext.fresh("s"))
        self.assertEqual(1, recorder["opens"])
        self.assertEqual(3 * nframes, recorder["written"])

    async def test_barge_in_cuts_the_chunk_on_the_device(self):
        recorder: dict = {}
        blob, nframes = make_sine_wav(2.0)
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_write(_samples):
            loop.call_soon_threadsafe(started.set)
            time.sleep(0.01)

        queue = AudioOutputQueue(render=make_sounddevice_render())
        context = TurnContext.fresh("s")

        async def audio():
            yield blob
            yield blob

        async def cancel_when_playing():
            await started.wait()
            context.cancellation.cancel()

        with mock.patch.dict(sys.modules, {"sounddevice": make_fake_sounddevice(recorder, slow_write), "numpy": make_fake_numpy({})}):
            canceller = asyncio.create_task(cancel_when_playing())
            with self.assertRaises(TurnCancelled):
                await queue.play(audio(), context)
            await canceller
            await asyncio.sleep(0.1)  # let the executor thread observe the abort
        self.assertGreaterEqual(recorder["aborts"], 1)
        self.assertLess(recorder["written"], nframes // 2)
        self.assertEqual(0, queue.state()["pending"])

    async def test_aborted_turn_chunk_is_never_rendered(self):
        recorder: dict = {}
        render = make_sounddevice_render()
        render.abort("old")
        blob, _ = make_sine_wav(0.1)
        with mock.patch.dict(sys.modules, {"sounddevice": make_fake_sounddevice(recorder), "numpy": make_fake_numpy({})}):
            await render("old", blob)
            self.assertNotIn("written", recorder)
            await render("new", blob)
        self.assertGreater(recorder["written"], 0)


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

        def unplugged(_samples) -> None:
            raise _FakePortAudioError("device unplugged")

        fake_sd = make_fake_sounddevice({}, unplugged)
        fake_np = make_fake_numpy({})
        blob, _ = make_sine_wav()

        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": fake_np}):
            with self.assertRaises(ProviderUnavailable) as raised:
                await render("turn", blob)

        self.assertEqual("audio", raised.exception.provider)

    async def test_device_failure_is_contained_by_the_queue(self):
        def unplugged(_samples) -> None:
            raise _FakePortAudioError("device unplugged")

        fake_sd = make_fake_sounddevice({}, unplugged)
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
