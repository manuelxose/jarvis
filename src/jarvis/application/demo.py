"""Headless end-to-end acceptance demo.

Builds a fully-healthy fake runtime, exercises the fast-command, fast-model,
Hermes-agent, memory, and barge-in paths, then shuts down cleanly. Produces a
structured result suitable for the final acceptance matrix.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from jarvis.adapters.fakes import ScriptedSTT
from jarvis.application.runtime import build_runtime
from jarvis.config import RuntimeConfig
from jarvis.core.contracts import Transcript
from jarvis.core.turn import TurnContext

# Known file content the hardware acceptance writes and expects to read back.
NOTAS_CONTENT = "contenido de prueba de notas"


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


async def _run_hardware_acceptance(config: RuntimeConfig) -> dict[str, Any]:
    """Prove wake -> command -> real file tool -> spoken response on the fake runtime.

    Points memory at a temporary database and confines the file tool to a
    temporary working directory containing a real ``notas.txt``. The scripted
    STT utters ``lee el archivo notas.txt``, which routes to the ``file`` tool,
    and the result is spoken back through the playback queue.
    """
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = replace(
            config, memory=replace(config.memory, db_path=str(tmp_path / "jarvis.db"))
        )
        os.chdir(tmp)
        try:
            (tmp_path / "notas.txt").write_text(NOTAS_CONTENT, encoding="utf-8")

            runtime = build_runtime(
                config,
                use_fakes=True,
                fake_stt=ScriptedSTT(
                    [Transcript("lee el archivo notas.txt", is_final=True)]
                ),
            )
            await runtime.start()
            startup_state = runtime.state.value
            try:
                await runtime.voice_loop.run()
            finally:
                await runtime.stop()
            shutdown_state = runtime.state.value

            return {
                "startup_state": startup_state,
                "shutdown_state": shutdown_state,
                "turns": [
                    {
                        "transcript": turn.transcript,
                        "route": turn.route,
                        "response": turn.response,
                        "cancelled": turn.cancelled,
                    }
                    for turn in runtime.voice_loop.turns
                ],
                "voice_loop": runtime.voice_loop.state(),
                "audio_queue": runtime.components.audio_output.state(),
            }
        finally:
            os.chdir(original_cwd)


def run_hardware_acceptance(config: RuntimeConfig) -> dict[str, Any]:
    """Run the scripted real-hardware acceptance and return its structured result."""
    return asyncio.run(_run_hardware_acceptance(config))
