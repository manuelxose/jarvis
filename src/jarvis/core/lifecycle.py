"""Supervise managed runtime components and aggregate their health."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

from .contracts import HealthReport, HealthStatus, ManagedComponent
from .events import EventSink, HealthChanged, RuntimeStateChanged
from .state import RuntimeState, transition


@dataclass(frozen=True)
class SupervisorPolicy:
    retries: int = 2
    backoff_seconds: float = 0.25


class Supervisor:
    def __init__(
        self,
        components: Iterable[ManagedComponent],
        *,
        policy: SupervisorPolicy | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._components = tuple(components)
        self._policy = policy or SupervisorPolicy()
        self._event_sink = event_sink
        self._state = RuntimeState.STARTING
        self._stop_event = asyncio.Event()
        self._health: dict[str, HealthReport] = {}
        self._start_task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._active: dict[str, ManagedComponent] = {}

    @property
    def state(self) -> RuntimeState:
        return self._state

    def health_snapshot(self) -> tuple[HealthReport, ...]:
        return tuple(self._health.values())

    async def start(self) -> None:
        if self._stop_task is not None:
            await asyncio.shield(self._stop_task)
            return
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start())
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        try:
            await asyncio.gather(
                *(self._start_component(component) for component in self._components)
            )
        except asyncio.CancelledError:
            # Only stop() cancels this owned task. gather has awaited its children,
            # including adapters which finish an acquisition during cancellation.
            return
        for report in self.health_snapshot():
            if self._stop_task is not None:
                return
            await self._publish(HealthChanged(report))
        if self._stop_task is not None:
            return
        await self._set_state(self._aggregate_state(self._health.values()))

    async def stop(self) -> None:
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop())
        try:
            await asyncio.shield(self._stop_task)
        except asyncio.CancelledError:
            # Cancelling a waiter must not cancel or abandon shared cleanup.
            await asyncio.shield(self._stop_task)
            raise

    async def _stop(self) -> None:
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        await self._set_state(RuntimeState.STOPPING)
        shutdown_failed = False
        for component in reversed(self._components):
            if component.name in self._active and not await self._stop_component(component):
                await self._publish(HealthChanged(self._health[component.name]))
                shutdown_failed = shutdown_failed or component.required
        if shutdown_failed or "event_sink" in self._health:
            await self._set_state(RuntimeState.FAILED)
        self._stop_event.set()

    async def _stop_component(self, component: ManagedComponent) -> bool:
        try:
            await component.stop()
        except (Exception, asyncio.CancelledError) as error:
            previous = self._health.get(component.name)
            detail = f"stop: {str(error) or type(error).__name__}"
            if previous is not None and previous.detail:
                detail = f"{previous.detail}; {detail}"
            self._health[component.name] = HealthReport(
                component.name, HealthStatus.FAILED, detail, required=component.required,
            )
            return False
        self._active.pop(component.name, None)
        return True

    async def run_until_stopped(self) -> None:
        try:
            await self.start()
            await self._stop_event.wait()
        finally:
            await self.stop()

    async def _start_component(self, component: ManagedComponent) -> HealthReport:
        self._health[component.name] = HealthReport(
            component.name, HealthStatus.DEGRADED, "startup pending", required=component.required,
        )
        for attempt in range(self._policy.retries + 1):
            # start() may acquire resources before it raises or is cancelled.
            self._active[component.name] = component
            try:
                await component.start()
                report = await component.health()
            except Exception as error:
                report = await self._failure_report(component, error)
            self._health[component.name] = report
            if report.status is HealthStatus.HEALTHY or not report.retryable:
                return report
            if attempt < self._policy.retries:
                if not await self._stop_component(component):
                    return self._health[component.name]
                if self._policy.backoff_seconds:
                    await asyncio.sleep(min(self._policy.backoff_seconds, 0.25))
        return report

    async def _failure_report(
        self, component: ManagedComponent, error: Exception
    ) -> HealthReport:
        try:
            report = await component.health()
        except Exception:
            report = HealthReport(component.name, HealthStatus.FAILED, required=component.required)
        return HealthReport(
            component.name,
            HealthStatus.FAILED,
            str(error),
            required=component.required,
            retryable=report.retryable,
        )

    def _aggregate_state(self, reports: Iterable[HealthReport]) -> RuntimeState:
        reports = tuple(reports)
        if any(report.required and report.status is HealthStatus.FAILED for report in reports):
            return RuntimeState.FAILED
        if any(report.status is not HealthStatus.HEALTHY for report in reports):
            return RuntimeState.DEGRADED
        return RuntimeState.READY

    async def _set_state(self, target: RuntimeState) -> None:
        if target is self._state:
            return
        previous = self._state
        self._state = transition(previous, target)
        await self._publish(RuntimeStateChanged(previous, self._state))

    async def _publish(self, event: HealthChanged | RuntimeStateChanged) -> None:
        if self._event_sink is not None:
            try:
                await self._event_sink.publish(event)
            except (Exception, asyncio.CancelledError) as error:
                previous = self._health.get("event_sink")
                detail = str(error) or type(error).__name__
                if previous is not None:
                    detail = previous.detail if detail in previous.detail else f"{previous.detail}; {detail}"
                self._health["event_sink"] = HealthReport("event_sink", HealthStatus.FAILED, detail)
                # Do not publish recursively through a failed sink.
                if self._state is not RuntimeState.FAILED:
                    self._state = transition(self._state, RuntimeState.FAILED)
