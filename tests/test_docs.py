"""Consistency checks for the docs/ tree.

Guards two things: markdown links must resolve to real files, and the
before/after numbers in docs/latency-report.md must match the raw benchmark
data in docs/bench/latest.json exactly (no drift, no invented numbers).
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
LATENCY_REPORT = DOCS_DIR / "latency-report.md"
LATEST_JSON = DOCS_DIR / "bench" / "latest.json"
CONFIGURATION = DOCS_DIR / "configuration.md"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ENV_VAR_RE = re.compile(r"\bJARVIS_[A-Z_]+\b")


M007_RECORD = DOCS_DIR / "engineering" / "m007-verification.md"
M007_EVIDENCE = DOCS_DIR / "bench" / "windows-acceptance-2026-09-30.json"


def _markdown_files():
    files = list(DOCS_DIR.glob("*.md")) + list((DOCS_DIR / "engineering").glob("*.md"))
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    return files


def _get(data, *path):
    for key in path:
        data = data[key]
    return data


class TestRelativeLinksResolve(unittest.TestCase):
    def test_relative_links_resolve(self):
        for md_file in _markdown_files():
            text = md_file.read_text(encoding="utf-8")
            for target in LINK_RE.findall(text):
                target = target.strip()
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if not target:
                    continue
                resolved = (md_file.parent / target).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"{md_file.relative_to(REPO_ROOT)} links to missing "
                    f"target {target!r} (resolved {resolved})",
                )


class TestLatencyReportMatchesBench(unittest.TestCase):
    def test_latency_report_matches_bench(self):
        bench = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        report_text = LATENCY_REPORT.read_text(encoding="utf-8")

        checks = [
            ("stt", "transcribe_ms", "p50"),
            ("stt", "transcribe_ms", "p95"),
            ("command", "cached_ack_to_device_ms (real output stream, silent)", "p50"),
            ("command", "cached_ack_to_device_ms (real output stream, silent)", "p95"),
            ("interrupt", "playback_stop_ms", "p50"),
            ("interrupt", "playback_stop_ms", "p95"),
            ("llm", "ttft_ms", "p50"),
            ("llm", "ttft_ms", "p95"),
            ("e2e", "end_of_utterance_to_first_audio_ms", "p50"),
            ("e2e", "end_of_utterance_to_first_audio_ms", "p95"),
            ("tts", "warm_ttfa_ms", "p50"),
            ("tts", "warm_ttfa_ms", "p95"),
        ]

        for path in checks:
            value = _get(bench, *path)
            formatted = str(value)
            self.assertIn(
                formatted,
                report_text,
                f"latency-report.md is missing bench value {formatted!r} "
                f"for {'.'.join(path)}",
            )

    def test_latency_report_declares_unmeasured(self):
        report_text = LATENCY_REPORT.read_text(encoding="utf-8")
        self.assertIn("Not measured yet", report_text)
        self.assertIn("stt-bakeoff.md", report_text)


class TestEnvVarsDocumented(unittest.TestCase):
    def test_env_vars_documented(self):
        found: set[str] = set()
        for base in (REPO_ROOT / "src", REPO_ROOT / "scripts"):
            for py_file in base.rglob("*.py"):
                if "__pycache__" in py_file.parts:
                    continue
                found.update(ENV_VAR_RE.findall(py_file.read_text(encoding="utf-8")))

        self.assertTrue(found, "expected at least one JARVIS_* env var reference in src/ or scripts/")

        config_text = CONFIGURATION.read_text(encoding="utf-8")
        for name in sorted(found):
            self.assertIn(
                name, config_text,
                f"docs/configuration.md does not document env var {name!r}",
            )
        self.assertIn("DEEPSEEK_API_KEY", config_text)
        self.assertIn("DASHSCOPE_API_KEY", config_text)


class TestM007VerificationRecord(unittest.TestCase):
    def setUp(self):
        self.assertTrue(M007_RECORD.exists(), f"{M007_RECORD} is missing")
        self.text = M007_RECORD.read_text(encoding="utf-8")
        self.evidence = json.loads(M007_EVIDENCE.read_text(encoding="utf-8"))

    def test_lists_the_seven_success_criteria(self):
        for number in range(1, 8):
            self.assertRegex(self.text, rf"(?m)^### {number}\. .+: (Met|Partial|Owner UAT pending)$")

    def test_single_owner_uat_checklist_with_items(self):
        self.assertEqual(self.text.count("## Owner UAT checklist"), 1)
        checklist = self.text.split("## Owner UAT checklist", 1)[1]
        # Items are ticked as the owner observes them; the unobserved ones must stay explicit.
        self.assertGreaterEqual(checklist.count("- [ ] ") + checklist.count("- [x] "), 6)
        self.assertGreaterEqual(checklist.count("- [ ] "), 1)
        self.assertIn("## Accepted limitations", self.text)
        self.assertIn("## Follow-ups", self.text)

    def test_evidence_numbers_match_raw_json(self):
        startup = _get(self.evidence, "perf_bench_claps_startup", "startup")
        claps = _get(self.evidence, "perf_bench_claps_startup", "claps")
        steps = {s["step"]: s for s in self.evidence["daemon_acceptance"]}
        idle = steps["idle_resources"]["resources"]
        values = {
            "chime callback p50": _get(startup, "primed", "gesture_to_chime_callback_ms", "p50"),
            "confirmation p50": _get(claps, "confirmation_after_last_clap_ms", "p50"),
            "confirmation p95": _get(claps, "confirmation_after_last_clap_ms", "p95"),
            "cold p95": _get(startup, "cold", "gesture_to_chime_callback_ms", "p95"),
            "first_sound": _get(steps["active"], "timings_ms_since_gesture", "first_sound"),
            "interactive": _get(steps["active"], "timings_ms_since_gesture", "interactive"),
            "idle cpu mean": _get(idle, "cpu_percent_one_core", "mean"),
            "idle cpu max": _get(idle, "cpu_percent_one_core", "max"),
            "idle rss": _get(idle, "rss_mb", "last"),
            "vram idle": steps["idle_vram"]["vram_mb"],
            "vram active": steps["active_vram"]["vram_mb"],
            "vram after sleep": steps["after_sleep_vram"]["vram_mb"],
            "vram after quit": steps["cleanup"]["vram_mb"],
            "vram preflight": steps["preflight"]["vram_mb"],
        }
        for name, value in values.items():
            self.assertIn(str(value), self.text, f"m007-verification.md is missing {name} = {value!r}")
        self.assertIn(str(_get(claps, "detected")), self.text)


if __name__ == "__main__":
    unittest.main()
