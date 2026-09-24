"""Owner voice profile: validate, enroll, inspect and delete (stdlib only).

The profile lives outside the repository, in the per-user local app data
directory (``%LOCALAPPDATA%\\jarvis\\voice\\<name>`` on Windows), which only
the owning Windows account can read by default. It holds ``reference.wav``,
``profile.json`` and, once the TTS worker has run, the cached conditioning
``prompt.pt``. Nothing here uploads anything anywhere.
"""

from __future__ import annotations

import array
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import wave

DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MIN_SECONDS = 3.0
MAX_SECONDS = 20.0
MIN_RATE = 16000


def default_profile_dir(name: str = "default") -> Path:
    """Directory of a named voice profile under ``%LOCALAPPDATA%\\jarvis\\voice``."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / "jarvis" / "voice" / name


@dataclass
class ReferenceReport:
    """Quality measurements of a reference recording and the problems found."""
    duration_s: float
    sample_rate: int
    peak_dbfs: float
    rms_dbfs: float
    clipped_ratio: float
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _read_mono(path: Path, start_s: float, duration_s: float | None) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("reference must be 16-bit PCM WAV (export as 'WAV 16-bit' in Audacity)")
        rate, channels = wav.getframerate(), wav.getnchannels()
        wav.setpos(min(wav.getnframes(), int(start_s * rate)))
        count = wav.getnframes() - wav.tell() if duration_s is None else int(duration_s * rate)
        raw = array.array("h", wav.readframes(count))
    if sys.byteorder == "big":
        raw.byteswap()
    if channels > 1:
        raw = array.array("h", (sum(raw[i:i + channels]) // channels for i in range(0, len(raw), channels)))
    return raw, rate


def _dbfs(value: float) -> float:
    return 20 * math.log10(value / 32768) if value > 0 else -120.0


def validate_reference(path: Path, start_s: float = 0.0, duration_s: float | None = None) -> ReferenceReport:
    """Measure a reference WAV (duration, level, clipping, sample rate) and list problems."""
    samples, rate = _read_mono(Path(path), start_s, duration_s)
    n = len(samples) or 1
    peak = max((abs(s) for s in samples), default=0)
    rms = math.sqrt(sum(s * s for s in samples) / n)
    report = ReferenceReport(
        duration_s=len(samples) / rate,
        sample_rate=rate,
        peak_dbfs=round(_dbfs(peak), 1),
        rms_dbfs=round(_dbfs(rms), 1),
        clipped_ratio=sum(1 for s in samples if abs(s) >= 32700) / n,
    )
    if report.duration_s < MIN_SECONDS:
        report.errors.append(f"too short: {report.duration_s:.1f}s (need >= {MIN_SECONDS:.0f}s of speech)")
    if report.duration_s > MAX_SECONDS:
        report.errors.append(
            f"too long: {report.duration_s:.1f}s (max {MAX_SECONDS:.0f}s; pick a window with --start/--duration)"
        )
    if rate < MIN_RATE:
        report.errors.append(f"sample rate {rate} Hz too low (need >= {MIN_RATE} Hz)")
    if report.clipped_ratio > 0.001:
        report.errors.append(f"clipping on {report.clipped_ratio:.2%} of samples; record at a lower gain")
    if report.rms_dbfs < -40:
        report.errors.append(f"too quiet (RMS {report.rms_dbfs} dBFS); record closer to the microphone")
    elif report.rms_dbfs < -30:
        report.warnings.append(f"quiet (RMS {report.rms_dbfs} dBFS)")
    return report


def enroll(
    source: Path,
    profile_dir: Path,
    *,
    ref_text: str = "",
    start_s: float = 0.0,
    duration_s: float | None = None,
    model: str = DEFAULT_MODEL,
    name: str = "owner",
    icl: bool = False,
) -> dict:
    """Validate *source* and (re)write the profile; returns the stored metadata.

    Default mode is speaker-embedding cloning (xvec): measured on the RTX 3070
    it starts speaking ~250 ms sooner than in-context cloning (ICL) and uses
    ~1.4 GB less VRAM. ``icl=True`` puts the reference audio in context (may
    sound closer to the owner) and needs the exact ``ref_text``.
    """
    if icl and not ref_text.strip():
        raise ValueError("ICL mode needs the exact transcript of the sample (--text)")
    report = validate_reference(Path(source), start_s, duration_s)
    if report.errors:
        raise ValueError("; ".join(report.errors))
    samples, rate = _read_mono(Path(source), start_s, duration_s)
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    with wave.open(str(profile_dir / "reference.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes() if sys.byteorder == "little" else _swapped(samples))
    (profile_dir / "prompt.pt").unlink(missing_ok=True)
    (profile_dir / "preview.wav").unlink(missing_ok=True)
    meta = {
        "name": name,
        "model": model,
        "mode": "icl" if icl else "xvec",
        "ref_text": ref_text.strip(),
        "duration_s": round(report.duration_s, 2),
        "sample_rate": rate,
        "rms_dbfs": report.rms_dbfs,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (profile_dir / "profile.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def _swapped(samples: array.array) -> bytes:
    copy = array.array("h", samples)
    copy.byteswap()
    return copy.tobytes()


def status(profile_dir: Path) -> dict:
    """Describe the enrolled profile, or report that none exists."""
    profile_dir = Path(profile_dir)
    meta_path = profile_dir / "profile.json"
    if not (profile_dir / "reference.wav").is_file() or not meta_path.is_file():
        return {"enrolled": False, "path": str(profile_dir)}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "enrolled": True,
        "path": str(profile_dir),
        "prepared": (profile_dir / "prompt.pt").is_file(),
        "preview": str(profile_dir / "preview.wav") if (profile_dir / "preview.wav").is_file() else None,
        **{k: meta.get(k) for k in ("name", "mode", "model", "duration_s", "created")},
    }


def delete(profile_dir: Path) -> bool:
    """Remove a profile with its recording and cached conditioning; return True if it existed."""
    profile_dir = Path(profile_dir)
    if not profile_dir.exists():
        return False
    shutil.rmtree(profile_dir)
    return True


# A passage the owner reads aloud during `record`: the transcript is then exact,
# which in-context (ICL) cloning requires.
READ_ALOUD = (
    "Hola, soy yo. Hoy he quedado a las diez y cuarto con Juan en la plaza, "
    "pero antes quiero comprobar el correo y la agenda de mañana."
)


def record(path: Path, seconds: float = 12.0, rate: int = 24000, device: int | None = None) -> Path:
    """Record the owner reading READ_ALOUD from the microphone (16-bit mono WAV)."""
    import sounddevice as sd  # noqa: PLC0415

    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16", device=device)
    sd.wait()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(audio.tobytes())
    return Path(path)


def prepare(profile_dir: Path, worker_python: str, text: str = "") -> int:
    """Build the cached conditioning (prompt.pt) and a Spanish preview.wav locally."""
    import subprocess  # noqa: PLC0415

    from jarvis.adapters.tts.qwen_clone import WORKER_SCRIPT  # noqa: PLC0415

    command = [worker_python, str(WORKER_SCRIPT), "prepare", "--profile-dir", str(profile_dir)]
    return subprocess.run(command + (["--text", text] if text else []), check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point: ``record``, ``enroll``, ``prepare``, ``status``, ``delete``."""
    import argparse  # noqa: PLC0415

    from jarvis.adapters.tts.qwen_clone import default_worker_python  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="python -m jarvis.voice_profile", description="Owner voice profile")
    parser.add_argument("command", choices=("record", "enroll", "prepare", "status", "delete"))
    parser.add_argument("--profile-dir", default=str(default_profile_dir()))
    parser.add_argument("--audio", help="enroll: 16-bit PCM WAV of the owner's voice")
    parser.add_argument("--text", default="", help="exact transcript of the sample (required with --icl)")
    parser.add_argument("--icl", action="store_true", help="in-context cloning: slower, maybe closer identity")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--seconds", type=float, default=12.0, help="record: length")
    parser.add_argument("--input-device", type=int, default=None)
    parser.add_argument("--worker-python", default=default_worker_python())
    parser.add_argument("--yes", action="store_true", help="delete: do not ask")
    args = parser.parse_args(argv)
    profile = Path(args.profile_dir)

    if args.command == "status":
        print(json.dumps(status(profile), indent=2, ensure_ascii=False))
        return 0
    if args.command == "delete":
        if not args.yes and input(f"Delete voice profile {profile}? [y/N] ").strip().lower() != "y":
            return 1
        print("deleted" if delete(profile) else "no profile")
        return 0
    if args.command == "prepare":
        return prepare(profile, args.worker_python, args.text)
    text = args.text
    source = args.audio
    if args.command == "record":
        print("Lee en voz alta, con tu voz natural, cuando pulses Enter:\n\n  " + READ_ALOUD + "\n")
        input("Enter para empezar...")
        source = record(profile.parent / f"{profile.name}-recording.wav", args.seconds, device=args.input_device)
        text = text or READ_ALOUD
    if not source:
        parser.error("enroll needs --audio")
    try:
        meta = enroll(
            Path(source), profile, ref_text=text, start_s=args.start, duration_s=args.duration, icl=args.icl
        )
    except ValueError as error:
        print(f"rejected: {error}", file=sys.stderr)
        return 2
    finally:
        if args.command == "record":
            Path(source).unlink(missing_ok=True)  # the profile keeps its own copy
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print("Building conditioning and a Spanish preview (local GPU)...")
    code = prepare(profile, args.worker_python)
    if code == 0:
        print(f"Listen before using it: {profile / 'preview.wav'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
