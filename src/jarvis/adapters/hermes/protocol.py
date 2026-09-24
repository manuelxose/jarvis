"""Structured stdio JSON-lines protocol between Jarvis and the Hermes child.

Every message carries ``request_id``, ``turn_id``, ``event_type``, ``payload``,
and a ``timestamp`` so events are traceable and never parsed from arbitrary
terminal text.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Optional

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
    """One decoded JSON-lines message exchanged with the Hermes child."""
    request_id: str
    turn_id: str
    event_type: str
    payload: Mapping[str, Any]
    timestamp: float


def new_request_id() -> str:
    """Return a fresh unique request id."""
    return uuid.uuid4().hex


def encode_message(
    request_id: str,
    turn_id: str,
    event_type: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> str:
    """Serialize one protocol message as a single JSON line."""
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
    """Parse one JSON-lines record, ignoring malformed transport input."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None

    request_id = obj.get("request_id")
    turn_id = obj.get("turn_id")
    event_type = obj.get("event_type")
    payload = obj.get("payload")
    timestamp = obj.get("timestamp")
    if (
        not all(isinstance(value, str) and value for value in (request_id, turn_id, event_type))
        or not isinstance(payload, dict)
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        return None

    return HermesMessage(
        request_id=request_id,
        turn_id=turn_id,
        event_type=event_type,
        payload=payload,
        timestamp=float(timestamp),
    )


def parse_messages(lines: Iterable[str]) -> Iterator[HermesMessage]:
    """Yield valid Hermes events from a streaming JSON-lines source."""
    for line in lines:
        message = parse_message(line)
        if message is not None:
            yield message
