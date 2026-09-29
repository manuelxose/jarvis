"""Locks committed real-laptop streaming runs (docs/bench/e2e-*.json) and,
below, the evidence-building logic in scripts/e2e_turns.py against synthetic
turns -- provider/fallback provenance, warmed segment-to-audio isolation, and
the fail-loud-on-missing-evidence contract cannot be exercised by the two
committed historical files alone."""

import json
import sys
from pathlib import Path
import re
import unittest

BENCH_DIR = Path(__file__).resolve().parents[1] / "docs" / "bench"
EVIDENCE_FILES = tuple(
    p for p in (BENCH_DIR / "e2e-streaming.json", BENCH_DIR / "e2e-acceptance.json") if p.exists()
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from jarvis.application.turn_manager import TurnResult  # noqa: E402

import e2e_turns  # noqa: E402
import _preflight_check  # noqa: E402


class E2EStreamingEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(EVIDENCE_FILES, "no e2e evidence files found under docs/bench/")

    def test_schema_and_meta(self):
        for evidence in EVIDENCE_FILES:
            with self.subTest(evidence=evidence.name):
                data = json.loads(evidence.read_text(encoding="utf-8"))
                for key in ("meta", "stages", "turns", "spend_today_usd", "first_phrase_before_completion_ratio"):
                    self.assertIn(key, data)
                meta = data["meta"]
                for key in ("date", "host_label", "python_version", "turns", "command"):
                    self.assertIn(key, meta)
                self.assertIsInstance(meta["turns"], int)

    def test_turns_count_and_shape(self):
        for evidence in EVIDENCE_FILES:
            with self.subTest(evidence=evidence.name):
                data = json.loads(evidence.read_text(encoding="utf-8"))
                turns = data["turns"]
                self.assertGreaterEqual(len(turns), 20)
                self.assertGreaterEqual(data["meta"]["turns"], 20)
                for turn in turns:
                    for key in ("route", "first_segment_ms", "tts_first_audio_ms", "total_request_ms", "provider"):
                        self.assertIn(key, turn)
                    self.assertNotIn("transcript", turn)
                    self.assertNotIn("response", turn)
                    self.assertNotIn("reply", turn)

    def test_ratio_and_spend_present(self):
        for evidence in EVIDENCE_FILES:
            with self.subTest(evidence=evidence.name):
                data = json.loads(evidence.read_text(encoding="utf-8"))
                ratio = data["first_phrase_before_completion_ratio"]
                self.assertIsInstance(ratio, (int, float))
                self.assertGreaterEqual(ratio, 0.0)
                self.assertLessEqual(ratio, 1.0)
                self.assertIsInstance(data["spend_today_usd"], (int, float))
                self.assertGreaterEqual(data["spend_today_usd"], 0.0)

    def test_stages_present(self):
        for evidence in EVIDENCE_FILES:
            with self.subTest(evidence=evidence.name):
                data = json.loads(evidence.read_text(encoding="utf-8"))
                for stage in ("routing_ms", "llm_first_token_ms", "first_segment_ms", "tts_first_audio_ms", "total_request_ms"):
                    self.assertIn(stage, data["stages"])

    def test_no_private_data_or_audio(self):
        for evidence in EVIDENCE_FILES:
            with self.subTest(evidence=evidence.name):
                raw = evidence.read_text(encoding="utf-8")
                self.assertNotRegex(raw, r"(?i)c:\\\\users")
                self.assertNotRegex(raw, r"sk-")
                self.assertNotRegex(raw, r"(?i)api_key")
                self.assertNotRegex(raw, r"(?i)pcm|base64|\.wav")
                self.assertIsNone(re.search(r"[A-Za-z0-9+/]{200,}", raw))


class RecoveryEvidenceTests(unittest.TestCase):
    """Locks docs/bench/e2e-recovery.json (S05-T03): either a fully measured
    >=20-turn run with provider/p50/p95 on every turn, or an honestly labeled
    failed/blocked run with a non-empty cause -- never a fabricated pass."""

    PATH = BENCH_DIR / "e2e-recovery.json"

    def setUp(self):
        self.assertTrue(self.PATH.exists(), "docs/bench/e2e-recovery.json is required for S05-T03")
        self.data = json.loads(self.PATH.read_text(encoding="utf-8"))

    def test_status_is_measured_or_failed_with_cause(self):
        status = self.data.get("status")
        self.assertIn(status, ("measured", "failed", "blocked"))
        if status in ("failed", "blocked"):
            self.assertTrue(self.data.get("cause"), "failed/blocked status must carry a non-empty cause")

    def test_measured_run_has_full_turn_shape(self):
        if self.data.get("status") != "measured":
            self.skipTest("run was not measured")
        turns = self.data["turns"]
        self.assertGreaterEqual(len(turns), 20)
        self.assertGreaterEqual(self.data["meta"]["turns"], 20)
        for turn in turns:
            self.assertIn("provider", turn)
            self.assertTrue(turn["provider"])
        for stage in ("tts_first_audio_ms", "total_request_ms", "segment_to_first_audio_ms"):
            self.assertIn(stage, self.data["stages"])
            self.assertIsNotNone(self.data["stages"][stage]["p50"])
            self.assertIsNotNone(self.data["stages"][stage]["p95"])

    def test_no_private_data_audio_or_keys(self):
        raw = self.PATH.read_text(encoding="utf-8")
        self.assertNotRegex(raw, r"(?i)c:\\\\users")
        self.assertNotRegex(raw, r"sk-")
        self.assertNotRegex(raw, r"(?i)api_key")
        self.assertNotRegex(raw, r"(?i)authorization")
        self.assertNotRegex(raw, r"(?i)pcm|base64|\.wav")
        self.assertIsNone(re.search(r"[A-Za-z0-9+/]{200,}", raw))
        for key in ("transcript", "response", "reply"):
            self.assertNotIn(f'"{key}"', raw)


class _Args:
    """Enough of argparse.Namespace for build_evidence's meta/command fields."""

    def __init__(
        self,
        turns=20,
        output="docs/bench/e2e-synthetic.json",
        host_label="unit-test",
        config="config.win.json",
        llm="configured",
        token_delay_ms=30.0,
    ):
        self.turns = turns
        self.output = output
        self.host_label = host_label
        self.config = config
        self.llm = llm
        self.token_delay_ms = token_delay_ms


def _turn_result(*, route="fast_model", trace, cost, response, transcript):
    return TurnResult(
        transcript=transcript,
        response=response,
        route=route,
        elapsed_ms=1234.0,
        trace=trace,
        cost=cost,
    )


_CLOUD_SUCCESS = _turn_result(
    trace={
        "trace_id": "t1",
        "routing_ms": 1.0,
        "llm_first_token_ms": 400.0,
        "first_segment_ms": 600.0,
        "tts_first_audio_ms": 900.0,
        "total_request_ms": 2000.0,
    },
    cost={"trace_id": "t1", "route": "fast_model", "entries": [], "total_usd": 0.001,
          "provider": "openai", "fallback": []},
    response="la respuesta privada del turno en la nube",
    transcript="la pregunta privada del usuario en la nube",
)

_LOCAL_FALLBACK = _turn_result(
    trace={
        "trace_id": "t2",
        "routing_ms": 1.0,
        "llm_first_token_ms": 500.0,
        "first_segment_ms": 800.0,
        "tts_first_audio_ms": 1200.0,
        "total_request_ms": 3000.0,
    },
    cost={"trace_id": "t2", "route": "fast_model", "entries": [], "total_usd": 0.0,
          "provider": "ollama",
          "fallback": [{"provider": "openai", "selected": False,
                         "reason": "upstream 500: [redacted]", "transient": False}]},
    response="la respuesta privada del turno local",
    transcript="la pregunta privada del usuario en local",
)

_MISSING_EVIDENCE = _turn_result(
    trace={
        "trace_id": "t3",
        "routing_ms": 1.0,
        "llm_first_token_ms": 500.0,
        "first_segment_ms": 800.0,
        "total_request_ms": 5000.0,
        # tts_first_audio_ms deliberately absent: a turn that never got to speak.
    },
    cost={"trace_id": "t3", "route": "fast_model", "entries": [], "total_usd": 0.0,
          "provider": None, "fallback": []},
    response="no debería aparecer nunca",
    transcript="tampoco esto",
)


class BuildEvidenceProvenanceTests(unittest.TestCase):
    """Synthetic turns covering scripts/e2e_turns.py's evidence logic that the
    two committed historical JSON files cannot exercise on their own."""

    def test_cloud_success_reports_actual_provider_with_no_fallback(self):
        evidence = e2e_turns.build_evidence(_Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}})
        turn = evidence["turns"][0]
        self.assertEqual("openai", turn["provider"])
        self.assertEqual([], turn["fallback"])
        self.assertEqual({"openai": 1}, evidence["provider_counts"])

    def test_local_fallback_reports_provider_and_sanitized_cause(self):
        evidence = e2e_turns.build_evidence(_Args(), [_LOCAL_FALLBACK], {"model": {"providers": []}})
        turn = evidence["turns"][0]
        self.assertEqual("ollama", turn["provider"])
        self.assertEqual(1, len(turn["fallback"]))
        self.assertEqual("openai", turn["fallback"][0]["provider"])
        self.assertFalse(turn["fallback"][0]["selected"])
        self.assertEqual("upstream 500: [redacted]", turn["fallback"][0]["reason"])

    def test_segment_to_first_audio_is_isolated_from_end_to_end_latency(self):
        """900ms end-to-end tts_first_audio_ms includes LLM latency up to the
        600ms first-segment mark; the warmed synthesis-only cost (300ms) must
        be reported separately, not conflated with the end-to-end number."""
        evidence = e2e_turns.build_evidence(_Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}})
        turn = evidence["turns"][0]
        self.assertEqual(300.0, turn["segment_to_first_audio_ms"])
        self.assertEqual(900.0, turn["tts_first_audio_ms"])
        self.assertIn(e2e_turns.SEGMENT_TO_AUDIO_STAGE, evidence["stages"])
        self.assertEqual(300, evidence["stages"][e2e_turns.SEGMENT_TO_AUDIO_STAGE]["p50"])

    def test_missing_stage_and_provider_are_flagged_not_silently_passed(self):
        evidence = e2e_turns.build_evidence(_Args(), [_MISSING_EVIDENCE], {"model": {"providers": []}})
        problems = e2e_turns.missing_fields(evidence["turns"])
        self.assertTrue(any("tts_first_audio_ms" in p for p in problems))
        self.assertTrue(any("provider" in p for p in problems))

    def test_fully_populated_turns_report_no_missing_fields(self):
        evidence = e2e_turns.build_evidence(
            _Args(), [_CLOUD_SUCCESS, _LOCAL_FALLBACK], {"model": {"providers": []}}
        )
        self.assertEqual([], e2e_turns.missing_fields(evidence["turns"]))

    def test_non_model_route_reports_its_route_label_without_being_flagged(self):
        """A fast_command turn never calls the provider chain, so cost["provider"]
        is legitimately None -- that must read as the route label, not as a
        missing-evidence problem the way a fast_model turn without a provider
        would (see turn_provider())."""
        command_turn = _turn_result(
            route="fast_command",
            trace={
                "trace_id": "t4",
                "routing_ms": 1.0,
                "first_segment_ms": 5.0,
                "tts_first_audio_ms": 10.0,
                "total_request_ms": 20.0,
            },
            cost={"trace_id": "t4", "route": "fast_command", "entries": [], "total_usd": 0.0,
                  "provider": None, "fallback": []},
            response="volumen subido",
            transcript="sube el volumen",
        )
        evidence = e2e_turns.build_evidence(_Args(), [command_turn], {"model": {"providers": []}})
        self.assertEqual("fast_command", evidence["turns"][0]["provider"])
        self.assertEqual([], e2e_turns.missing_fields(evidence["turns"]))

    def test_evidence_never_leaks_response_or_transcript_text(self):
        evidence = e2e_turns.build_evidence(
            _Args(), [_CLOUD_SUCCESS, _LOCAL_FALLBACK, _MISSING_EVIDENCE], {"model": {"providers": []}}
        )
        raw = json.dumps(evidence)
        for result in (_CLOUD_SUCCESS, _LOCAL_FALLBACK, _MISSING_EVIDENCE):
            self.assertNotIn(result.response, raw)
            self.assertNotIn(result.transcript, raw)


class DecomposeFirstSegmentTests(unittest.TestCase):
    """Pure-arithmetic unit tests for the S06 worker/IPC/handoff decomposition,
    independent of build_evidence or a real clone worker."""

    def test_arithmetic(self):
        result = e2e_turns.decompose_first_segment(1700.0, {"ttfa_ms": 400.0, "client_ttfa_ms": 430.0})
        self.assertEqual(400.0, result["worker_ttfa_ms"])
        self.assertEqual(430.0, result["client_ttfa_ms"])
        self.assertEqual(30.0, result["ipc_ms"])
        self.assertEqual(1270.0, result["handoff_ms"])

    def test_none_propagation_when_timing_missing(self):
        result = e2e_turns.decompose_first_segment(1700.0, None)
        self.assertIsNone(result["worker_ttfa_ms"])
        self.assertIsNone(result["client_ttfa_ms"])
        self.assertIsNone(result["ipc_ms"])
        self.assertIsNone(result["handoff_ms"])

    def test_none_propagation_when_segment_to_audio_missing(self):
        result = e2e_turns.decompose_first_segment(None, {"ttfa_ms": 400.0, "client_ttfa_ms": 430.0})
        self.assertEqual(30.0, result["ipc_ms"])
        self.assertIsNone(result["handoff_ms"])

    def test_none_propagation_when_client_ttfa_missing(self):
        result = e2e_turns.decompose_first_segment(1700.0, {"ttfa_ms": 400.0})
        self.assertIsNone(result["ipc_ms"])
        self.assertIsNone(result["handoff_ms"])


_CLONE_DECOMPOSED = _turn_result(
    trace={
        "trace_id": "t5",
        "routing_ms": 1.0,
        "llm_first_token_ms": 300.0,
        "first_segment_ms": 1000.0,
        "tts_first_audio_ms": 2700.0,
        "total_request_ms": 3200.0,
        "worker_ttfa_ms": 400.0,
        "client_ttfa_ms": 430.0,
    },
    cost={"trace_id": "t5", "route": "fast_model", "entries": [], "total_usd": 0.0,
          "provider": "scripted", "fallback": []},
    response="respuesta con voz clonada",
    transcript="pregunta con voz clonada",
)


class DecompositionEvidenceTests(unittest.TestCase):
    """build_evidence must surface the per-turn decomposition, the run
    condition, and status -- and must not flag a scripted-mode provider while
    still flagging a genuinely missing configured-mode provider (MEM162)."""

    def test_build_evidence_includes_decomposition_condition_and_status(self):
        evidence = e2e_turns.build_evidence(
            _Args(llm="scripted", token_delay_ms=25.0), [_CLONE_DECOMPOSED], {"model": {"providers": []}}
        )
        turn = evidence["turns"][0]
        self.assertEqual(400.0, turn["worker_ttfa_ms"])
        self.assertEqual(430.0, turn["client_ttfa_ms"])
        self.assertEqual(30.0, turn["ipc_ms"])
        self.assertEqual(1270.0, turn["handoff_ms"])
        self.assertEqual("scripted-remote-llm", evidence["meta"]["condition"])
        self.assertEqual(25.0, evidence["meta"]["token_delay_ms"])
        self.assertEqual("measured", evidence["status"])
        for stage in ("worker_ttfa_ms", "client_ttfa_ms", "ipc_ms", "handoff_ms"):
            self.assertIn(stage, evidence["stages"])
            self.assertIsNotNone(evidence["stages"][stage]["p50"])

    def test_local_llm_meta_records_cpu_only_ollama_config(self):
        from jarvis.adapters.models.ollama import OllamaProvider

        provider = OllamaProvider(
            model="qwen2.5:3b-instruct", keep_alive="30m", extra_body={"options": {"num_gpu": 0}}
        )
        chain = type("Chain", (), {"providers": (object(), provider)})()
        local_llm = e2e_turns.local_llm_meta(chain)
        self.assertEqual(
            {"model": "qwen2.5:3b-instruct", "options": {"num_gpu": 0}, "keep_alive": "30m"}, local_llm
        )
        evidence = e2e_turns.build_evidence(
            _Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}}, local_llm=local_llm
        )
        self.assertEqual(0, evidence["meta"]["local_llm"]["options"]["num_gpu"])
        self.assertNotIn("api_key", json.dumps(evidence["meta"]))

    def test_local_llm_meta_is_none_without_ollama_provider(self):
        chain = type("Chain", (), {"providers": (object(),)})()
        self.assertIsNone(e2e_turns.local_llm_meta(chain))
        evidence = e2e_turns.build_evidence(_Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}})
        self.assertIsNone(evidence["meta"]["local_llm"])

    def test_configured_condition_is_the_default(self):
        evidence = e2e_turns.build_evidence(_Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}})
        self.assertEqual("configured-llm", evidence["meta"]["condition"])

    def test_missing_decomposition_inputs_are_none_not_fabricated(self):
        evidence = e2e_turns.build_evidence(_Args(), [_CLOUD_SUCCESS], {"model": {"providers": []}})
        turn = evidence["turns"][0]
        self.assertIsNone(turn["worker_ttfa_ms"])
        self.assertIsNone(turn["client_ttfa_ms"])
        self.assertIsNone(turn["ipc_ms"])
        self.assertIsNone(turn["handoff_ms"])

    def test_scripted_provider_not_flagged_while_missing_configured_provider_still_is(self):
        scripted_turn = _turn_result(
            trace={
                "trace_id": "t6", "routing_ms": 1.0, "first_segment_ms": 500.0,
                "tts_first_audio_ms": 900.0, "total_request_ms": 1500.0,
            },
            cost={"trace_id": "t6", "route": "fast_model", "entries": [], "total_usd": 0.0,
                  "provider": "scripted", "fallback": []},
            response="r", transcript="t",
        )
        evidence = e2e_turns.build_evidence(
            _Args(llm="scripted"), [scripted_turn, _MISSING_EVIDENCE], {"model": {"providers": []}}
        )
        problems = e2e_turns.missing_fields(evidence["turns"])
        self.assertFalse(any(p.startswith("turn 1:") and "provider" in p for p in problems))
        self.assertTrue(any(p.startswith("turn 2:") and "provider" in p for p in problems))


class CriterionOneFullPipelineEvidenceTests(unittest.TestCase):
    """Locks docs/bench/e2e-segment-audio-{local,remote}.json (S06-T02) and the
    criterion-1 row in docs/voice-clone.md (S06-T03) together: the doc's
    verdict marks and quoted p50 numbers must be derivable from the measured
    JSON, so the two cannot silently drift apart."""

    LOCAL = BENCH_DIR / "e2e-segment-audio-local.json"
    REMOTE = BENCH_DIR / "e2e-segment-audio-remote.json"
    DOC = Path(__file__).resolve().parents[1] / "docs" / "voice-clone.md"

    def setUp(self):
        self.assertTrue(self.LOCAL.exists(), "docs/bench/e2e-segment-audio-local.json is required for S06-T03")
        self.assertTrue(self.REMOTE.exists(), "docs/bench/e2e-segment-audio-remote.json is required for S06-T03")
        self.local = json.loads(self.LOCAL.read_text(encoding="utf-8"))
        self.remote = json.loads(self.REMOTE.read_text(encoding="utf-8"))
        rows = [
            line for line in self.DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith("| Warmed clone")
        ]
        self.assertEqual(1, len(rows), "expected exactly one criterion-1 row in docs/voice-clone.md")
        self.row = rows[0]

    def test_status_measured_or_failed_with_cause(self):
        for data, label in ((self.local, "local"), (self.remote, "remote")):
            with self.subTest(condition=label):
                status = data.get("status")
                self.assertIn(status, ("measured", "failed"))
                if status == "measured":
                    self.assertGreaterEqual(len(data["turns"]), 20)
                    self.assertGreaterEqual(data["meta"]["turns"], 20)
                else:
                    self.assertTrue(data.get("cause"))

    def test_decomposition_keys_and_meta_condition(self):
        self.assertEqual("configured-llm", self.local["meta"]["condition"])
        self.assertEqual("scripted-remote-llm", self.remote["meta"]["condition"])
        for data in (self.local, self.remote):
            for stage in ("segment_to_first_audio_ms", "worker_ttfa_ms", "client_ttfa_ms", "ipc_ms", "handoff_ms"):
                self.assertIn(stage, data["stages"])
                self.assertIn("p50", data["stages"][stage])
                self.assertIn("p95", data["stages"][stage])
            for turn in data["turns"]:
                for key in ("worker_ttfa_ms", "client_ttfa_ms", "ipc_ms", "handoff_ms"):
                    self.assertIn(key, turn)

    def test_no_private_data_in_new_evidence(self):
        for path in (self.LOCAL, self.REMOTE):
            with self.subTest(path=path.name):
                raw = path.read_text(encoding="utf-8")
                self.assertNotRegex(raw, r"(?i)c:\\\\users")
                self.assertNotRegex(raw, r"sk-")
                self.assertNotRegex(raw, r"(?i)api_key")
                self.assertNotRegex(raw, r"(?i)authorization")
                self.assertNotRegex(raw, r"(?i)pcm|base64|\.wav")
                self.assertIsNone(re.search(r"[A-Za-z0-9+/]{200,}", raw))
                for key in ("transcript", "response", "reply"):
                    self.assertNotIn(f'"{key}"', raw)

    def test_doc_verdict_and_numbers_match_measured_p50(self):
        """The criterion-1 row must quote each condition's measured p50 and
        mark it pass/fail per the JSON's own <700 ms threshold -- this test
        fails if the doc smooths a per-condition failure into a single pass,
        or if the numbers in the row don't match the evidence."""
        local_p50 = self.local["stages"]["segment_to_first_audio_ms"]["p50"]
        remote_p50 = self.remote["stages"]["segment_to_first_audio_ms"]["p50"]
        local_str = str(round(local_p50))
        remote_str = str(round(remote_p50))
        self.assertIn(local_str, self.row, "doc row does not quote the local-condition p50")
        self.assertIn(remote_str, self.row, "doc row does not quote the remote-condition p50")

        # Cell layout for "| Criterion | Evidence | Verdict |" is
        # ['', criterion, evidence, verdict, '']; verdict is second-to-last.
        cells = [c.strip() for c in self.row.split("|")]
        verdict_cell = cells[-2]
        self.assertIn(remote_str, verdict_cell, "verdict cell does not quote the remote-condition p50")
        self.assertIn(local_str, verdict_cell, "verdict cell does not quote the local-condition p50")

        def nearest_mark_before(text, idx):
            marks = list(re.finditer(r"[\u2705\u274c]", text[:idx]))
            self.assertTrue(marks, "no \u2705/\u274c verdict mark precedes the quoted number")
            return marks[-1].group()

        expected_local_mark = "\u2705" if local_p50 < 700 else "\u274c"
        expected_remote_mark = "\u2705" if remote_p50 < 700 else "\u274c"
        remote_idx = verdict_cell.index(remote_str)
        local_idx = verdict_cell.index(local_str, remote_idx)
        self.assertEqual(expected_remote_mark, nearest_mark_before(verdict_cell, remote_idx))
        self.assertEqual(expected_local_mark, nearest_mark_before(verdict_cell, local_idx))

    def test_local_run_used_cpu_only_fallback(self):
        """S08: the local figure must come from a CPU-only Ollama fallback, so
        it cannot silently regress to the GPU-contended 7B condition."""
        if self.local["status"] != "measured":
            self.skipTest("local run not measured; nothing to lock")
        local_llm = self.local["meta"].get("local_llm")
        self.assertIsNotNone(local_llm, "meta.local_llm missing from local evidence")
        self.assertEqual(0, local_llm["options"]["num_gpu"])
        for i, turn in enumerate(self.local["turns"], 1):
            self.assertEqual("ollama", turn.get("provider"), f"turn {i} not served by ollama")
        self.assertTrue(
            "num_gpu" in self.row or "CPU" in self.row,
            "criterion-1 row does not state the CPU-only fallback condition",
        )


class CloudRouteSummaryTests(unittest.TestCase):
    """Locks scripts/e2e_turns.py's cloud_route_summary() (S07-T01): the
    served/external_blocker/failed verdict must be derivable from per-turn
    provider/fallback evidence alone, without reading raw reason text."""

    def test_all_served_under_cap_is_served(self):
        turns = [{"route": "fast_model", "provider": "deepseek", "fallback": []} for _ in range(3)]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.05, max_daily_usd=1.0)
        self.assertEqual(3, summary["cloud_served"])
        self.assertEqual(0, summary["fallback_turns"])
        self.assertEqual({}, summary["fallback_causes"])
        self.assertEqual(0, summary["unexplained_fallbacks"])
        self.assertTrue(summary["within_cap"])
        self.assertEqual("served", summary["verdict"])

    def test_all_fallen_back_with_402_is_external_blocker(self):
        turns = [
            {
                "route": "fast_model",
                "provider": "ollama",
                "fallback": [{"provider": "deepseek", "reason": "upstream 402: Insufficient Balance"}],
            }
            for _ in range(20)
        ]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=1.0)
        self.assertEqual(0, summary["cloud_served"])
        self.assertEqual(20, summary["fallback_turns"])
        self.assertEqual({"insufficient_balance": 20}, summary["fallback_causes"])
        self.assertEqual("external_blocker", summary["verdict"])

    def test_mixed_timeouts_with_zero_served_is_failed(self):
        turns = [
            {
                "route": "fast_model",
                "provider": "ollama",
                "fallback": [{"provider": "deepseek", "reason": "connection timeout"}],
            },
            {
                "route": "fast_model",
                "provider": "ollama",
                "fallback": [{"provider": "deepseek", "reason": "upstream 402: Insufficient Balance"}],
            },
        ]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=1.0)
        self.assertEqual(0, summary["cloud_served"])
        self.assertEqual({"timeout": 1, "insufficient_balance": 1}, summary["fallback_causes"])
        self.assertEqual("failed", summary["verdict"])

    def test_served_run_over_cap_is_not_served(self):
        turns = [{"route": "fast_model", "provider": "deepseek", "fallback": []} for _ in range(3)]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=2.0, max_daily_usd=1.0)
        self.assertEqual(3, summary["cloud_served"])
        self.assertFalse(summary["within_cap"])
        self.assertNotEqual("served", summary["verdict"])

    def test_local_turn_with_empty_fallback_is_unexplained(self):
        turns = [{"route": "fast_model", "provider": "ollama", "fallback": []}]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=1.0)
        self.assertEqual(1, summary["unexplained_fallbacks"])
        self.assertNotEqual("served", summary["verdict"])

    def test_non_fast_model_routes_are_excluded(self):
        turns = [
            {"route": "fast_command", "provider": "fast_command", "fallback": []},
            {"route": "hermes", "provider": None, "fallback": []},
        ]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=1.0)
        self.assertEqual(0, summary["cloud_served"])
        self.assertEqual(0, summary["unexplained_fallbacks"])
        self.assertEqual(0, summary["fallback_turns"])
        self.assertEqual("failed", summary["verdict"])

    def test_no_max_daily_usd_leaves_within_cap_none(self):
        turns = [{"route": "fast_model", "provider": "deepseek", "fallback": []}]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=None)
        self.assertIsNone(summary["within_cap"])
        self.assertEqual("served", summary["verdict"])

    def test_no_raw_reason_text_in_summary(self):
        turns = [
            {
                "route": "fast_model",
                "provider": "ollama",
                "fallback": [
                    {
                        "provider": "deepseek",
                        "reason": "upstream 402: Insufficient Balance request_id=abc123",
                    }
                ],
            }
        ]
        summary = e2e_turns.cloud_route_summary(turns, spend_today_usd=0.0, max_daily_usd=1.0)
        raw = json.dumps(summary)
        self.assertNotIn("request_id", raw)
        self.assertNotIn("abc123", raw)


class PreflightBalanceSummaryTests(unittest.TestCase):
    """Locks scripts/_preflight_check.py's summarize_balance() (S07-T01), the
    pure-parsing half of the secret-free DeepSeek balance probe."""

    def test_summarize_balance_with_funds(self):
        payload = {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "12.34"}],
        }
        summary = _preflight_check.summarize_balance(payload)
        self.assertIn("is_available=True", summary)
        self.assertIn("total_balance=12.34", summary)
        self.assertIn("currency=USD", summary)

    def test_summarize_balance_with_no_funds(self):
        payload = {"is_available": False, "balance_infos": [{"currency": "USD", "total_balance": "0.00"}]}
        summary = _preflight_check.summarize_balance(payload)
        self.assertIn("is_available=False", summary)
        self.assertIn("total_balance=0.00", summary)

    def test_summarize_balance_with_no_balance_infos(self):
        summary = _preflight_check.summarize_balance({"is_available": False})
        self.assertIn("is_available=False", summary)
        self.assertIn("total_balance=None", summary)


class CloudPrimaryEvidenceTests(unittest.TestCase):
    """Locks docs/bench/e2e-cloud-primary.json (S07-T02) and the cloud-route
    row of the M006 acceptance table (S07-T03): the row's mark and quoted
    numbers must follow from the measured turns, so a fabricated pass or a
    dropped external blocker fails here."""

    EVIDENCE = BENCH_DIR / "e2e-cloud-primary.json"
    DOC = Path(__file__).resolve().parents[1] / "docs" / "voice-clone.md"
    LABEL = "Cloud LLM primary serves voice turns"
    MARKS = {"served": "\u2705", "external_blocker": "\u26d4", "failed": "\u274c"}

    def setUp(self):
        self.assertTrue(self.EVIDENCE.exists(), "docs/bench/e2e-cloud-primary.json is required for S07-T03")
        self.raw = self.EVIDENCE.read_text(encoding="utf-8")
        self.data = json.loads(self.raw)
        self.cloud = self.data["cloud_route"]
        rows = [
            line for line in self.DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith(f"| {self.LABEL}")
        ]
        self.assertEqual(1, len(rows), "expected exactly one cloud-route row in docs/voice-clone.md")
        self.row = rows[0]
        self.verdict_cell = [c.strip() for c in self.row.split("|")][-2]

    def test_twenty_turns_and_no_private_data(self):
        self.assertGreaterEqual(self.data["meta"]["turns"], 20)
        self.assertGreaterEqual(len(self.data["turns"]), 20)
        self.assertNotRegex(self.raw, r"(?i)c:\\\\users")
        self.assertNotRegex(self.raw, r"sk-")
        self.assertNotRegex(self.raw, r"(?i)api_key")
        self.assertNotRegex(self.raw, r"(?i)authorization")
        self.assertNotIn("request_id", self.raw)
        self.assertNotRegex(self.raw, r"(?i)pcm|base64|\.wav")
        self.assertIsNone(re.search(r"[A-Za-z0-9+/]{200,}", self.raw))
        for key in ("transcript", "response", "reply"):
            self.assertNotIn(f'"{key}"', self.raw)

    def test_doc_row_has_no_secrets(self):
        self.assertNotRegex(self.row, r"(?i)sk-|api_key|authorization|request_id")

    def test_verdict_matches_recomputation_from_turns(self):
        recomputed = e2e_turns.cloud_route_summary(
            self.data["turns"], self.cloud["spend_today_usd"], self.cloud["max_daily_usd"]
        )
        self.assertEqual(recomputed["verdict"], self.cloud["verdict"])
        self.assertEqual(recomputed["cloud_served"], self.cloud["cloud_served"])
        self.assertEqual(recomputed["fallback_causes"], self.cloud["fallback_causes"])

    def test_doc_mark_matches_verdict_and_only_that_mark(self):
        verdict = self.cloud["verdict"]
        self.assertIn(verdict, self.MARKS)
        for name, mark in self.MARKS.items():
            if name == verdict:
                self.assertIn(mark, self.verdict_cell)
            else:
                self.assertNotIn(mark, self.verdict_cell, f"row carries {mark} but evidence verdict is {verdict}")

    def test_doc_row_quotes_evidence_numbers(self):
        served = self.cloud["cloud_served"]
        self.assertRegex(self.verdict_cell, rf"(?:cloud_served {served}\b|\b{served}/20 cloud)")
        if self.cloud["verdict"] == "external_blocker":
            self.assertIn("402 Insufficient Balance", self.verdict_cell)
            self.assertIn(f"{self.cloud['fallback_turns']}/20 turns fell back", self.verdict_cell)
        elif self.cloud["verdict"] == "served":
            self.assertIn(f"${self.cloud['spend_today_usd']:.2f}", self.verdict_cell)
        self.assertIn("bench/e2e-cloud-primary.json", self.row)


if __name__ == "__main__":
    unittest.main()
