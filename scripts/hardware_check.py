"""Real-hardware checks: speaker -> room -> microphone.

    python scripts/hardware_check.py loopback --trials 5
    python scripts/hardware_check.py music --seconds 30 [--file song.mp3]
    python scripts/hardware_check.py speech FILE.wav [FILE.wav ...]

loopback: plays a synthetic three-clap pattern through the default speakers
while recording the default microphone, and runs the live detector on the
recording (detection + latency per trial).
music/speech: plays audio through the speakers while recording, counts
false activations and reports the microphone level during playback (proves
the mic keeps working under music / speech). Prints JSON; writes nothing.
Synthetic claps through laptop speakers are a proxy: the owner's real claps
must still be checked with ``jarvis claps test``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import sounddevice as sd

from jarvis.adapters.audio.claps import ClapDetector, ClapTuning

RATE = 48000
DET_RATE = 16000
RNG = np.random.default_rng(3)


def synth_clap(amplitude: float = 0.9) -> np.ndarray:
    t = np.arange(int(RATE * 0.12)) / RATE
    burst = np.diff(RNG.standard_normal(t.size) * np.exp(-t / 0.01), prepend=0.0)
    return (amplitude * burst / np.abs(burst).max()).astype(np.float32)


def synth_music(seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    chords = sum(np.sin(2 * np.pi * f * t) for f in (110, 220, 277, 330, 440)) * 0.06
    track = chords.astype(np.float32)
    for at in np.arange(0.0, seconds, 0.5):  # kick + snare every beat, like rock
        c = synth_clap(0.5)
        i = int(at * RATE)
        track[i:i + c.size] += c[: track.size - i]
    return np.clip(track, -1, 1)


def to_det(recording: np.ndarray, rate: int) -> np.ndarray:
    mono = recording.mean(axis=1) if recording.ndim > 1 else recording
    positions = np.arange(0, mono.size, rate / DET_RATE)
    return np.interp(positions, np.arange(mono.size), mono).astype(np.float32)


def detect(signal: np.ndarray, tuning: ClapTuning) -> tuple[list, ClapDetector]:
    detector = ClapDetector(tuning)
    block = 320
    gestures = [g for s in range(0, signal.size, block) if (g := detector.feed(signal[s:s + block]))]
    tail = detector.feed(np.zeros(DET_RATE, dtype=np.float32))
    return gestures + ([tail] if tail else []), detector


def playrec(audio: np.ndarray) -> np.ndarray:
    stereo = np.repeat(audio[:, None], 2, axis=1)
    recording = sd.playrec(stereo, samplerate=RATE, channels=1, dtype="float32")
    sd.wait()
    return recording


def loopback(trials: int, tuning: ClapTuning) -> dict:
    results = []
    for trial in range(trials):
        pattern = np.zeros(int(RATE * 3.0), dtype=np.float32)
        for at in (0.8, 1.2, 1.6):
            c = synth_clap()
            i = int(at * RATE)
            pattern[i:i + c.size] += c
        rec = to_det(playrec(pattern), RATE)
        gestures, detector = detect(rec, tuning)
        results.append({
            "trial": trial,
            "detected": len(gestures),
            "latency_ms": round(gestures[0].latency_seconds * 1000) if gestures else None,
            "claps_accepted": [c.features for c in detector.accepted],
            "rejected": detector.rejected[-3:],
            "mic_peak_dbfs": round(20 * np.log10(max(float(np.abs(rec).max()), 1e-9)), 1),
        })
    return {"check": "loopback", "trials": trials, "detected_trials": sum(r["detected"] == 1 for r in results), "results": results}


def playback_fp(name: str, audio: np.ndarray, tuning: ClapTuning) -> dict:
    rec = to_det(playrec(audio), RATE)
    gestures, detector = detect(rec, tuning)
    hop = DET_RATE // 10
    frames = rec[: rec.size // hop * hop].reshape(-1, hop)
    levels = np.sqrt((frames ** 2).mean(axis=1))
    return {
        "check": name,
        "seconds": round(rec.size / DET_RATE, 1),
        "false_activations": len(gestures),
        "single_claps_accepted": len(detector.accepted),
        "mic_rms_dbfs_p50": round(20 * np.log10(max(float(np.median(levels)), 1e-9)), 1),
        "mic_rms_dbfs_p95": round(20 * np.log10(max(float(np.percentile(levels, 95)), 1e-9)), 1),
    }


def load(path: str) -> np.ndarray:
    import soundfile as sf  # noqa: PLC0415

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    positions = np.arange(0, mono.size, rate / RATE)
    return np.interp(positions, np.arange(mono.size), mono).astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=("loopback", "music", "speech", "devices"))
    parser.add_argument("files", nargs="*")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--file")
    parser.add_argument("--volume", type=float, default=0.8)
    args = parser.parse_args()
    tuning = ClapTuning()
    if args.check == "devices":
        print(json.dumps({"input": sd.query_devices(kind="input")["name"], "output": sd.query_devices(kind="output")["name"]}))
        return 0
    if args.check == "loopback":
        result = loopback(args.trials, tuning)
    elif args.check == "music":
        audio = load(args.file)[: int(RATE * args.seconds)] if args.file else synth_music(args.seconds)
        result = playback_fp("music" + (" (file)" if args.file else " (synthetic rock beat)"), audio * args.volume, tuning)
    else:
        audio = np.concatenate([load(f) for f in args.files])
        result = playback_fp("speech", audio * args.volume, tuning)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
