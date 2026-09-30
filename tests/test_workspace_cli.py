"""`jarvis workspace start|status|stop` through the CLI with a real (Linux) child process."""

import io
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps.cli import main


@unittest.skipIf(sys.platform == "win32", "uses a POSIX child and os.kill")
class WorkspaceCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.dir / "appdata")})
        env.start()
        self.addCleanup(env.stop)
        self.pids = set()
        self.addCleanup(self._kill_children)
        self.config = self.dir / "config.json"
        self.config.write_text(
            json.dumps({
                "runtime": {},
                "workspace": {
                    "default_profile": "p",
                    "profiles": {"p": {"tasks": [{
                        "name": "sleeper",
                        "command": [sys.executable, "-c", "import time; time.sleep(60)"],
                        "window": "hidden",
                        "timeout_seconds": 1,
                        "retries": 0,
                    }]}},
                },
            }),
            encoding="utf-8",
        )

    def _kill_children(self):
        # _terminate_tree needs psutil, which the Linux dev host may lack
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = main(["workspace", *args, "--config", str(self.config)], stdout=out, stderr=err)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_second_start_launches_nothing_and_stop_succeeds(self):
        code, first, _ = self.run_cli("start", "p")
        (task,) = first["tasks"]
        self.pids.add(task["pid"])
        self.assertEqual((code, task["name"], task["status"]), (0, "sleeper", "launched"))
        self.assertIsInstance(task["pid"], int)
        self.assertIn("total_ms", first)

        code, second, _ = self.run_cli("start", "p")
        (again,) = second["tasks"]
        self.assertEqual((code, again["status"], again["detail"], again["pid"]), (0, "already_running", "managed by Jarvis", task["pid"]))

        code, status, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertEqual(status["p"]["managed"]["sleeper"]["pid"], task["pid"])

        code, stopped, _ = self.run_cli("stop", "p")
        self.assertEqual(code, 0)
        self.assertEqual([t["name"] for t in stopped["tasks"]], ["sleeper"])


if __name__ == "__main__":
    unittest.main()
