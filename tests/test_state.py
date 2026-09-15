import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.contracts import HealthReport, HealthStatus, ManagedComponent
from jarvis.core.events import HealthChanged, RuntimeStateChanged
from jarvis.core.state import InvalidTransition, RuntimeState, transition


class RuntimeStateTests(unittest.TestCase):
    def test_allows_the_standard_turn_lifecycle(self):
        state = RuntimeState.STARTING
        for target in (
            RuntimeState.READY,
            RuntimeState.LISTENING,
            RuntimeState.THINKING,
            RuntimeState.SPEAKING,
        ):
            state = transition(state, target)

        self.assertEqual(state, RuntimeState.SPEAKING)

    def test_rejects_failed_to_speaking(self):
        with self.assertRaises(InvalidTransition):
            transition(RuntimeState.FAILED, RuntimeState.SPEAKING)

    def test_allows_stopping_to_failed_for_required_shutdown_failure(self):
        self.assertEqual(
            RuntimeState.FAILED,
            transition(RuntimeState.STOPPING, RuntimeState.FAILED),
        )

    def test_health_contract_and_events_are_typed_records(self):
        report = HealthReport("audio", HealthStatus.DEGRADED, "unavailable", False, True)

        self.assertEqual(report.name, "audio")
        self.assertEqual(HealthChanged(report).report, report)
        self.assertEqual(
            RuntimeStateChanged(RuntimeState.READY, RuntimeState.LISTENING).current,
            RuntimeState.LISTENING,
        )
        self.assertTrue(hasattr(ManagedComponent, "start"))


if __name__ == "__main__":
    unittest.main()
