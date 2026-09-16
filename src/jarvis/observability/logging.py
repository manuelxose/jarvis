"""JSON logging with recursive secret redaction."""

from __future__ import annotations

import json
import logging
import sys
import re
from copy import copy
from collections.abc import Mapping, Sequence
from typing import Any, TextIO


_REDACTED = "<redacted>"
_SECRET_KEYS = frozenset({"api_key", "api-key", "fast_model_api_key", "authorization", "token", "password"})
# Named values in formatted messages (including HTTP authorization schemes).
_SECRET_TEXT = re.compile(
    r"(?i)(\b(?:fast_model_api_key|api[_-]key|authorization|token|password)"
    r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|(?:Bearer|Basic)\s+[^\s,;]+|[^\s,;]+)"
)
_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if str(key).lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_redact(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Keep caller-owned dictionaries and records intact. Redact structured
        # inputs before getMessage() turns them into unstructured strings.
        record = copy(record)
        record.msg = _redact(record.msg)
        record.args = _redact(record.args)
        data = {
            "level": record.levelname,
            "logger": record.name,
            "message": _SECRET_TEXT.sub(lambda match: match[1] + _REDACTED, record.getMessage()),
            **{key: value for key, value in record.__dict__.items() if key not in _LOG_RECORD_FIELDS},
        }
        return json.dumps(_redact(data), sort_keys=True)


def configure_logging(
    logger: logging.Logger | None = None, *, stream: TextIO | None = None
) -> logging.Logger:
    """Configure a logger to emit one redacted JSON record per line."""
    logger = logger or logging.getLogger("jarvis")
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
