"""Unit and integration tests for the turn manager.

The turn manager owns one voice interaction end-to-end: it routes the
transcript, executes the chosen path (fast command, fast model, or Hermes),
propagates cooperative cancellation through every stage, and records trace
timing. These tests use the deterministic fakes from ``jarvis.adapters.fakes``
plus a few inline doubles to exercise cancellation and barge-in without audio
hardware or provider credentials.
"""

import asyncio
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import (
    EchoTTS,
    RecordingAudioPlayer,
    ScriptedHermes,
    ScriptedMemory,
    ScriptedModel,
)
from jarvis.application.routing import Router
from jarvis.application.turn_manager import SentenceChunker, TurnManager, TurnResult
from jarvis.core.contracts import AgentStatus, AgentToken, AgentToolRequest, TurnContext
from jarvis.core.errors import ToolExecutionError
from jarvis.core.state import RuntimeState
from jarvis.core.turn import TurnCancelled


async def _noop_tools(name, arguments, context):
    context.cancellation.raise_if_cancelled()
    return "ok"


def _make_manager(*, model=None, tts=None, audio=None, hermes=None, tools=None,
                  memory=None, state_setter=None):
    return TurnManager(
        router=Router(),
        tools=tools or _noop_tools,
        model=model if model is not None else ScriptedModel(),
        tts=tts if tts is not None else EchoTTS(),
        audio=audio if audio is not None else RecordingAudioPlayer(),
        hermes=hermes,
        memory=memory,
        state_setter=state_setter,
    )


class _SlowModel:
    """Stream tokens slowly so a concurrent interrupt can barge in."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay

    async def generate(self, prompt, context):
        for word in ("hola", "que", "tal", "amigo"):
            context.cancellation.raise_if_cancelled()
            yield word + " "
            await asyncio.sleep(self.delay)


class _SlowHermes:
    """Emit agent tokens slowly so a concurrent interrupt can barge in."""

    async def respond(self, text, context):
        for i in range(20):
            context.cancellation.raise_if_cancelled()
            yield AgentToken(f"token{i}")
            await asyncio.sleep(0.05)


class TurnResultTests(unittest.TestCase):
    def test_turn_result_is_frozen_and_observable(self):
        result = TurnResult(
            transcript="hola",
            response="respuesta",
            route="fast_model",
            elapsed_ms=12.5,
            trace={"trace_id": "abc"},
        )

        self.assertEqual("hola", result.transcript)
        self.assertEqual("respuesta", result.response)
        self.assertEqual("fast_model", result.route)
        self.assertEqual(12.5, result.elapsed_ms)
        self.assertEqual({"trace_id": "abc"}, result.trace)
        self.assertFalse(result.cancelled)

        with self.assertRaises(FrozenInstanceError):
            result.response = "otra"


class SentenceChunkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_at_sentence_boundary(self):
        # The first sentence exceeds the 40-char minimum so it flushes at the
        # sentence boundary; the trailing short phrase flushes at end of stream.
        first = "Este es un mensaje lo suficientemente largo como para superar el minimo."

        async def tokens():
            yield first
            yield " Adios."

        chunks = []
        async for chunk in SentenceChunker(tokens(), TurnContext.fresh("c")):
            chunks.append(chunk)

        self.assertEqual([first, "Adios."], chunks)

    async def test_chunker_flushes_remaining_text_without_boundary(self):
        async def tokens():
            yield "texto sin punto final"

        chunks = []
        async for chunk in SentenceChunker(tokens(), TurnContext.fresh("c")):
            chunks.append(chunk)

        self.assertEqual(["texto sin punto final"], chunks)

    async def test_chunker_raises_when_context_cancelled(self):
        context = TurnContext.fresh("c")
        context.cancellation.cancel()

        async def tokens():
            yield "texto"

        with self.assertRaises(TurnCancelled):
            async for _ in SentenceChunker(tokens(), context):
                pass


class TurnManagerFastCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_command_invokes_tool_and_returns_result(self):
        calls = []

        async def tools(name, arguments, context):
            calls.append((name, arguments))
            return "hecho"

        manager = _make_manager(tools=tools)
        result = await manager.handle("sube el volumen")

        self.assertEqual("fast_command", result.route)
        self.assertEqual("hecho", result.response)
        self.assertEqual([("volume_up", {})], calls)
        self.assertFalse(result.cancelled)

    async def test_fast_command_none_result_uses_default_ack(self):
        async def tools(name, arguments, context):
            return None

        manager = _make_manager(tools=tools)
        result = await manager.handle("sube el volumen")

        self.assertEqual("He subido el volumen.", result.response)

    async def test_fast_command_tool_error_returns_message(self):
        async def tools(name, arguments, context):
            raise ToolExecutionError("la herramienta fallo")

        manager = _make_manager(tools=tools)
        result = await manager.handle("sube el volumen")

        self.assertEqual("la herramienta fallo", result.response)

    async def test_fast_command_records_trace_stages(self):
        manager = _make_manager()
        result = await manager.handle("sube el volumen")

        self.assertIn("trace_id", result.trace)
        self.assertIn("routing_ms", result.trace)
        self.assertIn("total_request_ms", result.trace)
        self.assertGreaterEqual(result.elapsed_ms, 0.0)


class TurnManagerFastModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_model_streams_and_plays(self):
        model = ScriptedModel(default="Hola mundo.")
        tts = EchoTTS()
        audio = RecordingAudioPlayer()
        manager = _make_manager(model=model, tts=tts, audio=audio)

        result = await manager.handle("cual es la capital de francia")

        self.assertEqual("fast_model", result.route)
        self.assertEqual("Hola mundo.", result.response)
        self.assertTrue(tts.chunks)
        self.assertTrue(audio.played)
        self.assertIn("playback_start_ms", result.trace)


class TurnManagerHermesTests(unittest.IsolatedAsyncioTestCase):
    async def test_hermes_collects_tokens_and_handles_tools(self):
        tool_calls = []

        async def tools(name, arguments, context):
            tool_calls.append(name)
            return "ok"

        hermes = ScriptedHermes(
            [
                AgentStatus("started"),
                AgentToken("resultado"),
                AgentToolRequest("open_url", {"url": "https://example.com"}),
                AgentToken("final."),
            ]
        )
        manager = _make_manager(hermes=hermes, tools=tools)

        result = await manager.handle("investiga algo")

        self.assertEqual("hermes", result.route)
        self.assertEqual("resultado final.", result.response)
        self.assertEqual(["open_url"], tool_calls)

    async def test_hermes_without_tokens_returns_fallback(self):
        hermes = ScriptedHermes([AgentStatus("started")])
        manager = _make_manager(hermes=hermes)

        result = await manager.handle("planifica una tarea")

        self.assertEqual("No he podido completar la tarea.", result.response)


class TurnManagerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_cancels_active_turn(self):
        manager = _make_manager(model=_SlowModel(delay=0.05))
        task = asyncio.create_task(manager.handle("hola jarvis"))
        await asyncio.sleep(0.01)

        self.assertTrue(manager.active)
        await manager.interrupt()

        result = await task
        self.assertTrue(result.cancelled)
        self.assertEqual("fast_model", result.route)
        self.assertFalse(manager.active)

    async def test_interrupt_when_idle_is_noop(self):
        manager = _make_manager()

        self.assertFalse(manager.active)
        await manager.interrupt()
        self.assertFalse(manager.active)

    async def test_hermes_cancellation_propagates(self):
        manager = _make_manager(hermes=_SlowHermes())
        task = asyncio.create_task(manager.handle("planifica una tarea"))
        await asyncio.sleep(0.01)

        await manager.interrupt()

        result = await task
        self.assertTrue(result.cancelled)
        self.assertEqual("hermes", result.route)
        self.assertFalse(manager.active)

    async def test_tool_cancellation_is_surfaced_as_cancelled(self):
        async def tools(name, arguments, context):
            raise TurnCancelled()

        manager = _make_manager(tools=tools)

        result = await manager.handle("sube el volumen")

        self.assertTrue(result.cancelled)
        self.assertEqual("fast_command", result.route)


class TurnManagerStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_sets_thinking_state_before_execution(self):
        states = []
        manager = _make_manager(state_setter=states.append)

        await manager.handle("sube el volumen")

        self.assertEqual([RuntimeState.THINKING], states)


class TurnManagerMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_recall_augments_prompt(self):
        model = ScriptedModel()
        memory = ScriptedMemory(["recuerdo importante"])
        manager = _make_manager(model=model, memory=memory)

        await manager.handle("cual es la capital de francia")

        self.assertIn("recuerdo importante", model.calls[0])

    async def test_memory_failure_falls_back_to_plain_prompt(self):
        class FailingMemory:
            async def recall(self, query, context):
                raise RuntimeError("boom")

        model = ScriptedModel()
        manager = _make_manager(model=model, memory=FailingMemory())

        await manager.handle("cual es la capital de francia")

        self.assertEqual("cual es la capital de francia", model.calls[0])


if __name__ == "__main__":
    unittest.main()
