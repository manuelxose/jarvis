"""Integration test for the voice loop wired into the fake runtime.

Proves that ``build_runtime(config, use_fakes=True)`` composes VAD and the
``VoiceLoop`` and that ``run_until_stopped()`` drives one
deterministic turn end-to-end before the scripted audio source exhausts.
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jarvis.application.runtime import build_runtime
import sys
sys.path.insert(0, str(Path(__file__).parent))  # standalone `-m unittest tests.X` runs
from committed_config import load_committed_config


class RuntimeVoiceLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_runtime_completes_one_deterministic_turn(self):
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

        runtime = build_runtime(config, use_fakes=True)
        await runtime.run_until_stopped()

        self.assertEqual(1, runtime.voice_loop.state()["turns"])
        self.assertGreaterEqual(runtime.components.audio_output.state()["played"], 1)
        self.assertEqual("fast_model", runtime.voice_loop.turns[0].route)
        self.assertEqual("hola", runtime.voice_loop.turns[0].transcript)

    def test_real_runtime_wires_echo_guard_without_barge_in(self):
        # use_fakes=True builds VoiceLoop with its bare defaults (no echo tail,
        # barge-in on) rather than runtime.py's audio.barge_in-derived wiring,
        # so only the real build path proves the echo guard is actually wired.
        config = load_committed_config()
        runtime = build_runtime(config, use_fakes=False)

        self.assertFalse(config.audio.barge_in)
        self.assertGreater(runtime.voice_loop.echo_tail_frames, 0)
        self.assertEqual(0, runtime.voice_loop.barge_in_frames)


if __name__ == "__main__":
    unittest.main()
