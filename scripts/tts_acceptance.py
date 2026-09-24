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
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.output import AudioOutputQueue, StreamRenderer  # noqa: E402
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
    args = parser.parse_args()

    tts = QwenCloneTTS(
        worker_command(args.worker_python, profile_dir=args.profile_dir, chunk_size=args.chunk_size),
        stderr_path="logs/tts-worker.log",
    )
    renderer = CountingRenderer(args.output_device)
    queue = AudioOutputQueue(render=renderer, render_timeout_seconds=60)
    started = time.perf_counter()
    await tts.start()
    info = await tts.wait_ready()
    ready_s = time.perf_counter() - started
    try:
        runs = []
        for index in range(args.turns):
            runs.append(await one_turn(tts, queue, renderer, TURNS[index % len(TURNS)]))
        barge = await barge_in(tts, queue, renderer)
        after = await one_turn(tts, queue, renderer, "Sigo aquí.")
    finally:
        await tts.stop()
        renderer.close()
    first = [r["first_audio_ms"] for r in runs]
    print(json.dumps({
        "worker_ready_s": round(ready_s, 1),
        "worker": info,
        "turns": len(runs),
        "segment_to_first_audio_ms": {"p50": pct(first, 50), "p95": pct(first, 95), "max": round(max(first), 1)},
        "under_700ms": sum(1 for f in first if f < 700),
        "underruns": renderer.underruns,
        "barge_in": barge,
        "turn_after_barge_in_first_audio_ms": round(after["first_audio_ms"], 1),
        "worker_state": tts.state(),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
