"""Secret-safe STT comparison on operator-supplied WAV clips.

Clip language is an evidence tag only: both adapters use config.stt.language.
No reference, hypothesis, credentials, workspace ID, or audio bytes enter the
record. Runs are sequential and isolated; diagnostics are bounded categories.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import re
import statistics
import sys
import time
import unicodedata
import wave
from pathlib import Path
from typing import Sequence, TextIO

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from jarvis.adapters.stt.alibaba_qwen import AlibabaQwenSTT  # noqa: E402
from jarvis.adapters.stt.whisper import WhisperSTT  # noqa: E402
from jarvis.config import load_config  # noqa: E402
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable  # noqa: E402
from jarvis.core.turn import TurnContext  # noqa: E402
from jarvis.observability.cost import ProviderRate, UsageRecord, estimate_cost  # noqa: E402

RECORD_SCHEMA = "jarvis.stt-bakeoff.v1"
PROVIDERS = ("whisper", "alibaba_qwen")
OUTCOMES = ("ok", "no_transcript", "unavailable", "config_error", "missing_audio")
ROW_KEYS = ("clip", "language", "provider", "outcome", "audio_seconds", "first_partial_ms", "final_ms", "cer", "wer", "estimated_usd")


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded if not unicodedata.combining(ch)).split())


def _distance(a: Sequence, b: Sequence) -> int:
    previous = list(range(len(b) + 1))
    for index, left in enumerate(a, 1):
        current = [index]
        for column, right in enumerate(b, 1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (left != right)))
        previous = current
    return previous[-1]


def _error_rates(reference: str, hypothesis: str) -> tuple[float | None, float | None]:
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    if not ref:
        return None, None
    return round(_distance(ref, hyp) / len(ref), 4), round(_distance(ref.split(), hyp.split()) / len(ref.split()), 4)


def _load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list) or not data["clips"]:
        raise ValueError("invalid manifest")
    clips = []
    seen = set()
    for clip in data["clips"]:
        if not isinstance(clip, dict) or set(clip) != {"id", "audio", "language", "reference"} or any(
            not isinstance(clip[key], str) or not clip[key].strip() for key in ("id", "audio", "language")
        ) or not isinstance(clip["reference"], str):
            raise ValueError("invalid manifest clip")
        if (clip["id"] in seen or not re.fullmatch(r"[A-Za-z0-9_.-]+", clip["id"])
                or not re.fullmatch(r"[A-Za-z0-9_-]+", clip["language"])
                or not isinstance(clip["audio"], str)
                or not (path.parent / clip["audio"]).resolve().is_relative_to(path.parent.resolve())):
            raise ValueError("duplicate or invalid clip id/language/audio")
        seen.add(clip["id"])
        clips.append(clip)
    return clips


def _read_audio(path: Path, sample_rate: int) -> tuple[bytes, float] | None:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != sample_rate or wav.getcomptype() != "NONE":
                return None
            frames = wav.getnframes()
            if not frames:
                return None
            return wav.readframes(frames), frames / sample_rate
    except (OSError, EOFError, wave.Error):
        return None


def _resolve_providers(config, names: Sequence[str]) -> dict:
    resolved = {}
    for name in names:
        if name == "whisper":
            resolved[name] = WhisperSTT(
                model=config.stt.model, language=config.stt.language,
                device=config.stt.device, sample_rate=config.audio.sample_rate,
                hotwords=config.activation.wake_word,
            )
        else:
            resolved[name] = AlibabaQwenSTT.from_config(config)
    return resolved


async def _measure(provider, pcm: bytes, sample_rate: int, realtime: bool, timeout: float) -> tuple[str, float | None, float | None, str]:
    frame_size = max(1, round(sample_rate * 0.02)) * 2
    feed_end = None
    started = time.perf_counter()
    first = None
    final_arrival = None
    finals = []
    partial = ""
    context = TurnContext.fresh("stt-bakeoff", timeout_seconds=timeout)

    async def frames():
        nonlocal feed_end
        for offset in range(0, len(pcm), frame_size):
            if realtime:
                await asyncio.sleep(min(0.02, (len(pcm) - offset) / (sample_rate * 2)))
            if offset + frame_size >= len(pcm):
                feed_end = time.perf_counter()
            yield pcm[offset:offset + frame_size]

    async def consume():
        nonlocal first, final_arrival, partial
        async for transcript in provider.transcribe(frames(), context):
            now = time.perf_counter()
            if first is None:
                first = now
            if transcript.is_final:
                final_arrival = now
                finals.append(transcript.text)
            else:
                partial = transcript.text

    try:
        await asyncio.wait_for(consume(), timeout=timeout)
    except ProviderConfigError:
        return "config_error", None, None, ""
    except (ProviderUnavailable, ImportError, asyncio.TimeoutError):
        return "unavailable", None, None, ""
    except Exception:  # noqa: BLE001 - provider boundary; never disclose exception text
        return "unavailable", None, None, ""
    finally:
        context.cancellation.cancel()
    text = " ".join(finals).strip() if finals else partial.strip()
    if not text:
        return "no_transcript", None, None, ""
    # A provider that stops consuming early has no measured end-of-audio point.
    final_ms = round(max(0.0, (final_arrival - feed_end) * 1000), 3) if final_arrival is not None and feed_end is not None else None
    return "ok", round((first - started) * 1000, 3), final_ms, text


def build_record(rows: Sequence[dict], names: Sequence[str]) -> dict:
    summary = {}
    for name in names:
        ok = [row for row in rows if row["provider"] == name and row["outcome"] == "ok"]

        def median(key):
            values = [row[key] for row in ok if row[key] is not None]
            return round(statistics.median(values), 3) if values else None

        def mean(key):
            values = [row[key] for row in ok if row[key] is not None]
            return round(statistics.mean(values), 4) if values else None
        costs = [row["estimated_usd"] for row in ok if row["estimated_usd"] is not None]
        summary[name] = {"ok": len(ok), "median_first_partial_ms": median("first_partial_ms"),
                         "median_final_ms": median("final_ms"), "mean_cer": mean("cer"),
                         "mean_wer": mean("wer"), "total_estimated_usd": round(sum(costs), 6) if costs else None}
    return {"schema": RECORD_SCHEMA, "platform": platform.system(), "python": platform.python_version(),
            "runs": [{key: row[key] for key in ROW_KEYS} for row in rows], "summary": summary}


def _write_record(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_report(record: dict, out: TextIO, *, as_json: bool) -> None:
    if as_json:
        out.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    else:
        out.write("clip provider outcome first_partial_ms final_ms cer wer estimated_usd\n")
        for row in record["runs"]:
            out.write(" ".join(str(row[key]) if row[key] is not None else "-" for key in
                               ("clip", "provider", "outcome", "first_partial_ms", "final_ms", "cer", "wer", "estimated_usd")) + "\n")
    out.flush()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare local and Alibaba STT on WAV clips; emit secret-safe evidence")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--providers", default=",".join(PROVIDERS))
    parser.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--usd-per-audio-minute", action="append", default=[], metavar="PROVIDER=RATE")
    parser.add_argument("--show-transcripts", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    names = args.providers.split(",")
    rates = {}
    try:
        if not names or len(set(names)) != len(names) or any(name not in PROVIDERS for name in names):
            raise ValueError("invalid providers")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("invalid timeout")
        for item in args.usd_per_audio_minute:
            if "=" not in item:
                raise ValueError("invalid rate")
            name, value = item.split("=", 1)
            rate = float(value)
            if name not in names or name in rates or not math.isfinite(rate) or rate < 0:
                raise ValueError("invalid rate")
            rates[name] = rate
        clips = _load_manifest(Path(args.manifest))
        config = load_config(Path(args.config))
    except (OSError, ValueError, TypeError, KeyError):
        # Never echo untrusted configuration/manifest content or provider messages.
        err.write("error: invalid arguments, manifest, or configuration\n")
        return 2

    rows = []
    providers = {}
    construction_outcomes = {}
    for clip in clips:
        audio = _read_audio(Path(args.manifest).parent / clip["audio"], config.audio.sample_rate)
        for name in names:
            row = dict.fromkeys(ROW_KEYS)
            row.update(clip=clip["id"], language=clip["language"], provider=name,
                       outcome="missing_audio" if audio is None else "unavailable")
            if audio is not None:
                pcm, seconds = audio
                row["audio_seconds"] = round(seconds, 4)
                if name not in providers and name not in construction_outcomes:
                    try:
                        providers[name] = _resolve_providers(config, (name,))[name]
                    except ProviderConfigError:
                        construction_outcomes[name] = "config_error"
                    except Exception:  # noqa: BLE001 - construction failure is per-provider
                        construction_outcomes[name] = "unavailable"
                if name in construction_outcomes:
                    outcome, first, final, hypothesis = construction_outcomes[name], None, None, ""
                else:
                    outcome, first, final, hypothesis = asyncio.run(
                        _measure(providers[name], pcm, config.audio.sample_rate, args.realtime, args.timeout))
                row.update(outcome=outcome, first_partial_ms=first, final_ms=final)
                if outcome == "ok":
                    row["cer"], row["wer"] = _error_rates(clip["reference"], hypothesis)
                    row["estimated_usd"] = estimate_cost(
                        UsageRecord(provider=name, kind="stt", audio_seconds=seconds),
                        ProviderRate(audio_second=rates[name] / 60) if name in rates else None)
                    if args.show_transcripts:
                        err.write(f"clip {clip['id']} provider {name} transcript: {hypothesis}\n")
            if row["outcome"] != "ok":
                err.write(f"clip {clip['id']} provider {name}: {row['outcome']}\n")
            rows.append(row)
    record = build_record(rows, names)
    if args.output:
        try:
            _write_record(Path(args.output), record)
        except OSError:
            err.write("error: could not write record\n")
            return 2
    _write_report(record, out, as_json=args.json)
    return 0 if all(row["outcome"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
