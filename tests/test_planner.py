"""Desktop planner, spoken confirmation and desktop routing."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway, ToolResult
from jarvis.application import planner as planner_module
from jarvis.application.planner import DesktopPlanner, _ground_steps, VoiceConfirmer, is_affirmative, parse_plan
from jarvis.application.routing import FastCommandClassifier, Router
from jarvis.core.turn import TurnContext


class LoneBackslashPlanTests(unittest.TestCase):
    def test_single_backslash_windows_paths_parse_as_literal(self):
        raw = r'{"steps":[{"tool":"file_delete","arguments":{"path":"C:\Users\Admin\temp\prueba.txt"}}],"say":"ok","ask":null}'
        plan = parse_plan(raw, {"file_delete"})
        self.assertEqual(plan.steps[0][1]["path"], "C:\\Users\\Admin\\temp\\prueba.txt")


class GroundStepsTests(unittest.TestCase):
    def test_named_folder_pins_the_path_and_permanent_needs_to_be_said(self):
        guessed = [("file_delete", {"path": "\\temp\\prueba.txt", "permanent": True})]
        said = "borra el archivo prueba.txt de la carpeta temporal"
        (tool, args), = _ground_steps(said, guessed)
        self.assertEqual((args["path"], args["permanent"]), (str(Path(tempfile.gettempdir()) / "prueba.txt"), False))
        (_, args), = _ground_steps(said + " permanentemente", guessed)
        self.assertTrue(args["permanent"])
        (_, args), = _ground_steps("borra prueba.txt", guessed)  # no folder named: path untouched
        self.assertEqual(args["path"], "\\temp\\prueba.txt")


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
        self.assertIn("temporal=" + tempfile.gettempdir(), model.prompts[0])  # real folders, not invented Linux paths

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
        self.assertEqual(await planner.handle("abre el proyecto", TurnContext.fresh("t")), "¿Qué proyecto quieres abrir?")

    async def test_unrelated_two_step_plan_retries_then_refuses_without_execution(self):
        workspace, vscode, command = Recorder("workspace"), Recorder("vscode_open"), Recorder("run_command")
        planner, _, model = self.make(plan_json([("run_command", {"action": "start"})]), workspace, vscode, command)
        answer = await planner.handle("arranca el backend y abre VS Code", TurnContext.fresh("t"))
        self.assertIn("No he entendido", answer)
        self.assertEqual(2, len(model.prompts))
        self.assertNotIn("run_command(", model.prompts[0])
        self.assertEqual([], command.calls)

    async def test_explicit_two_step_recovers_from_unrelated_model_only_with_unique_context(self):
        workspace, vscode, unrelated = Recorder("workspace"), Recorder("vscode_open"), Recorder("run_command")
        gateway = ToolGateway([workspace, vscode, unrelated])
        model = ScriptedModel(plan_json([("run_command", {"command": "unexpected"})]))
        planner = DesktopPlanner(model=model, gateway=gateway,
                                 context=lambda: {"profiles": ["dev"], "last_project": "C:\\dev\\jarvis"})
        answer = await planner.handle("arranca el backend y abre VS Code", TurnContext.fresh("t"))
        self.assertIn("vscode_open hecho", answer)
        self.assertEqual([{"action": "start", "profile": "dev"}], workspace.calls)
        self.assertEqual([{"path": "C:\\dev\\jarvis"}], vscode.calls)
        self.assertEqual([], unrelated.calls)
        self.assertEqual(2, len(model.prompts))

    async def test_two_step_rejects_forced_or_unrelated_folder_without_execution(self):
        for workspace_args, folder in (({"action": "start", "force": True}, "C:\\dev\\jarvis"),
                                       ({"action": "start"}, "C:\\other")):
            with self.subTest(workspace_args=workspace_args, folder=folder):
                workspace, vscode = Recorder("workspace"), Recorder("vscode_open")
                planner, _, model = self.make(plan_json([("workspace", workspace_args),
                                                         ("vscode_open", {"path": folder})]), workspace, vscode)
                answer = await planner.handle("arranca el backend y abre VS Code", TurnContext.fresh("t"))
                self.assertIn("No he entendido", answer)
                self.assertEqual(2, len(model.prompts))
                self.assertEqual([], workspace.calls)
                self.assertEqual([], vscode.calls)

    async def test_malformed_first_plan_recovers_only_after_valid_ordered_reply(self):
        workspace, vscode = Recorder("workspace"), Recorder("vscode_open")
        planner, _, model = self.make("{broken", workspace, vscode)
        valid = plan_json([("workspace", {"action": "start"}), ("vscode_open", {"path": "C:\\dev\\jarvis"})])
        async def generate(prompt, context):
            model.prompts.append(prompt)
            yield "{broken" if len(model.prompts) == 1 else valid
        model.generate = generate
        self.assertIn("vscode_open hecho", await planner.handle("arranca el backend y abre VS Code", TurnContext.fresh("t")))
        self.assertEqual([{"action": "start"}], workspace.calls)
        self.assertEqual(1, len(vscode.calls))

    async def test_planner_steps_still_go_through_risk_policy(self):
        danger = Recorder("danger", risk=Risk.HIGH_RISK)
        planner, _, _ = self.make(plan_json([("danger", {})]), danger)
        answer = await planner.handle("borra todo", TurnContext.fresh("t"))
        self.assertIn("No he podido completar danger", answer)  # no confirmer -> denied
        self.assertEqual(danger.calls, [])

    async def test_trusted_medium_without_scopes_is_denied_for_planner(self):
        medium = Recorder("workspace", risk=Risk.MEDIUM)
        gateway = ToolGateway([medium], trusted=["workspace"])
        planner = DesktopPlanner(model=ScriptedModel(plan_json([("workspace", {"action": "start"})])), gateway=gateway, context=dict)
        answer = await planner.handle("arranca el backend", TurnContext.fresh("t"))
        self.assertIn("No he podido completar", answer)
        self.assertEqual([], medium.calls)

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

    def test_m009_s05_gap_phrasings_route_locally(self):
        classifier = FastCommandClassifier()
        cases = {
            "¿qué ventanas hay abiertas?": ("windows_list", {}),
            "lista mis ventanas": ("windows_list", {}),
            "qué tengo abierto": ("windows_list", {}),
            "list windows": ("windows_list", {}),
            "¿cómo va la red?": ("network_stats", {}),
            "cómo va internet": ("network_stats", {}),
            "abre el proyecto jarvis en vscode": ("project_open", {"name": "jarvis"}),
            "abre el proyecto jarvis en VS Code": ("project_open", {"name": "jarvis"}),
            "abre el repo jarvis en visual studio code": ("project_open", {"name": "jarvis"}),
            "abre el proyecto jarvis": ("project_open", {"name": "jarvis"}),
            "lista mis repositorios": ("project_list", {}),
            "qué repos tengo": ("project_list", {}),
            "list my repos": ("project_list", {}),
            "¿a qué volumen está?": ("volume_get", {}),
            "qué volumen tengo": ("volume_get", {}),
            "cuál es el volumen": ("volume_get", {}),
            "what is the volume": ("volume_get", {}),
            "pon el volumen al 40": ("volume_set", {"level": "40"}),
        }
        for text, (name, args) in cases.items():
            with self.subTest(text=text):
                match = classifier.match(text)
                self.assertIsNotNone(match)
                self.assertEqual((match.name, dict(match.arguments)), (name, args))

    async def test_repo_list_is_local_but_repo_analysis_stays_with_hermes(self):
        router = Router()
        self.assertEqual((await router.route("lista mis repositorios", TurnContext.fresh("t"))).route, "fast_command")
        for text in ("revisa mis proyectos", "analiza el repositorio"):
            with self.subTest(text=text):
                self.assertIsNone(FastCommandClassifier().match(text))
                self.assertEqual((await router.route(text, TurnContext.fresh("t"))).route, "hermes")

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
            "borra el archivo jarvis-uat-delete.txt de la carpeta temporal",
        ):
            with self.subTest(text=text):
                self.assertEqual((await router.route(text, TurnContext.fresh("t"))).route, "desktop")
        self.assertEqual((await router.route("abre spotify", TurnContext.fresh("t"))).route, "fast_command")
        self.assertEqual((await router.route("mira este error y arréglalo", TurnContext.fresh("t"))).route, "hermes")


if __name__ == "__main__":
    unittest.main()
