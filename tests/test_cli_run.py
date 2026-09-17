"""CLI ``run`` acceptance test: the fake voice loop completes one turn end-to-end.

The milestone demo is ``jarvis run --use-fakes`` driving a full deterministic
turn through the CLI entrypoint. This test invokes ``main`` directly with a
minimal ``{"runtime": {}}`` config and asserts the exit code is 0, the report
shows ``ready``, and the ``voice_loop`` line proves exactly one turn completed.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
import unittest

from jarvis.apps.cli import main


class CliRunTests(unittest.TestCase):
    def test_run_use_fakes_completes_one_deterministic_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"runtime": {}}', encoding="utf-8")

            stdout = io.StringIO()
            result = main(
                ["run", "--use-fakes", "--config", str(config_path)],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        output = stdout.getvalue()
        self.assertEqual(0, result)
        self.assertIn("ready", output)
        self.assertIn("turns=1", output)


if __name__ == "__main__":
    unittest.main()
