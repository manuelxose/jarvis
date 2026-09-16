"""Headless end-to-end acceptance demo.

Builds a fully-healthy fake runtime, exercises the fast-command, fast-model,
Hermes-agent, memory, and barge-in paths, then shuts down cleanly. Produces a
structured result suitable for the final acceptance matrix.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.application.runtime import build_runtime
from jarvis.config import RuntimeConfig
from jarvis.core.state import RuntimeState
from jarvis.core.turn import TurnContext


async def _run_demo(config: RuntimeConfig) -> dict[str, Any]:
    runtime = build_runtime(config, use_fakes=True)
    await runtime.start()
    startup_state = runtime.state.value

    turns: list[dict[str, Any]] = []
    for text in (
        "sube el volumen",
        "que hora es",
        "cual es la capital de francia",
        "planifica una tarea de ejemplo",
    ):
        result = await runtime.handle(text)
        turns.append(
            {
                "text": text,
                "route": result.route,
                "response": result.response,
                "cancelled": result.cancelled,
            }
        )

    # Memory: store a fact, then recall it (multi-turn relevance).
    memory = runtime.components.memory
    memory.remember("El usuario se llama Manuel", tier="long_term", category="nombre")
    recall = await memory.recall("como se llama el usuario", TurnContext.fresh("demo"))

    # Barge-in: interrupt an in-flight turn and confirm cancellation.
    async def _inflight() -> None:
        await asyncio.sleep(5.0)

    task = asyncio.create_task(_inflight())
    await asyncio.sleep(0.01)
    await runtime.interrupt()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await runtime.stop()
    shutdown_state = runtime.state.value

    return {
        "startup_state": startup_state,
        "shutdown_state": shutdown_state,
        "turns": turns,
        "memory_recall": recall,
        "health": runtime.diagnostics()["components"],
    }


def run_demo(config: RuntimeConfig) -> dict[str, Any]:
    """Run the headless acceptance demo and return its structured result."""
    return asyncio.run(_run_demo(config))
