import sys
import time
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.events import TurnCompleted, TurnStarted
from jarvis.core.turn import CancellationToken, TurnCancelled, TurnContext


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


if __name__ == "__main__":
    unittest.main()
