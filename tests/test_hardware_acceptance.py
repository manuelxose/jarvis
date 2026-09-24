"""Hardware-acceptance harness test.

Proves the scripted wake-word -> command -> real ``file`` tool -> spoken
response path through the real ``build_runtime``/``VoiceLoop`` entrypoints (with
deterministic fake adapters). The scripted STT utters ``lee el archivo notas.txt``,
which routes to the ``file`` tool, reads a real file from the working directory,
and the result is spoken back through the playback queue.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jarvis.application.demo import NOTAS_CONTENT, run_hardware_acceptance
from committed_config import load_committed_config


class HardwareAcceptanceTests(unittest.TestCase):
    def test_scripted_run_reads_real_file_and_speaks_response(self):
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

        result = run_hardware_acceptance(config)

        self.assertEqual("ready", result["startup_state"])
        self.assertEqual("stopping", result["shutdown_state"])

        turns = result["turns"]
        self.assertEqual(1, len(turns))
        turn = turns[0]
        self.assertEqual("fast_command", turn["route"])
        self.assertEqual(NOTAS_CONTENT, turn["response"])
        self.assertFalse(turn["cancelled"])

        self.assertGreaterEqual(result["audio_queue"]["played"], 1)
        self.assertEqual(1, result["voice_loop"]["turns"])


if __name__ == "__main__":
    unittest.main()
