"""Latency metric collection with percentile aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable


class LatencyMetrics:
    """Collect measured latency samples and compute percentiles."""

    def __init__(self) -> None:
        self._values: dict[str, list[float]] = defaultdict(list)

    def record(self, name: str, value_ms: float) -> None:
        self._values[name].append(value_ms)

    def percentile(self, name: str, p: float) -> float | None:
        values = sorted(self._values.get(name, []))
        if not values:
            return None
        index = min(len(values) - 1, int(round(p / 100.0 * (len(values) - 1))))
        return values[index]

    def summary(self) -> dict[str, dict[str, float | None]]:
        result: dict[str, dict[str, float | None]] = {}
        for name, values in sorted(self._values.items()):
            result[name] = {
                "count": len(values),
                "p50": self.percentile(name, 50),
                "p95": self.percentile(name, 95),
            }
        return result


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))
    return ordered[index]
