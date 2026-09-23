"""One-time generation of the fast-command acknowledgement audio cache.

Synthesizes every fixed acknowledgement string in
``jarvis.application.turn_manager._COMMAND_ACKS`` through the configured TTS
provider and writes the resulting audio to ``cache/tts_acks/``. Run this once
per configured voice (or whenever the ack text or voice changes); the fast
command path then plays these files directly and never calls the TTS provider
for a known acknowledgement.

Usage (PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\generate_ack_cache.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from jarvis.adapters.tts.ack_cache import cache_key  # noqa: E402
from jarvis.adapters.tts.resolve import resolve_tts  # noqa: E402
from jarvis.application.turn_manager import _COMMAND_ACKS  # noqa: E402
from jarvis.config import load_config  # noqa: E402
from jarvis.core.turn import TurnContext  # noqa: E402


async def _one_shot(text: str) -> AsyncIterator[str]:
    yield text


async def _synthesize(tts, text: str, context: TurnContext) -> bytes:
    chunks = [chunk async for chunk in tts.synthesize(_one_shot(text), context)]
    return b"".join(chunks)


async def _generate(tts, texts: list[str], out_dir: Path, extension: str) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for text in texts:
        context = TurnContext.fresh("ack-cache-generation")
        data = await _synthesize(tts, text, context)
        path = out_dir / f"{cache_key(text)}.{extension}"
        path.write_bytes(data)
        written.append(f"{text!r} -> {path.name}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json", help="path to config.json")
    parser.add_argument(
        "--out-dir", default="cache/tts_acks", help="directory to write cached ack audio into"
    )
    parser.add_argument("--extension", default="wav", help="audio file extension (default: wav)")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    tts = resolve_tts(config)

    texts = sorted({text for text in _COMMAND_ACKS.values() if text})
    if not texts:
        print("No non-empty acknowledgement strings configured.", file=sys.stderr)
        return 1

    written = asyncio.run(_generate(tts, texts, Path(args.out_dir), args.extension))
    print(f"Wrote {len(written)} cached acknowledgement(s) to {args.out_dir}:")
    for line in written:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
