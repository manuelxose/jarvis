"""Executable streaming port handoff, with no provider or audio SDKs."""

import asyncio
import inspect
import unittest
from typing import AsyncIterator, get_type_hints

from jarvis.config import RuntimeConfig
from jarvis.core import contracts
from jarvis.core.turn import CancellationToken, TurnContext


class StreamingContractTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_ports_return_typed_streams_directly(self):
        namespace = {**vars(contracts), "TurnContext": TurnContext, "RuntimeConfig": RuntimeConfig}
        for port, method, item in (
            (contracts.AudioCapture, "capture", bytes),
            (contracts.SpeechToText, "transcribe", contracts.Transcript),
            (contracts.TextToSpeech, "synthesize", bytes),
            (contracts.ModelProvider, "generate", str),
            (contracts.AgentRuntime, "respond", contracts.AgentEvent),
        ):
            with self.subTest(port=port.__name__):
                function = getattr(port, method)
                self.assertFalse(inspect.iscoroutinefunction(function))
                self.assertEqual(get_type_hints(function, namespace)["return"], AsyncIterator[item])
                self.assertEqual(get_type_hints(function, namespace)["context"], TurnContext)
        for port, method, argument, item in (
            (contracts.SpeechToText, "transcribe", "audio", bytes),
            (contracts.TextToSpeech, "synthesize", "text", str),
            (contracts.AudioPlayer, "play", "audio", bytes),
        ):
            self.assertEqual(get_type_hints(getattr(port, method), namespace)[argument], AsyncIterator[item])

    async def test_partial_transcript_tokens_agent_events_and_audio_arrive_incrementally(self):
        release = asyncio.Event()
        context = TurnContext("stream", "conversation", float("inf"), CancellationToken())

        class Capture:
            async def capture(self, context):
                yield b"first frame"
                await release.wait()
                yield b"last frame"

        class STT:
            async def transcribe(self, audio, context):
                async for frame in audio:
                    yield contracts.Transcript(frame.decode(), is_final=frame == b"last frame")

        class Model:
            async def generate(self, prompt, context):
                yield "first phrase"
                await release.wait()
                yield "last phrase"

        class TTS:
            async def synthesize(self, text, context):
                async for phrase in text:
                    yield phrase.encode()

        class Agent:
            async def respond(self, text, context):
                yield contracts.AgentToken("working")
                await release.wait()
                yield contracts.AgentToolRequest("clock", {})
                yield contracts.AgentStatus("completed")

        capture: contracts.AudioCapture = Capture()
        stt: contracts.SpeechToText = STT()
        model: contracts.ModelProvider = Model()
        tts: contracts.TextToSpeech = TTS()
        agent: contracts.AgentRuntime = Agent()
        transcripts = stt.transcribe(capture.capture(context), context)
        audio = tts.synthesize(model.generate("hello", context), context)
        events = agent.respond("time", context)
        self.assertEqual(await asyncio.wait_for(anext(transcripts), 1), contracts.Transcript("first frame", False))
        self.assertEqual(await asyncio.wait_for(anext(audio), 1), b"first phrase")
        self.assertEqual(await asyncio.wait_for(anext(events), 1), contracts.AgentToken("working"))
        release.set()
        self.assertEqual([part async for part in transcripts], [contracts.Transcript("last frame", True)])
        self.assertEqual([chunk async for chunk in audio], [b"last phrase"])
        self.assertEqual([event async for event in events], [
            contracts.AgentToolRequest("clock", {}), contracts.AgentStatus("completed"),
        ])
