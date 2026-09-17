"""Integration test for the voice loop wired into the fake runtime.

Proves that ``build_runtime(config, use_fakes=True)`` composes the wake
detector, VAD, and ``VoiceLoop`` and that ``run_until_stopped()`` drives one
deterministic turn end-to-end before the scripted audio source exhausts.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jarvis.application.runtime import build_runtime
from jarvis.config import load_config


class RuntimeVoiceLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_runtime_completes_one_deterministic_turn(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        config = load_config("config.json")
        config = replace(
            config,
            memory=replace(
                config.memory,
                db_path=str(Path(temporary_directory.name) / "jarvis.db"),
            ),
        )

        runtime = build_runtime(config, use_fakes=True)
        await runtime.run_until_stopped()

        self.assertEqual(1, runtime.voice_loop.state()["turns"])
        self.assertGreaterEqual(runtime.components.audio_output.state()["played"], 1)
        self.assertEqual("fast_model", runtime.voice_loop.turns[0].route)


if __name__ == "__main__":
    unittest.main()
