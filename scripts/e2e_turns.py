"""Text-driven end-to-end turns through the real runtime (Windows laptop).

Builds the production runtime from config.win.json (+ config.local.json),
starts the supervisor (TTS worker included), waits for the cloned voice, then
runs N typed turns through router -> LLM chain -> chunker -> TTS -> speakers
and reports per-stage trace percentiles. Microphone/STT are not exercised:
add the measured STT time (voice_loop log "stt NNN ms") for mic-to-audio.

Per-turn provider and fallback-cause evidence comes directly from
ProviderChain's own bookkeeping (``result.cost["provider"]``/``["fallback"]``,
sanitized and independent of billable usage -- see S05-T01), not from
inferring a provider off a billing entry or the routing label.

``--llm scripted`` replaces the local LLM with an in-script no-GPU stub that
streams a fixed Spanish answer, so the real chunker/TTSChain/clone
worker/audio queue are exercised without a local model contending with the
clone worker for the GPU (see S06-T01/T02: this isolates worker/IPC/handoff
latency from LLM-vs-TTS GPU contention).

    .venv\\Scripts\\python.exe scripts\\e2e_turns.py --turns 20 --pause 3
    .venv\\Scripts\\python.exe scripts\\e2e_turns.py --turns 20 --pause 3 --llm scripted
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.models.ollama import OllamaProvider  # noqa: E402
from jarvis.adapters.tts.qwen_clone import QwenCloneTTS  # noqa: E402
from jarvis.application.runtime import _warm_up, build_runtime  # noqa: E402
from jarvis.config import load_config  # noqa: E402
from jarvis.core.contracts import TurnContext  # noqa: E402
from jarvis.observability.cost import UsageRecord  # noqa: E402

QUERIES = [
    "Dime un dato curioso sobre Madrid.",
    "¿Qué tiempo suele hacer en Vigo en otoño?",
    "Explícame en una frase qué es un agujero negro.",
    "Dame una idea rápida para cenar esta noche.",
    "¿Cuántos días tiene un año bisiesto?",
]
# Stages read straight off InteractionTrace; each is elapsed ms since turn start.
STAGES = ("routing_ms", "llm_first_token_ms", "first_segment_ms", "tts_first_audio_ms", "total_request_ms")
# Derived stage, computed here rather than in InteractionTrace so it does not
# perturb that class's strict as_dict() stage set: the TTS-only latency once
# the first segment was ready, isolated from the end-to-end
# turn-start-to-first-audio number (tts_first_audio_ms), which also folds in
# LLM latency. This is the "warmed segment-to-audio" number the <700ms clone
# criterion is about.
SEGMENT_TO_AUDIO_STAGE = "segment_to_first_audio_ms"
# Decomposition of SEGMENT_TO_AUDIO_STAGE, computed per turn from the clone
# worker's own timing (worker_ttfa_ms) vs. what QwenCloneTTS observed
# client-side (client_ttfa_ms): ipc_ms is the gap between the two (message
# marshalling + process/queue scheduling), handoff_ms is everything before
# the request reached the worker (chunker -> TTSChain -> QwenCloneTTS.synthesize).
DECOMPOSED_STAGES = ("worker_ttfa_ms", "client_ttfa_ms", "ipc_ms", "handoff_ms")
ALL_STAGES = STAGES + (SEGMENT_TO_AUDIO_STAGE,) + DECOMPOSED_STAGES
CONDITION_LABELS = {"configured": "configured-llm", "scripted": "scripted-remote-llm"}


def pct(values, p):
    values = sorted(values)
    return round(values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))]) if values else None


def turn_provider(result) -> Optional[str]:
    """The actual selected provider for this turn, or None if attribution is
    genuinely missing on a route that should have it.

    Comes from ProviderChain's own bookkeeping (result.cost["provider"]),
    independent of whether any usage was billed -- a cloud call that streams
    a full answer but never emits a usage payload must still be reported as
    the selected provider. Falls back to the route label only for routes that
    never attribute a provider in the first place (fast_command/desktop/
    hermes never call the model chain here); on the fast_model route a
    missing provider is a genuine evidence gap and must surface as None, not
    be papered over with the "fast_model" route label -- that exact
    fallback-to-route-label behavior was S05-T01's root cause for the
    0/20-cloud-answer evidence.
    """
    provider = (result.cost or {}).get("provider")
    if provider:
        return provider
    return None if result.route == "fast_model" else result.route


def turn_fallback(result) -> list[dict[str, Any]]:
    """Sanitized non-winning provider attempts for this turn.

    Already redacted by ProviderChain._sanitize_reason(); never the raw
    exception, response text, or a key.
    """
    return list((result.cost or {}).get("fallback", []))


def segment_to_first_audio_ms(trace: dict) -> Optional[float]:
    """TTS-only latency: first-segment-ready to first-audio, or None if
    either stage did not fire (e.g. a turn that errored before speaking)."""
    segment = trace.get("first_segment_ms")
    audio = trace.get("tts_first_audio_ms")
    if segment is None or audio is None:
        return None
    return round(max(0.0, audio - segment), 3)


def decompose_first_segment(segment_to_audio: Optional[float], timing: Optional[dict]) -> dict[str, Optional[float]]:
    """Break one turn's segment_to_first_audio_ms into worker/IPC/handoff parts.

    ``timing`` is the clone worker's first-segment timing entry (``ttfa_ms``
    from the worker's own "done" event, ``client_ttfa_ms`` observed by
    QwenCloneTTS around the same request -- see qwen_clone.py). Every field is
    None when an input is missing (SAPI served the turn, no clone, or the
    worker never got to audio), never a fabricated zero.
    """
    worker_ttfa = timing.get("ttfa_ms") if timing else None
    client_ttfa = timing.get("client_ttfa_ms") if timing else None
    ipc_ms = round(client_ttfa - worker_ttfa, 1) if worker_ttfa is not None and client_ttfa is not None else None
    handoff_ms = (
        round(segment_to_audio - client_ttfa, 1)
        if segment_to_audio is not None and client_ttfa is not None
        else None
    )
    return {
        "worker_ttfa_ms": worker_ttfa,
        "client_ttfa_ms": client_ttfa,
        "ipc_ms": ipc_ms,
        "handoff_ms": handoff_ms,
    }


def _decomposition_for_trace(trace: dict) -> dict[str, Optional[float]]:
    timing = {"ttfa_ms": trace.get("worker_ttfa_ms"), "client_ttfa_ms": trace.get("client_ttfa_ms")}
    return decompose_first_segment(segment_to_first_audio_ms(trace), timing)


def _stage_value_lists(results: list) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {stage: [] for stage in ALL_STAGES}
    for result in results:
        trace = result.trace
        for stage in STAGES:
            if trace.get(stage) is not None:
                values[stage].append(trace[stage])
        derived = segment_to_first_audio_ms(trace)
        if derived is not None:
            values[SEGMENT_TO_AUDIO_STAGE].append(derived)
        decomposition = _decomposition_for_trace(trace)
        for stage in DECOMPOSED_STAGES:
            if decomposition[stage] is not None:
                values[stage].append(decomposition[stage])
    return values


# Stages actually written into each evidence["turns"] entry (a subset of
# STAGES: routing_ms/llm_first_token_ms are reported only as aggregate
# percentiles to keep per-turn evidence compact -- checking for them here
# would always "fail" since they are never per-turn keys in the first place).
PER_TURN_STAGES = ("first_segment_ms", "tts_first_audio_ms", "total_request_ms")


def missing_fields(turns: list[dict]) -> list[str]:
    """Evidence gaps that must fail the run rather than being silently read
    as zero/fast or as a passing benchmark: an absent stage means a turn did
    not complete the pipeline it claims to measure, and a missing provider on
    the fast_model route means attribution broke (see turn_provider())."""
    problems = []
    for index, turn in enumerate(turns, start=1):
        for stage in PER_TURN_STAGES:
            if turn.get(stage) is None:
                problems.append(f"turn {index}: missing {stage}")
        if turn.get("route") == "fast_model" and not turn.get("provider"):
            problems.append(f"turn {index}: missing provider")
    return problems


def _spend_today_usd(diagnostics: dict) -> float:
    total = sum(
        provider.get("spent_today_usd", 0.0) or 0.0
        for provider in diagnostics.get("model", {}).get("providers", ())
    )
    return round(total, 6)


def _max_daily_usd_from_diagnostics(diagnostics: dict) -> Optional[float]:
    """The cap as reported by the shared SpendLedger on any priced provider,
    or None if diagnostics carries no ledger (e.g. an all-local chain)."""
    for provider in diagnostics.get("model", {}).get("providers", ()):
        if "max_daily_usd" in provider:
            return provider["max_daily_usd"]
    return None


def _classify_fallback_reason(reason: str) -> str:
    """Sanitized cause label only -- never the raw reason text (S07-T01: the
    evidence must stay committable without leaking upstream error bodies)."""
    lowered = reason.lower()
    if "402" in reason or "insufficient balance" in lowered:
        return "insufficient_balance"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "cap" in lowered or "budget" in lowered:
        return "daily_cap"
    return "other"


def cloud_route_summary(
    turns: list[dict],
    spend_today_usd: float,
    max_daily_usd: Optional[float],
    cloud_provider: str = "deepseek",
) -> dict:
    """Derives served/external_blocker/failed for the cloud-primary route from
    per-turn provider/fallback evidence, so S07 does not require reading 20
    turn records by hand to decide whether the cloud route passed.

    Only fast_model-route turns carry provider/fallback attribution (see
    turn_provider()); hermes/fast_command/desktop turns never call the model
    chain and are excluded from every count below.
    """
    model_turns = [turn for turn in turns if turn.get("route") == "fast_model"]
    cloud_served = sum(1 for turn in model_turns if turn.get("provider") == cloud_provider)
    fallback_turns = 0
    unexplained_fallbacks = 0
    fallback_causes: dict[str, int] = {}
    for turn in model_turns:
        if turn.get("provider") == cloud_provider:
            continue
        fallback = turn.get("fallback") or []
        cloud_attempt = next((f for f in fallback if f.get("provider") == cloud_provider), None)
        if cloud_attempt is not None:
            fallback_turns += 1
            label = _classify_fallback_reason(cloud_attempt.get("reason") or "")
            fallback_causes[label] = fallback_causes.get(label, 0) + 1
        elif not fallback:
            unexplained_fallbacks += 1
    within_cap = None if max_daily_usd is None else spend_today_usd <= max_daily_usd
    if cloud_served > 0 and unexplained_fallbacks == 0 and within_cap is not False:
        verdict = "served"
    elif cloud_served == 0 and fallback_causes and set(fallback_causes) == {"insufficient_balance"}:
        verdict = "external_blocker"
    else:
        verdict = "failed"
    return {
        "cloud_served": cloud_served,
        "fallback_turns": fallback_turns,
        "fallback_causes": fallback_causes,
        "unexplained_fallbacks": unexplained_fallbacks,
        "spend_today_usd": spend_today_usd,
        "max_daily_usd": max_daily_usd,
        "within_cap": within_cap,
        "verdict": verdict,
    }


def local_llm_meta(model: Any) -> Optional[dict]:
    """Non-secret config of the first Ollama provider in the model chain, or None."""
    for provider in getattr(model, "providers", (model,)):
        if isinstance(provider, OllamaProvider):
            return {
                "model": provider.model,
                "options": dict((provider.extra_body.get("options") or {})),
                "keep_alive": provider.keep_alive,
            }
    return None


def build_evidence(
    args,
    results: list,
    diagnostics: dict,
    max_daily_usd: Optional[float] = None,
    local_llm: Optional[dict] = None,
) -> dict:
    """A committable, audio- and transcript-free record of streaming timing.

    ``first_phrase_before_completion_ms`` proves the slice demo ("first phrase
    spoken before the LLM or Hermes finishes") is measured, not asserted: it is
    the fraction of turns whose first spoken segment was ready strictly before
    the whole turn completed.
    """
    stage_values = _stage_value_lists(results)
    turns = []
    before_completion = 0
    counted = 0
    provider_counts: dict[str, int] = {}
    for result in results:
        trace = result.trace
        first_segment = trace.get("first_segment_ms")
        total = trace.get("total_request_ms")
        if first_segment is not None and total is not None:
            counted += 1
            if first_segment < total:
                before_completion += 1
        provider = turn_provider(result)
        provider_key = provider or "unknown"
        provider_counts[provider_key] = provider_counts.get(provider_key, 0) + 1
        decomposition = _decomposition_for_trace(trace)
        turns.append(
            {
                "route": result.route,
                "first_segment_ms": first_segment,
                "tts_first_audio_ms": trace.get("tts_first_audio_ms"),
                "segment_to_first_audio_ms": segment_to_first_audio_ms(trace),
                "total_request_ms": total,
                "provider": provider,
                "fallback": turn_fallback(result),
                **decomposition,
            }
        )
    command = (
        f"python scripts/e2e_turns.py --config {args.config} --turns {args.turns} "
        f"--host-label {args.host_label} --output {args.output}"
    )
    llm_mode = getattr(args, "llm", "configured")
    spend_today_usd = _spend_today_usd(diagnostics)
    resolved_max_daily_usd = _max_daily_usd_from_diagnostics(diagnostics)
    if resolved_max_daily_usd is None:
        resolved_max_daily_usd = max_daily_usd
    return {
        "status": "measured",
        "meta": {
            "date": datetime.now(timezone.utc).isoformat(),
            "host_label": args.host_label,
            "python_version": platform.python_version(),
            "turns": len(results),
            "command": command,
            "condition": CONDITION_LABELS.get(llm_mode, CONDITION_LABELS["configured"]),
            "token_delay_ms": getattr(args, "token_delay_ms", None),
            "local_llm": local_llm,
        },
        "stages": {
            stage: {"p50": pct(values, 50), "p95": pct(values, 95)}
            for stage, values in stage_values.items()
        },
        "turns": turns,
        "provider_counts": provider_counts,
        "spend_today_usd": spend_today_usd,
        "first_phrase_before_completion_ratio": round(before_completion / counted, 4) if counted else 0.0,
        "cloud_route": cloud_route_summary(turns, spend_today_usd, resolved_max_daily_usd),
    }


class _ScriptedRemoteModel:
    """No-GPU stand-in for the local LLM: streams a fixed Spanish answer with
    a configurable per-token delay, emulating a remote token stream so the
    real chunker/TTSChain/clone worker/audio queue run without a local model
    contending with the clone worker for the RTX 3070 (see --llm scripted).

    Same generate(prompt, context) async-iterator interface as ModelProvider.
    Has no ``last_selected_provider``, so TurnManager's cost attribution
    falls back to ``self.name`` -- the turn's provider reads "scripted",
    never None, so it is never mistaken for a missing-evidence gap.
    """

    name = "scripted"
    model = "scripted-remote-llm"

    _RESPONSE = (
        "Entendido. Esta es una respuesta simulada que imita un modelo remoto "
        "para medir la canalizacion completa sin usar la GPU local."
    )

    def __init__(self, token_delay_ms: float = 30.0) -> None:
        self._delay_s = max(0.0, token_delay_ms) / 1000.0
        self.last_usage_record: Optional[UsageRecord] = None

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        self.last_usage_record = None
        words = self._RESPONSE.split(" ")
        for word in words:
            context.cancellation.raise_if_cancelled()
            yield word + " "
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
        self.last_usage_record = UsageRecord(
            provider=self.name, kind="llm", model=self.model,
            input_tokens=len(prompt.split()), output_tokens=len(words),
        )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.win.json")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--pause", type=float, default=3.0)
    parser.add_argument("--output", default=None, help="Write a committable evidence JSON to this path")
    parser.add_argument("--host-label", default="unknown", help="meta.host_label in --output")
    parser.add_argument(
        "--llm", choices=("configured", "scripted"), default="configured",
        help="'scripted' replaces the local LLM with a no-GPU stub (see _ScriptedRemoteModel)",
    )
    parser.add_argument(
        "--token-delay-ms", type=float, default=30.0,
        help="Per-token delay for --llm scripted, emulating a remote stream",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    runtime = build_runtime(config)
    await runtime.supervisor.start()
    providers = getattr(runtime.components.turn_manager._tts, "providers", ())
    clone = next((p for p in providers if isinstance(p, QwenCloneTTS)), None)
    try:
        info = await clone.wait_ready() if clone is not None else {}
        if args.llm == "scripted":
            remote_model = _ScriptedRemoteModel(token_delay_ms=args.token_delay_ms)
            runtime.components.turn_manager._model = remote_model
            runtime.components.model = remote_model
        await asyncio.to_thread(_warm_up, runtime.components.model, None)  # as `jarvis run` does
        results = []
        for index in range(args.turns):
            await asyncio.sleep(args.pause)
            # The clone's timing deque is shared across turns (bounded, not
            # per-turn), so the first entry appended during this turn is this
            # turn's first segment; see qwen_clone.py QwenCloneTTS._timings.
            timings_before = len(clone._timings) if clone is not None else 0
            result = await runtime.handle(QUERIES[index % len(QUERIES)])
            if clone is not None:
                new_timings = list(clone._timings)[timings_before:]
                if new_timings:
                    result.trace["worker_ttfa_ms"] = new_timings[0].get("ttfa_ms")
                    result.trace["client_ttfa_ms"] = new_timings[0].get("client_ttfa_ms")
            results.append(result)
            print(
                f"turn {index + 1}: provider={turn_provider(result)} route={result.route} "
                f"tts_first_audio={result.trace.get('tts_first_audio_ms')} "
                f"llm_first={result.trace.get('llm_first_token_ms')}",
                file=sys.stderr,
            )
        diagnostics = runtime.diagnostics()
        local_llm = local_llm_meta(runtime.components.model)
    finally:
        await runtime.supervisor.stop()

    evidence = build_evidence(
        args, results, diagnostics, max_daily_usd=config.models.max_daily_usd, local_llm=local_llm
    )
    stage_values = _stage_value_lists(results)
    report = {
        "worker": {k: info.get(k) for k in ("profile", "mode", "load_ms", "warmup_ms", "vram_free_mb")},
        "turns": len(results),
        "routes": sorted({r.route for r in results}),
        "provider_counts": evidence["provider_counts"],
        "stages_ms": {
            stage: {"p50": pct(values, 50), "p95": pct(values, 95), "max": pct(values, 100)}
            for stage, values in stage_values.items()
        },
        "tts_provider_used": diagnostics["tts"],
        "model": [{k: p.get(k) for k in ("name", "model", "spent_today_usd", "max_daily_usd", "priced")}
                  for p in diagnostics["model"]["providers"]],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    # An acceptance-scale run (the default and the documented target) must
    # fail loudly on a missing stage/provider rather than let pct()'s silent
    # skip-if-absent behavior make a broken turn look like a fast or unrouted
    # one. Smaller diagnostic runs (`--turns` below 20) are exempt.
    problems = missing_fields(evidence["turns"]) if args.turns >= 20 else []
    if problems:
        print("ACCEPTANCE VALIDATION FAILED (missing stage/provider evidence):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
