"""Foundation CLI diagnostics tests."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from jarvis.apps.cli import main
from jarvis.apps.runtime import FoundationRuntime
from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.lifecycle import Supervisor
from jarvis.observability import InteractionTrace


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path.write_text('{"runtime": {}}', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_help_lists_run_and_doctor(self) -> None:
        stdout = io.StringIO()

        result = main(["--help"], stdout=stdout, stderr=io.StringIO())

        self.assertEqual(0, result)
        self.assertIn("run", stdout.getvalue())
        self.assertIn("doctor", stdout.getvalue())

    def test_doctor_json_reports_state_and_component_health(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["doctor", "--config", str(self.config_path), "--json"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        report = json.loads(stdout.getvalue())
        self.assertEqual(1, result)
        self.assertEqual("degraded", report["state"])
        self.assertIn("audio input", report["components"])
        self.assertEqual("degraded", report["components"]["audio input"]["status"])
        self.assertIn("unavailable", report["components"]["audio input"]["detail"])

    def test_invalid_config_returns_actionable_error(self) -> None:
        invalid_path = Path(self.temp_dir.name) / "invalid.json"
        invalid_path.write_text('{"runtime": {"command_deadline_ms": 0}}', encoding="utf-8")
        stderr = io.StringIO()

        result = main(
            ["doctor", "--config", str(invalid_path)],
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(2, result)
        self.assertIn("invalid configuration", stderr.getvalue())
        self.assertIn("command_deadline_ms", stderr.getvalue())

    def test_check_only_returns_nonzero_without_voice_adapters(self) -> None:
        stdout = io.StringIO()

        result = main(
            ["run", "--config", str(self.config_path), "--check-only"],
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(1, result)
        self.assertIn("degraded", stdout.getvalue())

    def test_shutdown_failure_is_in_final_report_and_exit_status(self):
        class FailsOnStop:
            name = "required cleanup"
            required = True

            async def start(self):
                pass

            async def health(self):
                return HealthReport(self.name, HealthStatus.HEALTHY)

            async def stop(self):
                raise RuntimeError("release failed")

        for args in (["doctor", "--json"], ["run", "--check-only"], ["run"]):
            with self.subTest(args=args):
                supervisor = Supervisor([FailsOnStop()])
                runtime = FoundationRuntime(None, supervisor, InteractionTrace("shutdown"))
                async def run_until_stopped():
                    await supervisor.stop()
                stdout = io.StringIO()
                with patch("jarvis.apps.cli.create_foundation_runtime", return_value=runtime), patch.object(
                    supervisor, "run_until_stopped", side_effect=run_until_stopped
                ):
                    result = main([*args, "--config", str(self.config_path)], stdout=stdout, stderr=io.StringIO())
                self.assertEqual(result, 1)
                self.assertIn("failed", stdout.getvalue())
                self.assertIn("release failed", stdout.getvalue())

    def test_healthy_doctor_still_returns_success_after_normal_shutdown(self):
        runtime = FoundationRuntime(None, Supervisor([]), InteractionTrace("healthy"))
        stdout = io.StringIO()
        with patch("jarvis.apps.cli.create_foundation_runtime", return_value=runtime):
            result = main(["doctor", "--json", "--config", str(self.config_path)],
                          stdout=stdout, stderr=io.StringIO())
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["state"], "ready")


if __name__ == "__main__":
    unittest.main()
