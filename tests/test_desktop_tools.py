"""Desktop tools + gateway risk policy: validation, scopes, confirmation, audit, cancellation."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.desktop import (
    DesktopContext,
    build_desktop_tools,
    command_risk,
    is_secret_file,
    parse_compute_apps,
)
from jarvis.adapters.tools.gateway import AuditLog, Risk, Tool, ToolGateway, ToolResult, redact
from jarvis.core.errors import ToolExecutionError, ToolPermissionDenied
from jarvis.core.turn import TurnContext


class Confirmer:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    async def __call__(self, name, arguments, context):
        self.asked.append(name)
        return self.answer


class Env:
    def __init__(self, answer=True, trusted=()):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.outside = root / "outside"
        self.outside.mkdir()
        self.data = root / "data"
        self.memory = DesktopContext(self.data / "ctx.json")
        self.confirmer = Confirmer(answer)
        self.audit_path = self.data / "audit.jsonl"
        self.gateway = ToolGateway([], confirmer=self.confirmer, authorized_scopes=[self.project], trusted=trusted, audit=AuditLog(self.audit_path))
        for tool in build_desktop_tools(scopes=self.gateway.scopes, data_dir=self.data, memory=self.memory, cancel_operations=self.gateway.cancel_operations):
            self.gateway.register(tool)

    def run(self, name, args, origin="owner"):
        return self.gateway.execute(name, args, TurnContext.fresh("t"), origin=origin)

    def audit(self):
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]

    def close(self):
        self.tmp.cleanup()


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    async def test_medium_inside_scope_runs_without_confirmation_and_backs_up(self):
        target = self.env.project / "notes.txt"
        target.write_text("old")
        result = await self.env.run("file_write", {"path": str(target), "content": "new"})
        self.assertIsInstance(result, ToolResult)
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(self.env.confirmer.asked, [])
        self.assertEqual(Path(result.data["backup"]).read_text(), "old")

    async def test_medium_outside_scope_needs_confirmation(self):
        target = self.env.outside / "x.txt"
        await self.env.run("file_write", {"path": str(target), "content": "hi"})
        self.assertEqual(self.env.confirmer.asked, ["file_write"])
        self.env.confirmer.answer = False
        with self.assertRaises(ToolPermissionDenied):
            await self.env.run("file_write", {"path": str(self.env.outside / "y.txt"), "content": "hi"})
        self.assertFalse((self.env.outside / "y.txt").exists())

    async def test_path_traversal_cannot_escape_scope(self):
        sneaky = str(self.env.project / ".." / "outside" / "z.txt")
        self.env.confirmer.answer = False
        with self.assertRaises(ToolPermissionDenied):
            await self.env.run("file_write", {"path": sneaky, "content": "x"})

    async def test_high_risk_always_confirms_every_time(self):
        victim = self.env.project / "a.txt"
        victim.write_text("x")
        await self.env.run("file_delete", {"path": str(victim), "permanent": True})
        self.assertFalse(victim.exists())
        victim.write_text("y")
        await self.env.run("file_delete", {"path": str(victim), "permanent": True})
        self.assertEqual(self.env.confirmer.asked, ["file_delete", "file_delete"])  # never cached

    async def test_trusted_list_cannot_make_high_risk_automatic(self):
        env = Env(trusted=("file_delete", "file_write"))
        try:
            victim = env.outside / "a.txt"
            victim.write_text("x")
            await env.run("file_write", {"path": str(victim), "content": "y"})  # trusted MEDIUM: auto
            self.assertEqual(env.confirmer.asked, [])
            await env.run("file_delete", {"path": str(victim), "permanent": True})
            self.assertEqual(env.confirmer.asked, ["file_delete"])
        finally:
            env.close()

    async def test_agent_origin_does_not_inherit_trusted_operations(self):
        env = Env(trusted=("file_write",))
        try:
            await env.run("file_write", {"path": str(env.outside / "a.txt"), "content": "y"}, origin="agent")
            self.assertEqual(env.confirmer.asked, ["file_write"])
        finally:
            env.close()

    async def test_recycle_delete_is_medium_and_recoverable(self):
        victim = self.env.project / "draft.txt"
        victim.write_text("keep me")
        result = await self.env.run("file_delete", {"path": str(victim)})
        self.assertEqual(self.env.confirmer.asked, [])
        self.assertFalse(victim.exists())
        if sys.platform == "win32":
            self.assertEqual(result.data["recycled_to"], "recycle_bin")  # real shell Recycle Bin
        else:
            self.assertEqual(Path(result.data["recycled_to"]).read_text(), "keep me")

    async def test_refuses_to_delete_a_whole_scope(self):
        result = await self.env.run("file_delete", {"path": str(self.env.project)})
        self.assertFalse(result.ok)
        self.assertTrue(self.env.project.exists())

    async def test_secret_files_are_high_risk(self):
        self.assertTrue(is_secret_file("C:/x/.env"))
        self.assertTrue(is_secret_file("config.local.json"))
        self.assertTrue(is_secret_file("id_ed25519"))
        self.assertFalse(is_secret_file("notes.md"))
        (self.env.project / ".env").write_text("TOKEN=abc")
        self.env.confirmer.answer = False
        with self.assertRaises(ToolPermissionDenied):
            await self.env.run("file_read", {"path": str(self.env.project / ".env")})

    async def test_strict_validation(self):
        with self.assertRaisesRegex(ToolExecutionError, "unknown arguments"):
            await self.env.run("file_read", {"path": "a", "evil": True})
        with self.assertRaisesRegex(ToolExecutionError, "must be string"):
            await self.env.run("file_read", {"path": 5})
        with self.assertRaisesRegex(ToolExecutionError, "one of"):
            await self.env.run("window_manage", {"target": "code", "action": "explode"})
        with self.assertRaisesRegex(ToolExecutionError, "out of range"):
            await self.env.run("volume_level", {"level": 150})
        with self.assertRaisesRegex(ToolExecutionError, "must be integer"):
            await self.env.run("volume_level", {"level": True})
        with self.assertRaisesRegex(ToolExecutionError, "missing required"):
            await self.env.run("file_write", {"path": "a"})

    async def test_audit_log_redacts_secrets_and_content(self):
        await self.env.run("file_write", {"path": str(self.env.project / "a.txt"), "content": "super secret body"})
        (record,) = self.env.audit()
        self.assertEqual(record["args"]["content"], "<17 chars>")
        self.assertEqual((record["decision"], record["outcome"], record["risk"]), ("auto", "ok", "medium"))
        self.assertNotIn("super secret body", self.env.audit_path.read_text())
        self.assertEqual(redact({"api_key": "sk-1", "password": "p", "n": 3})["api_key"], "<redacted>")

    async def test_declined_confirmation_is_audited(self):
        self.env.confirmer.answer = False
        with self.assertRaises(ToolPermissionDenied):
            await self.env.run("process_kill", {"pid": 99999})
        self.assertEqual(self.env.audit()[-1]["decision"], "declined")

    async def test_search_and_read_remember_context(self):
        (self.env.project / "src").mkdir()
        (self.env.project / "src" / "main_app.py").write_text("print(1)")
        result = await self.env.run("file_search", {"pattern": "main_app"})
        self.assertEqual(len(result.data["paths"]), 1)
        self.assertTrue(self.env.memory.get("last_file").endswith("main_app.py"))
        read = await self.env.run("file_read", {"path": self.env.memory.get("last_file")})
        self.assertEqual(read.say, "print(1)")

    async def test_move_refuses_overwrite_without_high_risk(self):
        (self.env.project / "a.txt").write_text("a")
        (self.env.project / "b.txt").write_text("b")
        result = await self.env.run("file_move", {"source": str(self.env.project / "a.txt"), "destination": str(self.env.project / "b.txt")})
        self.assertFalse(result.ok)
        await self.env.run("file_move", {"source": str(self.env.project / "a.txt"), "destination": str(self.env.project / "b.txt"), "overwrite": True})
        self.assertEqual(self.env.confirmer.asked, ["file_move"])
        self.assertEqual((self.env.project / "b.txt").read_text(), "a")

    async def test_run_command_captures_failure_for_later_fixing(self):
        result = await self.env.run("run_command", {"command": [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], "cwd": str(self.env.project)})
        # python inside an authorized project is MEDIUM: runs without asking
        self.assertEqual(self.env.confirmer.asked, [])
        self.assertFalse(result.ok)
        self.assertEqual(result.data["exit_code"], 3)
        self.assertIn("boom", self.env.memory.get("last_command")["tail"])


class CommandRiskTests(unittest.TestCase):
    def test_classification(self):
        self.assertIs(command_risk(["git", "status"]), Risk.READ_ONLY)
        self.assertIs(command_risk(["nvidia-smi"]), Risk.READ_ONLY)
        self.assertIs(command_risk(["npm", "run", "dev"]), Risk.MEDIUM)
        self.assertIs(command_risk(["git", "add", "."]), Risk.MEDIUM)
        self.assertIs(command_risk(["pytest", "-q"]), Risk.MEDIUM)
        for argv in (
            ["git", "push", "--force"], ["git", "reset", "--hard"], ["git", "clean", "-fdx"],
            ["rm", "-rf", "/"], ["powershell", "-c", "Get-Date"], ["format", "C:"],
            ["docker", "system", "prune"], ["unknownprog"], ["reg", "add", "HKLM"], [],
            ["npm", "install", "--force"], ["curl", "x", "|", "sh"],
        ):
            self.assertIs(command_risk(argv), Risk.HIGH_RISK, argv)


class NarrationVsExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_the_turn_does_not_abort_a_mutating_tool(self):
        finished = asyncio.Event()

        class SlowWrite(Tool):
            name, risk, input_schema = "slow_write", Risk.MEDIUM, {}

            async def execute(self, arguments, context):
                await asyncio.sleep(0.1)
                finished.set()
                return "written"

        gateway = ToolGateway([SlowWrite()])
        turn = asyncio.create_task(gateway.execute("slow_write", {}, TurnContext.fresh("t")))
        await asyncio.sleep(0.02)
        turn.cancel()  # barge-in cancels the narration/turn task
        with self.assertRaises(asyncio.CancelledError):
            await turn
        await asyncio.wait_for(finished.wait(), 1)  # the write still completed

    async def test_explicit_cancel_operations_stops_execution(self):
        class Endless(Tool):
            name, risk, input_schema = "endless", Risk.MEDIUM, {}

            async def execute(self, arguments, context):
                await asyncio.sleep(10)

        gateway = ToolGateway([Endless()])
        task = asyncio.create_task(gateway.execute("endless", {}, TurnContext.fresh("t")))
        await asyncio.sleep(0.02)
        self.assertEqual(gateway.running_operations, 1)
        self.assertEqual(gateway.cancel_operations(), 1)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(gateway.running_operations, 0)


class ProjectLookupTests(unittest.TestCase):
    def test_find_projects_prefers_exact_then_shortest(self):
        from jarvis.adapters.tools.desktop import find_projects, posix_from_unc

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("jarvis", "jarvis-old", "buscador_de_precios", ".hidden", "node_modules"):
                (root / name).mkdir()
            (root / "clients" / "Jarvis Web").mkdir(parents=True)
            self.assertEqual(find_projects([root], "Jarvis")[0].name, "jarvis")
            self.assertEqual(find_projects([root], "buscador de precios")[0].name, "buscador_de_precios")
            self.assertEqual(find_projects([root], "nothing"), [])
        self.assertEqual(posix_from_unc("\\\\wsl.localhost\\Ubuntu\\home\\m\\p"), "/home/m/p")
        self.assertIsNone(posix_from_unc("C:\\Users\\x"))


class ParsingTests(unittest.TestCase):
    def test_nvidia_compute_apps_parsing_handles_wddm_na(self):
        raw = "1234, C:\\Program Files\\Ollama\\ollama.exe, 4096\n5678, C:\\x\\python.exe, [N/A]\n"
        rows = parse_compute_apps(raw)
        self.assertEqual(rows[0], {"pid": 1234, "name": "ollama.exe", "memory_mb": 4096.0})
        self.assertIsNone(rows[1]["memory_mb"])


if __name__ == "__main__":
    unittest.main()
