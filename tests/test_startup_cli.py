"""`jarvis startup --use-fakes`: the foreground sequence through the CLI, without an audio device."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps.cli import main


class StartupCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        modules = mock.patch.dict(sys.modules, {"sounddevice": None, "jarvis.adapters.audio.mixer": None})
        modules.start()
        self.addCleanup(modules.stop)
        self.config = self.dir / "config.json"
        self.config.write_text(json.dumps({"runtime": {}}), encoding="utf-8")

    def run_cli(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(["startup", "--use-fakes", "--config", str(self.config)], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_reports_phase_and_timings_without_touching_audio(self):
        code, out, err = self.run_cli()
        report = json.loads(out)
        self.assertEqual(report["trigger"], "cli")
        self.assertIn(report["phase"], ("ready", "degraded", "failed"))
        self.assertEqual(code, 0 if report["phase"] == "ready" else 1)
        for key in ("first_sound", "music_started", "services_ready", "welcome_spoken"):
            self.assertIn(key, report["timings_ms"])
        self.assertLess(report["timings_ms"]["first_sound"], report["timings_ms"]["welcome_spoken"])
        self.assertTrue(report["welcome"])
        self.assertEqual(report["music"], "none")
        self.assertNotIn("mixer unavailable", err)  # fakes never try the mixer

    def test_ready_only_when_every_essential_component_is_healthy(self):
        code, out, _ = self.run_cli()
        report = json.loads(out)
        if report["phase"] == "ready":
            self.assertEqual((code, report["issues"]), (0, []))
        else:
            self.assertEqual(code, 1)
            self.assertNotIn("operativos", report["welcome"])


if __name__ == "__main__":
    unittest.main()
