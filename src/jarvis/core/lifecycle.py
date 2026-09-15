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
        self._started = False
        self._stopped = False

    @property
    def state(self) -> RuntimeState:
        return self._state

    def health_snapshot(self) -> dict[str, HealthReport]:
        return dict(self._health)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        reports = await asyncio.gather(
            *(self._start_component(component) for component in self._components)
        )
        self._health = {report.name: report for report in reports}
        for report in reports:
            await self._publish(HealthChanged(report))
        await self._set_state(self._aggregate_state(reports))

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        await self._set_state(RuntimeState.STOPPING)
        shutdown_failed = False
        for component in reversed(self._components):
            try:
                await component.stop()
            except Exception as error:
                report = HealthReport(
                    component.name,
                    HealthStatus.FAILED,
                    str(error),
                    required=component.required,
                )
                self._health[component.name] = report
                await self._publish(HealthChanged(report))
                shutdown_failed = shutdown_failed or component.required
        if shutdown_failed:
            # STOPPING is terminal in the shared transition table; a required
            # shutdown failure is the lifecycle exception that must surface as failed.
            previous = self._state
            self._state = RuntimeState.FAILED
            await self._publish(RuntimeStateChanged(previous, self._state))

    async def run_until_stopped(self) -> None:
        await self.start()
        await self._stop_event.wait()

    async def _start_component(self, component: ManagedComponent) -> HealthReport:
        for attempt in range(self._policy.retries + 1):
            try:
                await component.start()
                report = await component.health()
            except Exception as error:
                report = await self._failure_report(component, error)
            if report.status is HealthStatus.HEALTHY or not report.retryable:
                return report
            if attempt < self._policy.retries and self._policy.backoff_seconds:
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
            await self._event_sink.publish(event)
