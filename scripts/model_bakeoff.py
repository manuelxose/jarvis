"""Secret-safe provider bake-off harness.

Runs three sequential single-turn cases against the configured model chain and
emits a bounded evidence record containing only public provider identity, route
and fallback outcomes, monotonic first-token/total milliseconds, and a bounded
categorical quality assessment.

Credentials, prompts, generated text, and free-form notes are deliberately
excluded from the record. The live streamed response is written only to the
diagnostic stream (stderr) so an operator can judge quality; it is never
retained in the record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TextIO

# Make the `jarvis` package importable when this script is run directly from a
# source checkout (mirrors the bootstrap used by the test suite).
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from jarvis.adapters.models.fallback import ProviderChain  # noqa: E402
from jarvis.adapters.models.ollama import OllamaProvider  # noqa: E402
from jarvis.adapters.models.openai_compat import OpenAICompatProvider  # noqa: E402
from jarvis.application.runtime import _build_model_chain  # noqa: E402
from jarvis.config import RuntimeConfig, load_config  # noqa: E402
from jarvis.core.contracts import ModelProvider  # noqa: E402
from jarvis.core.errors import ProviderUnavailable  # noqa: E402
from jarvis.core.turn import TurnContext  # noqa: E402

RECORD_SCHEMA = "jarvis.model-bakeoff.v1"

# Bounded categorical quality assessment vocabulary.
QUALITY_VALUES = ("accepted", "needs-review", "rejected")

# Bounded outcome vocabulary. No free-form provider error text is recorded.
OUTCOME_OK = "ok"
OUTCOME_NO_TOKEN = "no_token"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_PARTIAL = "partial"
OUTCOME_MISSING_LOCAL = "missing_local"

_OUTCOME_MESSAGES = {
    OUTCOME_OK: None,
    OUTCOME_NO_TOKEN: "no provider produced a token",
    OUTCOME_UNAVAILABLE: "no provider produced a token (all failed or timed out)",
    OUTCOME_PARTIAL: "provider failed mid-stream after committing output",
    OUTCOME_MISSING_LOCAL: "no local Ollama provider configured",
}

DEFAULT_PROMPT = "Responde en una frase: ¿cuál es la capital de Francia?"

# Whitelisted record keys. Everything else on a provider is excluded so a
# credential or prompt attribute can never leak into the record.
_PROVIDER_IDENTITY_KEYS = ("role", "name", "kind", "base_url", "model")
_CASE_KEYS = (
    "case",
    "providers",
    "route",
    "fallback",
    "first_token_ms",
    "total_ms",
    "quality",
    "outcome",
)


def validate_quality(value: str) -> str:
    """Reject any quality category outside the bounded vocabulary."""
    if value not in QUALITY_VALUES:
        raise ValueError(
            "quality must be one of {!r}: got {!r}".format(QUALITY_VALUES, value)
        )
    return value


@dataclass(frozen=True)
class _StreamResult:
    """Measured outcome of one streamed turn, including any captured error."""

    first_token_ms: int | None
    total_ms: int | None
    tokens: int
    error: Exception | None


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _provider_kind(provider: ModelProvider) -> str:
    kind = getattr(provider, "kind", None)
    if kind:
        return kind
    if isinstance(provider, OllamaProvider):
        return "ollama"
    if isinstance(provider, OpenAICompatProvider):
        return "openai_compat"
    return type(provider).__name__


def _provider_identity(provider: ModelProvider, role: str) -> dict:
    """Return only the whitelisted public identity fields for *provider*."""
    return {
        "role": role,
        "name": getattr(provider, "name", type(provider).__name__),
        "kind": _provider_kind(provider),
        "base_url": getattr(provider, "base_url", None),
        "model": getattr(provider, "model", ""),
    }


def _case_entry(
    *,
    case: str,
    providers: Sequence[dict],
    route: str,
    fallback: str | None,
    first_token_ms: int | None,
    total_ms: int | None,
    quality: str | None,
    outcome: str,
) -> dict:
    return {
        "case": case,
        "providers": [dict(p) for p in providers],
        "route": route,
        "fallback": fallback,
        "first_token_ms": first_token_ms,
        "total_ms": total_ms,
        "quality": quality,
        "outcome": outcome,
    }


def build_record(cases: Sequence[dict]) -> dict:
    """Assemble the whitelisted record envelope."""
    return {
        "schema": RECORD_SCHEMA,
        "platform": platform.system(),
        "cases": [dict(c) for c in cases],
    }


async def _stream_measure(
    provider: ModelProvider,
    prompt: str,
    context: TurnContext,
    sink: Callable[[str], None] | None,
) -> _StreamResult:
    """Stream one turn, measuring monotonic first-token and total milliseconds.

    The response text is forwarded to *sink* (the live operator stream) but is
    never returned or retained here, so it cannot enter the record.
    """
    started = time.monotonic()
    first_token_ms: int | None = None
    tokens = 0
    error: Exception | None = None
    try:
        async for token in provider.generate(prompt, context):
            if first_token_ms is None:
                first_token_ms = _elapsed_ms(started)
            tokens += 1
            if sink is not None:
                sink(token)
    except Exception as exc:  # noqa: BLE001 - classified into a bounded outcome
        error = exc
    return _StreamResult(first_token_ms, _elapsed_ms(started), tokens, error)


def _classify(result: _StreamResult) -> tuple[str, int | None, int | None]:
    """Map a stream result onto a bounded (outcome, first_token_ms, total_ms)."""
    if result.error is not None:
        if result.tokens > 0:
            return OUTCOME_PARTIAL, result.first_token_ms, result.total_ms
        return OUTCOME_UNAVAILABLE, None, None
    if result.tokens == 0:
        return OUTCOME_NO_TOKEN, None, None
    return OUTCOME_OK, result.first_token_ms, result.total_ms


def _entry_for(
    case: str,
    providers: Sequence[ModelProvider],
    result: _StreamResult,
    selected: Sequence[str],
    quality: str | None,
) -> dict:
    outcome, first_ms, total_ms = _classify(result)
    identities = [
        _provider_identity(provider, "primary" if index == 0 else "fallback")
        for index, provider in enumerate(providers)
    ]
    primary_name = identities[0]["name"] if identities else None
    if result.tokens == 0:
        route = "none"
        fallback: str | None = None
    else:
        route = selected[-1] if selected else (primary_name or "none")
        fallback = "not_used" if route == primary_name else "used"
    if outcome != OUTCOME_OK:
        quality = None  # no assessable response to grade
    return _case_entry(
        case=case,
        providers=identities,
        route=route,
        fallback=fallback,
        first_token_ms=first_ms,
        total_ms=total_ms,
        quality=quality,
        outcome=outcome,
    )


async def _run_chain_case(
    case: str,
    providers: Sequence[ModelProvider],
    prompt: str,
    context: TurnContext,
    sink: Callable[[str], None] | None,
    quality: str | None,
    *,
    retries: int = 2,
    backoff_seconds: float = 0.25,
) -> dict:
    selected: list[str] = []
    chain = ProviderChain(
        list(providers),
        retries=retries,
        backoff_seconds=backoff_seconds,
        on_select=selected.append,
    )
    result = await _stream_measure(chain, prompt, context, sink)
    return _entry_for(case, providers, result, selected, quality)


async def _run_local_case(
    case: str,
    provider: ModelProvider,
    prompt: str,
    context: TurnContext,
    sink: Callable[[str], None] | None,
    quality: str | None,
) -> dict:
    result = await _stream_measure(provider, prompt, context, sink)
    outcome, first_ms, total_ms = _classify(result)
    route = (
        getattr(provider, "name", type(provider).__name__)
        if result.tokens > 0
        else "none"
    )
    if outcome != OUTCOME_OK:
        quality = None
    return _case_entry(
        case=case,
        providers=[_provider_identity(provider, "primary")],
        route=route,
        fallback=None,
        first_token_ms=first_ms,
        total_ms=total_ms,
        quality=quality,
        outcome=outcome,
    )


def _local_provider(providers: Sequence[ModelProvider]) -> OllamaProvider | None:
    for provider in providers:
        if isinstance(provider, OllamaProvider):
            return provider
    return None


def _context_timeout(providers: Sequence[ModelProvider]) -> float:
    ceiling = max(
        (getattr(provider, "timeout_seconds", 60.0) for provider in providers),
        default=60.0,
    )
    # ponytail: ceiling on the harness turn deadline so a misconfigured long
    # provider timeout cannot wedge the operator session indefinitely.
    return min(ceiling + 30.0, 3600.0)


class _ForcedPrimary:
    """A provider that always fails before emitting any token.

    Reuses ``ProviderChain`` so the forced case proves the existing pre-token
    commitment rule: before the first token is committed, a transient primary
    failure advances to the fallback instead of hanging or duplicating output.
    """

    def __init__(self, identity: dict) -> None:
        self.name = identity["name"]
        self.kind = identity["kind"]
        self.base_url = identity["base_url"]
        self.model = identity["model"]

    async def generate(self, prompt, context):
        raise ProviderUnavailable("forced pre-token failure", provider=self.name)
        yield  # pragma: no cover


async def _run_cases(
    providers: Sequence[ModelProvider],
    prompt: str,
    quality: str,
    stream: TextIO | None,
) -> list[dict]:
    """Run the three sequential bake-off cases and return their record entries."""
    resolved = list(providers)
    context = TurnContext.fresh("bakeoff", timeout_seconds=_context_timeout(resolved))
    entries: list[dict] = []

    def _header(case: str) -> None:
        if stream is not None:
            stream.write(f"[case={case}]\n")
            stream.flush()

    def _sink() -> Callable[[str], None] | None:
        if stream is None:
            return None

        def _write(token: str) -> None:
            stream.write(token)
            stream.flush()

        return _write

    _header("cloud_chain")
    entries.append(
        await _run_chain_case("cloud_chain", resolved, prompt, context, _sink(), quality)
    )

    local = _local_provider(resolved)

    _header("local_baseline")
    if local is None:
        entries.append(
            _case_entry(
                case="local_baseline",
                providers=[],
                route="none",
                fallback=None,
                first_token_ms=None,
                total_ms=None,
                quality=None,
                outcome=OUTCOME_MISSING_LOCAL,
            )
        )
    else:
        entries.append(
            await _run_local_case("local_baseline", local, prompt, context, _sink(), quality)
        )

    _header("forced_fallback")
    if local is None:
        entries.append(
            _case_entry(
                case="forced_fallback",
                providers=[],
                route="none",
                fallback=None,
                first_token_ms=None,
                total_ms=None,
                quality=None,
                outcome=OUTCOME_MISSING_LOCAL,
            )
        )
    else:
        forced = _ForcedPrimary(_provider_identity(resolved[0], "primary"))
        entries.append(
            await _run_chain_case(
                "forced_fallback",
                [forced, *resolved[1:]],
                prompt,
                context,
                _sink(),
                quality,
                retries=0,
                backoff_seconds=0.0,
            )
        )

    return entries


def _resolve_providers(config: RuntimeConfig) -> list[ModelProvider]:
    """Resolve the production configured model chain into ordered providers."""
    return list(_build_model_chain(config).providers)


def _write_record(path: Path, record: dict) -> None:
    path.write_text(
        json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _dash(value: int | str | None) -> str:
    return "-" if value is None else str(value)


def _write_report(record: dict, out: TextIO, *, as_json: bool) -> None:
    if as_json:
        json.dump(record, out, sort_keys=True, ensure_ascii=False)
        out.write("\n")
        out.flush()
        return
    out.write(f"schema: {record['schema']}\n")
    out.write(
        f"{'case':<16}{'route':<12}{'fallback':<10}{'first_ms':>9}{'total_ms':>9}"
        f"{'quality':<13}{'outcome':<13}\n"
    )
    for entry in record["cases"]:
        out.write(
            f"{entry['case']:<16}{entry['route']:<12}{_dash(entry['fallback']):<10}"
            f"{_dash(entry['first_token_ms']):>9}{_dash(entry['total_ms']):>9}"
            f"{_dash(entry['quality']):<13}{entry['outcome']:<13}\n"
        )
    out.flush()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_bakeoff",
        description=(
            "Run three sequential single-turn provider bake-off cases and emit a "
            "secret-safe evidence record."
        ),
    )
    parser.add_argument(
        "--config", default="config.json", help="path to config.json (default: config.json)"
    )
    parser.add_argument(
        "--prompt", default=DEFAULT_PROMPT, help="fixed operator turn; never recorded"
    )
    parser.add_argument(
        "--quality",
        default="needs-review",
        help="categorical quality assessment: one of accepted, needs-review, rejected",
    )
    parser.add_argument("--output", default=None, help="write the JSON record to this file")
    parser.add_argument("--json", action="store_true", help="emit the record as JSON on stdout")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    try:
        args = _parse_args(argv)
    except SystemExit as error:  # argparse usage errors
        return int(error.code or 0)

    try:
        quality = validate_quality(args.quality)
    except ValueError as error:
        err.write(f"error: {error}\n")
        return 2

    try:
        config = load_config(Path(args.config))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        err.write(f"error: invalid configuration: {error}\n")
        return 2

    providers = _resolve_providers(config)
    if not providers:
        err.write(
            "error: no model providers configured (expected a primary and a local fallback)\n"
        )
        return 2

    try:
        entries = asyncio.run(
            _run_cases(providers, args.prompt, quality, stream=err)
        )
    except Exception as error:  # noqa: BLE001 - surfaced as an actionable failure
        err.write(f"error: bake-off failed: {error}\n")
        return 2

    record = build_record(entries)
    if args.output:
        _write_record(Path(args.output), record)
    _write_report(record, out, as_json=args.json)

    failed = [entry for entry in entries if entry["outcome"] != OUTCOME_OK]
    for entry in failed:
        err.write(f"case {entry['case']}: {_OUTCOME_MESSAGES[entry['outcome']]}\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
