import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.output import AudioOutputQueue
from jarvis.adapters.fakes import ScriptedAudioInput
from jarvis.core.turn import TurnCancelled, TurnContext


class AudioQueueTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_non_positive_queue_limits(self):
        with self.assertRaises(ValueError):
            AudioOutputQueue(max_pending=0)
        with self.assertRaises(ValueError):
            AudioOutputQueue(render_timeout_seconds=0)

    async def test_plays_chunks_in_order_with_turn_tags(self):
        played = []
        async def render(turn_id, chunk):
            played.append((turn_id, chunk))

        queue = AudioOutputQueue(render=render)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"
            yield b"c"

        await queue.play(audio(), context)
        self.assertEqual([chunk for _, chunk in played], [b"a", b"b", b"c"])
        self.assertTrue(all(tid == context.trace_id for tid, _ in played))
        self.assertEqual(3, queue.state()["played"])

    async def test_flush_drops_pending_chunks_for_turn(self):
        released = asyncio.Event()
        played = []

        async def render(turn_id, chunk):
            played.append(chunk)
            await released.wait()

        queue = AudioOutputQueue(render=render)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"

        task = asyncio.create_task(queue.play(audio(), context))
        await asyncio.sleep(0.01)
        queue.flush(context.trace_id)
        released.set()
        await task
        # chunk "a" may or may not have played; chunk "b" must be dropped.
        self.assertNotIn(b"b", played)

    async def test_cancellation_propagates_from_input_stream(self):
        queue = AudioOutputQueue()
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            context.cancellation.cancel()
            yield b"b"

        with self.assertRaises(TurnCancelled):
            await queue.play(audio(), context)
        # No queued chunk may survive cancellation.
        self.assertEqual(0, queue.state()["pending"])

    async def test_device_error_does_not_wedge_queue(self):
        async def render(turn_id, chunk):
            raise RuntimeError("device lost")

        queue = AudioOutputQueue(render=render)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"

        await queue.play(audio(), context)
        self.assertEqual(2, queue.state()["played"])
        self.assertEqual(2, queue.state()["render_errors"])

    async def test_renderer_timeout_does_not_wedge_queue(self):
        async def render(turn_id, chunk):
            await asyncio.Event().wait()

        queue = AudioOutputQueue(render=render, render_timeout_seconds=0.01)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"

        await queue.play(audio(), context)
        self.assertEqual(2, queue.state()["played"])
        self.assertEqual(2, queue.state()["render_timeouts"])

    async def test_play_waits_for_active_chunk_to_finish(self):
        started = asyncio.Event()
        released = asyncio.Event()

        async def render(turn_id, chunk):
            started.set()
            await released.wait()

        queue = AudioOutputQueue(render=render)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"

        task = asyncio.create_task(queue.play(audio(), context))
        await started.wait()
        self.assertFalse(task.done())
        self.assertEqual(context.trace_id, queue.state()["current_turn"])

        released.set()
        await task
        self.assertIsNone(queue.state()["current_turn"])

    async def test_cancellation_while_draining_flushes_pending_audio(self):
        started = asyncio.Event()
        released = asyncio.Event()
        played = []

        async def render(turn_id, chunk):
            played.append(chunk)
            started.set()
            await released.wait()

        queue = AudioOutputQueue(render=render)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"

        task = asyncio.create_task(queue.play(audio(), context))
        await started.wait()
        context.cancellation.cancel()

        with self.assertRaises(TurnCancelled):
            await task
        self.assertEqual(0, queue.state()["pending"])
        self.assertNotIn(b"b", played)

        released.set()
        await asyncio.sleep(0)

    async def test_backpressures_a_fast_producer_when_device_is_blocked(self):
        started = asyncio.Event()
        released = asyncio.Event()

        async def render(turn_id, chunk):
            started.set()
            await released.wait()

        queue = AudioOutputQueue(render=render, max_pending=1)
        context = TurnContext.fresh("c")

        async def audio():
            yield b"a"
            yield b"b"
            yield b"c"

        task = asyncio.create_task(queue.play(audio(), context))
        await started.wait()
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        self.assertEqual(1, queue.state()["pending"])
        self.assertEqual(1, queue.state()["capacity"])

        released.set()
        await task


if __name__ == "__main__":
    unittest.main()
