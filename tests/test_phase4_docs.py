"""Phase 4 documentation contract: six docs exist, links and evidence resolve, final status is honest.

Reads only tracked docs/engineering files; negative cases use temporary fixtures.
"""

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING = REPO_ROOT / "docs" / "engineering"

SIX_DOCS = (
    "phase-4-report.md",
    "visual-core-architecture.md",
    "audio-visualization.md",
    "startup-cinematic.md",
    "phase-4-test-report.md",
    "phase-5-handoff.md",
)
STATUSES = {"PASS", "FAIL", "BLOCKED", "UNMEASURED"}
REQUIRED_CATEGORIES = (
    "Python unit suite",
    "Build and lint",
    "Phase 4 Playwright suite",
    "Screenshot evidence",
    "Cancellation",
    "Disconnect, reconnect",
    "Stale and out-of-order",
    "Real microphone",
    "Real playback",
    "Native Overlay and Command Center",
    "User-configured startup",
    "Native idle CPU",
    "Native idle RAM",
)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
STATUS_RE = re.compile(r"^PHASE 4 STATUS: (PASS|FAIL|BLOCKED)\s*$", re.MULTILINE)


def _local_targets(doc: Path):
    for target in LINK_RE.findall(doc.read_text(encoding="utf-8")):
        target = target.split("#", 1)[0].strip()
        if target and "://" not in target and not target.startswith("mailto:"):
            yield target


def check_docs(root: Path) -> list[str]:
    """Return a list of problems found under a docs/engineering-shaped root."""
    problems: list[str] = []
    for name in SIX_DOCS:
        if not (root / name).is_file():
            problems.append(f"missing doc: {name}")
    for doc in sorted(root.glob("*.md")):
        for target in _local_targets(doc):
            if not (doc.parent / target).exists():
                problems.append(f"{doc.name}: broken link {target}")
    report = root / "phase-4-report.md"
    if report.is_file():
        problems += _check_report(report.read_text(encoding="utf-8"))
    index = root / "phase-4-evidence" / "index.json"
    problems += _check_index(index)
    return problems


def _check_report(text: str) -> list[str]:
    problems: list[str] = []
    statuses = STATUS_RE.findall(text)
    if len(statuses) != 1:
        problems.append(f"expected exactly one PHASE 4 STATUS line, found {len(statuses)}")
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and cells[1] in STATUSES:
            rows[cells[0]] = cells[1]
    for category in REQUIRED_CATEGORIES:
        if not any(name.startswith(category) for name in rows):
            problems.append(f"missing acceptance category: {category}")
    unproven = [n for n, s in rows.items() if s != "PASS"]
    if statuses:
        final = statuses[0]
        if final == "PASS" and unproven:
            problems.append(f"final status PASS but rows not PASS: {unproven}")
        if final == "BLOCKED" and not any(s in ("BLOCKED", "UNMEASURED") for s in rows.values()):
            problems.append("final status BLOCKED but no BLOCKED/UNMEASURED row")
    return problems


def _check_index(index: Path) -> list[str]:
    if not index.is_file():
        return ["missing evidence index.json"]
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        entries = data["entries"]
        if not isinstance(entries, list) or not entries:
            return ["evidence index has no entries"]
        problems = []
        for entry in entries:
            for key in ("file", "source", "assertion"):
                if not entry.get(key):
                    problems.append(f"index entry missing {key}: {entry}")
            if entry.get("file") and not (index.parent / entry["file"]).is_file():
                problems.append(f"index image missing: {entry['file']}")
        return problems
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"malformed evidence index: {exc!r}"]


REV_RE = re.compile(r"^[0-9a-f]{40}$")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_acceptance(evidence: Path) -> list[str]:
    """Validate acceptance-runs.json and its transcripts under an evidence-shaped dir."""
    manifest = evidence / "acceptance-runs.json"
    if not manifest.is_file():
        return ["missing acceptance-runs.json"]
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        problems: list[str] = []
        revision = data["tested_source_revision"]
        if not REV_RE.match(str(revision)):
            problems.append(f"invalid tested revision: {revision!r}")
        scope = str(data["scope"]).lower()
        if "fixture" not in scope or "no native" not in scope:
            problems.append("scope must label fixture evidence and disclaim native claims")
        skips = data["intentional_skips"]
        if not skips or not all(k.get("reason") and k.get("count", 0) > 0 for k in skips):
            problems.append("intentional skips must be enumerated with counts and reasons")
        if data.get("missing_prerequisite_skips") != 0:
            problems.append("missing-prerequisite skips are not a success")
        if not data["critical_scenarios"]:
            problems.append("no critical scenarios recorded")
        runs = data["runs"]
        if len(runs) != 2 or [r.get("run") for r in runs] != [1, 2]:
            return problems + ["expected exactly runs 1 and 2 in order"]
        if len({r["transcript"] for r in runs}) != 2:
            problems.append("runs must have distinct transcripts")
        skipped_total = sum(k["count"] for k in skips)
        prev_end = None
        for r in runs:
            tag = f"run {r['run']}"
            if r["exit_code"] != 0 or r["failed"] != 0 or r.get("flaky", 0) != 0:
                problems.append(f"{tag}: not a clean pass")
            if r["passed"] <= 0:
                problems.append(f"{tag}: no executed coverage")
            if r["skipped"] != skipped_total:
                problems.append(f"{tag}: skipped {r['skipped']} != enumerated {skipped_total}")
            if r.get("source_revision_after") != revision:
                problems.append(f"{tag}: source revision mismatch")
            if not (r.get("tree_clean_before") and r.get("tree_clean_after")):
                problems.append(f"{tag}: tree not clean")
            start, end = _ts(r["started_at"]), _ts(r["finished_at"])
            if end <= start or (prev_end is not None and start < prev_end):
                problems.append(f"{tag}: invalid timestamp order")
            prev_end = end
            name = r["transcript"]
            if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "/" in name or "\\" in name:
                problems.append(f"{tag}: transcript path must be a plain relative name: {name}")
                continue
            path = evidence / name
            if not path.is_file():
                problems.append(f"{tag}: missing transcript {name}")
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != r["transcript_sha256"]:
                problems.append(f"{tag}: transcript digest mismatch")
            text = raw.decode("utf-8", errors="replace")
            if f"{r['passed']} passed" not in text or f"{r['skipped']} skipped" not in text:
                problems.append(f"{tag}: transcript totals do not match manifest")
            if "\x1b" in text or "/home/" in text:
                problems.append(f"{tag}: transcript not sanitized")
            for scenario in data["critical_scenarios"]:
                if scenario not in text:
                    problems.append(f"{tag}: critical scenario not executed: {scenario}")
        return problems
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"malformed acceptance manifest: {exc!r}"]


NATIVE_ROW_IDS = (
    "runtime-source-resolution", "microphone-envelope", "playback-envelope", "audible-playback-human",
    "cancellation-envelope", "disconnect-recovery", "reconnect-no-replay", "startup-two-clap",
    "startup-assets", "native-overlay", "native-command-center", "stale-ordering-native",
    "idle-cpu", "idle-ram", "reduced-motion-native", "av-sync-human",
)
ROW_STATUSES = {"PASS", "FAIL", "BLOCKED", "UNMEASURED"}
EVIDENCE_SCOPES = {"native-programmatic", "human-observation", "fixture", "none"}
NATIVE_PROVENANCE = {"native-runner-output-sanitized"}
HUMAN_PROVENANCE = {"human-observation-record"}
ARTIFACT_PROVENANCE = NATIVE_PROVENANCE | HUMAN_PROVENANCE | {"fixture", "synthetic", "transcribed"}
LEAK_MARKERS = ("/home/", "C:\\", "\\\\wsl", "/mnt/", "password", "api_key", "secret", "token=")


def _safe_artifact_path(base: Path, name: object) -> Path | None:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return None
    posix = PurePosixPath(name)
    if posix.is_absolute() or PureWindowsPath(name).is_absolute() or ".." in posix.parts or "." in posix.parts:
        return None
    resolved = (base / name).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None
    return resolved


def derive_aggregate(rows: list[dict]) -> str:
    statuses = {r["status"] for r in rows}
    if "FAIL" in statuses:
        return "FAIL"
    return "PASS" if statuses == {"PASS"} else "BLOCKED"


def _check_native_sample(path: Path, row: dict, tag: str) -> list[str]:
    try:
        sample = json.loads(path.read_text(encoding="utf-8"))
        runs = {r["entry"]: r for r in sample["runs"]}
    except (ValueError, KeyError, TypeError) as exc:
        return [f"{tag}: native sample unreadable: {exc!r}"]
    run = runs.get(row.get("sample_entry"))
    if run is None:
        return [f"{tag}: sample has no entry {row.get('sample_entry')!r}"]
    if run.get("status") != "PASS" or run.get("exit_code") != 0 or run.get("remaining_count") or run.get("error"):
        return [f"{tag}: native sample entry is not a clean PASS"]
    return []


def check_native_hardware(evidence: Path) -> list[str]:
    """Validate native-hardware.json: fail closed on any unsupported promotion."""
    manifest = evidence / "native-hardware.json"
    if not manifest.is_file():
        return ["missing native-hardware.json"]
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        problems: list[str] = []
        if data["schema_version"] != 1:
            problems.append(f"unsupported schema_version: {data['schema_version']!r}")
        rows = data["rows"]
        artifacts = {}
        for art in data["artifacts"]:
            aid = art["id"]
            if aid in artifacts:
                problems.append(f"duplicate artifact id: {aid}")
            artifacts[aid] = art
            if art.get("provenance") not in ARTIFACT_PROVENANCE:
                problems.append(f"artifact {aid}: invalid provenance {art.get('provenance')!r}")
            path = _safe_artifact_path(evidence, art.get("path"))
            if path is None:
                problems.append(f"artifact {aid}: path must be a safe relative path inside the evidence directory: {art.get('path')!r}")
                continue
            if not path.is_file():
                problems.append(f"artifact {aid}: missing file {art['path']}")
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != art.get("sha256"):
                problems.append(f"artifact {aid}: digest mismatch")
            text = raw.decode("utf-8", errors="replace")
            if "\x1b" in text or any(m.lower() in text.lower() for m in LEAK_MARKERS):
                problems.append(f"artifact {aid}: not sanitized")
        ids = [r.get("id") for r in rows]
        if len(set(ids)) != len(ids):
            problems.append("duplicate row ids")
        for missing in sorted(set(NATIVE_ROW_IDS) - set(ids)):
            problems.append(f"missing row: {missing}")
        for extra in sorted(set(ids) - set(NATIVE_ROW_IDS)):
            problems.append(f"unknown row: {extra}")
        for row in rows:
            tag = f"row {row.get('id')}"
            status = row.get("status")
            scope = row.get("evidence_scope")
            if status not in ROW_STATUSES:
                problems.append(f"{tag}: invalid status {status!r}")
                continue
            if scope not in EVIDENCE_SCOPES:
                problems.append(f"{tag}: invalid evidence scope {scope!r}")
            requires = row.get("requires")
            if not requires or not set(requires) <= {"native", "human"}:
                problems.append(f"{tag}: requires must be a non-empty subset of native/human")
                continue
            linked = []
            for aid in row.get("artifacts", []):
                if aid not in artifacts:
                    problems.append(f"{tag}: unknown artifact {aid}")
                else:
                    linked.append(artifacts[aid])
            if status == "PASS":
                if "native" in requires:
                    native = [a for a in linked if a.get("provenance") in NATIVE_PROVENANCE]
                    if not native or scope != "native-programmatic":
                        problems.append(f"{tag}: PASS without native proof")
                    for art in native:
                        path = _safe_artifact_path(evidence, art.get("path"))
                        if path is not None and path.is_file():
                            problems += _check_native_sample(path, row, tag)
                if "human" in requires and not any(a.get("provenance") in HUMAN_PROVENANCE for a in linked):
                    problems.append(f"{tag}: PASS without human evidence")
                if any(a.get("provenance") in {"fixture", "synthetic", "transcribed"} for a in linked):
                    problems.append(f"{tag}: PASS backed by fixture/synthetic/transcribed artifact")
            elif not row.get("blocker"):
                problems.append(f"{tag}: non-PASS row needs an explicit blocker")
            for item in row.get("history", []):
                if item.get("provenance") != "narrative-unverified":
                    problems.append(f"{tag}: history must be narrative-unverified")
                numeric = item.get("numeric", {})
                for key in ("cpu_percent", "private_mb"):
                    for v in numeric.get(key, []):
                        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 16384 or (key == "cpu_percent" and v > 100):
                            problems.append(f"{tag}: numeric {key} out of bounds: {v!r}")
        try:
            if data["aggregate_status"] != derive_aggregate(rows):
                problems.append(f"aggregate_status {data['aggregate_status']!r} != derived {derive_aggregate(rows)!r}")
        except KeyError:
            pass
        return problems
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"malformed native hardware manifest: {exc!r}"]

CURRENT_AGG_RE = re.compile(r"^\*\*Current aggregate status: (PASS|FAIL|BLOCKED)\*\*\s*$", re.MULTILINE)
HISTORY_HEADING_RE = re.compile(r"^## Historical", re.MULTILINE)
ID_CELL_RE = re.compile(r"`([a-z0-9-]+)`")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def check_cross_surface(root: Path) -> list[str]:
    """Manifest, index.json, report matrix and Windows table must agree on aggregate and per-row status."""
    evidence = root / "phase-4-evidence"
    try:
        manifest = json.loads((evidence / "native-hardware.json").read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in manifest["rows"]}
        aggregate = manifest["aggregate_status"]
        index = json.loads((evidence / "index.json").read_text(encoding="utf-8"))
        windows = (root / "phase-4-windows-acceptance.md").read_text(encoding="utf-8")
        report = (root / "phase-4-report.md").read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"cannot load surfaces: {exc!r}"]
    problems: list[str] = []

    section = index.get("native_hardware") if isinstance(index, dict) else None
    if not isinstance(section, dict):
        problems.append("index.json: missing native_hardware section")
    else:
        if section.get("manifest") != "native-hardware.json":
            problems.append("index.json: native_hardware must link native-hardware.json")
        if section.get("aggregate_status") != aggregate:
            problems.append(f"index.json aggregate {section.get('aggregate_status')!r} != manifest {aggregate!r}")
        raw_rows = section.get("rows")
        if not isinstance(raw_rows, list):
            problems.append("index.json: native_hardware.rows must be a list")
            raw_rows = []
        by_id: dict = {}
        for raw in raw_rows:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                problems.append(f"index.json: malformed native_hardware row {raw!r}")
            elif raw["id"] in by_id:
                problems.append(f"index.json: duplicate row {raw['id']}")
            else:
                by_id[raw["id"]] = raw
        paths = {a["id"]: a["path"] for a in manifest["artifacts"]}
        for rid, row in rows.items():
            entry = by_id.get(rid)
            if entry is None:
                problems.append(f"index.json: missing row {rid}")
                continue
            if entry.get("status") != row["status"] or entry.get("evidence_scope") != row["evidence_scope"]:
                problems.append(f"index.json: row {rid} disagrees with manifest")
            arts = entry.get("artifacts")
            if not isinstance(arts, list) or not all(isinstance(a, str) for a in arts):
                problems.append(f"index.json: row {rid} artifacts must be a list of paths")
                continue
            if sorted(arts) != sorted(paths.get(a) for a in row["artifacts"]):
                problems.append(f"index.json: row {rid} artifacts disagree with manifest")
            for art in arts:
                if _safe_artifact_path(evidence, art) is None or not (evidence / art).is_file():
                    problems.append(f"index.json: row {rid} artifact missing or unsafe: {art!r}")
        for rid in set(by_id) - set(rows):
            problems.append(f"index.json: unknown row {rid}")

    # Windows document: one current aggregate line, a table keyed by manifest id, history clearly separated.
    split = HISTORY_HEADING_RE.search(windows)
    current, history = (windows[: split.start()], windows[split.start():]) if split else (windows, "")
    if not split:
        problems.append("windows: missing '## Historical' section")
    elif "unverified" not in history.lower() or "not acceptance" not in history.lower():
        problems.append("windows: historical section must be labelled unverified / not acceptance")
    aggs = CURRENT_AGG_RE.findall(windows)
    if len(aggs) != 1 or not CURRENT_AGG_RE.search(current):
        problems.append(f"windows: expected exactly one current aggregate line before the historical section, found {len(aggs)}")
    elif aggs[0] != aggregate:
        problems.append(f"windows aggregate {aggs[0]} != manifest {aggregate}")
    if re.search(r"^\*\*Overall status", windows, re.MULTILINE):
        problems.append("windows: stale 'Overall status' line")
    seen = {}
    for line in current.splitlines():
        cells = _cells(line) if line.startswith("|") else []
        m = ID_CELL_RE.fullmatch(cells[0]) if cells else None
        if m and m.group(1) in rows and len(cells) >= 6:
            rid = m.group(1)
            seen[rid] = cells
            if cells[2] != rows[rid]["status"] or cells[3] != rows[rid]["evidence_scope"]:
                problems.append(f"windows: row {rid} disagrees with manifest")
            for art in rows[rid]["artifacts"]:
                path = next(a["path"] for a in manifest["artifacts"] if a["id"] == art)
                if f"phase-4-evidence/{path}" not in cells[5]:
                    problems.append(f"windows: row {rid} missing evidence link {path}")
            if rows[rid]["status"] != "PASS" and not rows[rid]["blocker"][:20] in cells[5]:
                problems.append(f"windows: row {rid} missing blocker text")
    for rid in set(rows) - set(seen):
        problems.append(f"windows: missing row {rid}")
    if "/tmp/" in windows:
        problems.append("windows: unusable temporary path reference")
    for stale in re.findall(r"\]\(([^)]*)\)", history):
        if stale.startswith(("/", "file:")) or "\\" in stale:
            problems.append(f"windows: external link in history: {stale}")

    # Report: matrix rows citing manifest ids must carry the derived status and the manifest scope.
    final = STATUS_RE.findall(report)
    if final and final[0] != aggregate:
        problems.append(f"report final status {final[0]} != manifest aggregate {aggregate}")
    if f"hardware acceptance is {aggregate}" not in report:
        problems.append("report: opening must state the manifest-backed hardware status")
    cited: set[str] = set()
    for line in report.splitlines():
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) < 3 or cells[1] not in STATUSES:
            continue
        ids = [i for i in ID_CELL_RE.findall(cells[2]) if i in rows]
        if not ids:
            continue
        cited.update(ids)
        if cells[1] != derive_aggregate([rows[i] for i in ids]):
            problems.append(f"report: row {cells[0]!r} status {cells[1]} disagrees with manifest {ids}")
        scopes = {rows[i]["evidence_scope"] for i in ids}
        scope = scopes.pop() if len(scopes) == 1 else "none"
        if not cells[2].startswith(f"{scope}:"):
            problems.append(f"report: row {cells[0]!r} evidence must start with scope '{scope}:' (no fixture/native conflation)")
        for i in ids:
            for art in rows[i]["artifacts"]:
                path = next(a["path"] for a in manifest["artifacts"] if a["id"] == art)
                if path not in cells[2]:
                    problems.append(f"report: row {cells[0]!r} missing evidence link {path}")
    for rid in sorted(set(rows) - cited):
        problems.append(f"report: manifest row {rid} not in acceptance matrix")
    for line in report.splitlines():
        cells = _cells(line) if line.startswith("|") else []
        if len(cells) >= 3 and cells[1] in STATUSES and not any(i in rows for i in ID_CELL_RE.findall(cells[2])):
            if re.match(r"(Real|Native|Two-clap|Human|User-configured|Windows runtime)", cells[0]):
                problems.append(f"report: hardware row {cells[0]!r} does not cite a manifest id")
    return problems


def _manifest_aggregate() -> str:
    return json.loads((ENGINEERING / "phase-4-evidence" / "native-hardware.json").read_text())["aggregate_status"]


def _other(status: str) -> str:
    return "BLOCKED" if status == "PASS" else "PASS"


class TrackedDocsContract(unittest.TestCase):
    def test_real_docs_satisfy_contract(self):
        self.assertEqual(check_docs(ENGINEERING), [])

    def test_report_status_follows_the_manifest(self):
        text = (ENGINEERING / "phase-4-report.md").read_text(encoding="utf-8")
        aggregate = _manifest_aggregate()
        self.assertEqual(STATUS_RE.findall(text), [aggregate])
        self.assertTrue(text.rstrip().endswith(f"PHASE 4 STATUS: {aggregate}"))
        if aggregate == "PASS":  # PASS is only reachable through hash-verified samples and human records
            self.assertEqual(check_native_hardware(ENGINEERING / "phase-4-evidence"), [])

    def test_every_png_in_evidence_is_indexed(self):
        evidence = ENGINEERING / "phase-4-evidence"
        indexed = {e["file"] for e in json.loads((evidence / "index.json").read_text())["entries"]}
        self.assertEqual({p.name for p in evidence.glob("*.png")}, indexed)

    def test_acceptance_manifest_and_transcripts_are_valid(self):
        self.assertEqual(check_acceptance(ENGINEERING / "phase-4-evidence"), [])

    def test_native_hardware_manifest_is_valid_and_aggregate_is_derived(self):
        evidence = ENGINEERING / "phase-4-evidence"
        self.assertEqual(check_native_hardware(evidence), [])
        data = json.loads((evidence / "native-hardware.json").read_text())
        self.assertEqual(data["aggregate_status"], derive_aggregate(data["rows"]))

    def test_published_surfaces_agree_with_manifest(self):
        self.assertEqual(check_cross_surface(ENGINEERING), [])
        report = (ENGINEERING / "phase-4-report.md").read_text(encoding="utf-8")
        windows = (ENGINEERING / "phase-4-windows-acceptance.md").read_text(encoding="utf-8")
        self.assertIn(f"**Current aggregate status: {_manifest_aggregate()}**", windows)

    def test_test_report_references_manifest_and_transcripts(self):
        text = (ENGINEERING / "phase-4-test-report.md").read_text(encoding="utf-8")
        for ref in ("acceptance-runs.json", "acceptance-run-1.txt", "acceptance-run-2.txt", "supersedes"):
            self.assertIn(ref, text)

    def test_phase5_handoff_is_documentation_only(self):
        text = (ENGINEERING / "phase-5-handoff.md").read_text(encoding="utf-8")
        self.assertIn("not implemented", text)


class NegativeCases(unittest.TestCase):
    def _fixture(self, tmp: str) -> Path:
        root = Path(tmp)
        for name in SIX_DOCS:
            (root / name).write_text("# stub\n", encoding="utf-8")
        ev = root / "phase-4-evidence"
        ev.mkdir()
        (ev / "a.png").write_bytes(b"\x89PNG")
        (ev / "index.json").write_text(
            json.dumps({"entries": [{"file": "a.png", "source": "fixture", "assertion": "x"}]}), encoding="utf-8"
        )
        rows = "\n".join(f"| {c} | PASS | e |" for c in REQUIRED_CATEGORIES)
        (root / "phase-4-report.md").write_text(f"{rows}\n\nPHASE 4 STATUS: PASS\n", encoding="utf-8")
        return root

    def test_clean_fixture_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_docs(self._fixture(tmp)), [])

    def test_missing_doc_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            (root / "phase-5-handoff.md").unlink()
            self.assertTrue(any("missing doc" in p for p in check_docs(root)))

    def test_broken_link_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            (root / "startup-cinematic.md").write_text("[x](nope.md)\n", encoding="utf-8")
            self.assertTrue(any("broken link" in p for p in check_docs(root)))

    def test_malformed_index_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            (root / "phase-4-evidence" / "index.json").write_text("{not json", encoding="utf-8")
            self.assertTrue(any("malformed" in p for p in check_docs(root)))

    def test_index_entry_pointing_at_missing_image_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            (root / "phase-4-evidence" / "a.png").unlink()
            self.assertTrue(any("image missing" in p for p in check_docs(root)))

    def test_pass_with_blocked_row_is_inconsistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            text = (root / "phase-4-report.md").read_text().replace("Real microphone | PASS", "Real microphone | BLOCKED")
            (root / "phase-4-report.md").write_text(text, encoding="utf-8")
            self.assertTrue(any("final status PASS" in p for p in check_docs(root)))

    def test_duplicate_or_missing_status_line_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            report = root / "phase-4-report.md"
            report.write_text(report.read_text() + "PHASE 4 STATUS: PASS\n", encoding="utf-8")
            self.assertTrue(any("exactly one" in p for p in check_docs(root)))
            report.write_text("no status\n", encoding="utf-8")
            self.assertTrue(any("exactly one" in p for p in check_docs(root)))

    def test_missing_category_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            report = root / "phase-4-report.md"
            report.write_text(report.read_text().replace("| Native idle RAM | PASS | e |\n", ""), encoding="utf-8")
            self.assertTrue(any("Native idle RAM" in p for p in check_docs(root)))


class AcceptanceNegativeCases(unittest.TestCase):
    SCENARIO = "reload resyncs"

    def _fixture(self, tmp: str, mutate=None) -> Path:
        ev = Path(tmp)
        runs = []
        for n in (1, 2):
            text = f"  {self.SCENARIO}\n  60 skipped\n  154 passed (2.1m)\n"
            (ev / f"acceptance-run-{n}.txt").write_text(text, encoding="utf-8")
            runs.append({
                "run": n, "started_at": f"2026-10-16T21:0{n}:00Z", "finished_at": f"2026-10-16T21:0{n}:30Z",
                "exit_code": 0, "passed": 154, "failed": 0, "flaky": 0, "skipped": 60,
                "tree_clean_before": True, "tree_clean_after": True, "source_revision_after": "a" * 40,
                "transcript": f"acceptance-run-{n}.txt",
                "transcript_sha256": hashlib.sha256(text.encode()).hexdigest(),
            })
        data = {
            "tested_source_revision": "a" * 40,
            "scope": "fixture + bridge-stub; no native hardware",
            "intentional_skips": [{"count": 60, "reason": "duplicate viewport"}],
            "missing_prerequisite_skips": 0,
            "critical_scenarios": [self.SCENARIO],
            "runs": runs,
        }
        if mutate:
            mutate(data, ev)
        (ev / "acceptance-runs.json").write_text(json.dumps(data), encoding="utf-8")
        return ev

    def _problems(self, mutate=None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            return check_acceptance(self._fixture(tmp, mutate))

    def test_clean_fixture_passes(self):
        self.assertEqual(self._problems(), [])

    def test_missing_transcript(self):
        self.assertTrue(any("missing transcript" in p for p in self._problems(lambda d, e: (e / "acceptance-run-2.txt").unlink())))

    def test_corrupt_transcript(self):
        def m(d, e):
            (e / "acceptance-run-1.txt").write_text("tampered 154 passed 60 skipped " + self.SCENARIO, encoding="utf-8")
        self.assertTrue(any("digest mismatch" in p for p in self._problems(m)))

    def test_mismatched_revision(self):
        def m(d, e):
            d["runs"][1]["source_revision_after"] = "b" * 40
        self.assertTrue(any("revision mismatch" in p for p in self._problems(m)))

    def test_failed_run(self):
        def m(d, e):
            d["runs"][0]["failed"] = 1
            d["runs"][0]["exit_code"] = 1
        self.assertTrue(any("not a clean pass" in p for p in self._problems(m)))

    def test_unexpected_integration_skip(self):
        self.assertTrue(any("missing-prerequisite" in p for p in self._problems(lambda d, e: d.update(missing_prerequisite_skips=2))))
        self.assertTrue(any("skipped" in p for p in self._problems(lambda d, e: d["runs"][0].update(skipped=61))))

    def test_invalid_timestamp_order(self):
        def m(d, e):
            d["runs"][1]["started_at"] = "2026-10-16T20:00:00Z"
            d["runs"][1]["finished_at"] = "2026-10-16T20:00:30Z"
        self.assertTrue(any("timestamp order" in p for p in self._problems(m)))

    def test_path_traversal_and_absolute_path(self):
        for bad in ("../acceptance-run-1.txt", "/etc/passwd", "C:\\x.txt"):
            def m(d, e, bad=bad):
                d["runs"][0]["transcript"] = bad
            self.assertTrue(any("plain relative" in p for p in self._problems(m)), bad)

    def test_duplicate_runs_and_missing_scenario(self):
        def dup(d, e):
            d["runs"][1]["transcript"] = d["runs"][0]["transcript"]
        self.assertTrue(any("distinct" in p for p in self._problems(dup)))
        self.assertTrue(any("not executed" in p for p in self._problems(lambda d, e: d.update(critical_scenarios=["absent scenario"]))))

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_acceptance(Path(tmp)), ["missing acceptance-runs.json"])


class NativeHardwareNegativeCases(unittest.TestCase):
    def _fixture(self, tmp: str, mutate=None) -> Path:
        ev = Path(tmp)
        (ev / "native-samples").mkdir()
        sample = {"status": "PASS", "runs": [{"entry": "overlay", "status": "PASS", "exit_code": 0, "remaining_count": 0, "error": None}]}
        text = json.dumps(sample)
        (ev / "native-samples" / "s.json").write_text(text, encoding="utf-8")
        rows = []
        for rid in NATIVE_ROW_IDS:
            rows.append({"id": rid, "status": "BLOCKED", "evidence_scope": "none", "requires": ["native"],
                         "artifacts": [], "blocker": "no sample", "history": []})
        rows[NATIVE_ROW_IDS.index("native-overlay")].update(
            status="PASS", evidence_scope="native-programmatic", artifacts=["a"], sample_entry="overlay", blocker=None)
        data = {
            "schema_version": 1, "aggregate_status": "BLOCKED",
            "artifacts": [{"id": "a", "path": "native-samples/s.json", "provenance": "native-runner-output-sanitized",
                           "sha256": hashlib.sha256(text.encode()).hexdigest()}],
            "rows": rows,
        }
        if mutate:
            mutate(data, ev)
        (ev / "native-hardware.json").write_text(json.dumps(data), encoding="utf-8")
        return ev

    def _problems(self, mutate=None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            return check_native_hardware(self._fixture(tmp, mutate))

    def _has(self, needle: str, mutate) -> bool:
        return any(needle in p for p in self._problems(mutate))

    @staticmethod
    def _row(d, rid):
        return d["rows"][NATIVE_ROW_IDS.index(rid)]

    def test_clean_fixture_passes(self):
        self.assertEqual(self._problems(), [])

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_native_hardware(Path(tmp)), ["missing native-hardware.json"])

    def test_absent_sample(self):
        self.assertTrue(self._has("missing file", lambda d, e: (e / "native-samples" / "s.json").unlink()))

    def test_tampered_hash(self):
        self.assertTrue(self._has("digest mismatch", lambda d, e: (e / "native-samples" / "s.json").write_text("{}", encoding="utf-8")))

    def test_malformed_schema_and_status(self):
        self.assertTrue(self._has("schema_version", lambda d, e: d.update(schema_version=2)))
        self.assertTrue(self._has("invalid status", lambda d, e: self._row(d, "idle-cpu").update(status="DONE")))
        self.assertTrue(self._has("invalid evidence scope", lambda d, e: self._row(d, "idle-cpu").update(evidence_scope="magic")))
        self.assertTrue(self._has("malformed", lambda d, e: d.pop("rows")))

    def test_incomplete_or_unknown_inventory(self):
        self.assertTrue(self._has("missing row", lambda d, e: d["rows"].pop()))
        self.assertTrue(self._has("unknown row", lambda d, e: self._row(d, "idle-cpu").update(id="bogus")))

    def test_traversal_and_absolute_paths(self):
        for bad in ("../x.json", "/etc/passwd", "C:\\x.json", "native-samples/../../x.json", "a\\b.json"):
            self.assertTrue(self._has("safe relative path", lambda d, e, bad=bad: d["artifacts"][0].update(path=bad)), bad)

    def test_pass_without_native_proof(self):
        self.assertTrue(self._has("PASS without native proof", lambda d, e: self._row(d, "native-overlay").update(artifacts=[])))
        self.assertTrue(self._has("PASS without native proof", lambda d, e: self._row(d, "idle-cpu").update(status="PASS", blocker=None)))

    def test_pass_with_missing_human_evidence(self):
        self.assertTrue(self._has("PASS without human", lambda d, e: self._row(d, "native-overlay").update(requires=["native", "human"])))

    def test_synthetic_or_fixture_as_native(self):
        for prov in ("synthetic", "fixture", "transcribed"):
            self.assertTrue(self._has("PASS without native proof", lambda d, e, prov=prov: d["artifacts"][0].update(provenance=prov)), prov)

    def test_sample_entry_must_be_clean_pass(self):
        def m(d, e):
            sample = {"runs": [{"entry": "overlay", "status": "PASS", "exit_code": 1, "remaining_count": 0, "error": None}]}
            text = json.dumps(sample)
            (e / "native-samples" / "s.json").write_text(text, encoding="utf-8")
            d["artifacts"][0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        self.assertTrue(self._has("not a clean PASS", m))
        self.assertTrue(self._has("no entry", lambda d, e: self._row(d, "native-overlay").update(sample_entry="nope")))

    def test_unsupported_aggregate_pass(self):
        self.assertTrue(self._has("aggregate_status", lambda d, e: d.update(aggregate_status="PASS")))
        self.assertTrue(self._has("aggregate_status", lambda d, e: [r.update(status="PASS") for r in d["rows"]]))

    def test_non_pass_needs_blocker(self):
        self.assertTrue(self._has("explicit blocker", lambda d, e: self._row(d, "idle-cpu").update(blocker="")))

    def test_numeric_bounds_and_history_provenance(self):
        hist = {"provenance": "narrative-unverified", "claim": "x", "numeric": {"cpu_percent": [250]}}
        self.assertTrue(self._has("out of bounds", lambda d, e: self._row(d, "idle-cpu").update(history=[hist])))
        self.assertTrue(self._has("narrative-unverified", lambda d, e: self._row(d, "idle-cpu").update(history=[{"provenance": "native", "claim": "x"}])))

    def test_unsanitized_sample(self):
        def m(d, e):
            text = '{"runs": [], "p": "/home/user/x"}'
            (e / "native-samples" / "s.json").write_text(text, encoding="utf-8")
            d["artifacts"][0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        self.assertTrue(self._has("not sanitized", m))


class CrossSurfaceNegativeCases(unittest.TestCase):
    def _fixture(self, tmp: str, mutate=None) -> Path:
        root = Path(tmp)
        ev = root / "phase-4-evidence"
        shutil.copytree(ENGINEERING / "phase-4-evidence", ev, ignore=shutil.ignore_patterns("*.png", "acceptance-*"))
        for name in ("phase-4-report.md", "phase-4-windows-acceptance.md"):
            shutil.copy(ENGINEERING / name, root / name)
        if mutate:
            mutate(root)
        return root

    def _has(self, needle: str, mutate=None) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            return any(needle in p for p in check_cross_surface(self._fixture(tmp, mutate)))

    @staticmethod
    def _sub(name: str, old: str, new: str, count: int = 1):
        def m(root: Path):
            f = root / name
            text = f.read_text(encoding="utf-8")
            assert old in text, old
            f.write_text(text.replace(old, new, count), encoding="utf-8")
        return m

    def test_clean_copy_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check_cross_surface(self._fixture(tmp)), [])

    def test_contradictory_status_line(self):
        self.assertTrue(self._has("windows aggregate", self._sub("phase-4-windows-acceptance.md", f"Current aggregate status: {_manifest_aggregate()}", f"Current aggregate status: {_other(_manifest_aggregate())}")))
        self.assertTrue(self._has("expected exactly one", lambda r: (r / "phase-4-windows-acceptance.md").write_text(
            (r / "phase-4-windows-acceptance.md").read_text() + f"\n**Current aggregate status: {_other(_manifest_aggregate())}**\n")))
        self.assertTrue(self._has("report final status", self._sub("phase-4-report.md", f"PHASE 4 STATUS: {_manifest_aggregate()}", f"PHASE 4 STATUS: {_other(_manifest_aggregate())}")))
        self.assertTrue(self._has("stale 'Overall status'", lambda r: (r / "phase-4-windows-acceptance.md").write_text(
            (r / "phase-4-windows-acceptance.md").read_text() + "\n**Overall status: PASS**\n")))

    def test_missing_capability(self):
        def drop(root: Path):
            f = root / "phase-4-windows-acceptance.md"
            f.write_text("\n".join(l for l in f.read_text().splitlines() if not l.startswith("| `idle-ram`")), encoding="utf-8")
        self.assertTrue(self._has("windows: missing row idle-ram", drop))
        def drop_report(root: Path):
            f = root / "phase-4-report.md"
            f.write_text("\n".join(l for l in f.read_text().splitlines() if "`idle-ram`" not in l), encoding="utf-8")
        self.assertTrue(self._has("idle-ram not in acceptance matrix", drop_report))

        def drop_index(root: Path):
            f = root / "phase-4-evidence" / "index.json"
            d = json.loads(f.read_text())
            d["native_hardware"]["rows"] = [r for r in d["native_hardware"]["rows"] if r["id"] != "idle-ram"]
            f.write_text(json.dumps(d))
        self.assertTrue(self._has("index.json: missing row idle-ram", drop_index))

    def test_stale_unsupported_native_success(self):
        self.assertTrue(self._has("disagrees with manifest", self._sub("phase-4-report.md", "| Native idle RAM < 75 MB | PASS |", "| Native idle RAM < 75 MB | BLOCKED |")))
        self.assertTrue(self._has("windows: row microphone-envelope disagrees", self._sub("phase-4-windows-acceptance.md", "| `microphone-envelope` | 2 | PASS", "| `microphone-envelope` | 2 | BLOCKED")))

    def test_missing_evidence_link(self):
        self.assertTrue(self._has("missing evidence link", self._sub("phase-4-report.md", "[sample](phase-4-evidence/native-samples/native-smoke-sanitized.json)", "sample", 2)))
        self.assertTrue(self._has("missing evidence link", self._sub("phase-4-windows-acceptance.md", "phase-4-evidence/native-samples/native-smoke-sanitized.json)", "x)", 3)))

        def bad_artifact(root: Path):
            f = root / "phase-4-evidence" / "index.json"
            d = json.loads(f.read_text())
            for r in d["native_hardware"]["rows"]:
                if r["id"] == "native-overlay":
                    r["artifacts"] = ["native-samples/nope.json"]
            f.write_text(json.dumps(d))
        self.assertTrue(self._has("artifact missing or unsafe", bad_artifact))

    def test_machine_and_human_readable_disagree(self):
        def flip(root: Path):
            f = root / "phase-4-evidence" / "index.json"
            d = json.loads(f.read_text())
            d["native_hardware"]["aggregate_status"] = _other(d["native_hardware"]["aggregate_status"])
            f.write_text(json.dumps(d))
        self.assertTrue(self._has("index.json aggregate", flip))
        def flip_row(root: Path):
            f = root / "phase-4-evidence" / "index.json"
            d = json.loads(f.read_text())
            d["native_hardware"]["rows"][0]["status"] = _other(d["native_hardware"]["rows"][0]["status"])
            f.write_text(json.dumps(d))
        self.assertTrue(self._has("disagrees with manifest", flip_row))

    def _index_edit(self, edit):
        def m(root: Path):
            f = root / "phase-4-evidence" / "index.json"
            d = json.loads(f.read_text())
            edit(d)
            f.write_text(json.dumps(d))
        return m

    def test_missing_or_malformed_native_section(self):
        self.assertTrue(self._has("missing native_hardware section", self._index_edit(lambda d: d.pop("native_hardware"))))
        self.assertTrue(self._has("missing native_hardware section", self._index_edit(lambda d: d.update(native_hardware=[]))))
        self.assertTrue(self._has("must link native-hardware.json", self._index_edit(lambda d: d["native_hardware"].pop("manifest"))))
        self.assertTrue(self._has("rows must be a list", self._index_edit(lambda d: d["native_hardware"].update(rows=5))))
        self.assertTrue(self._has("malformed native_hardware row", self._index_edit(lambda d: d["native_hardware"]["rows"].append("x"))))
        self.assertTrue(self._has("artifacts must be a list", self._index_edit(lambda d: d["native_hardware"]["rows"][0].update(artifacts=7))))

    def test_duplicate_and_unknown_native_rows(self):
        self.assertTrue(self._has("duplicate row", self._index_edit(lambda d: d["native_hardware"]["rows"].append(dict(d["native_hardware"]["rows"][0])))))
        self.assertTrue(self._has("unknown row", self._index_edit(lambda d: d["native_hardware"]["rows"].append(
            {"id": "ghost", "status": "BLOCKED", "evidence_scope": "none", "artifacts": []}))))

    def test_native_evidence_scope_and_artifact_disagreement(self):
        self.assertTrue(self._has("row native-overlay disagrees", self._index_edit(lambda d: [
            r.update(evidence_scope="fixture") for r in d["native_hardware"]["rows"] if r["id"] == "native-overlay"])))
        self.assertTrue(self._has("native-overlay artifacts disagree", self._index_edit(lambda d: [
            r.update(artifacts=[]) for r in d["native_hardware"]["rows"] if r["id"] == "native-overlay"])))

    def test_fixture_native_scope_conflation(self):
        self.assertTrue(self._has("no fixture/native conflation", self._sub("phase-4-report.md", "native-programmatic: `native-overlay`", "fixture: `native-overlay`")))
        self.assertTrue(self._has("does not cite a manifest id", self._sub("phase-4-report.md", "native-programmatic: `startup-two-clap`; ", "fixture only; ")))

    def test_history_must_be_labelled_and_separated(self):
        self.assertTrue(self._has("missing '## Historical'", self._sub("phase-4-windows-acceptance.md", "## Historical probe", "## Probe")))
        self.assertTrue(self._has("temporary path", lambda r: (r / "phase-4-windows-acceptance.md").write_text(
            (r / "phase-4-windows-acceptance.md").read_text() + "\nsee /tmp/probe.py\n")))


if __name__ == "__main__":
    unittest.main()
