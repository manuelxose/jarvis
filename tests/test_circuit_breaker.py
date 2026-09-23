import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.circuit_breaker import CircuitBreaker


class CircuitBreakerTests(unittest.TestCase):
    def test_allows_requests_while_closed(self):
        breaker = CircuitBreaker(failure_threshold=3)
        self.assertTrue(breaker.allow("p"))
        breaker.record_failure("p")
        breaker.record_failure("p")
        self.assertTrue(breaker.allow("p"))  # below threshold, still closed

    def test_opens_after_threshold_failures(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
        breaker.record_failure("p")
        breaker.record_failure("p")
        self.assertFalse(breaker.allow("p"))
        self.assertTrue(breaker.is_open("p"))

    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure("p")
        breaker.record_success("p")
        breaker.record_failure("p")
        self.assertTrue(breaker.allow("p"))  # only one consecutive failure since reset

    def test_stays_open_before_cooldown_elapses(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0)
        breaker.record_failure("p")
        self.assertFalse(breaker.allow("p"))

    def test_half_open_probe_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        breaker.record_failure("q")
        self.assertTrue(breaker.allow("q"))  # zero cooldown: immediately eligible to probe

    def test_only_one_probe_in_flight(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        breaker.record_failure("p")
        self.assertTrue(breaker.allow("p"))  # consumes the probe slot
        self.assertFalse(breaker.allow("p"))  # a second concurrent probe is rejected

    def test_probe_success_closes_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.0)
        breaker.record_failure("p")
        self.assertTrue(breaker.allow("p"))
        breaker.record_success("p")
        self.assertTrue(breaker.allow("p"))
        self.assertFalse(breaker.is_open("p"))

    def test_probe_failure_reopens_and_restarts_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0)
        with mock.patch("jarvis.core.circuit_breaker.time.monotonic", return_value=0.0):
            breaker.record_failure("p")
        with mock.patch("jarvis.core.circuit_breaker.time.monotonic", return_value=61.0):
            self.assertTrue(breaker.allow("p"))  # cooldown elapsed: one probe allowed
            breaker.record_failure("p")  # probe failed: reopen and restart the cooldown
        with mock.patch("jarvis.core.circuit_breaker.time.monotonic", return_value=90.0):
            self.assertFalse(breaker.allow("p"))  # only 29s since the probe failure


if __name__ == "__main__":
    unittest.main()
