"""Offline benchmark harness that records measured values using fakes.

Real-provider latency (model TTFT, TTS first audio, STT finalization) requires
configured credentials and hardware; this harness measures the runtime plumbing
deterministically and reports those provider-bound metrics as unavailable.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.application.runtime import build_runtime
from jarvis.config import RuntimeConfig


async def _measure_turn(runtime, text: str) -> dict[str, Any]:
    started = time.monotonic()
    result = await runtime.handle(text)
    elapsed_ms = (time.monotonic() - started) * 1000
    return {
        "text": text,
        "route": result.route,
        "elapsed_ms": round(elapsed_ms, 2),
        "cancelled": result.cancelled,
    }


async def _measure_cold_startup(config: RuntimeConfig) -> float:
    runtime = build_runtime(config, use_fakes=True)
    started = time.monotonic()
    await runtime.start()
    elapsed_ms = (time.monotonic() - started) * 1000
    await runtime.stop()
    return round(elapsed_ms, 2)


async def _measure_barge_in(config: RuntimeConfig) -> float:
    runtime = build_runtime(config, use_fakes=True)
    await runtime.start()

    async def _slow_turn() -> None:
        # The fake model is instant, so emulate an in-flight turn with a delay.
        await asyncio.sleep(5.0)

    task = asyncio.create_task(_slow_turn())
    await asyncio.sleep(0.01)
    started = time.monotonic()
    await runtime.interrupt()
    elapsed_ms = (time.monotonic() - started) * 1000
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await runtime.stop()
    return round(elapsed_ms, 2)


def run_benchmarks(config: RuntimeConfig) -> dict[str, Any]:
    """Run the offline benchmark suite synchronously and return measured values."""
    cold_startup_ms = asyncio.run(_measure_cold_startup(config))

    results: dict[str, Any] = {"cold_startup_ms": cold_startup_ms}
    turn_results: list[dict[str, Any]] = []
    barge_in_ms: float | None = None

    async def _run_turns() -> None:
        nonlocal barge_in_ms
        runtime = build_runtime(config, use_fakes=True)
        await runtime.start()
        for text in (
            "sube el volumen",
            "que hora es",
            "cual es la capital de francia",
            "planifica una tarea de ejemplo",
        ):
            turn_results.append(await _measure_turn(runtime, text))
        barge_in_ms = await _measure_barge_in(config)
        await runtime.stop()

    asyncio.run(_run_turns())
    results["turns"] = turn_results
    results["barge_in_ms"] = barge_in_ms
    results["model_ttft_ms"] = None  # requires a real configured provider
    results["tts_first_audio_ms"] = None  # requires a real configured provider
    results["stt_final_ms"] = None  # requires real audio hardware
    return results
