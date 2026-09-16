"""Foundation CLI diagnostics tests."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
import unittest

from jarvis.apps.cli import main


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


if __name__ == "__main__":
    unittest.main()
