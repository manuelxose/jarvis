"""Desktop planner, spoken confirmation and M007 routing."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway, ToolResult
from jarvis.application import planner as planner_module
from jarvis.application.planner import DesktopPlanner, VoiceConfirmer, is_affirmative, parse_plan
from jarvis.application.routing import FastCommandClassifier, Router
from jarvis.core.turn import TurnContext


class ScriptedModel:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def generate(self, prompt, context):
        self.prompts.append(prompt)
        for i in range(0, len(self.reply), 7):
            yield self.reply[i:i + 7]


class Recorder(Tool):
    def __init__(self, name, risk=Risk.REVERSIBLE, delay=0.0, fail=False):
        self.name, self.risk, self.delay, self.fail = name, risk, delay, fail
        self.description = f"{name} tool"
        self.input_schema = {"path": {"type": "string"}, "action": {"type": "string", "enum": ["start", "stop"]}}
        self.calls = []

    async def execute(self, arguments, context):
        self.calls.append(dict(arguments))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("backend crashed")
        return ToolResult(f"{self.name} hecho.")


def plan_json(steps, say="Voy.", ask=None):
    return json.dumps({"steps": [{"tool": t, "arguments": a} for t, a in steps], "say": say, "ask": ask})


class ParsePlanTests(unittest.TestCase):
    def test_extracts_json_from_chatter_and_drops_unknown_tools(self):
        raw = 'Claro: ' + plan_json([("workspace", {"action": "start"}), ("rm_rf", {})]) + " ¡listo!"
        plan = parse_plan(raw, {"workspace"})
        self.assertEqual(plan.steps, [("workspace", {"action": "start"})])

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            parse_plan("no sé", {"x"})

    def test_step_count_is_bounded(self):
        plan = parse_plan(plan_json([("a", {})] * 20), {"a"})
        self.assertEqual(len(plan.steps), planner_module.MAX_STEPS)


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def make(self, reply, *tools):
        gateway = ToolGateway(list(tools))
        spoken = []

        async def speak(text, context):
            spoken.append(text)

        model = ScriptedModel(reply)
        planner = DesktopPlanner(model=model, gateway=gateway, context=lambda: {"last_project": "C:\\dev\\jarvis"}, speak=speak)
        return planner, spoken, model

    async def test_multi_step_plan_executes_in_order(self):
        workspace, vscode = Recorder("workspace"), Recorder("vscode_open")
        planner, spoken, model = self.make(
            plan_json([("workspace", {"action": "start"}), ("vscode_open", {"path": "C:\\dev\\jarvis"})], say="Arrancando el backend y abriendo VS Code."),
            workspace, vscode,
        )
        answer = await planner.handle("arranca el backend y abre VS Code", TurnContext.fresh("t"))
        self.assertEqual(workspace.calls, [{"action": "start"}])
        self.assertEqual(vscode.calls, [{"path": "C:\\dev\\jarvis"}])
        self.assertIn("vscode_open hecho", answer)
        self.assertEqual(spoken, [])  # fast: no progress update
        self.assertIn("C:\\\\dev\\\\jarvis", model.prompts[0])  # context reaches the model
        self.assertIn("workspace(path?:string, action?:start|stop)", model.prompts[0])

    async def test_one_progress_update_for_slow_steps(self):
        planner_module.PROGRESS_AFTER_SECONDS, saved = 0.02, planner_module.PROGRESS_AFTER_SECONDS
        try:
            a, b = Recorder("a", delay=0.06), Recorder("b", delay=0.06)
            planner, spoken, _ = self.make(plan_json([("a", {}), ("b", {})], say="Sigo con ello."), a, b)
            await planner.handle("haz a y b", TurnContext.fresh("t"))
            self.assertEqual(spoken, ["Sigo con ello."])
        finally:
            planner_module.PROGRESS_AFTER_SECONDS = saved

    async def test_failure_stops_and_reports_progress(self):
        a, b, c = Recorder("a"), Recorder("b", fail=True), Recorder("c")
        planner, _, _ = self.make(plan_json([("a", {}), ("b", {}), ("c", {})]), a, b, c)
        answer = await planner.handle("x", TurnContext.fresh("t"))
        self.assertIn("He completado 1 de 3 pasos", answer)
        self.assertIn("backend crashed", answer)
        self.assertEqual(c.calls, [])

    async def test_ambiguous_request_asks_a_targeted_question(self):
        planner, _, _ = self.make(plan_json([], ask="¿Qué proyecto: jarvis o web?"), Recorder("a"))
        self.assertEqual(await planner.handle("abre el proyecto", TurnContext.fresh("t")), "¿Qué proyecto: jarvis o web?")

    async def test_planner_steps_still_go_through_risk_policy(self):
        danger = Recorder("danger", risk=Risk.HIGH_RISK)
        planner, _, _ = self.make(plan_json([("danger", {})]), danger)
        answer = await planner.handle("borra todo", TurnContext.fresh("t"))
        self.assertIn("No he podido completar danger", answer)  # no confirmer -> denied
        self.assertEqual(danger.calls, [])

    async def test_garbage_model_output_is_handled(self):
        planner, _, _ = self.make("lo siento, no puedo", Recorder("a"))
        self.assertIn("No he entendido", await planner.handle("x", TurnContext.fresh("t")))


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def test_affirmative_parsing_is_strict(self):
        for yes in ("Confirmo.", "Sí, confirmo", "Jarvis, adelante", "hazlo", "sí"):
            self.assertTrue(is_affirmative(yes), yes)
        for no in ("no", "no, cancela", "sí, no lo hagas", "", "vale pero espera", "confirmo que no quiero nada de eso ahora mismo"):
            self.assertFalse(is_affirmative(no), no)

    async def test_confirmer_speaks_explanation_and_waits_for_answer(self):
        spoken = []
        confirmer = VoiceConfirmer(lambda name, args: f"borrar {args['path']} para siempre")

        async def speak(text, context):
            spoken.append(text)

        confirmer.speak = speak
        task = asyncio.create_task(confirmer("file_delete", {"path": "C:\\x"}, TurnContext.fresh("t")))
        await asyncio.sleep(0.01)
        self.assertTrue(confirmer.waiting)
        self.assertIn("borrar C:\\x para siempre", spoken[0])
        confirmer.answer("confirmo")
        self.assertTrue(await task)

    async def test_confirmer_timeout_declines(self):
        confirmer = VoiceConfirmer(lambda n, a: "algo", timeout_seconds=0.02)
        spoken = []

        async def speak(text, context):
            spoken.append(text)

        confirmer.speak = speak
        self.assertFalse(await confirmer("x", {}, TurnContext.fresh("t")))
        self.assertIn("No he oído confirmación", spoken[-1])

    async def test_without_speaker_nothing_is_confirmed(self):
        self.assertFalse(await VoiceConfirmer(lambda n, a: "x")("x", {}, TurnContext.fresh("t")))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_workspace_and_system_fast_commands(self):
        classifier = FastCommandClassifier()
        cases = {
            "Jarvis, arranca mi entorno de desarrollo": ("workspace", {"action": "start"}),
            "prepara mi entorno de trabajo": ("workspace", {"action": "start"}),
            "apaga mi entorno de desarrollo": ("workspace", {"action": "stop"}),
            "abre mis proyectos": ("workspace", {"action": "start", "profile": "projects"}),
            "reinicia Hermes": ("service_restart", {"name": "hermes"}),
            "¿qué está consumiendo memoria de la GPU?": ("gpu_processes", {}),
            "muéstrame qué está usando la gráfica": ("gpu_processes", {}),
            "cancela la operación": ("cancel_operations", {}),
            "¿cómo va el sistema?": ("system_stats", {}),
        }
        for text, (name, args) in cases.items():
            with self.subTest(text=text):
                match = classifier.match(text)
                self.assertIsNotNone(match)
                self.assertEqual((match.name, dict(match.arguments)), (name, args))

    def test_m009_local_commands_need_no_model(self):
        classifier = FastCommandClassifier()
        cases = {
            "minimiza chrome": ("window_manage", {"action": "minimize", "target": "chrome"}),
            "maximiza la ventana de spotify": ("window_manage", {"action": "maximize", "target": "spotify"}),
            "trae chrome al frente": ("window_focus", {"target": "chrome"}),
            "cierra spotify": ("app_close", {"target": "spotify"}),
            "close chrome": ("app_close", {"target": "chrome"}),
            "muéstrame la actividad de red": ("network_stats", {}),
            "network usage": ("network_stats", {}),
            "uso de la GPU": ("system_stats", {}),
            "¿cuánta RAM?": ("system_stats", {}),
            "abre una terminal": ("terminal_open", {}),
            "abre el proyecto jarvis": ("project_open", {"name": "jarvis"}),
            "pon el volumen al 40": ("volume_set", {"level": "40"}),
        }
        for text, (name, args) in cases.items():
            with self.subTest(text=text):
                match = classifier.match(text)
                self.assertEqual((match.name, dict(match.arguments)), (name, args))

    def test_vague_or_destructive_phrases_never_hit_a_local_tool(self):
        classifier = FastCommandClassifier()
        for text in ("cierra todo", "cierra esto", "borra el proyecto jarvis", "elimina todos los archivos", "formatea el disco"):
            with self.subTest(text=text):
                match = classifier.match(text)
                self.assertTrue(match is None or match.name not in {"app_close", "file_delete", "run_command", "process_kill"}, match)

    async def test_multi_action_and_references_go_to_desktop_planner(self):
        router = Router()
        for text in (
            "arranca el backend y abre VS Code",
            "abre el proyecto en el que estaba trabajando",
            "cierra todo lo relacionado con ese proyecto",
        ):
            with self.subTest(text=text):
                self.assertEqual((await router.route(text, TurnContext.fresh("t"))).route, "desktop")
        self.assertEqual((await router.route("abre spotify", TurnContext.fresh("t"))).route, "fast_command")
        self.assertEqual((await router.route("mira este error y arréglalo", TurnContext.fresh("t"))).route, "hermes")


if __name__ == "__main__":
    unittest.main()
