"""Real-hardware acceptance for the local cloned-voice TTS path (Windows laptop).

Drives the production pieces (QwenCloneTTS worker + AudioOutputQueue +
persistent StreamRenderer) against the real GPU and speakers and records:

* segment-ready -> first audio handed to the device (per turn, p50/p95/max)
* stream underruns reported by PortAudio (gapless playback check)
* barge-in: cancel -> device silent latency, and stale audio after cancel
* N sequential turns without restarting the worker, plus worker VRAM

Usage (Jarvis venv, from the repo root)::

    .venv\\Scripts\\python.exe scripts\\tts_acceptance.py --profile-dir %LOCALAPPDATA%\\jarvis\\voice\\default

Prints one JSON record; audible quality and voice identity still need a human
listening check.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.output import AudioOutputQueue, StreamRenderer  # noqa: E402
from jarvis.adapters.tts.fallback import TTSChain  # noqa: E402
from jarvis.adapters.tts.pyttsx3 import Pyttsx3TTS  # noqa: E402
from jarvis.adapters.tts.qwen_clone import QwenCloneTTS, default_worker_python, worker_command  # noqa: E402
from jarvis.core.turn import TurnCancelled, TurnContext  # noqa: E402

TURNS = [
    "Claro, ahora mismo lo miro.",
    "Mañana en Madrid habrá cielos despejados y una máxima de veintiocho grados.",
    "He abierto Spotify y he puesto tu lista de música para concentrarte.",
    "Son las diez y cuarto. Tienes una reunión a las once con el equipo de diseño.",
]
LONG = (
    "Te resumo el día. Por la mañana tienes dos reuniones, la primera a las nueve y media. "
    "Después hay un hueco libre hasta la comida, así que podrías aprovechar para revisar el informe. "
    "Por la tarde no hay nada programado."
)


class CountingRenderer(StreamRenderer):
    """StreamRenderer that timestamps the first write of each turn."""

    def __init__(self, device=None) -> None:
        super().__init__(device)
        self.first_write: dict[str, float] = {}
        self.last_write: dict[str, float] = {}
        self.underruns = 0

    def _open(self, sd, rate, channels):
        stream = super()._open(sd, rate, channels)
        return _Probe(stream, self)


class _Probe:
    def __init__(self, stream, owner: CountingRenderer) -> None:
        self._stream, self._owner = stream, owner

    def write(self, samples):
        now = time.perf_counter()
        turn = self._owner._playing
        self._owner.first_write.setdefault(turn, now)
        self._owner.last_write[turn] = now
        if self._stream.write(samples):  # sounddevice returns True on underflow
            self._owner.underruns += 1

    def __getattr__(self, name):
        return getattr(self._stream, name)


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return round(values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))], 1)


def _looks_like_path(value: object) -> bool:
    # Backslash only: Windows filesystem paths (e.g. profile_dir), not forward-slash
    # identifiers like the HF model repo id ("Qwen/Qwen3-TTS-...") in worker info.
    return isinstance(value, str) and "\\" in value


def _scrub_paths(data: dict) -> dict:
    """Drop any key whose value is a filesystem path (privacy: no C:\\Users leakage)."""
    return {key: value for key, value in data.items() if not _looks_like_path(value)}


def _scrub_worker_state(state: dict) -> dict:
    scrubbed = _scrub_paths(state)
    if isinstance(scrubbed.get("info"), dict):
        scrubbed["info"] = _scrub_paths(scrubbed["info"])
    return scrubbed


def gpu_temperature_c() -> float | None:
    """Current GPU temperature via nvidia-smi, or None if unavailable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    try:
        return float(line)
    except ValueError:
        return None


async def one_turn(tts, queue, renderer, text: str) -> dict:
    context = TurnContext.fresh("accept", timeout_seconds=60)

    async def segments():
        yield text

    start = time.perf_counter()
    await queue.play(tts.synthesize(segments(), context), context)
    return {
        "first_audio_ms": (renderer.first_write[context.trace_id] - start) * 1000,
        "total_s": time.perf_counter() - start,
    }


async def barge_in(tts, queue, renderer) -> dict:
    context = TurnContext.fresh("accept", timeout_seconds=60)

    async def segments():
        yield LONG

    task = asyncio.create_task(queue.play(tts.synthesize(segments(), context), context))
    while context.trace_id not in renderer.first_write:
        await asyncio.sleep(0.01)
    await asyncio.sleep(1.5)
    cancelled_at = time.perf_counter()
    context.cancellation.cancel()
    try:
        await task
    except TurnCancelled:
        pass
    stop_ms = (time.perf_counter() - cancelled_at) * 1000
    await asyncio.sleep(2.0)  # any stale audio would be written during this window
    stale = [t for t, at in renderer.last_write.items() if t == context.trace_id and at > cancelled_at + 0.1]
    return {"cancel_to_stop_ms": round(stop_ms, 1), "stale_writes_after_cancel": len(stale)}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--worker-python", default=default_worker_python())
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--chunk-size", type=int, default=4)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--output", default=None, help="Also write the JSON record to this path")
    parser.add_argument("--host-label", default="LAPTOP", help="Fixed label recorded instead of the real hostname")
    args = parser.parse_args()

    tts = QwenCloneTTS(
        worker_command(args.worker_python, profile_dir=args.profile_dir, chunk_size=args.chunk_size),
        stderr_path="logs/tts-worker.log",
    )
    renderer = CountingRenderer(args.output_device)
    queue = AudioOutputQueue(render=renderer, render_timeout_seconds=60)
    gpu_temp_before = gpu_temperature_c()
    started = time.perf_counter()
    await tts.start()
    warming_chain = TTSChain([tts, Pyttsx3TTS()])
    try:
        warm = await one_turn(warming_chain, queue, renderer, "Un momento, estoy despertando.")
        served_by = warming_chain.last_provider.name if warming_chain.last_provider else None
        warming_turn = {"served_by": served_by, "first_audio_ms": round(warm["first_audio_ms"], 1)}
    except Exception as error:  # pragma: no cover - hardware-dependent path
        warming_turn = {"error": type(error).__name__}
    info = await tts.wait_ready()
    ready_s = time.perf_counter() - started
    try:
        runs = []
        for index in range(args.turns):
            runs.append(await one_turn(tts, queue, renderer, TURNS[index % len(TURNS)]))
        barge = await barge_in(tts, queue, renderer)
        after = await one_turn(tts, queue, renderer, "Sigo aquí.")
    finally:
        gpu_temp_after = gpu_temperature_c()
        await tts.stop()
        renderer.close()
    first = [r["first_audio_ms"] for r in runs]
    record = {
        "worker_ready_s": round(ready_s, 1),
        "worker": _scrub_paths(info),
        "turns": len(runs),
        "segment_to_first_audio_ms": {"p50": pct(first, 50), "p95": pct(first, 95), "max": round(max(first), 1)},
        "under_700ms": sum(1 for f in first if f < 700),
        "underruns": renderer.underruns,
        "barge_in": barge,
        "turn_after_barge_in_first_audio_ms": round(after["first_audio_ms"], 1),
        "worker_state": _scrub_worker_state(tts.state()),
        "warming_turn": warming_turn,
        "meta": {
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host_label": args.host_label,
            "python_version": platform.python_version(),
            "chunk_size": args.chunk_size,
            "turns": args.turns,
            "command": (
                "python scripts\\tts_acceptance.py --profile-dir %LOCALAPPDATA%\\jarvis\\voice\\default "
                f"--turns {args.turns} --chunk-size {args.chunk_size}"
            ),
            "gpu_temp_c_before": gpu_temp_before,
            "gpu_temp_c_after": gpu_temp_after,
        },
    }
    text = json.dumps(record, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
