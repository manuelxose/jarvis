"""Workspace profiles: parsing, dependencies, concurrency, duplicates, readiness, managed shutdown."""

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.application.workspace import WorkspaceManager, parse_profiles, summarize


class FakeProcess:
    def __init__(self, pid, code=None):
        self.pid = pid
        self._code = code

    def poll(self):
        return self._code


class World:
    """A fake desktop: which things are running, and a spawn log."""

    def __init__(self, start_delay=0.05, fail=(), exit_codes=None, launch_delay=0.0):
        self.running = set()
        self.spawned = []
        self.terminated = []
        self.closed = []
        self.start_delay = start_delay
        self.fail = set(fail)
        self.exit_codes = exit_codes or {}
        self.launch_delay = launch_delay
        self._pid = 1000

    def probe(self, target):
        return target in self.running

    def spawn(self, task, log_dir):
        if task.name in self.fail:
            raise OSError("not found")
        time.sleep(self.launch_delay)
        self._pid += 1
        self.spawned.append((task.name, time.monotonic()))
        if task.name not in self.exit_codes:
            loop_delay = self.start_delay
            target = (task.ready or task.detect).get("process")
            if target:
                import threading

                threading.Timer(loop_delay, lambda: self.running.add(target)).start()
        return FakeProcess(self._pid, self.exit_codes.get(task.name))

    def manager(self, profiles, tmp):
        return WorkspaceManager(
            parse_profiles(profiles),
            state_path=Path(tmp) / "state.json",
            log_dir=Path(tmp) / "logs",
            probes={"process": self.probe, "window_title": self.probe, "http": self.probe},
            spawn=self.spawn,
            open_url=lambda url: self.spawned.append((url, 0)),
            terminate=lambda pid, ct: self.terminated.append(pid) or True,
            close_windows=lambda title: self.closed.append(title) or 1,
            identity=lambda pid: 123.0,
            poll_seconds=0.01,
        )


def task(name, **extra):
    return {"name": name, "command": [name], "detect": {"process": f"{name}.exe"}, "timeout_seconds": 1, **extra}


DEV = {
    "dev": {
        "tasks": [
            task("ollama", required=True),
            task("backend", depends_on=["ollama"]),
            task("vscode", stop={"window_title": "jarvis - Visual Studio Code"}),
            task("terminal"),
            {"name": "docs", "url": "https://example.com/docs"},
        ]
    }
}


class ParseTests(unittest.TestCase):
    def test_rejects_cycles_unknown_deps_and_bad_fields(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            parse_profiles({"p": {"tasks": [task("a", depends_on=["b"]), task("b", depends_on=["a"])]}})
        with self.assertRaisesRegex(ValueError, "unknown"):
            parse_profiles({"p": {"tasks": [task("a", depends_on=["zzz"])]}})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            parse_profiles({"p": {"tasks": [{"name": "x"}]}})
        with self.assertRaisesRegex(ValueError, "probes"):
            parse_profiles({"p": {"tasks": [task("a", detect={"shell": "rm"})]}})
        with self.assertRaisesRegex(ValueError, "http"):
            parse_profiles({"p": {"tasks": [{"name": "u", "url": "file:///etc/passwd"}]}})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_profiles({"p": {"tasks": [task("a"), task("a")]}})


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_launches_everything_respecting_dependencies(self):
        world = World()
        with tempfile.TemporaryDirectory() as tmp:
            results = await world.manager(DEV, tmp).start("dev")
        status = {r.name: r.status for r in results}
        self.assertEqual(status, {"ollama": "launched", "backend": "launched", "vscode": "launched", "terminal": "launched", "docs": "opened"})
        order = [name for name, _ in world.spawned]
        self.assertLess(order.index("ollama"), order.index("backend"))

    async def test_independent_tasks_start_concurrently(self):
        world = World(start_delay=0.2, launch_delay=0.0)
        profiles = {"p": {"tasks": [task(f"app{i}") for i in range(5)]}}
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            results = await world.manager(profiles, tmp).start("p")
        self.assertTrue(all(r.status == "launched" for r in results))
        self.assertLess(time.monotonic() - started, 0.6)  # 5 x 0.2 s sequentially would be 1 s

    async def test_already_running_is_not_duplicated_or_managed(self):
        world = World()
        world.running = {"vscode.exe", "ollama.exe"}
        with tempfile.TemporaryDirectory() as tmp:
            manager = world.manager(DEV, tmp)
            results = await manager.start("dev")
            self.assertEqual({r.name: r.status for r in results}["vscode"], "already_running")
            self.assertNotIn("vscode", [n for n, _ in world.spawned])
            self.assertNotIn("vscode", manager.managed("dev"))
            # a second start launches nothing new
            world.spawned.clear()
            again = await manager.start("dev")
            self.assertEqual([n for n, _ in world.spawned], ["https://example.com/docs"])
            self.assertTrue(all(r.ok for r in again))

    async def test_missing_executable_fails_and_skips_dependents(self):
        world = World(fail={"ollama"})
        with tempfile.TemporaryDirectory() as tmp:
            results = {r.name: r for r in await world.manager(DEV, tmp).start("dev")}
        self.assertEqual(results["ollama"].status, "failed")
        self.assertEqual(results["backend"].status, "skipped")
        self.assertEqual(results["vscode"].status, "launched")
        self.assertIn("no he podido arrancar ollama, backend", summarize(list(results.values())))

    async def test_timeout_and_bounded_retries(self):
        world = World(exit_codes={"slow": None})  # never becomes ready, never exits
        profiles = {"p": {"tasks": [{"name": "slow", "command": ["slow"], "ready": {"process": "never.exe"}, "timeout_seconds": 0.05, "retries": 2}]}}
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            (result,) = await world.manager(profiles, tmp).start("p")
        self.assertEqual(result.status, "timeout")
        self.assertEqual(len(world.spawned), 3)  # 1 + 2 retries, no more
        self.assertLess(time.monotonic() - started, 5)

    async def test_nonzero_exit_is_a_failure(self):
        world = World(exit_codes={"crash": 3})
        profiles = {"p": {"tasks": [task("crash", retries=0)]}}
        with tempfile.TemporaryDirectory() as tmp:
            (result,) = await world.manager(profiles, tmp).start("p")
        self.assertEqual((result.status, result.detail), ("failed", "exited with code 3"))

    async def test_stop_only_touches_managed_tasks(self):
        world = World()
        world.running = {"terminal.exe"}  # owner opened it themselves
        with tempfile.TemporaryDirectory() as tmp:
            manager = world.manager(DEV, tmp)
            await manager.start("dev")
            # state survives a new manager (e.g. daemon restarted)
            results = {r.name: r for r in await world.manager(DEV, tmp).stop("dev")}
            self.assertEqual(results["terminal"].status, "skipped")
            self.assertEqual(results["vscode"].status, "stopped")
            self.assertEqual(world.closed, ["jarvis - Visual Studio Code"])  # graceful close
            self.assertEqual(len(world.terminated), 2)  # ollama + backend pids only
            self.assertEqual(manager.managed("dev"), {})

    async def test_force_stop_is_explicit(self):
        world = World()
        world.running = {"vscode.exe"}
        with tempfile.TemporaryDirectory() as tmp:
            manager = world.manager(DEV, tmp)
            await manager.start("dev")
            results = {r.name: r for r in await manager.stop("dev", force=True, only={"vscode"})}
        self.assertEqual(results["vscode"].status, "stopped")

    async def test_restart_single_task(self):
        world = World()
        with tempfile.TemporaryDirectory() as tmp:
            manager = world.manager(DEV, tmp)
            await manager.start("dev")
            world.running.discard("backend.exe")
            world.spawned.clear()
            results = await manager.restart("dev", "backend")
        self.assertEqual([r.status for r in results], ["launched"])
        self.assertEqual([n for n, _ in world.spawned], ["backend"])

    async def test_concurrent_starts_of_same_profile_do_not_duplicate(self):
        world = World()
        with tempfile.TemporaryDirectory() as tmp:
            manager = world.manager(DEV, tmp)
            await asyncio.gather(manager.start("dev"), manager.start("dev"))
        names = [n for n, _ in world.spawned if not n.startswith("http")]
        self.assertEqual(sorted(names), ["backend", "ollama", "terminal", "vscode"])

    async def test_unknown_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unknown workspace profile"):
                await World().manager(DEV, tmp).start("nope")


if __name__ == "__main__":
    unittest.main()
