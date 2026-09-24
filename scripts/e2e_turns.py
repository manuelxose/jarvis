"""Text-driven end-to-end turns through the real runtime (Windows laptop).

Builds the production runtime from config.win.json (+ config.local.json),
starts the supervisor (TTS worker included), waits for the cloned voice, then
runs N typed turns through router -> LLM chain -> chunker -> TTS -> speakers
and reports per-stage trace percentiles. Microphone/STT are not exercised:
add the measured STT time (voice_loop log "stt NNN ms") for mic-to-audio.

    .venv\\Scripts\\python.exe scripts\\e2e_turns.py --turns 20 --pause 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts.qwen_clone import QwenCloneTTS  # noqa: E402
from jarvis.application.runtime import _warm_up, build_runtime  # noqa: E402
from jarvis.config import load_config  # noqa: E402

QUERIES = [
    "Dime un dato curioso sobre Madrid.",
    "¿Qué tiempo suele hacer en Vigo en otoño?",
    "Explícame en una frase qué es un agujero negro.",
    "Dame una idea rápida para cenar esta noche.",
    "¿Cuántos días tiene un año bisiesto?",
]
STAGES = ("routing_ms", "llm_first_token_ms", "first_segment_ms", "tts_first_audio_ms", "total_request_ms")


def pct(values, p):
    values = sorted(values)
    return round(values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))]) if values else None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.win.json")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--pause", type=float, default=3.0)
    args = parser.parse_args()
    runtime = build_runtime(load_config(args.config))
    await runtime.supervisor.start()
    providers = getattr(runtime.components.turn_manager._tts, "providers", ())
    clone = next((p for p in providers if isinstance(p, QwenCloneTTS)), None)
    try:
        info = await clone.wait_ready() if clone is not None else {}
        await asyncio.to_thread(_warm_up, runtime.components.model, None)  # as `jarvis run` does
        traces, routes = [], []
        for index in range(args.turns):
            await asyncio.sleep(args.pause)
            result = await runtime.handle(QUERIES[index % len(QUERIES)])
            traces.append(result.trace)
            routes.append(result.route)
            print(f"turn {index + 1}: {result.route} tts_first_audio={result.trace.get('tts_first_audio_ms')} "
                  f"llm_first={result.trace.get('llm_first_token_ms')} :: {result.response[:70]}", file=sys.stderr)
        diagnostics = runtime.diagnostics()
    finally:
        await runtime.supervisor.stop()
    report = {
        "worker": {k: info.get(k) for k in ("profile", "mode", "load_ms", "warmup_ms", "vram_free_mb")},
        "turns": len(traces),
        "routes": sorted(set(routes)),
        "stages_ms": {s: {"p50": pct([t[s] for t in traces if s in t], 50),
                          "p95": pct([t[s] for t in traces if s in t], 95),
                          "max": pct([t[s] for t in traces if s in t], 100)} for s in STAGES},
        "tts_provider_used": diagnostics["tts"],
        "model": [{k: p.get(k) for k in ("name", "model", "spent_today_usd", "max_daily_usd", "priced")}
                  for p in diagnostics["model"]["providers"]],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
