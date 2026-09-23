"""Startup sequence: transitions, idempotency, cancellation, ducking, degraded welcome."""

import asyncio
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from jarvis.adapters.audio.mixer import Mixer, synth_chime
from jarvis.application.startup import (
    StartupOptions,
    StartupPhase,
    StartupSequence,
    compose_welcome,
    greeting_for,
)
from jarvis.core.contracts import HealthReport, HealthStatus

HEALTHY = [HealthReport(n, HealthStatus.HEALTHY) for n in StartupOptions().essential]
NIGHT = datetime.datetime(2026, 9, 23, 22, 30)


class FakeMixer:
    def __init__(self, load_error=None):
        self.calls = []
        self.music_playing = False
        self._load_error = load_error

    def play_sfx(self, samples):
        self.calls.append(("sfx",))

    def load(self, path):
        if self._load_error:
            raise self._load_error
        self.calls.append(("load", path))

    def play_music(self, volume, fade):
        self.music_playing = True
        self.calls.append(("music", volume))

    def ramp(self, volume, seconds):
        self.calls.append(("ramp", volume))

    def fade_out(self, seconds):
        self.music_playing = False
        self.calls.append(("fade",))


def make(options=None, *, reports=HEALTHY, mixer=None, voice=True, services_delay=0.0, speak_log=None, phases=None):
    spoken = speak_log if speak_log is not None else []

    async def speak(text):
        spoken.append(text)

    async def services():
        await asyncio.sleep(services_delay)
        return list(reports)

    async def wait_voice():
        return voice

    return StartupSequence(
        options or StartupOptions(music_path="song.mp3"),
        mixer=mixer if mixer is not None else FakeMixer(),
        speak=speak,
        start_services=services,
        wait_voice=wait_voice,
        on_phase=(lambda p, d: phases.append(p)) if phases is not None else None,
        open_url=lambda url: spoken.append(f"URL {url}"),
        clock=lambda: NIGHT,
    ), spoken


class WelcomeTests(unittest.TestCase):
    def test_greeting_periods(self):
        self.assertEqual(greeting_for(datetime.datetime(2026, 1, 1, 8))[1], "Buenos días")
        self.assertEqual(greeting_for(datetime.datetime(2026, 1, 1, 15))[1], "Buenas tardes")
        self.assertEqual(greeting_for(NIGHT)[1], "Buenas noches")

    def test_all_healthy_uses_default_welcome(self):
        text, issues = compose_welcome(HEALTHY, StartupOptions(), NIGHT)
        self.assertEqual(issues, [])
        self.assertEqual(
            text,
            "Buenas noches, Manuel. Todos los sistemas están operativos. "
            "Preparando tu entorno de trabajo. ¿En qué puedo ayudarte?",
        )

    def test_failed_essential_never_claims_operational(self):
        reports = HEALTHY[:-1] + [HealthReport("fast model", HealthStatus.DEGRADED, "Ollama unreachable", required=False)]
        text, issues = compose_welcome(reports, StartupOptions(), NIGHT)
        self.assertNotIn("operativos", text)
        self.assertIn("el modelo de lenguaje no está disponible", text)
        self.assertEqual(issues, ["el modelo de lenguaje"])

    def test_voice_not_ready_is_reported(self):
        text, issues = compose_welcome(HEALTHY, StartupOptions(), NIGHT, voice_ready=False)
        self.assertIn("mi voz clonada", text)

    def test_nonessential_degradation_does_not_block_operational(self):
        text, _ = compose_welcome(HEALTHY + [HealthReport("network", HealthStatus.DEGRADED, required=False)], StartupOptions(), NIGHT)
        self.assertIn("operativos", text)

    def test_period_variant_and_name_are_configurable(self):
        options = StartupOptions(owner_name="Tony", welcome_variants={"evening": "{greeting}, {name}. Listo."})
        self.assertEqual(compose_welcome(HEALTHY, options, NIGHT)[0], "Buenas noches, Tony. Listo.")


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_sequence_order_and_ducking(self):
        mixer, phases = FakeMixer(), []
        seq, spoken = make(mixer=mixer, phases=phases)
        report = await seq.trigger("claps")
        self.assertEqual(report.phase, StartupPhase.READY)
        self.assertEqual(phases, [StartupPhase.ACKNOWLEDGED, StartupPhase.INITIALIZING, StartupPhase.ANNOUNCING, StartupPhase.READY])
        kinds = [c[0] for c in mixer.calls]
        self.assertEqual(kinds, ["sfx", "load", "music", "ramp", "fade"])  # chime first, duck, fade
        self.assertEqual(mixer.calls[3], ("ramp", StartupOptions().duck_volume))
        self.assertIn("operativos", spoken[0])
        self.assertEqual(report.music, "file")
        for key in ("first_sound", "services_ready", "voice_ready", "welcome_spoken"):
            self.assertIn(key, report.timings_ms)
        self.assertLess(report.timings_ms["first_sound"], report.timings_ms["welcome_spoken"])

    async def test_restore_after_welcome(self):
        mixer = FakeMixer()
        seq, _ = make(StartupOptions(music_path="x", after_welcome="restore"), mixer=mixer)
        await seq.trigger()
        self.assertEqual(mixer.calls[-1], ("ramp", StartupOptions().music_volume))

    async def test_duplicate_trigger_joins_running_sequence(self):
        seq, spoken = make(services_delay=0.05)
        first, second = await asyncio.gather(seq.trigger("claps"), seq.trigger("hotkey"))
        self.assertIs(first, second)
        self.assertEqual(len(spoken), 1)
        again = await seq.trigger("claps")  # completed: no repeat
        self.assertEqual(len(spoken), 1)
        self.assertEqual(again.phase, StartupPhase.READY)
        seq.reset()
        await seq.trigger("claps")
        self.assertEqual(len(spoken), 2)

    async def test_cancel_stops_music_and_speech(self):
        mixer = FakeMixer()
        seq, spoken = make(mixer=mixer, services_delay=1.0)
        task = asyncio.create_task(seq.trigger())
        await asyncio.sleep(0.02)
        await seq.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(seq.phase, StartupPhase.CANCELLED)
        self.assertEqual(spoken, [])
        self.assertEqual(mixer.calls[-1], ("fade",))

    async def test_missing_media_falls_back_to_url_then_continues(self):
        mixer = FakeMixer(load_error=FileNotFoundError("nope"))
        seq, spoken = make(StartupOptions(music_path="missing.mp3", music_url="https://example.invalid/x"), mixer=mixer)
        report = await seq.trigger()
        self.assertEqual(report.music, "url")
        self.assertEqual(spoken[0], "URL https://example.invalid/x")
        self.assertEqual(report.phase, StartupPhase.READY)
        self.assertNotIn(("ramp", StartupOptions().duck_volume), mixer.calls)  # nothing to duck

    async def test_undecodable_media_without_url_still_welcomes(self):
        seq, spoken = make(mixer=FakeMixer(load_error=RuntimeError("bad mp3")))
        report = await seq.trigger()
        self.assertEqual(report.music, "error")
        self.assertIn("operativos", spoken[0])

    async def test_degraded_and_failed_final_states(self):
        degraded = HEALTHY[1:] + [HealthReport("configuration", HealthStatus.DEGRADED)]
        seq, spoken = make(reports=degraded)
        self.assertEqual((await seq.trigger()).phase, StartupPhase.DEGRADED)
        failed = HEALTHY[1:] + [HealthReport("configuration", HealthStatus.FAILED, required=True)]
        seq, spoken = make(reports=failed)
        report = await seq.trigger()
        self.assertEqual(report.phase, StartupPhase.FAILED)
        self.assertNotIn("operativos", spoken[0])

    async def test_service_timeout_is_degraded_not_hung(self):
        seq, spoken = make(StartupOptions(services_timeout_seconds=0.05), services_delay=1.0)
        report = await seq.trigger()
        self.assertEqual(report.phase, StartupPhase.FAILED)
        self.assertNotIn("operativos", spoken[0])

    async def test_workspace_runs_concurrently_and_does_not_block_welcome(self):
        release = asyncio.Event()

        async def workspace():
            await release.wait()
            return {"launched": ["vscode"]}

        seq, spoken = make()
        seq._start_workspace = workspace
        report = await seq.trigger()
        self.assertEqual(report.phase, StartupPhase.READY)  # welcome done before the workspace
        self.assertIsNone(report.workspace)
        release.set()
        await seq.workspace_task
        self.assertEqual(report.workspace, {"launched": ["vscode"]})

    async def test_voice_timeout_reports_clone_unavailable(self):
        async def never():
            await asyncio.sleep(10)

        seq, spoken = make(StartupOptions(voice_ready_timeout_seconds=0.05))
        seq._wait_voice = never
        report = await seq.trigger()
        self.assertEqual(report.phase, StartupPhase.DEGRADED)
        self.assertIn("mi voz clonada", spoken[0])


class MixerRenderTests(unittest.TestCase):
    def _mixer_with_music(self, seconds=1.0):
        mixer = Mixer()
        mixer._music = np.full((int(mixer.rate * seconds), 2), 0.5, dtype=np.float32)
        return mixer

    def test_fade_in_ramps_linearly_without_steps(self):
        mixer = self._mixer_with_music()
        mixer.ramp(0.8, 0.1)
        out = mixer.render(int(mixer.rate * 0.1))
        steps = np.abs(np.diff(out[:, 0]))
        self.assertLess(steps.max(), 0.001)  # no audible zipper step
        self.assertAlmostEqual(mixer.gain, 0.8, places=3)

    def test_duck_then_fade_out_stops_music(self):
        mixer = self._mixer_with_music()
        mixer.ramp(0.6, 0.0)
        mixer.render(10)
        mixer.ramp(0.12, 0.05)
        mixer.render(int(mixer.rate * 0.05))
        self.assertAlmostEqual(mixer.gain, 0.12, places=3)
        mixer.fade_out(0.05)
        mixer.render(int(mixer.rate * 0.06))
        self.assertFalse(mixer.music_playing)

    def test_chime_mixes_over_music_and_clips_safely(self):
        mixer = self._mixer_with_music()
        mixer.ramp(1.0, 0.0)
        mixer._sfx.append([synth_chime(mixer.rate) * 4, 0])
        out = mixer.render(2048)
        self.assertLessEqual(float(np.abs(out).max()), 1.0)
        self.assertGreater(mixer.level, 0.0)

    def test_other_sample_rates_are_resampled_not_reopened(self):
        import tempfile

        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.wav"
            sf.write(str(path), np.zeros(22050, dtype=np.float32), 22050)  # 1 s mono
            mixer = Mixer()
            mixer.load(path)
            self.assertEqual(mixer.rate, 44100)
            self.assertEqual(mixer._music.shape, (44100, 2))
            mixer.stop()
            path.unlink()  # a cached track no longer needs the file
            mixer.load(path)
        self.assertEqual(mixer._music.shape, (44100, 2))

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            Mixer().load("/definitely/missing.mp3")


if __name__ == "__main__":
    unittest.main()
