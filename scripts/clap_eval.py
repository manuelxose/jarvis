"""Count triple-clap activations in real recordings (false-positive evaluation).

Usage: python scripts/clap_eval.py FILE [FILE ...] [--sensitivity 0.5]

Any format libsndfile reads (WAV, FLAC, MP3 with libsndfile >= 1.1). Files are
downmixed and resampled to 16 kHz, then streamed through the detector in 20 ms
blocks exactly like the live listener. Prints one JSON line per file; nothing
is written to disk, so private recordings never leave the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import soundfile as sf

from jarvis.adapters.audio.claps import ClapDetector, ClapTuning

RATE = 16000


def load_mono_16k(path: str) -> np.ndarray:
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != RATE:
        # ponytail: linear-interpolation resampling aliases a little above 8 kHz;
        # fine for counting transients, use a polyphase filter for anything finer.
        positions = np.arange(0, mono.size, rate / RATE)
        mono = np.interp(positions, np.arange(mono.size), mono).astype(np.float32)
    return mono


def evaluate(path: str, tuning: ClapTuning) -> dict:
    audio = load_mono_16k(path)
    detector = ClapDetector(tuning)
    block = RATE // 50
    started = time.perf_counter()
    gestures = [g for s in range(0, audio.size, block) if (g := detector.feed(audio[s:s + block]))]
    cpu = time.perf_counter() - started
    seconds = audio.size / RATE
    return {
        "file": Path(path).name,
        "seconds": round(seconds, 1),
        "activations": len(gestures),
        "activation_times": [g.claps[0].time for g in gestures],
        "single_claps_accepted": len(detector.accepted),
        "cpu_realtime_factor": round(cpu / max(seconds, 1e-9), 5),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--sensitivity", type=float, default=ClapTuning().sensitivity)
    parser.add_argument("--min-peak-dbfs", type=float, default=ClapTuning().min_peak_dbfs)
    parser.add_argument("--min-hf-ratio", type=float, default=ClapTuning().min_hf_ratio)
    args = parser.parse_args(argv)
    tuning = ClapTuning(sensitivity=args.sensitivity, min_peak_dbfs=args.min_peak_dbfs, min_hf_ratio=args.min_hf_ratio)
    total = 0
    for path in args.files:
        result = evaluate(path, tuning)
        total += result["activations"]
        print(json.dumps(result, ensure_ascii=False))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
