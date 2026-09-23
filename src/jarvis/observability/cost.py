"""Per-turn API usage cost estimation and aggregate rollups.

Pricing is supplied by the caller (a plain mapping, config-driven, never
hardcoded here) so it can be updated without a code change as providers
revise their rates. A provider with no configured rate still records its
usage; its cost is simply ``None`` rather than raising, since missing pricing
data must never break a turn.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class UsageRecord:
    """One provider call's raw usage counters for one turn."""

    provider: str
    kind: str  # "llm" | "tts" | "stt"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    audio_seconds: float = 0.0
    characters: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ProviderRate:
    """USD rates for one provider. Fields not applicable to it stay 0.0."""

    input_token_per_million: float = 0.0
    output_token_per_million: float = 0.0
    audio_second: float = 0.0
    character: float = 0.0


def estimate_cost(usage: UsageRecord, rate: Optional[ProviderRate]) -> Optional[float]:
    """Return the estimated USD cost of *usage*, or None if *rate* is unknown."""
    if rate is None:
        return None
    return (
        usage.input_tokens / 1_000_000 * rate.input_token_per_million
        + usage.output_tokens / 1_000_000 * rate.output_token_per_million
        + usage.audio_seconds * rate.audio_second
        + usage.characters * rate.character
    )


class CostTracker:
    """Accumulate per-turn usage and report aggregate cost rollups."""

    def __init__(self, pricing: Optional[Mapping[str, ProviderRate]] = None) -> None:
        self._pricing = dict(pricing or {})
        self._records: list[tuple[UsageRecord, Optional[float]]] = []

    def record(self, usage: UsageRecord) -> Optional[float]:
        """Record *usage* and return its estimated cost (None if unpriced)."""
        cost = estimate_cost(usage, self._pricing.get(usage.provider))
        self._records.append((usage, cost))
        return cost

    def summary(self) -> dict:
        """Aggregate rollups: totals, per-interaction/minute averages, projections."""
        priced = [cost for _, cost in self._records if cost is not None]
        total_cost = sum(priced)
        interactions = len(self._records)
        avg_per_interaction = total_cost / interactions if interactions else 0.0

        by_provider: dict[str, float] = defaultdict(float)
        for usage, cost in self._records:
            if cost is not None:
                by_provider[usage.provider] += cost

        timestamps = [usage.timestamp for usage, _ in self._records]
        span_seconds = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0.0
        span_minutes = span_seconds / 60.0
        avg_per_minute = total_cost / span_minutes if span_minutes > 0 else 0.0

        # ponytail: daily/monthly projections extrapolate the observed
        # cost-per-minute as if conversation ran continuously 24/7. That is a
        # ceiling, not a usage forecast; upgrade path is weighting by actual
        # observed active-minutes-per-day once real usage logs exist.
        return {
            "interactions": interactions,
            "priced_interactions": len(priced),
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_interaction_usd": round(avg_per_interaction, 6),
            "avg_cost_per_conversational_minute_usd": round(avg_per_minute, 6),
            "projected_daily_usd": round(avg_per_minute * 60 * 24, 4),
            "projected_monthly_usd": round(avg_per_minute * 60 * 24 * 30, 4),
            "provider_distribution": {k: round(v, 6) for k, v in sorted(by_provider.items())},
        }


class SpendLedger:
    """Persisted per-day cloud spend with a hard cap (survives restarts).

    Stored as a tiny JSON file in the per-user data directory. The cap is
    checked before a request is opened, so an exhausted budget costs nothing:
    callers raise a non-transient error and the provider chain moves on to a
    local model instead of spending more.
    """

    def __init__(self, path, max_daily_usd: float) -> None:
        from pathlib import Path  # noqa: PLC0415

        self._path = Path(path)
        self.max_daily_usd = max_daily_usd

    def _load(self) -> dict:
        import json  # noqa: PLC0415

        today = time.strftime("%Y-%m-%d")
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("date") != today:
            data = {"date": today, "usd": 0.0, "by_provider": {}}
        return data

    def spent_today(self) -> float:
        return round(self._load()["usd"], 6)

    def exhausted(self) -> bool:
        return self.max_daily_usd > 0 and self.spent_today() >= self.max_daily_usd

    def record(self, provider: str, usd: float) -> None:
        import json  # noqa: PLC0415

        data = self._load()
        data["usd"] = round(data["usd"] + usd, 6)
        data["by_provider"][provider] = round(data["by_provider"].get(provider, 0.0) + usd, 6)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data), encoding="utf-8")
