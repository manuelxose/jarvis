"""Voice control of the startup music and of the assistant (sleep / restart / shutdown)."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.desktop import AssistantControlTool, MusicTool
from jarvis.adapters.tools.gateway import ToolGateway
from jarvis.application.routing import FastCommandClassifier
from jarvis.apps import daemon
from jarvis.core.errors import ToolPermissionDenied
from jarvis.core.turn import TurnContext

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_daemon as td  # noqa: E402 - reuse the fake runtime/mixer


class RoutingTests(unittest.TestCase):
    def test_phrases(self):
        c = FastCommandClassifier()
        cases = {
            "para la música": ("music", {"action": "stop"}),
            "quita la música": ("music", {"action": "stop"}),
            "apaga la música": ("music", {"action": "stop"}),
            "detén la canción": ("music", {"action": "stop"}),
            "baja la música": ("music", {"action": "lower"}),
            "sube un poco la música": ("music", {"action": "raise"}),
            "reiníciate": ("assistant_restart", {}),
            "apágate": ("assistant_shutdown", {}),
            "apágate del todo": ("assistant_shutdown", {}),
            "a dormir": ("sleep", {}),
            "reinicia hermes": ("service_restart", {"name": "hermes"}),  # unchanged
            "para": ("stop", {}),  # unchanged: stops the reply
        }
        for text, (name, args) in cases.items():
            with self.subTest(text=text):
                m = c.match(text)
                self.assertEqual((m.name, dict(m.arguments)), (name, args))

    def test_windows_restart_or_shutdown_is_not_the_assistant(self):
        c = FastCommandClassifier()
        for text in ("reinicia el ordenador", "apaga el ordenador", "reinicia el sistema"):
            m = c.match(text)
            self.assertTrue(m is None or not m.name.startswith("assistant_"), (text, m))


class FakeMixer:
    def __init__(self, playing=True):
        self.music_playing, self.gain, self.calls = playing, 0.55, []

    def fade_out(self, seconds):
        self.calls.append(("fade", seconds))
        self.music_playing = False

    def ramp(self, level, seconds):
        self.calls.append(("ramp", round(level, 3)))


class MusicToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_lower_raise_startup_music(self):
        mixer = FakeMixer()
        tool = MusicTool(mixer)
        self.assertEqual((await tool.execute({"action": "lower"}, TurnContext.fresh("t"))).say, "Música más baja.")
        self.assertEqual(mixer.calls[-1], ("ramp", 0.275))
        self.assertEqual((await tool.execute({"action": "stop"}, TurnContext.fresh("t"))).say, "Música detenida.")
        self.assertEqual(mixer.calls[-1][0], "fade")

    async def test_without_startup_music_stop_pauses_the_media_player(self):
        pressed = []

        async def media_key():
            pressed.append(True)

        result = await MusicTool(FakeMixer(playing=False), media_key=media_key).execute({"action": "stop"}, TurnContext.fresh("t"))
        self.assertEqual((result.say, pressed), ("Pausado.", [True]))
        lower = await MusicTool(None).execute({"action": "lower"}, TurnContext.fresh("t"))
        self.assertFalse(lower.ok)


class AssistantControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_needs_spoken_confirmation(self):
        called = []
        tool = AssistantControlTool("shutdown", lambda: called.append("off"))

        async def decline(name, args, ctx):
            return False

        with self.assertRaises(ToolPermissionDenied):
            await ToolGateway([tool], confirmer=decline).execute("assistant_shutdown", {}, TurnContext.fresh("t"))
        self.assertEqual(called, [])

        async def approve(name, args, ctx):
            return True

        result = await ToolGateway([tool], confirmer=approve).execute("assistant_shutdown", {}, TurnContext.fresh("t"))
        self.assertEqual((called, result.say), (["off"], "Apagando. Hasta pronto."))
        self.assertIn("arrancarme a mano", tool.describe({}))

    async def test_restart_is_immediate_and_needs_the_daemon(self):
        called = []
        result = await ToolGateway([AssistantControlTool("restart", lambda: called.append("r"))]).execute("assistant_restart", {}, TurnContext.fresh("t"))
        self.assertEqual(called, ["r"])
        self.assertIn("Reiniciando", result.say)
        alone = await AssistantControlTool("restart").execute({}, TurnContext.fresh("t"))
        self.assertFalse(alone.ok)


class GoodbyeIsHeardTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_after_turn_lets_the_reply_finish(self):
        from test_voice_loop import _build
        from jarvis.adapters.fakes import ScriptedSTT
        from jarvis.core.contracts import Transcript

        frames = [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6
        loop, manager, player = _build(frames * 3, stt=ScriptedSTT([Transcript("Jarvis, a dormir", is_final=True)]))
        from jarvis.adapters.tools.desktop import SleepTool
        from jarvis.adapters.tools.gateway import ToolGateway

        gateway = ToolGateway([SleepTool(loop.request_stop_after_turn)])
        manager._tools = gateway.execute
        await asyncio.wait_for(loop.run(), 2)
        self.assertEqual(len(loop.turns), 1)
        self.assertFalse(loop.turns[0].cancelled)  # "Hasta luego" was not cut off
        self.assertEqual(loop.turns[0].response, "Hasta luego. Estaré atento.")


class MusicAwareListeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_threshold_follows_the_music_and_is_restored(self):
        from jarvis.application.startup import StartupOptions

        vad = SimpleNamespace(threshold=300.0)
        mixer = FakeMixer()
        mixer.gain = 0.10
        state = {"current_turn": None}
        runtime = SimpleNamespace(voice_loop=SimpleNamespace(_vad=vad),
                                  components=SimpleNamespace(audio_output=SimpleNamespace(state=lambda: state)))
        task = asyncio.create_task(daemon._duck_during_speech(mixer, runtime, StartupOptions()))
        await asyncio.sleep(0.08)
        self.assertAlmostEqual(vad.threshold, 300 + 1.5 * daemon.MUSIC_MIC_COUPLING * 0.10)
        state["current_turn"] = "t"  # Jarvis speaks: music ducks further
        await asyncio.sleep(0.08)
        self.assertEqual(mixer.calls[-1], ("ramp", 0.04))
        mixer.music_playing = False  # "para la música"
        await asyncio.wait_for(task, 1)
        self.assertEqual(vad.threshold, 300.0)


class PrimeOnFirstClapTests(td.ActivationStateTests):
    async def test_first_clap_opens_the_speaker_stream(self):
        primed = []

        class PrimeMixer(td.FakeMixer):
            def prime(self):
                primed.append(True)

        sentinel = self.sentinel()
        sentinel._mixer_factory = PrimeMixer
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.detector.on_candidate(SimpleNamespace(time=1.0))
        await self.wait_for(lambda: bool(primed))
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)


class DaemonRestartTests(td.ActivationStateTests):
    async def test_voice_restart_ends_session_and_requests_relaunch(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active")
        sentinel._end_session("restart")  # what the assistant_restart tool calls
        await asyncio.wait_for(task, 3)
        self.assertTrue(sentinel.restart_requested)

    async def test_control_restart_while_idle(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.request_restart()
        await asyncio.wait_for(task, 3)
        self.assertTrue(sentinel.restart_requested)

    def test_relaunch_argv(self):
        self.assertEqual(daemon.relaunch_argv("pythonw.exe", ["C:/j/scripts/jarvis_daemon.pyw", "--config", "c.json"]),
                         ["pythonw.exe", "C:/j/scripts/jarvis_daemon.pyw", "--config", "c.json"])
        self.assertEqual(daemon.relaunch_argv("python.exe", ["C:/j/src/jarvis/__main__.py", "daemon", "--config", "c.json"]),
                         ["python.exe", "-m", "jarvis", "daemon", "--config", "c.json"])


if __name__ == "__main__":
    unittest.main()
