"""Real Windows smoke run of the desktop tools through the ToolGateway.

Read-only tools run against the live desktop; file tools run in a temporary
authorized scope; a permanent delete is attempted with a confirmer that
declines, proving HIGH risk is blocked. Nothing outside the temp folder is
modified (no volume, focus or window changes). Prints JSON.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.desktop import DesktopContext, build_desktop_tools
from jarvis.adapters.tools.gateway import AuditLog, ToolGateway
from jarvis.core.errors import ToolPermissionDenied
from jarvis.core.turn import TurnContext


async def main() -> dict:
    asked: list[str] = []

    async def decline(name, arguments, context):
        asked.append(name)
        return False

    report: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        scope = Path(tmp) / "project"
        scope.mkdir()
        audit = Path(tmp) / "audit.jsonl"
        gateway = ToolGateway([], confirmer=decline, authorized_scopes=[scope], audit=AuditLog(audit))
        memory = DesktopContext(None)
        for tool in build_desktop_tools(scopes=gateway.scopes, data_dir=Path(tmp) / "data", memory=memory, cancel_operations=gateway.cancel_operations):
            gateway.register(tool)

        async def call(name, args=None):
            started = time.perf_counter()
            try:
                result = await gateway.execute(name, args or {}, TurnContext.fresh("smoke"))
                report[name] = {"ok": getattr(result, "ok", True), "say": str(result)[:220], "ms": round((time.perf_counter() - started) * 1000)}
            except ToolPermissionDenied as error:
                report[name] = {"denied": str(error)}
            return report[name]

        for name in ("system_stats", "gpu_processes", "windows_list"):
            await call(name)
        await call("process_list", {"sort": "memory", "limit": 5})
        await call("file_write", {"path": str(scope / "notes.txt"), "content": "hola"})
        await call("file_search", {"pattern": "notes", "root": str(scope)})
        await call("file_read", {"path": str(scope / "notes.txt")})
        await call("run_command", {"command": ["git", "--version"], "cwd": str(scope)})
        await call("file_delete", {"path": str(scope / "notes.txt"), "permanent": True})
        report["file_still_exists_after_declined_delete"] = (scope / "notes.txt").exists()
        report["confirmations_requested"] = asked
        report["audit_lines"] = len(audit.read_text(encoding="utf-8").splitlines())
        report["context_summary_keys"] = sorted(memory.summary())
        report["recent_vscode_folders"] = memory.summary()["recent_vscode_folders"]
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), ensure_ascii=False, indent=1))
