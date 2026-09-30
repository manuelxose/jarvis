"""Risk authorization as composed by the real runtime (_build_tools + _register_desktop_tools + VoiceConfirmer)."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.desktop import DesktopContext
from jarvis.adapters.tools.gateway import Risk
from jarvis.application.planner import VoiceConfirmer
from jarvis.application.runtime import _build_tools, _register_desktop_tools
from jarvis.config import load_config
from jarvis.core.errors import ToolPermissionDenied
from jarvis.core.turn import TurnContext


class _Hermes:
    async def start(self):
        pass

    async def stop(self):
        pass


class DesktopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.outside = root / "outside"
        self.outside.mkdir()
        env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root / "appdata")})
        env.start()
        self.addCleanup(env.stop)
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "runtime": {},
                    "desktop": {"authorized_scopes": [str(self.project)], "trusted_operations": ["file_write"]},
                }
            ),
            encoding="utf-8",
        )
        self.config = load_config(config_path, {})
        self.audit_path = root / "appdata" / "jarvis" / "audit.jsonl"
        self.confirmer = VoiceConfirmer(lambda name, args: self.tools.describe(name, args), timeout_seconds=0.2)
        self.tools = _build_tools(self.config, confirmer=self.confirmer)
        _register_desktop_tools(self.config, self.tools, DesktopContext(root / "ctx.json"), None, hermes=_Hermes())
        self.spoken: list[str] = []

        async def speak(text, context):
            self.spoken.append(text)

        self.confirmer.speak = speak

    def audit(self):
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]

    def start(self, name, args, origin="owner"):
        return asyncio.ensure_future(self.tools.execute(name, args, TurnContext.fresh("t"), origin=origin))

    async def until_waiting(self, task):
        for _ in range(200):
            if self.confirmer.waiting or task.done():
                break
            await asyncio.sleep(0.005)
        self.assertTrue(self.confirmer.waiting, "gateway never asked for confirmation")

    def make_file(self, folder, name="doomed.txt"):
        path = folder / name
        path.write_text("data", encoding="utf-8")
        return path

    async def test_registry_exposes_desktop_tools_with_schema_and_risk(self):
        expected = {
            "open_application", "window_focus", "app_close", "volume_set", "volume_level",
            "system_stats", "gpu_processes", "file_delete", "process_kill",
        }
        self.assertLessEqual(expected, set(self.tools.names))
        for tool in self.tools.tools():
            self.assertIsInstance(tool.input_schema, dict, tool.name)
            self.assertIsInstance(tool.risk, Risk, tool.name)

    async def test_recycle_delete_in_scope_runs_without_speaking(self):
        target = self.make_file(self.project)
        result = await self.tools.execute("file_delete", {"path": str(target)}, TurnContext.fresh("t"))
        self.assertTrue(result.ok)
        self.assertFalse(target.exists())
        self.assertEqual([], self.spoken)
        entry = self.audit()[-1]
        self.assertEqual(("medium", "auto", "ok", "owner"), (entry["risk"], entry["decision"], entry["outcome"], entry["origin"]))

    async def test_permanent_delete_asks_every_time_and_removes_after_confirmo(self):
        for name in ("one.txt", "two.txt"):
            target = self.make_file(self.project, name)
            self.spoken.clear()
            task = self.start("file_delete", {"path": str(target), "permanent": True})
            await self.until_waiting(task)
            self.assertTrue(target.exists())
            self.assertIn(target.name, self.spoken[0])  # spoken as name + folder, not the full path
            self.confirmer.answer("confirmo")
            result = await task
            self.assertTrue(result.ok)
            self.assertFalse(target.exists())
        entries = [e for e in self.audit() if e["tool"] == "file_delete"]
        self.assertEqual(["confirmed", "confirmed"], [e["decision"] for e in entries])
        self.assertEqual({"high_risk"}, {e["risk"] for e in entries})

    async def test_declined_permanent_delete_keeps_file_and_audits_decline(self):
        target = self.make_file(self.project)
        task = self.start("file_delete", {"path": str(target), "permanent": True})
        await self.until_waiting(task)
        self.confirmer.answer("no")
        with self.assertRaises(ToolPermissionDenied):
            await task
        self.assertTrue(target.exists())
        entry = self.audit()[-1]
        self.assertEqual(("file_delete", "declined", "high_risk"), (entry["tool"], entry["decision"], entry["risk"]))
        self.assertIn("Entendido, no lo hago.", self.spoken)

    async def test_confirmation_timeout_denies_and_keeps_file(self):
        target = self.make_file(self.project)
        with self.assertRaises(ToolPermissionDenied):
            await self.tools.execute("file_delete", {"path": str(target), "permanent": True}, TurnContext.fresh("t"))
        self.assertTrue(target.exists())
        self.assertTrue(any("No he oído confirmación" in text for text in self.spoken))
        self.assertEqual("declined", self.audit()[-1]["decision"])

    async def test_medium_write_outside_scope_asks(self):
        target = self.outside / "note.txt"
        task = self.start("file_write", {"path": str(target), "content": "x"}, origin="agent")
        await self.until_waiting(task)
        self.confirmer.answer("no")
        with self.assertRaises(ToolPermissionDenied):
            await task
        self.assertFalse(target.exists())

    async def test_agent_origin_never_inherits_trusted_operations(self):
        target = self.outside / "agent.txt"
        task = self.start("file_write", {"path": str(target), "content": "x"}, origin="agent")
        await self.until_waiting(task)
        self.confirmer.answer("confirmo")
        await task
        self.assertTrue(target.exists())
        entry = self.audit()[-1]
        self.assertEqual(("agent", "confirmed"), (entry["origin"], entry["decision"]))

    async def test_owner_trusted_write_outside_scope_runs_without_asking(self):
        target = self.outside / "owner.txt"
        result = await self.tools.execute("file_write", {"path": str(target), "content": "x"}, TurnContext.fresh("t"))
        self.assertTrue(result.ok)
        self.assertEqual([], self.spoken)
        self.assertEqual("auto", self.audit()[-1]["decision"])

    async def test_audit_redacts_file_content(self):
        target = self.project / "secret-plan.txt"
        await self.tools.execute("file_write", {"path": str(target), "content": "top secret text"}, TurnContext.fresh("t"))
        raw = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("top secret text", raw)
        self.assertEqual("<15 chars>", self.audit()[-1]["args"]["content"])


if __name__ == "__main__":
    unittest.main()
