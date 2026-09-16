import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.core.contracts import HealthReport, HealthStatus
from jarvis.core.events import HealthChanged, RuntimeStateChanged
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


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[HealthChanged | RuntimeStateChanged] = []

    async def publish(self, event: HealthChanged | RuntimeStateChanged) -> None:
        self.events.append(event)


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
        self.assertIsInstance(supervisor.health_snapshot(), tuple)
        self.assertEqual({"first", "second"}, {report.name for report in supervisor.health_snapshot()})

    async def test_optional_start_failure_degrades_without_stopping_healthy_components(self) -> None:
        healthy = FakeComponent("healthy")
        optional = FakeComponent("optional", required=False, start_failures=1)
        supervisor = Supervisor([healthy, optional])

        await supervisor.start()

        self.assertEqual(RuntimeState.DEGRADED, supervisor.state)
        self.assertEqual(1, healthy.starts)
        self.assertEqual(HealthStatus.FAILED, supervisor.health_snapshot()[1].status)

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

    async def test_stop_during_startup_leaves_supervisor_stopping(self) -> None:
        release = asyncio.Event()
        component = BlockingComponent("blocked", release)
        supervisor = Supervisor([component])
        task = asyncio.create_task(supervisor.run_until_stopped())

        await asyncio.wait_for(component.entered.wait(), timeout=0.01)
        await supervisor.stop()
        release.set()
        await task

        self.assertEqual(RuntimeState.STOPPING, supervisor.state)
        self.assertEqual(1, component.stops)

    async def test_required_shutdown_failure_marks_the_supervisor_failed(self) -> None:
        supervisor = Supervisor([FakeComponent("stuck", stop_fails=True)])
        await supervisor.start()

        await supervisor.stop()

        self.assertEqual(RuntimeState.FAILED, supervisor.state)
        self.assertEqual(HealthStatus.FAILED, supervisor.health_snapshot()[0].status)

    async def test_concurrent_start_callers_share_startup_completion(self):
        release = asyncio.Event()
        component = BlockingComponent("blocked", release)
        supervisor = Supervisor([component])
        first = asyncio.create_task(supervisor.start())
        await component.entered.wait()
        second = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0)
        pending = not second.done()
        release.set()
        await asyncio.gather(first, second)
        await supervisor.stop()
        self.assertTrue(pending)
        self.assertEqual(component.starts, 1)

    async def test_stop_awaits_cancelled_start_before_releasing_resources(self):
        release = asyncio.Event()
        cancelled = asyncio.Event()

        class LateAcquisition(BlockingComponent):
            active = False

            async def start(self):
                self.entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                self.active = True

            async def stop(self):
                await super().stop()
                self.active = False

        component = LateAcquisition("late", release)
        supervisor = Supervisor([component])
        start = asyncio.create_task(supervisor.start())
        await component.entered.wait()
        stop = asyncio.create_task(supervisor.stop())
        try:
            await asyncio.wait_for(cancelled.wait(), 0.1)
            self.assertFalse(stop.done())
        finally:
            release.set()
            await asyncio.gather(start, stop)
        self.assertFalse(component.active)
        self.assertEqual(component.stops, 1)

    async def test_concurrent_stops_and_runner_wait_for_shared_cleanup(self):
        release = asyncio.Event()
        entered = asyncio.Event()

        class SlowStop(FakeComponent):
            async def stop(self):
                entered.set()
                await release.wait()
                await super().stop()

        component = SlowStop("slow")
        supervisor = Supervisor([component])
        await supervisor.start()
        runner = asyncio.create_task(supervisor.run_until_stopped())
        first = asyncio.create_task(supervisor.stop())
        await entered.wait()
        second = asyncio.create_task(supervisor.stop())
        await asyncio.sleep(0)
        pending = not second.done() and not runner.done()
        release.set()
        await asyncio.gather(first, second, runner)
        self.assertTrue(pending)
        self.assertEqual(component.stops, 1)

    async def test_runner_cancellation_cleans_up_during_and_after_startup(self):
        for during_startup in (False, True):
            with self.subTest(during_startup=during_startup):
                release = asyncio.Event()
                component = BlockingComponent("cancelled", release)
                supervisor = Supervisor([component])
                if not during_startup:
                    release.set()
                    await supervisor.start()
                runner = asyncio.create_task(supervisor.run_until_stopped())
                await component.entered.wait()
                await asyncio.sleep(0)
                runner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await runner
                self.assertEqual(component.stops, 1)
                self.assertEqual(supervisor.state, RuntimeState.STOPPING)

    async def test_start_after_stop_does_not_acquire_components(self):
        component = FakeComponent("unused")
        supervisor = Supervisor([component])
        await supervisor.stop()
        await supervisor.start()
        self.assertEqual(component.starts, 0)

    async def test_retry_releases_each_failed_acquisition(self):
        class Resource(FakeComponent):
            active = 0
            peak = 0

            async def start(self):
                self.active += 1
                self.peak = max(self.peak, self.active)
                await super().start()

            async def stop(self):
                await super().stop()
                self.active -= 1

        for failures in (2, 3):
            with self.subTest(failures=failures):
                component = Resource("resource", start_failures=failures, retryable=True)
                supervisor = Supervisor([component], policy=SupervisorPolicy(backoff_seconds=0))
                await supervisor.start()
                await supervisor.stop()
                self.assertEqual(component.peak, 1)
                self.assertEqual(component.active, 0)
                self.assertEqual(component.starts, component.stops)

    async def test_retry_does_not_start_again_when_attempt_cleanup_fails(self):
        component = FakeComponent("resource", start_failures=1, retryable=True, stop_fails=True)
        supervisor = Supervisor([component], policy=SupervisorPolicy(backoff_seconds=0))
        await supervisor.start()
        self.assertEqual(component.starts, 1)
        self.assertEqual(supervisor.state, RuntimeState.FAILED)
        self.assertIn("would not stop", supervisor.health_snapshot()[0].detail)
        component.stop_fails = False
        await supervisor.stop()

    async def test_retry_cleans_up_a_started_but_unhealthy_component(self):
        class Unhealthy(FakeComponent):
            async def health(self):
                return HealthReport(self.name, HealthStatus.FAILED if self.starts == 1 else HealthStatus.HEALTHY,
                                    retryable=True)

        component = Unhealthy("resource")
        supervisor = Supervisor([component], policy=SupervisorPolicy(backoff_seconds=0))
        await supervisor.start()
        await supervisor.stop()
        self.assertEqual(component.calls, ["start:resource", "stop:resource", "start:resource", "stop:resource"])

    async def test_cancelled_stop_waiter_does_not_abandon_cleanup(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowStop(FakeComponent):
            async def stop(self):
                entered.set()
                await release.wait()
                await super().stop()

        component = SlowStop("slow")
        supervisor = Supervisor([component])
        await supervisor.start()
        stop = asyncio.create_task(supervisor.stop())
        await entered.wait()
        stop.cancel()
        await asyncio.sleep(0)
        pending = not stop.done()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await stop
        await supervisor.stop()
        self.assertTrue(pending)
        self.assertEqual(component.stops, 1)

    async def test_cancelled_component_cleanup_does_not_skip_siblings(self):
        class CancelledStop(FakeComponent):
            async def stop(self):
                raise asyncio.CancelledError()

        healthy = FakeComponent("healthy")
        supervisor = Supervisor([healthy, CancelledStop("cancelled")])
        await supervisor.start()
        await supervisor.stop()
        self.assertEqual(healthy.stops, 1)
        self.assertEqual(supervisor.state, RuntimeState.FAILED)
        self.assertIn("CancelledError", supervisor.health_snapshot()[1].detail)

    async def test_delivery_and_stop_errors_do_not_skip_other_cleanup(self):
        class BrokenSink:
            async def publish(self, event):
                raise RuntimeError("delivery failed")

        healthy = FakeComponent("healthy")
        stuck = FakeComponent("stuck", stop_fails=True)
        supervisor = Supervisor([healthy, stuck], event_sink=BrokenSink())
        await supervisor.start()
        await supervisor.stop()
        await supervisor.stop()
        self.assertEqual((healthy.stops, stuck.stops), (1, 1))
        self.assertEqual(supervisor.state, RuntimeState.FAILED)
        details = " ".join(report.detail for report in supervisor.health_snapshot())
        self.assertIn("delivery failed", details)
        self.assertIn("would not stop", details)

    async def test_shutdown_publish_error_does_not_skip_cleanup(self):
        class BrokenShutdownSink:
            async def publish(self, event):
                if isinstance(event, RuntimeStateChanged) and event.current is RuntimeState.STOPPING:
                    raise RuntimeError("shutdown delivery failed")

        component = FakeComponent("healthy")
        supervisor = Supervisor([component], event_sink=BrokenShutdownSink())
        await supervisor.start()
        await supervisor.stop()
        await supervisor.stop()
        self.assertEqual(component.stops, 1)
        self.assertEqual(supervisor.state, RuntimeState.FAILED)

    async def test_publishes_typed_startup_and_shutdown_events(self) -> None:
        sink = RecordingEventSink()
        supervisor = Supervisor([FakeComponent("audio")], event_sink=sink)

        await supervisor.start()
        await supervisor.stop()

        self.assertEqual([HealthChanged, RuntimeStateChanged, RuntimeStateChanged], [
            type(event) for event in sink.events
        ])
        self.assertEqual("audio", sink.events[0].report.name)
        self.assertEqual(HealthStatus.HEALTHY, sink.events[0].report.status)
        self.assertEqual(
            (RuntimeState.STARTING, RuntimeState.READY),
            (sink.events[1].previous, sink.events[1].current),
        )
        self.assertEqual(
            (RuntimeState.READY, RuntimeState.STOPPING),
            (sink.events[2].previous, sink.events[2].current),
        )


if __name__ == "__main__":
    unittest.main()
