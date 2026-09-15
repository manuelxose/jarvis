import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.lifecycle import Supervisor, SupervisorPolicy
from jarvis.core.state import RuntimeState


class FakeComponent:
    def __init__(
        self,
        name: str,
        *,
        required: bool = True,
        start_failures: int = 0,
        stop_fails: bool = False,
        retryable: bool = False,
        calls: list[str] | None = None,
    ) -> None:
        self.name = name
        self.required = required
        self.start_failures = start_failures
        self.stop_fails = stop_fails
        self.retryable = retryable
        self.calls = calls if calls is not None else []
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1
        self.calls.append(f"start:{self.name}")
        if self.starts <= self.start_failures:
            raise RuntimeError(f"{self.name} unavailable")

    async def stop(self) -> None:
        self.stops += 1
        self.calls.append(f"stop:{self.name}")
        if self.stop_fails:
            raise RuntimeError(f"{self.name} would not stop")

    async def health(self) -> HealthReport:
        status = HealthStatus.HEALTHY if self.starts > self.start_failures else HealthStatus.FAILED
        return HealthReport(
            self.name,
            status,
            required=self.required,
            retryable=self.retryable,
        )


class BlockingComponent(FakeComponent):
    def __init__(self, name: str, release: asyncio.Event) -> None:
        super().__init__(name)
        self.entered = asyncio.Event()
        self.release = release

    async def start(self) -> None:
        self.starts += 1
        self.entered.set()
        await self.release.wait()


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_independent_components_concurrently(self) -> None:
        release = asyncio.Event()
        first = BlockingComponent("first", release)
        second = BlockingComponent("second", release)
        supervisor = Supervisor([first, second])
        task = asyncio.create_task(supervisor.start())

        await asyncio.wait_for(
            asyncio.gather(first.entered.wait(), second.entered.wait()), timeout=0.01
        )
        release.set()
        await task

    async def test_starts_independent_components_and_reports_ready(self) -> None:
        first = FakeComponent("first")
        second = FakeComponent("second")
        supervisor = Supervisor([first, second])

        await supervisor.start()

        self.assertEqual(RuntimeState.READY, supervisor.state)
        self.assertEqual(1, first.starts)
        self.assertEqual(1, second.starts)
        self.assertEqual({"first", "second"}, set(supervisor.health_snapshot()))

    async def test_optional_start_failure_degrades_without_stopping_healthy_components(self) -> None:
        healthy = FakeComponent("healthy")
        optional = FakeComponent("optional", required=False, start_failures=1)
        supervisor = Supervisor([healthy, optional])

        await supervisor.start()

        self.assertEqual(RuntimeState.DEGRADED, supervisor.state)
        self.assertEqual(1, healthy.starts)
        self.assertEqual(HealthStatus.FAILED, supervisor.health_snapshot()["optional"].status)

    async def test_restarts_retryable_component_only_up_to_policy_limit(self) -> None:
        component = FakeComponent("flaky", start_failures=3, retryable=True)
        supervisor = Supervisor([component], policy=SupervisorPolicy(retries=2, backoff_seconds=0))

        await supervisor.start()

        self.assertEqual(3, component.starts)
        self.assertEqual(RuntimeState.FAILED, supervisor.state)

    async def test_stop_is_idempotent_and_reverses_startup_order(self) -> None:
        calls: list[str] = []
        first = FakeComponent("first", calls=calls)
        second = FakeComponent("second", calls=calls)
        supervisor = Supervisor([first, second])
        await supervisor.start()

        await supervisor.stop()
        await supervisor.stop()

        self.assertEqual(RuntimeState.STOPPING, supervisor.state)
        self.assertEqual(["stop:second", "stop:first"], calls[2:])
        self.assertEqual(1, first.stops)
        self.assertEqual(1, second.stops)

    async def test_run_until_stopped_returns_when_stopped(self) -> None:
        supervisor = Supervisor([])
        task = asyncio.create_task(supervisor.run_until_stopped())

        await asyncio.sleep(0)
        await supervisor.stop()
        await task

    async def test_required_shutdown_failure_marks_the_supervisor_failed(self) -> None:
        supervisor = Supervisor([FakeComponent("stuck", stop_fails=True)])
        await supervisor.start()

        await supervisor.stop()

        self.assertEqual(RuntimeState.FAILED, supervisor.state)
        self.assertEqual(HealthStatus.FAILED, supervisor.health_snapshot()["stuck"].status)


if __name__ == "__main__":
    unittest.main()
