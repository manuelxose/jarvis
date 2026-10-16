"""Promote Phase 4 hardware rows from retained evidence and refresh every published surface.

    python docs/engineering/evidence/hardware-run-scripts/build_manifest.py

A row is promoted to PASS only when its retained sample says PASS (native rows) and, for human rows, a
human-observation record exists. The manifest, index.json, the Windows acceptance table and the report matrix are
rewritten from the manifest so they cannot disagree; tests/test_phase4_docs.py is the gate.
"""
import hashlib, json, re
from pathlib import Path

ENG = Path(__file__).resolve().parents[2]
EV = ENG / "phase-4-evidence"
MAN = EV / "native-hardware.json"

NATIVE = ["runtime-source-resolution", "microphone-envelope", "playback-envelope", "cancellation-envelope", "disconnect-recovery",
          "reconnect-no-replay", "startup-two-clap", "startup-assets", "stale-ordering-native", "idle-cpu", "idle-ram",
          "reduced-motion-native", "av-sync-human"]
HUMAN = ["audible-playback-human", "av-sync-human"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sample_ok(path, entry):
    runs = {r["entry"]: r for r in json.loads(path.read_text(encoding="utf-8"))["runs"]}
    r = runs.get(entry)
    return bool(r) and r["status"] == "PASS" and r["exit_code"] == 0 and not r.get("remaining_count") and not r.get("error")


def main():
    m = json.loads(MAN.read_text(encoding="utf-8"))
    arts = {a["id"]: a for a in m["artifacts"]}
    rows = {r["id"]: r for r in m["rows"]}
    for rid in NATIVE:
        p = EV / "native-samples" / f"{rid}.json"
        if p.is_file() and sample_ok(p, rid):
            arts[f"{rid}-sample"] = {"id": f"{rid}-sample", "path": f"native-samples/{rid}.json", "sha256": sha(p), "provenance": "native-runner-output-sanitized",
                                     "description": "Sanitized output of hardware-run-scripts/native_rows.py (or av_sync_live.py) on Windows: counts, booleans, numbers and state names only."}
    for rid in HUMAN:
        p = EV / "human-records" / f"{rid}.json"
        if p.is_file():
            arts[f"{rid}-record"] = {"id": f"{rid}-record", "path": f"human-records/{rid}.json", "sha256": sha(p), "provenance": "human-observation-record",
                                     "description": "Owner observation collected in the working session; quotes kept verbatim."}
    for rid in NATIVE:
        r = rows[rid]
        if f"{rid}-sample" not in arts:
            continue
        linked = [f"{rid}-sample"]
        if "human" in r["requires"]:
            if f"{rid}-record" not in arts:
                continue
            linked.append(f"{rid}-record")
        r.update(status="PASS", evidence_scope="native-programmatic", artifacts=linked, sample_entry=rid)
        r.pop("blocker", None)
    r = rows["audible-playback-human"]
    if "audible-playback-human-record" in arts:
        r.update(status="PASS", evidence_scope="human-observation", artifacts=["audible-playback-human-record"])
        r.pop("blocker", None)
    m["artifacts"] = list(arts.values())
    statuses = {x["status"] for x in m["rows"]}
    m["aggregate_status"] = "FAIL" if "FAIL" in statuses else ("PASS" if statuses == {"PASS"} else "BLOCKED")
    blocked = [x["id"] for x in m["rows"] if x["status"] != "PASS"]
    m["aggregate_rationale"] = ("Every row has a retained, hash-verified native sample and, where required, a human-observation record." if not blocked
                                else "Rows without durable evidence: " + ", ".join(blocked) + ".")
    MAN.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    idx = json.loads((EV / "index.json").read_text(encoding="utf-8"))
    paths = {a["id"]: a["path"] for a in m["artifacts"]}
    idx["native_hardware"]["aggregate_status"] = m["aggregate_status"]
    idx["native_hardware"]["rows"] = [{"id": x["id"], "status": x["status"], "evidence_scope": x["evidence_scope"], "artifacts": [paths[a] for a in x["artifacts"]]} for x in m["rows"]]
    (EV / "index.json").write_text(json.dumps(idx, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Windows acceptance table
    win = ENG / "phase-4-windows-acceptance.md"
    text = win.read_text(encoding="utf-8")
    text = re.sub(r"^\*\*Current aggregate status: \w+\*\*", f"**Current aggregate status: {m['aggregate_status']}**", text, flags=re.M)
    for x in m["rows"]:
        if x["status"] == "PASS":
            ev = "; ".join(f"[{a}](phase-4-evidence/{paths[a]})" for a in x["artifacts"])
        else:
            ev = x["blocker"]
        req = "+".join(x["requires"])
        pat = re.compile(r"^\| `%s` \| ([^|]*) \|.*$" % re.escape(x["id"]), re.M)
        mm = pat.search(text)
        assert mm, x["id"]
        text = text.replace(mm.group(0), f"| `{x['id']}` | {mm.group(1).strip()} | {x['status']} | {x['evidence_scope']} | {req} | {ev} |", 1)
    win.write_text(text, encoding="utf-8")

    # Report matrix: one row per scope, never mixing native and human evidence
    rep = ENG / "phase-4-report.md"
    text = rep.read_text(encoding="utf-8")
    R = {x["id"]: x for x in m["rows"]}
    def cell(label, ids, note):
        scope = {R[i]["evidence_scope"] for i in ids}
        assert len(scope) == 1, (label, scope)
        st = "PASS" if all(R[i]["status"] == "PASS" for i in ids) else "BLOCKED"
        links = " ".join(f"[sample](phase-4-evidence/{paths[a]})" for i in ids for a in R[i]["artifacts"])
        return f"| {label} | {st} | {scope.pop()}: " + ", ".join(f"`{i}`" for i in ids) + f"; {note} {links} |"
    spec = [
        ("Windows runtime source resolution", ["runtime-source-resolution"], "worktree source imported under Windows Python"),
        ("Real microphone envelope", ["microphone-envelope"], "real input device through `CoreSignals.microphone`"),
        ("Real playback envelope, start and end", ["playback-envelope"], "real speech through the production output path"),
        ("Human-heard playback", ["audible-playback-human"], "owner observation, see record"),
        ("Native cancellation and stop", ["cancellation-envelope"], "cancel at flush, zero output after"),
        ("Native disconnected recovery", ["disconnect-recovery"], "Core killed and relaunched under a live window"),
        ("Native reconnect without startup replay", ["reconnect-no-replay"], "one snapshot per connection, same session"),
        ("Two-clap startup entry on hardware", ["startup-two-clap"], "real claps, phases to ready"),
        ("User-configured startup preservation", ["startup-assets"], "owner's configured music, cached welcome and apps"),
        ("Native Overlay and Command Center windows", ["native-overlay", "native-command-center"], "a hardware check only, not audible or A/V acceptance"),
        ("Native stale and out-of-order telemetry", ["stale-ordering-native"], "stale and foreign-session events ignored by the live window"),
        ("Native idle CPU < 2 %", ["idle-cpu"], "60 s window, Electron tree over logical cores"),
        ("Native idle RAM < 75 MB", ["idle-ram"], "private bytes of the Electron tree"),
        ("Native reduced motion", ["reduced-motion-native"], "zero rAF callbacks, data-motion reduced"),
        ("Native audio/visual follow", ["av-sync-human"], "live window followed real playback; see the human record"),
    ]
    start = text.index("| Windows runtime source resolution |")
    end = text.index("\n\n", start) + 1  # the matrix ends at the first blank line
    new = "\n".join(cell(*s) for s in spec) + "\n"
    text = text[:start] + new + text[end:]
    text = re.sub(r"hardware acceptance is \w+", f"hardware acceptance is {m['aggregate_status']}", text)
    text = re.sub(r"^PHASE 4 STATUS: \w+\s*$", f"PHASE 4 STATUS: {m['aggregate_status']}", text.rstrip() + "\n", flags=re.M)
    rep.write_text(text, encoding="utf-8")
    print("aggregate", m["aggregate_status"], "| non-PASS:", blocked)


if __name__ == "__main__":
    main()
