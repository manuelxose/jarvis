import asyncio
import sys
import unittest

from jarvis.adapters.hermes import protocol
from jarvis.adapters.hermes.child import HermesChildAdapter, default_command
from jarvis.core.contracts import AgentStatus, AgentToken, AgentToolRequest, HealthStatus
from jarvis.core.turn import TurnCancelled, TurnContext


async def collect(adapter: HermesChildAdapter, text: str, context: TurnContext):
    return [event async for event in adapter.respond(text, context)]


class HermesChildAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_child_streams_tokens_tools_and_healthy_lifecycle(self):
        adapter = HermesChildAdapter(timeout_seconds=1)
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "estado del sistema", TurnContext.fresh("conversation"))

        self.assertTrue(any(isinstance(event, AgentToken) for event in events))
        self.assertTrue(any(isinstance(event, AgentToolRequest) for event in events))
        self.assertTrue(any(isinstance(event, AgentStatus) and event.detail == "done" for event in events))
        self.assertEqual(HealthStatus.HEALTHY, (await adapter.health()).status)
        await adapter.stop()
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_crashed_child_restarts_once_then_returns_degraded_fallback(self):
        adapter = HermesChildAdapter(timeout_seconds=1, restart_max=1)
        self.addAsyncCleanup(adapter.stop)

        first_failure = await collect(adapter, "__CRASH__", TurnContext.fresh("conversation"))
        recovered = await collect(adapter, "continua", TurnContext.fresh("conversation"))
        second_failure = await collect(adapter, "__CRASH__", TurnContext.fresh("conversation"))
        exhausted = await collect(adapter, "continua", TurnContext.fresh("conversation"))

        self.assertTrue(_unavailable(first_failure))
        self.assertTrue(any(isinstance(event, AgentToken) for event in recovered))
        self.assertTrue(_unavailable(second_failure))
        self.assertEqual("Hermes unavailable: Hermes restart budget exhausted", exhausted[0].detail)
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_timeout_degrades_and_terminates_unresponsive_child(self):
        adapter = HermesChildAdapter(
            [sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(5)"],
            timeout_seconds=0.02,
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "wait", TurnContext.fresh("conversation", timeout_seconds=1))

        self.assertTrue(_unavailable(events))
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_cancellation_interrupts_an_in_flight_child_read(self):
        adapter = HermesChildAdapter(
            [sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(5)"],
            timeout_seconds=1,
        )
        self.addAsyncCleanup(adapter.stop)
        context = TurnContext.fresh("conversation", timeout_seconds=1)

        consumer = asyncio.create_task(collect(adapter, "wait", context))
        await asyncio.sleep(0.1)
        context.cancellation.cancel()

        with self.assertRaises(TurnCancelled):
            await consumer
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_missing_command_reports_degraded_fallback_without_spawning(self):
        adapter = HermesChildAdapter([])

        events = await collect(adapter, "hello", TurnContext.fresh("conversation"))

        self.assertEqual("Hermes unavailable: Hermes command not configured", events[0].detail)
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_real_child_ignores_malformed_inbound_record(self):
        process = await asyncio.create_subprocess_exec(
            *default_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        self.addAsyncCleanup(_stop_process, process)
        process.stdin.write(b"[]\n")
        process.stdin.write(
            (
                protocol.encode_message("request", "turn", protocol.REQUEST, {"text": "hello"}) + "\n"
            ).encode()
        )
        await process.stdin.drain()

        message = protocol.parse_message(
            (await asyncio.wait_for(process.stdout.readline(), timeout=1)).decode()
        )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(protocol.STARTED, message.event_type)


def _unavailable(events) -> bool:
    return any(isinstance(event, AgentStatus) and event.detail.startswith("Hermes unavailable:") for event in events)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        await process.wait()


if __name__ == "__main__":
    unittest.main()
