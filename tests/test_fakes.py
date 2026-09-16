import unittest

from jarvis.adapters.fakes import (
    EchoTTS,
    FailingModel,
    RecordingAudioPlayer,
    ScriptedAudioInput,
    ScriptedHermes,
    ScriptedMemory,
    ScriptedModel,
    ScriptedSTT,
)
from jarvis.core.contracts import AgentStatus, AgentToken, Transcript
from jarvis.core.errors import ProviderUnavailable
from jarvis.core.turn import TurnCancelled, TurnContext


async def stream(items):
    for item in items:
        yield item


class FakeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_scripted_model_uses_default_mapping_and_callable_responses(self):
        default_model = ScriptedModel(default="respuesta predeterminada")
        default_context = TurnContext.fresh("test")
        default_tokens = [
            token async for token in default_model.generate("saludo", default_context)
        ]
        self.assertEqual(["respuesta ", "predeterminada "], default_tokens)
        self.assertEqual(["saludo"], default_model.calls)

        mapped_model = ScriptedModel({"clima": "hace sol"}, default="sin coincidencia")
        mapped_context = TurnContext.fresh("test")
        mapped_tokens = [
            token
            async for token in mapped_model.generate("dime el clima", mapped_context)
        ]
        self.assertEqual(["hace ", "sol "], mapped_tokens)
        self.assertEqual(["dime el clima"], mapped_model.calls)

        callable_model = ScriptedModel(lambda prompt: f"eco {prompt}")
        callable_context = TurnContext.fresh("test")
        callable_tokens = [
            token async for token in callable_model.generate("hola", callable_context)
        ]
        self.assertEqual(["eco ", "hola "], callable_tokens)
        self.assertEqual(["hola"], callable_model.calls)

    async def test_failing_model_fails_then_yields_fallback(self):
        model = FailingModel(failures=2)

        for _ in range(2):
            with self.assertRaises(ProviderUnavailable):
                [token async for token in model.generate("hola", TurnContext.fresh("test"))]

        tokens = [
            token async for token in model.generate("hola", TurnContext.fresh("test"))
        ]
        self.assertEqual(["respuesta de reserva"], tokens)
        self.assertEqual(3, model.attempts)

    async def test_echo_tts_records_and_encodes_each_chunk(self):
        tts = EchoTTS()
        chunks = [
            chunk
            async for chunk in tts.synthesize(
                stream(["hola ", "jarvis"]), TurnContext.fresh("test")
            )
        ]
        self.assertEqual([b"hola ", b"jarvis"], chunks)
        self.assertEqual(["hola ", "jarvis"], tts.chunks)

    async def test_scripted_stt_drains_audio_before_yielding_transcripts(self):
        consumed = []

        async def audio():
            for frame in [b"one", b"two"]:
                consumed.append(frame)
                yield frame

        transcripts = [Transcript("hola", is_final=False), Transcript("jarvis", is_final=True)]
        stt = ScriptedSTT(transcripts)
        actual = [
            transcript
            async for transcript in stt.transcribe(audio(), TurnContext.fresh("test"))
        ]
        self.assertEqual([b"one", b"two"], consumed)
        self.assertEqual(transcripts, actual)

    async def test_scripted_audio_input_yields_scripted_frames(self):
        frames = [b"first", b"second"]
        capture = ScriptedAudioInput(frames)
        actual = [frame async for frame in capture.capture(TurnContext.fresh("test"))]
        self.assertEqual(frames, actual)

    async def test_recording_audio_player_records_trace_id_and_chunks(self):
        player = RecordingAudioPlayer()
        context = TurnContext.fresh("test")
        await player.play(stream([b"one", b"two"]), context)
        self.assertEqual(
            [(context.trace_id, b"one"), (context.trace_id, b"two")], player.played
        )

    async def test_scripted_hermes_yields_scripted_events(self):
        events = [AgentStatus("started"), AgentToken("respuesta")]
        runtime = ScriptedHermes(events)
        actual = [
            event async for event in runtime.respond("hola", TurnContext.fresh("test"))
        ]
        self.assertEqual(events, actual)

    async def test_scripted_memory_returns_a_copy_of_scripted_memories(self):
        memories = ["prefiere español", "usa respuestas breves"]
        memory = ScriptedMemory(memories)
        actual = await memory.recall("preferencias", TurnContext.fresh("test"))
        self.assertEqual(memories, actual)
        self.assertIsNot(memories, actual)

    async def test_streaming_fakes_propagate_preexisting_cancellation(self):
        async def cancelled_context():
            context = TurnContext.fresh("test")
            context.cancellation.cancel()
            return context

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            [token async for token in ScriptedModel().generate("hola", context)]

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            [
                chunk
                async for chunk in EchoTTS().synthesize(stream(["hola"]), context)
            ]

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            [
                transcript
                async for transcript in ScriptedSTT().transcribe(stream([b"audio"]), context)
            ]

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            [frame async for frame in ScriptedAudioInput().capture(context)]

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            await RecordingAudioPlayer().play(stream([b"audio"]), context)

        context = await cancelled_context()
        with self.assertRaises(TurnCancelled):
            [event async for event in ScriptedHermes().respond("hola", context)]


if __name__ == "__main__":
    unittest.main()
