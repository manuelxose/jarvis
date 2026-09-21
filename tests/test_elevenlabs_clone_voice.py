"""Deterministic coverage for the ElevenLabs voice-cloning provisioning script."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

import elevenlabs_clone_voice as clone_script  # noqa: E402


class BuildMultipartBodyTests(unittest.TestCase):
    def test_body_contains_name_field_and_each_wav_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "sample1.wav"
            wav_path.write_bytes(b"RIFF....WAVEfmt ")

            body, content_type = clone_script._build_multipart_body("my-voice", [wav_path])

        self.assertIn("multipart/form-data; boundary=", content_type)
        boundary = content_type.split("boundary=")[1]
        self.assertIn(boundary.encode(), body)
        self.assertIn(b'name="name"', body)
        self.assertIn(b"my-voice", body)
        self.assertIn(b'name="files"; filename="sample1.wav"', body)
        self.assertIn(b"RIFF....WAVEfmt ", body)


class MainTests(unittest.TestCase):
    def test_main_fails_fast_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(1, clone_script.main(["--samples-dir", "/nonexistent"]))

    def test_main_fails_fast_without_wav_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "secret"}, clear=True):
                self.assertEqual(1, clone_script.main(["--samples-dir", tmp]))

    def test_main_prints_the_voice_id_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sample1.wav").write_bytes(b"RIFF....WAVEfmt ")
            with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "secret"}, clear=True), patch.object(
                clone_script, "clone_voice", return_value={"voice_id": "cloned-id"}
            ):
                self.assertEqual(0, clone_script.main(["--samples-dir", tmp]))


if __name__ == "__main__":
    unittest.main()
