import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import voice_profile as vp

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

    def test_personal_audio_patterns_are_git_ignored(self):
        candidates = (
            "voice_samples/owner.wav",
            "owner.mp3",
            "owner.flac",
            "owner.m4a",
            "owner.ogg",
            "prompt.pt",
            "embedding.npy",
            "default-recording.wav",
        )
        for rel_path in candidates:
            with self.subTest(path=rel_path):
                result = subprocess.run(["git", "check-ignore", "-q", rel_path], cwd=ROOT, check=False)
                self.assertEqual(0, result.returncode, f"{rel_path} is not git-ignored")


class ProfileDirOutsideRepoTests(unittest.TestCase):
    def test_default_profile_dir_is_outside_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                profile_dir = vp.default_profile_dir()
        self.assertNotIn(ROOT, (profile_dir, *profile_dir.parents))


if __name__ == "__main__":
    unittest.main()
