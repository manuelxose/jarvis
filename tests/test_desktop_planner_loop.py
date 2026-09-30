"""Desktop route, planner, gateway risk and audit exercised together without launching apps."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import EchoTTS, RecordingAudioPlayer, ScriptedModel
from jarvis.adapters.tools.desktop import DesktopContext
from jarvis.adapters.tools.gateway import AuditLog, Risk, Tool, ToolGateway, ToolResult
from jarvis.application import planner as planner_module
from jarvis.application.planner import DesktopPlanner, VoiceConfirmer
from jarvis.application.routing import Router
from jarvis.application.runtime import _build_tools, _register_desktop_tools
from jarvis.application.turn_manager import TurnManager
from jarvis.config import load_config
from jarvis.core.turn import TurnContext


REQUEST = "arranca el backend y abre VS Code"


def plan(*steps, say="Voy con ello."):
    return json.dumps({"steps": [{"tool": name, "arguments": args} for name, args in steps], "say": say, "ask": None})


class Recorder(Tool):
    def __init__(self, name, calls, risk=Risk.REVERSIBLE, delay=0):
        self.name, self.risk, self.calls, self.delay = name, risk, calls, delay
        self.description = f"{name} de prueba"
        self.input_schema = {"action": {"type": "string"}, "path": {"type": "string"}}

    def scope_paths(self, arguments):
        return [arguments["path"]] if "path" in arguments else []

    async def execute(self, arguments, context):
        await asyncio.sleep(self.delay)
        self.calls.append((self.name, dict(arguments)))
        return ToolResult(f"{self.name} hecho.")


class _Hermes:
    async def start(self):
        pass

    async def stop(self):
        pass


class DesktopPlannerLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit_path = self.root / "audit.jsonl"
        self.spoken = []

    def audit(self):
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]

    def build(self, steps, *, delay=0, high=False, trusted=(), scope=()):
        calls = []
        tools = [Recorder("workspace", calls, Risk.MEDIUM, delay),
                 Recorder("vscode_open", calls, delay=delay)]
        if high:
            tools.append(Recorder("danger", calls, Risk.HIGH_RISK, delay))
        confirmer = VoiceConfirmer(lambda name, args: f"ejecutar {name}", timeout_seconds=1)
        gateway = ToolGateway(tools, confirmer=confirmer, trusted=trusted, authorized_scopes=scope,
                              audit=AuditLog(self.audit_path))
        model = ScriptedModel(default=plan(*steps))
        manager = TurnManager(router=Router(), tools=gateway.execute, model=model,
                              tts=EchoTTS(), audio=RecordingAudioPlayer())

        async def speak(text, context):
            self.spoken.append(text)
            await manager._speak_text(text, context)

        manager.planner = DesktopPlanner(model=model, gateway=gateway, context=lambda: {"last_project": str(self.root / "project")}, speak=speak)
        confirmer.speak = speak
        return manager, gateway, confirmer, calls

    async def wait_confirmation(self, confirmer, task):
        for _ in range(200):
            if confirmer.waiting or task.done():
                break
            await asyncio.sleep(0.005)
        self.assertTrue(confirmer.waiting, "planner did not request spoken confirmation")

    async def test_two_steps_route_execute_in_order_and_audit_agent(self):
        steps = [("workspace", {"action": "start", "path": str(self.root)}),
                 ("vscode_open", {"path": str(self.root / "project")})]
        manager, _, _, calls = self.build(steps, scope=[self.root])
        result = await manager.handle(REQUEST)
        self.assertEqual("desktop", result.route)
        self.assertEqual(steps, calls)
        self.assertEqual([], self.spoken)
        self.assertIn("vscode_open hecho", result.response)
        self.assertEqual(["agent", "agent"], [entry["origin"] for entry in self.audit()])

    async def test_slow_steps_speak_one_progress_sentence(self):
        steps = [("workspace", {"action": "start", "path": str(self.root)}), ("vscode_open", {"path": str(self.root / "project")})]
        manager, _, _, calls = self.build(steps, delay=0.04, scope=[self.root])
        with mock.patch.object(planner_module, "PROGRESS_AFTER_SECONDS", 0.005):
            result = await manager.handle(REQUEST)
        self.assertEqual("desktop", result.route)
        self.assertEqual(steps, calls)
        self.assertEqual(["Voy con ello."], self.spoken)

    async def test_high_risk_confirmed_continues_and_declined_stops(self):
        steps = [("workspace", {"action": "start", "path": str(self.root)}),
                 ("danger", {"path": str(self.root)}), ("vscode_open", {})]
        for reply, expected in (("confirmo", ["workspace", "danger", "vscode_open"]),
                                ("no", ["workspace"])):
            with self.subTest(reply=reply):
                self.spoken.clear()
                self.audit_path.unlink(missing_ok=True)
                manager, _, confirmer, calls = self.build(steps, high=True, scope=[self.root])
                task = asyncio.create_task(manager.handle("arranca workspace, ejecuta danger y abre VS Code"))
                await self.wait_confirmation(confirmer, task)
                self.assertIn("ejecutar danger", self.spoken[0])
                confirmer.answer(reply)
                result = await task
                self.assertEqual(expected, [name for name, _ in calls])
                self.assertEqual("desktop", result.route)
                if reply == "no":
                    self.assertIn("He completado 1 de 3 pasos", result.response)
                    self.assertEqual("declined", self.audit()[-1]["decision"])
                else:
                    self.assertEqual("confirmed", self.audit()[1]["decision"])
                self.assertEqual({"agent"}, {entry["origin"] for entry in self.audit()})

    async def test_trusted_medium_outside_scope_asks_agent_but_owner_runs(self):
        steps = [("workspace", {"path": str(self.root / "outside")})]
        scope = self.root / "inside"
        scope.mkdir()
        manager, gateway, confirmer, calls = self.build(steps, trusted=["workspace"], scope=[scope])
        task = asyncio.create_task(manager.handle("arranca workspace y abre VS Code"))
        await self.wait_confirmation(confirmer, task)
        self.assertEqual([], calls)
        confirmer.answer("no")
        self.assertIn("No he podido completar", (await task).response)
        self.assertEqual("declined", self.audit()[0]["decision"])
        await gateway.execute("workspace", steps[0][1], TurnContext.fresh("direct"), origin="owner")
        self.assertEqual(steps, calls)
        self.assertEqual(("owner", "auto"), (self.audit()[-1]["origin"], self.audit()[-1]["decision"]))

    async def test_runtime_registry_schema_reaches_model_prompt(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "appdata")}):
            config_path = self.root / "config.json"
            config_path.write_text(json.dumps({"runtime": {}, "desktop": {}}), encoding="utf-8")
            config = load_config(config_path, {})
            confirmer = VoiceConfirmer(lambda name, args: name)
            gateway = _build_tools(config, confirmer)
            workspace = mock.Mock(profiles={"dev": object()})
            _register_desktop_tools(config, gateway, DesktopContext(self.root / "context.json"), workspace, _Hermes())
            model = ScriptedModel(default=plan())
            planner = DesktopPlanner(model=model, gateway=gateway, context=dict)
            await planner.plan("abre la carpeta con VS Code", TurnContext.fresh("prompt"))
            self.assertIn("workspace(", model.calls[0])
            self.assertIn("vscode_open(", model.calls[0])


if __name__ == "__main__":
    unittest.main()
