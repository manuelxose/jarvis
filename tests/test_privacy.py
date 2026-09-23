import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PRIVATE = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".pt", ".npy")


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class NoPersonalAudioInGitTests(unittest.TestCase):
    def test_no_voice_recordings_or_conditioning_are_tracked(self):
        files = subprocess.run(
            ["git", "ls-files", "--cached"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        tracked = [f for f in files if f.lower().endswith(_PRIVATE)]
        self.assertEqual([], tracked, "personal audio/voice conditioning must stay out of Git")

    def test_local_secret_override_is_ignored(self):
        result = subprocess.run(["git", "check-ignore", "-q", "config.local.json"], cwd=ROOT, check=False)
        self.assertEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
