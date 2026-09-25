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

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _markdown_files():
    files = list(DOCS_DIR.glob("*.md"))
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


if __name__ == "__main__":
    unittest.main()
