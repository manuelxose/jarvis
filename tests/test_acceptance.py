import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jarvis.application.demo import run_demo
from committed_config import load_committed_config


class HeadlessAcceptanceTests(unittest.TestCase):
    def test_demo_meets_headless_acceptance_matrix(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        config = load_committed_config()
        config = replace(
            config,
            memory=replace(
                config.memory,
                db_path=str(Path(temporary_directory.name) / "jarvis.db"),
            ),
        )

        result = run_demo(config)

        self.assertEqual("ready", result["startup_state"])
        self.assertEqual("stopping", result["shutdown_state"])

        turns = result["turns"]
        self.assertEqual(4, len(turns))
        self.assertEqual(
            ["fast_command", "fast_command", "fast_model", "hermes"],
            [turn["route"] for turn in turns],
        )
        for turn in turns:
            self.assertIsInstance(turn["response"], str)
            self.assertTrue(turn["response"])
            self.assertFalse(turn["cancelled"])

        self.assertIn("El usuario se llama Manuel", result["memory_recall"])

        health = result["health"]
        self.assertIsInstance(health, dict)
        self.assertEqual(10, len(health))
        self.assertTrue(
            all(component["status"] == "healthy" for component in health.values())
        )


if __name__ == "__main__":
    unittest.main()
