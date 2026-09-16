"""A small, safe supervised agent child for Hermes.

Runs a deterministic multi-step workflow over stdio JSON-lines: it inspects a
local fact (current date/time) and reports a short summary, streaming events so
the supervisor can observe progress. It performs no destructive actions.
"""

from __future__ import annotations

import datetime
import json
import sys
import time


def emit(request_id: str, turn_id: str, event_type: str, payload: dict | None = None) -> None:
    message = {
        "request_id": request_id,
        "turn_id": turn_id,
        "event_type": event_type,
        "payload": payload or {},
        "timestamp": time.time(),
    }
    print(json.dumps(message, separators=(",", ":")), flush=True)


def handle(request_id: str, turn_id: str, text: str) -> None:
    if text.strip() == "__CRASH__":
        sys.exit(1)
    emit(request_id, turn_id, "started", {})
    emit(request_id, turn_id, "thinking", {"detail": "planning a safe multi-step task"})
    emit(request_id, turn_id, "tool_started", {"name": "system_info", "arguments": {}})
    now = datetime.datetime.now().isoformat(timespec="seconds")
    emit(request_id, turn_id, "tool_completed", {"name": "system_info", "arguments": {}})
    for part in ("He inspeccionado el estado del sistema.", f"Ahora son las {now}.", "Tarea completada de forma segura."):
        emit(request_id, turn_id, "partial_response", {"text": part})
        time.sleep(0.005)
    emit(request_id, turn_id, "completed", {"detail": "done"})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = message.get("event_type")
        if event_type == "request":
            handle(
                str(message.get("request_id", "")),
                str(message.get("turn_id", "")),
                str((message.get("payload") or {}).get("text", "")),
            )
        elif event_type == "cancel":
            emit(
                str(message.get("request_id", "")),
                str(message.get("turn_id", "")),
                "cancelled",
                {},
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
