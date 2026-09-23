"""One-time Alibaba Model Studio voice cloning provisioning.

Validates a reference WAV sample, uploads it, and registers a cloned voice
with Alibaba Cloud Model Studio. Voice cloning is a provisioning step, not
something that runs on every startup -- run this once, then paste the printed
``voice_id`` into ``config.local.json``.

Usage (PowerShell):
    $env:DASHSCOPE_API_KEY = "sk-..."
    $env:ALIBABA_MODEL_STUDIO_WORKSPACE_ID = "ws-..."   # required for --region singapore
    .\\.venv\\Scripts\\python.exe scripts\\alibaba_voice_clone.py create voice_samples\\sample.wav
    .\\.venv\\Scripts\\python.exe scripts\\alibaba_voice_clone.py status <voice_id>
    .\\.venv\\Scripts\\python.exe scripts\\alibaba_voice_clone.py test <voice_id>

The cloned voice can only be used for synthesis with the same
``--target-model`` it was created with (Alibaba's own constraint, not ours):
whatever model you pass to ``create`` must match ``alibaba.tts_model`` in
config.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import json
import os
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import List

_DEFAULT_TARGET_MODEL = "qwen-audio-3.0-tts-flash"  # documented Spanish + cloning support
_REGION_CODES = {"singapore": "ap-southeast-1", "beijing": "cn-beijing"}


def validate_reference_audio(path: Path) -> List[str]:
    """Return a list of problems with the reference sample; empty means it looks usable."""
    problems: List[str] = []
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            n_frames = wav_file.getnframes()
            frames = wav_file.readframes(n_frames)
    except wave.Error as error:
        return [f"not a readable WAV file: {error}"]

    duration = n_frames / float(sample_rate) if sample_rate else 0.0
    if channels != 1:
        problems.append(f"expected mono audio, got {channels} channel(s)")
    if sample_rate < 16000:
        problems.append(f"sample rate is {sample_rate}Hz; use at least 16000Hz")
    if sample_width != 2:
        problems.append(f"expected 16-bit PCM, got {sample_width * 8}-bit")
    if duration < 3.0:
        problems.append(f"reference audio is only {duration:.1f}s; use at least 3s")
    if duration > 60.0:
        problems.append(f"reference audio is {duration:.1f}s; keep it under 60s")

    if sample_width == 2 and frames:
        max_possible = 2 ** (sample_width * 8 - 1) - 1
        peak = audioop.max(frames, sample_width)
        if peak >= max_possible * 0.99:
            problems.append("audio appears clipped (peak amplitude near maximum)")
        rms = audioop.rms(frames, sample_width)
        if rms < max_possible * 0.01:
            problems.append("audio signal level is very low (near silence)")

        window_bytes = int(sample_rate * 0.02) * sample_width
        if window_bytes and len(frames) >= window_bytes:
            total_windows = 0
            silent_windows = 0
            for offset in range(0, len(frames) - window_bytes, window_bytes):
                window = frames[offset : offset + window_bytes]
                total_windows += 1
                if audioop.rms(window, sample_width) < max_possible * 0.02:
                    silent_windows += 1
            if total_windows and silent_windows / total_windows > 0.5:
                problems.append(
                    f"{silent_windows / total_windows:.0%} of the sample is near-silent"
                )

    return problems


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"{name} is not set.", file=sys.stderr)
        raise SystemExit(1)
    return value


def _configure_dashscope(region: str, workspace_id: str, api_key: str):
    import dashscope  # noqa: PLC0415

    dashscope.api_key = api_key
    region_code = _REGION_CODES[region]
    if region_code != "cn-beijing":
        if not workspace_id:
            print(
                "ALIBABA_MODEL_STUDIO_WORKSPACE_ID is required for --region singapore.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        dashscope.set_region(region=region_code, workspace_id=workspace_id)
    return dashscope


def cmd_create(args: argparse.Namespace) -> int:
    api_key = _require_env("DASHSCOPE_API_KEY")
    workspace_id = os.environ.get("ALIBABA_MODEL_STUDIO_WORKSPACE_ID", "")
    reference_path = Path(args.reference_audio)
    if not reference_path.is_file():
        print(f"No such file: {reference_path}", file=sys.stderr)
        return 1

    problems = validate_reference_audio(reference_path)
    if problems:
        print("Reference audio looks unusable for cloning:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        if not args.force:
            print("Re-run with --force to upload anyway.", file=sys.stderr)
            return 1
        print("Continuing anyway (--force).", file=sys.stderr)

    _configure_dashscope(args.region, workspace_id, api_key)
    from dashscope.audio.tts_v2 import VoiceEnrollmentException, VoiceEnrollmentService  # noqa: PLC0415
    from dashscope.utils.oss_utils import OssUtils  # noqa: PLC0415

    print(f"Uploading {reference_path}...")
    file_url, _ = OssUtils.upload(model=args.target_model, file_path=str(reference_path), api_key=api_key)

    service = VoiceEnrollmentService(api_key=api_key, workspace=workspace_id or None)
    try:
        voice_id = service.create_voice(
            target_model=args.target_model,
            prefix=args.prefix,
            url=file_url,
            language_hints=[args.language],
        )
    except VoiceEnrollmentException as error:
        print(f"Alibaba rejected the request: {error}", file=sys.stderr)
        return 1

    print(f"voice_id: {voice_id}")
    print("Add this to config.local.json:")
    print(
        json.dumps(
            {
                "tts": {"provider": "alibaba_qwen", "voice": voice_id, "api_key": "${DASHSCOPE_API_KEY}"},
                "alibaba": {
                    "region": args.region,
                    "workspace_id": "${ALIBABA_MODEL_STUDIO_WORKSPACE_ID}" if workspace_id else None,
                    "tts_model": args.target_model,
                },
            },
            indent=2,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    api_key = _require_env("DASHSCOPE_API_KEY")
    workspace_id = os.environ.get("ALIBABA_MODEL_STUDIO_WORKSPACE_ID", "")
    _configure_dashscope(args.region, workspace_id, api_key)
    from dashscope.audio.tts_v2 import VoiceEnrollmentException, VoiceEnrollmentService  # noqa: PLC0415

    service = VoiceEnrollmentService(api_key=api_key, workspace=workspace_id or None)
    try:
        details = service.query_voice(args.voice_id)
    except VoiceEnrollmentException as error:
        print(f"Alibaba rejected the request: {error}", file=sys.stderr)
        return 1
    print(json.dumps(details, indent=2, ensure_ascii=False))
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    api_key = _require_env("DASHSCOPE_API_KEY")
    workspace_id = os.environ.get("ALIBABA_MODEL_STUDIO_WORKSPACE_ID", "")

    from jarvis.adapters.tts.alibaba_qwen import AlibabaQwenTTS  # noqa: PLC0415
    from jarvis.core.turn import TurnContext  # noqa: PLC0415

    fake_config = SimpleNamespace(alibaba=SimpleNamespace(region=args.region, workspace_id=workspace_id))
    adapter = AlibabaQwenTTS(
        api_key=api_key,
        model=args.target_model,
        voice_id=args.voice_id,
        language=args.language,
        config=fake_config,
    )

    async def _run() -> bytes:
        async def _one_chunk():
            yield args.phrase

        context = TurnContext.fresh("voice-clone-test")
        chunks = [chunk async for chunk in adapter.synthesize(_one_chunk(), context)]
        return b"".join(chunks)

    audio = asyncio.run(_run())
    if not audio:
        print("No audio was returned.", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)  # AudioFormat.PCM_24000HZ_MONO_16BIT
        wav_file.writeframes(audio)
    print(f"Wrote {len(audio)} bytes of PCM audio to {out_path}")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", choices=("singapore", "beijing"), default="singapore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Validate, upload, and register a cloned voice.")
    create.add_argument("reference_audio", help="Path to a mono 16-bit WAV reference sample (3-60s).")
    create.add_argument("--target-model", default=_DEFAULT_TARGET_MODEL, help="Model the voice will be bound to.")
    create.add_argument("--prefix", default="jarvis", help="Voice name prefix (digits/lowercase letters, <10 chars).")
    create.add_argument("--language", default="es", help="Primary language hint for the cloned voice.")
    create.add_argument("--force", action="store_true", help="Upload even if reference-audio validation fails.")
    create.set_defaults(func=cmd_create)

    status = subparsers.add_parser("status", help="Query a previously created voice_id.")
    status.add_argument("voice_id")
    status.set_defaults(func=cmd_status)

    test = subparsers.add_parser("test", help="Synthesize a short phrase with a voice_id and save it as WAV.")
    test.add_argument("voice_id")
    test.add_argument("--target-model", default=_DEFAULT_TARGET_MODEL, help="Must match the model used at creation.")
    test.add_argument("--language", default="es")
    test.add_argument("--phrase", default="Hola, soy Jarvis.")
    test.add_argument("--output", default="voice_clone_test.wav")
    test.set_defaults(func=cmd_test)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
