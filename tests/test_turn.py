import sys
import time
import unittest
from dataclasses import fields, FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.events import HealthChanged, RuntimeStateChanged, TurnCompleted, TurnStarted
from jarvis.core.events import TurnCancelled as TurnCancelledEvent
from jarvis.core.state import RuntimeState
from jarvis.core.turn import CancellationToken, TurnCancelled, TurnContext
from jarvis.config import (
    MemorySettings,
    ProviderSettings,
    RuntimeConfig,
    RuntimeSettings,
    SecuritySettings,
)


class CancellationTokenTests(unittest.TestCase):
    def test_cancellation_is_idempotent_and_observable(self):
        token = CancellationToken()

        self.assertFalse(token.cancelled)
        token.cancel()
        token.cancel()

        self.assertTrue(token.cancelled)
        self.assertTrue(token.wait(0))

    def test_raise_if_cancelled_raises_local_exception(self):
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(TurnCancelled):
            token.raise_if_cancelled()


class TurnContextTests(unittest.TestCase):
    def test_from_config_creates_deadline_trace_and_fresh_cancellation(self):
        config = RuntimeConfig(
            runtime=RuntimeSettings(command_deadline_ms=250),
            providers=ProviderSettings(),
            memory=MemorySettings(),
            security=SecuritySettings(),
        )

        with patch("jarvis.core.turn.time.monotonic", return_value=10.0):
            context = TurnContext.from_config(config, "conversation")
            other_context = TurnContext.from_config(config, "other-conversation")

        self.assertEqual(context.deadline_monotonic, 10.25)
        self.assertEqual(context.conversation_id, "conversation")
        self.assertRegex(context.trace_id, r"[0-9a-f]{32}")
        self.assertFalse(context.cancellation.cancelled)
        self.assertIsNot(context.cancellation, other_context.cancellation)

    def test_expired_deadline_is_observable(self):
        context = TurnContext(
            trace_id="trace",
            conversation_id="conversation",
            deadline_monotonic=time.monotonic() - 0.001,
            cancellation=CancellationToken(),
        )

        self.assertTrue(context.expired)

    def test_turn_events_are_immutable_records(self):
        context = TurnContext(
            trace_id="trace",
            conversation_id="conversation",
            deadline_monotonic=time.monotonic() + 1,
            cancellation=CancellationToken(),
        )

        self.assertEqual(TurnStarted(context).context, context)
        self.assertEqual(TurnCompleted(context, 12.5).elapsed_ms, 12.5)
        with self.assertRaises(AttributeError):
            TurnStarted(context).context = context

        events = (
            TurnStarted(context), TurnCompleted(context, 12.5), TurnCancelledEvent(context),
            RuntimeStateChanged(RuntimeState.STARTING, RuntimeState.READY),
            HealthChanged(HealthReport("audio", HealthStatus.HEALTHY)),
        )
        for event in events:
            for field in fields(event):
                with self.subTest(event=type(event).__name__, field=field.name):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(event, field.name, getattr(event, field.name))


if __name__ == "__main__":
    unittest.main()
