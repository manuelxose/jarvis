"""Evaluate the clap-activation gesture offline: real recordings or a synthetic labeled corpus.

Usage:
  python scripts/clap_eval.py FILE [FILE ...] [--config config.win.json] [--claps-required 2|3]
  python scripts/clap_eval.py --synthetic [--config config.win.json]

Files are downmixed and resampled to 16 kHz, then streamed through the detector
in 20 ms blocks exactly like the live listener. WAV files need only numpy; other
formats need the optional ``soundfile`` package. Recordings are expected to
contain no gesture: exit 0 only with zero activations. ``--synthetic`` runs a
seeded corpus (speech, music, TTS-like, typing negatives in 2- and 3-clap mode;
correctly spaced positives) and exits 0 only with zero false activations and
every positive detected exactly once. ``--config`` builds the tuning through the
daemon's ``clap_tuning`` (config values over the saved calibration) so the eval
matches the live detector. One JSON line per case plus a final summary line;
nothing is written to disk, so private recordings never leave the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from jarvis.adapters.audio.claps import AUDIO_READ_ERRORS, ClapDetector, ClapTuning, load_audio_mono

RATE = 16000


# -- evaluation ---------------------------------------------------------------------

def evaluate_signal(name: str, audio: np.ndarray, tuning: ClapTuning, label: str | None = None) -> dict:
    detector = ClapDetector(tuning)
    block = RATE // 50
    started = time.perf_counter()
    gestures = [g for s in range(0, audio.size, block) if (g := detector.feed(audio[s:s + block]))]
    cpu = time.perf_counter() - started
    seconds = audio.size / RATE
    result = {"file": name}
    if label is not None:
        result["label"] = label
    result.update({
        "claps_required": tuning.claps_required,
        "seconds": round(seconds, 1),
        "activations": len(gestures),
        "activation_times": [g.claps[0].time for g in gestures],
        "latency_ms": [round(g.latency_seconds * 1000.0, 1) for g in gestures],
        "single_claps_accepted": len(detector.accepted),
        "rejected_transients": len(detector.rejected),
        "cpu_realtime_factor": round(cpu / max(seconds, 1e-9), 5),
    })
    return result


def evaluate(path: str, tuning: ClapTuning) -> dict:
    return evaluate_signal(Path(path).name, load_audio_mono(path, RATE), tuning)


# -- synthetic corpus (signal shapes mirror tests/test_claps.py) ----------------------

def silence(rng, seconds, level=0.001):
    return (rng.standard_normal(int(RATE * seconds)) * level).astype(np.float32)


def clap(rng, amplitude=0.5, tau=0.012):
    t = np.arange(int(RATE * 0.15)) / RATE
    burst = rng.standard_normal(t.size) * np.exp(-t / tau)
    burst = np.diff(burst, prepend=0.0)  # tilt the spectrum up like a real clap
    return (amplitude * burst / np.abs(burst).max()).astype(np.float32)


def _add(signal, at, burst):
    start = int(at * RATE)
    if start < signal.size:
        signal[start:start + burst.size] += burst[: signal.size - start]


def claps(rng, times, total, amplitude=0.5, floor=0.001):
    signal = silence(rng, total, floor)
    for at in times:
        _add(signal, at, clap(rng, amplitude))
    return signal


def speech_like(rng, seconds):
    """Voiced syllables: 110-220 Hz harmonics with 4-6 Hz syllable envelope and plosives."""
    t = np.arange(int(RATE * seconds)) / RATE
    f0 = 140 + 30 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12))
    envelope = np.clip(np.sin(2 * np.pi * 4.5 * t), 0, None) ** 0.6
    signal = (0.25 * voiced * envelope).astype(np.float32)
    for at in np.arange(0.2, seconds, 0.45):  # "p"/"t" bursts at syllable starts
        _add(signal, at, (rng.standard_normal(160) * 0.15).astype(np.float32))
    return signal + silence(rng, seconds)


def music_like(rng, seconds, bpm=120):
    """Sustained chord plus a snare-like noise hit on every beat."""
    t = np.arange(int(RATE * seconds)) / RATE
    signal = (sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440)) * 0.05).astype(np.float32)
    for at in np.arange(0.1, seconds, 60 / bpm):
        _add(signal, at, clap(rng, 0.4, tau=0.03))
    return signal


def tts_like(rng, seconds):
    """Formant speech: words with sharp onsets, sibilant tails and pauses, normalised to -3 dBFS peak."""
    t = np.arange(int(RATE * seconds)) / RATE
    f0 = 120 + 25 * np.sin(2 * np.pi * 0.5 * t)
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    signal = np.zeros(t.size, dtype=np.float32)
    at = 0.3
    while at < seconds - 0.5:
        vowel = rng.choice([(700, 1200, 2500), (300, 2200, 3000), (500, 1000, 2400)])
        length = float(rng.uniform(0.18, 0.4))
        n = int(length * RATE)
        seg = np.arange(n) / RATE
        word = np.zeros(n)
        for k in range(1, 30):  # harmonics weighted by three formant resonances
            weight = sum(np.exp(-0.5 * ((k * 120 - f) / 120) ** 2) for f in vowel)
            start = int(at * RATE)
            word += weight * np.sin(k * phase[start:start + n])[:n] if start + n <= phase.size else 0
        envelope = np.minimum(1.0, seg / 0.004) * np.exp(-seg / (length * 1.5))  # 4 ms attack
        burst = np.zeros(n)
        burst[: int(0.05 * RATE)] = np.diff(rng.standard_normal(int(0.05 * RATE) + 1)) * 0.5  # sibilant onset
        _add(signal, at, (word * envelope + burst * np.exp(-seg / 0.05)).astype(np.float32))
        at += length + float(rng.uniform(0.08, 0.35))
    signal = signal / max(float(np.abs(signal).max()), 1e-9) * 0.708
    return signal + silence(rng, seconds)


def typing_like(rng, seconds):
    """Irregular low-HF keyboard/knock thumps: low-passed noise plus a 150-400 Hz body, over low-frequency room tone."""
    signal = np.convolve(silence(rng, seconds, 0.008), np.ones(16) / 16, mode="same").astype(np.float32)  # ~-54 dBFS
    at = 0.5
    while at < seconds - 0.3:
        n = int(0.12 * RATE)
        t = np.arange(n) / RATE
        noise = np.convolve(rng.standard_normal(n), np.ones(48) / 48, mode="same")  # low-pass ~330 Hz
        body = np.sin(2 * np.pi * float(rng.uniform(150, 400)) * t)
        # a soft ~4 ms attack: a step onset would be broadband, which real thumps are not
        burst = (noise * 2.0 + body) * np.minimum(1.0, t / 0.004) * np.exp(-t / 0.015)
        _add(signal, at, (0.35 * burst / np.abs(burst).max()).astype(np.float32))
        at += float(rng.uniform(0.15, 1.5))
    return signal


def synthetic_corpus(seed: int = 7) -> list[tuple[str, str, int | None, np.ndarray]]:
    """(name, label, claps_required-if-positive, audio); fixed seed so runs are reproducible."""
    rng = np.random.default_rng(seed)
    return [
        ("speech_like", "negative", None, speech_like(rng, 20.0)),
        ("music_like_120bpm", "negative", None, music_like(rng, 20.0, 120)),
        ("music_like_90bpm", "negative", None, music_like(rng, 20.0, 90)),
        ("tts_like", "negative", None, tts_like(rng, 20.0)),
        ("typing_knocks", "negative", None, typing_like(rng, 20.0)),
        ("quiet_room", "negative", None, silence(rng, 10.0, 0.003)),
        ("two_claps", "positive", 2, claps(rng, [0.8, 1.2], 3.0)),
        ("three_claps", "positive", 3, claps(rng, [0.8, 1.2, 1.6], 3.0)),
    ]


def run_synthetic(tuning: ClapTuning, modes: tuple[int, ...]) -> tuple[list[dict], dict]:
    results = []
    for name, label, required, audio in synthetic_corpus():
        for mode in modes:
            if label == "positive" and required != mode:
                continue
            result = evaluate_signal(name, audio, replace(tuning, claps_required=mode), label)
            result["ok"] = result["activations"] == (1 if label == "positive" else 0)
            results.append(result)
    positives = [r for r in results if r["label"] == "positive"]
    summary = {
        "summary": True,
        "mode": "synthetic",
        "cases": len(results),
        "false_activations": sum(r["activations"] for r in results if r["label"] == "negative"),
        "missed_positives": sum(1 for r in positives if not r["ok"]),
        "max_latency_ms": max((ms for r in positives for ms in r["latency_ms"]), default=None),
        "max_cpu_realtime_factor": max(r["cpu_realtime_factor"] for r in results),
    }
    summary["ok"] = summary["false_activations"] == 0 and summary["missed_positives"] == 0
    return results, summary


# -- CLI ----------------------------------------------------------------------------

def build_tuning(args: argparse.Namespace) -> ClapTuning:
    tuning = ClapTuning()
    if args.config:
        from jarvis.apps.daemon import clap_tuning  # noqa: PLC0415
        from jarvis.config import load_config  # noqa: PLC0415

        tuning = clap_tuning(load_config(Path(args.config)))
    overrides = {
        key: value
        for key, value in (
            ("sensitivity", args.sensitivity),
            ("min_peak_dbfs", args.min_peak_dbfs),
            ("min_hf_ratio", args.min_hf_ratio),
            ("claps_required", args.claps_required),
        )
        if value is not None
    }
    return replace(tuning, **overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--synthetic", action="store_true", help="run the seeded labeled corpus")
    parser.add_argument("--config", help="config JSON; tuning = config.claps over the saved calibration, like the daemon")
    parser.add_argument("--claps-required", type=int, choices=(2, 3), default=None)
    parser.add_argument("--sensitivity", type=float, default=None)
    parser.add_argument("--min-peak-dbfs", type=float, default=None)
    parser.add_argument("--min-hf-ratio", type=float, default=None)
    args = parser.parse_args(argv)
    if not args.files and not args.synthetic:
        parser.error("give FILE arguments and/or --synthetic")
    try:
        tuning = build_tuning(args)
    except (OSError, ValueError) as error:  # includes JSONDecodeError
        print(json.dumps({"error": f"invalid configuration: {error}"}, ensure_ascii=False))
        return 2

    ok = True
    if args.synthetic:
        modes = (args.claps_required,) if args.claps_required else (2, 3)
        results, summary = run_synthetic(tuning, modes)
        for result in results:
            print(json.dumps(result, ensure_ascii=False))
        print(json.dumps(summary, ensure_ascii=False))
        ok = summary["ok"]
    if args.files:
        total = errors = 0
        for path in args.files:
            try:
                result = evaluate(path, tuning)
            except AUDIO_READ_ERRORS as error:
                errors += 1
                print(json.dumps({"file": Path(path).name, "error": str(error)}, ensure_ascii=False))
                continue
            total += result["activations"]
            print(json.dumps(result, ensure_ascii=False))
        summary = {"summary": True, "mode": "files", "files": len(args.files), "activations": total, "errors": errors}
        summary["ok"] = total == 0 and errors == 0
        print(json.dumps(summary, ensure_ascii=False))
        ok = ok and summary["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
