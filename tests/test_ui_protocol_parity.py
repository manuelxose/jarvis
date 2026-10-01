"""Guards the UI protocol contract and the Phase 1 frontend docs.

``ui/src/protocol/events.ts`` claims to mirror ``event_hub.SCHEMAS`` field for
field; this test makes that claim executable so SCHEMAS drift fails the suite.
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jarvis.observability.event_hub import SCHEMAS  # noqa: E402

EVENTS_TS = REPO_ROOT / "ui" / "src" / "protocol" / "events.ts"

NAME_RE = re.compile(r"\bname:\s*'([a-z_]+\.[a-z_]+)'")

PHASE1_DOCS = (
    "docs/engineering/jarvis-ui-phase-1-audit.md",
    "docs/engineering/frontend-skills.md",
    "docs/architecture/frontend-architecture.md",
    "docs/architecture/desktop-shell-adr.md",
    "docs/frontend/frontend-quality-gates.md",
    "docs/frontend/future-capabilities.md",
)


class UiProtocolParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(EVENTS_TS.is_file(), f"missing {EVENTS_TS}")
        self.ts = EVENTS_TS.read_text(encoding="utf-8")

    def test_event_names_match_schemas(self) -> None:
        ts_names = set(NAME_RE.findall(self.ts))
        py_names = set(SCHEMAS)
        self.assertFalse(py_names - ts_names, f"events in SCHEMAS but missing from events.ts: {sorted(py_names - ts_names)}")
        self.assertFalse(ts_names - py_names, f"events in events.ts but missing from SCHEMAS: {sorted(ts_names - py_names)}")

    def test_payload_fields_present_in_ts(self) -> None:
        missing = [
            f"{name}.{field}"
            for name, schema in SCHEMAS.items()
            for field in schema
            if not re.search(rf"\b{re.escape(field)}\??:", self.ts)
        ]
        self.assertFalse(missing, f"SCHEMAS payload fields missing from events.ts: {missing}")

    def test_fields_declared_inside_their_own_event(self) -> None:
        # Stricter than a whole-file search: a field must sit in its event's interface block.
        blocks = re.split(r"(?=export interface )", self.ts)
        by_name = {m.group(1): b for b in blocks if (m := NAME_RE.search(b))}
        missing = [
            f"{name}.{field}"
            for name, schema in SCHEMAS.items()
            for field in schema
            if not re.search(rf"\b{re.escape(field)}\??:", by_name.get(name, ""))
        ]
        self.assertFalse(missing, f"fields not declared in their event interface: {missing}")


class Phase1DocsTests(unittest.TestCase):
    def test_phase1_docs_exist_and_are_non_empty(self) -> None:
        for rel in PHASE1_DOCS:
            path = REPO_ROOT / rel
            with self.subTest(doc=rel):
                self.assertTrue(path.is_file(), f"missing doc: {rel}")
                self.assertTrue(path.read_text(encoding="utf-8").strip(), f"empty doc: {rel}")

    def test_audit_requires_owner_approval_for_s02(self) -> None:
        text = (REPO_ROOT / PHASE1_DOCS[0]).read_text(encoding="utf-8").lower()
        self.assertIn("explicit owner approval", text)


if __name__ == "__main__":
    unittest.main()
