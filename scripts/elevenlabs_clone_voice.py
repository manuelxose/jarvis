"""One-time ElevenLabs Instant Voice Cloning setup.

Uploads the WAV samples in ``voice_samples/`` to ElevenLabs and prints the
resulting ``voice_id``. Run this once; paste the printed ``voice_id`` into
``config.local.json``'s ``tts.voice`` field to enable the ElevenLabs TTS
adapter (``tts.provider: "elevenlabs"``). Never re-run this per session: each
run creates a new cloned voice in your ElevenLabs account.

Usage (PowerShell):
    $env:ELEVENLABS_API_KEY = "sk-..."
    .\\.venv\\Scripts\\python.exe scripts\\elevenlabs_clone_voice.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_ADD_VOICE_URL = "https://api.elevenlabs.io/v1/voices/add"


def _build_multipart_body(name: str, wav_paths: list[Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    def _field(field_name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"\r\n\r\n{value}\r\n'.encode(
                "utf-8"
            )
        )

    _field("name", name)
    for wav_path in wav_paths:
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                f'filename="{wav_path.name}"\r\nContent-Type: audio/wav\r\n\r\n'
            ).encode("utf-8")
        )
        parts.append(wav_path.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def clone_voice(api_key: str, name: str, wav_paths: list[Path]) -> dict:
    body, content_type = _build_multipart_body(name, wav_paths)
    request = urllib.request.Request(_ADD_VOICE_URL, data=body, method="POST")
    request.add_header("xi-api-key", api_key)
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(500).decode("utf-8", "replace")
        raise SystemExit(f"ElevenLabs rejected the request ({error.code}): {detail}")
    except urllib.error.URLError as error:
        raise SystemExit(f"Could not reach ElevenLabs: {error.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples-dir",
        default=str(Path(__file__).resolve().parent.parent / "voice_samples"),
        help="Directory of sample*.wav files to clone from (default: voice_samples/).",
    )
    parser.add_argument("--name", default="jarvis-owner-voice", help="Name for the cloned voice.")
    args = parser.parse_args(argv)

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ELEVENLABS_API_KEY is not set.", file=sys.stderr)
        return 1

    samples_dir = Path(args.samples_dir)
    wav_paths = sorted(samples_dir.glob("*.wav"))
    if not wav_paths:
        print(f"No .wav files found in {samples_dir}.", file=sys.stderr)
        return 1

    print(f"Uploading {len(wav_paths)} sample(s) from {samples_dir}...")
    result = clone_voice(api_key, args.name, wav_paths)
    voice_id = result.get("voice_id")
    if not voice_id:
        print(f"Unexpected response, no voice_id: {result}", file=sys.stderr)
        return 1

    print(f"voice_id: {voice_id}")
    print("Add this to config.local.json:")
    print(
        json.dumps(
            {"tts": {"provider": "elevenlabs", "voice": voice_id, "api_key": "${ELEVENLABS_API_KEY}"}},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
