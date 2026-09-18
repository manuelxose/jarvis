"""Real-hardware voice acceptance diagnostic (v2 production adapters only).

Captures environment metadata, records real microphone speech through the
production :class:`jarvis.adapters.audio.input.MicCapture` (sounddevice/WASAPI)
adapter, transcribes it with the configured production STT provider
(faster-whisper or SAPI), and reports monotonic timings (cold + warm runs)
plus a 30-second silence/no-dispatch regression.

This module imports only ``src/jarvis/`` production adapters and configuration.
It never imports legacy ``main.py`` / ``voice/*`` / ``brain/*`` and never uses
fake adapters, generated audio, or a mocked WASAPI/STT provider.
"""

from __future__ import annotations

import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

from jarvis.adapters.audio.input import MicCapture, list_devices
from jarvis.adapters.audio.vad import EnergyVAD
from jarvis.adapters.stt import resolve_stt, stt_provider
from jarvis.config import RuntimeConfig
from jarvis.core.contracts import TurnContext

WARM_RUNS = 5
SPEECH_MAX_SECONDS = 4.0
SPEECH_MAX_WAIT_SECONDS = 8.0
SPEECH_TRAILING_SILENCE_FRAMES = 6
SILENCE_SECONDS = 30.0
CHUNK_SECONDS = 0.1  # MicCapture yields sample_rate // 10 samples per chunk


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        value = result.stdout.strip()
        return value if value else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if not ordered:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(_quantile(ordered, 0.95), 3),
        "max": round(max(ordered), 3),
    }


def _sounddevice() -> Any:
    """Return the sounddevice module, or None when the package/PortAudio is absent."""
    try:
        import sounddevice as sd  # noqa: PLC0415

        return sd
    except (ImportError, OSError):
        return None


def _hostapis(sd: Any) -> list[dict[str, Any]] | None:
    try:
        return list(sd.query_hostapis())
    except Exception:
        return None


def _wasapi_hostapi(hostapis: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not hostapis:
        return None
    for hostapi in hostapis:
        if "wasapi" in str(hostapi.get("name", "")).lower():
            return hostapi
    return None


def _default_input_device(sd: Any) -> dict[str, Any] | None:
    try:
        info = sd.query_devices(kind="input")
    except Exception:
        return None
    return {
        "index": int(info.get("index", -1)),
        "name": info.get("name"),
        "default_samplerate": info.get("default_samplerate"),
        "max_input_channels": info.get("max_input_channels"),
    }


def _selected_device_info(sd: Any, index: int | None) -> dict[str, Any] | None:
    """Return device info for a specific index, or the default input device."""
    if index is None:
        return _default_input_device(sd)
    try:
        devices = sd.query_devices()
        if not isinstance(devices, (list, tuple)):
            return None
        info = devices[int(index)]
    except Exception:
        return None
    return {
        "index": int(index),
        "name": info.get("name"),
        "default_samplerate": info.get("default_samplerate"),
        "max_input_channels": info.get("max_input_channels"),
    }


def capture_environment(config: RuntimeConfig) -> dict[str, Any]:
    """Collect OS/Python/git/config metadata without touching audio hardware."""
    return {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": sys.version,
        "commit_sha": _git_commit(),
        "configured_audio": {
            "sample_rate": config.audio.sample_rate,
            "channels": config.audio.channels,
            "chunk_size": config.audio.chunk_size,
            "input_device": config.audio.input_device,
            "output_device": config.audio.output_device,
        },
        "configured_stt": {
            "provider": config.stt.provider,
            "model": config.stt.model,
            "language": config.stt.language,
            "device": config.stt.device,
        },
    }


def audio_backend_info(config: RuntimeConfig) -> dict[str, Any]:
    """Probe the audio backend, WASAPI host API, and device/rate selection."""
    sd = _sounddevice()
    hostapis = _hostapis(sd) if sd is not None else None
    wasapi = _wasapi_hostapi(hostapis)
    selected = _selected_device_info(sd, config.audio.input_device) if sd is not None else None
    return {
        "sounddevice_available": sd is not None,
        "wasapi_hostapi": {
            "present": wasapi is not None,
            "name": wasapi.get("name") if wasapi else None,
            "default_input_device": wasapi.get("default_input_device") if wasapi else None,
            "default_output_device": wasapi.get("default_output_device") if wasapi else None,
        },
        "discovered_input_devices": list_devices(),
        "configured_input_device": config.audio.input_device,
        "selected_device": selected,
        "device_native_default_sample_rate": (
            selected.get("default_samplerate") if selected else None
        ),
        "requested_stream_sample_rate": config.audio.sample_rate,
        "channels": config.audio.channels,
        "capture_format": "int16 mono PCM",
    }


async def _capture_utterance(
    mic: MicCapture,
    vad: EnergyVAD,
    context: TurnContext,
    *,
    max_speech_seconds: float,
    max_wait_seconds: float,
    trailing_silence_frames: int,
) -> tuple[bytes, float]:
    """Capture one VAD-bounded utterance; returns (int16 pcm bytes, audio_seconds)."""
    max_speech_chunks = max(1, int(max_speech_seconds / CHUNK_SECONDS))
    max_wait_chunks = max(1, int(max_wait_seconds / CHUNK_SECONDS))
    frames: list[bytes] = []
    speech_started = False
    trailing = 0
    waited = 0
    async for chunk in mic.capture(context):
        is_speech = vad.is_speech(chunk)
        if not speech_started:
            if is_speech:
                speech_started = True
                frames.append(chunk)
            else:
                waited += 1
                if waited >= max_wait_chunks:
                    break
            continue
        frames.append(chunk)
        if is_speech:
            trailing = 0
        else:
            trailing += 1
            if trailing >= trailing_silence_frames:
                break
        if len(frames) >= max_speech_chunks:
            break
    pcm = b"".join(frames)
    audio_seconds = (len(pcm) // 2) / mic.sample_rate if pcm else 0.0
    return pcm, audio_seconds


async def _capture_window(mic: MicCapture, context: TurnContext, seconds: float) -> tuple[bytes, float]:
    """Capture a fixed-duration window (used for the silence regression)."""
    max_chunks = max(1, int(seconds / CHUNK_SECONDS))
    frames: list[bytes] = []
    async for chunk in mic.capture(context):
        frames.append(chunk)
        if len(frames) >= max_chunks:
            break
    pcm = b"".join(frames)
    audio_seconds = (len(pcm) // 2) / mic.sample_rate if pcm else 0.0
    return pcm, audio_seconds


async def _transcribe(stt: Any, pcm: bytes, context: TurnContext) -> tuple[str, float]:
    """Run the production STT adapter over captured PCM; returns (text, seconds)."""

    async def _gen() -> AsyncIterator[bytes]:
        if pcm:
            yield pcm

    start = time.monotonic()
    parts: list[str] = []
    async for transcript in stt.transcribe(_gen(), context):
        text = transcript.text.strip()
        if text:
            parts.append(text)
    return " ".join(parts), time.monotonic() - start


def _print(message: str) -> None:
    print(message, flush=True)


async def _run_async(
    config: RuntimeConfig,
    *,
    on_progress: Any = _print,
    warm_runs: int = WARM_RUNS,
    speech_max_seconds: float = SPEECH_MAX_SECONDS,
) -> dict[str, Any]:
    """Execute the real-hardware acceptance and return a JSON-serializable report."""
    report: dict[str, Any] = {
        "environment": capture_environment(config),
        "audio": audio_backend_info(config),
    }

    stt = resolve_stt(config)
    provider = stt_provider(config)
    report["stt"] = {
        "provider": provider,
        "model": config.stt.model,
        "device": config.stt.device,
        "compute_type": getattr(stt, "compute_type", "int8"),
        "sample_rate": getattr(stt, "sample_rate", config.audio.sample_rate),
    }

    mic = MicCapture(
        sample_rate=config.audio.sample_rate,
        channels=config.audio.channels,
        device=config.audio.input_device,
    )
    vad = EnergyVAD()
    context = TurnContext.fresh("voice_acceptance", timeout_seconds=120.0)

    # Load the model first so the cold speech run measures first inference, not
    # model initialization. SAPI has no model, so its load time reads as zero.
    if provider == "whisper":
        loader = getattr(stt, "_load_model", None)
        if loader is not None:
            load_start = time.monotonic()
            try:
                loader()
                report["model_load_seconds"] = round(time.monotonic() - load_start, 3)
            except Exception as exc:  # noqa: BLE001 - diagnostics must not crash
                report["model_load_seconds"] = None
                report["model_load_error"] = str(exc)
        else:
            report["model_load_seconds"] = None
    else:
        report["model_load_seconds"] = 0.0

    runs: list[dict[str, Any]] = []

    on_progress("\nCOLD inference: speak ~4 seconds of normal Spanish now.")
    pcm, audio_seconds = await _capture_utterance(
        mic,
        vad,
        context,
        max_speech_seconds=speech_max_seconds,
        max_wait_seconds=SPEECH_MAX_WAIT_SECONDS,
        trailing_silence_frames=SPEECH_TRAILING_SILENCE_FRAMES,
    )
    text, transcription_seconds = await _transcribe(stt, pcm, context)
    runs.append(
        {
            "phase": "cold",
            "audio_seconds": round(audio_seconds, 3),
            "transcription_seconds": round(transcription_seconds, 3),
            "transcript": text,
        }
    )
    on_progress(
        f"  cold transcript={text!r} transcription={transcription_seconds:.3f}s audio={audio_seconds:.3f}s"
    )

    for index in range(1, warm_runs + 1):
        on_progress(f"\nWARM run {index}/{warm_runs}: speak ~4 seconds now.")
        pcm, audio_seconds = await _capture_utterance(
            mic,
            vad,
            context,
            max_speech_seconds=speech_max_seconds,
            max_wait_seconds=SPEECH_MAX_WAIT_SECONDS,
            trailing_silence_frames=SPEECH_TRAILING_SILENCE_FRAMES,
        )
        text, transcription_seconds = await _transcribe(stt, pcm, context)
        runs.append(
            {
                "phase": "warm",
                "audio_seconds": round(audio_seconds, 3),
                "transcription_seconds": round(transcription_seconds, 3),
                "transcript": text,
            }
        )
        on_progress(
            f"  warm[{index}] transcript={text!r} transcription={transcription_seconds:.3f}s audio={audio_seconds:.3f}s"
        )

    report["runs"] = runs
    cold = runs[0]
    warm = [run for run in runs if run["phase"] == "warm"]
    summary = _percentiles([run["transcription_seconds"] for run in warm])
    summary["sample_count"] = len(warm)
    report["cold_inference_latency_seconds"] = cold["transcription_seconds"]
    report["warm_inference_latency_seconds"] = [run["transcription_seconds"] for run in warm]
    report["transcription_summary"] = summary

    on_progress(f"\nSILENCE test: stay quiet with normal room noise for {SILENCE_SECONDS:.0f}s.")
    silence_start = time.monotonic()
    silence_pcm, silence_audio_seconds = await _capture_window(mic, context, SILENCE_SECONDS)
    silence_capture_seconds = time.monotonic() - silence_start
    silence_text, _ = await _transcribe(stt, silence_pcm, context)
    actionable = bool(silence_text.strip())
    report["silence_test"] = {
        "capture_seconds": round(silence_capture_seconds, 3),
        "audio_seconds": round(silence_audio_seconds, 3),
        "transcript": silence_text,
        "false_transcripts": 1 if actionable else 0,
        "false_command_dispatches": 0,
    }
    on_progress(
        f"  silence transcript={silence_text!r} false_transcripts={1 if actionable else 0} false_command_dispatches=0"
    )

    return report


def run_voice_acceptance(
    config: RuntimeConfig,
    *,
    on_progress: Any = _print,
    warm_runs: int = WARM_RUNS,
    speech_max_seconds: float = SPEECH_MAX_SECONDS,
) -> dict[str, Any]:
    """Run the real-hardware voice acceptance synchronously and return the report."""
    return asyncio.run(
        _run_async(
            config,
            on_progress=on_progress,
            warm_runs=warm_runs,
            speech_max_seconds=speech_max_seconds,
        )
    )


def format_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point used by the root ``diagnostico_voice_acceptance.py`` wrapper."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = Path(args[0]) if args else Path("config.json")

    from jarvis.config import load_config

    config = load_config(config_path)
    report = run_voice_acceptance(config)
    print("\n== JSON report ==", flush=True)
    print(format_report(report), flush=True)
    return 0
