"""Structured stdio JSON-lines protocol between Jarvis and the Hermes child.

Every message carries ``request_id``, ``turn_id``, ``event_type``, ``payload``,
and a ``timestamp`` so events are traceable and never parsed from arbitrary
terminal text.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Outbound event types (child -> Jarvis).
STARTED = "started"
THINKING = "thinking"
TOOL_STARTED = "tool_started"
TOOL_COMPLETED = "tool_completed"
PARTIAL_RESPONSE = "partial_response"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

# Inbound request/control types (Jarvis -> child).
REQUEST = "request"
CANCEL = "cancel"


@dataclass(frozen=True)
class HermesMessage:
    request_id: str
    turn_id: str
    event_type: str
    payload: Mapping[str, Any]
    timestamp: float


def new_request_id() -> str:
    return uuid.uuid4().hex


def encode_message(
    request_id: str,
    turn_id: str,
    event_type: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "turn_id": turn_id,
            "event_type": event_type,
            "payload": payload or {},
            "timestamp": time.time(),
        },
        separators=(",", ":"),
    )


def parse_message(line: str) -> Optional[HermesMessage]:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return HermesMessage(
        request_id=str(obj.get("request_id", "")),
        turn_id=str(obj.get("turn_id", "")),
        event_type=str(obj.get("event_type", "")),
        payload=obj.get("payload") if isinstance(obj.get("payload"), dict) else {},
        timestamp=float(obj.get("timestamp", time.time())),
    )
