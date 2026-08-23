"""Structured JSON logging with request correlation kept out of metrics."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "smoke_correlation_id", default=None
)


@contextlib.contextmanager
def correlation_id(value: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for logs emitted in the current execution context."""

    bound = value or uuid.uuid4().hex
    token = _correlation.set(bound)
    try:
        yield bound
    finally:
        _correlation.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Emit one stable JSON object per record without raw payloads."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        bound = _correlation.get()
        if bound:
            payload["correlation_id"] = bound
        for key in ("component", "camera_id", "operation", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Return a logger configured for JSON output once per logger name."""

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


__all__ = ["JsonLogFormatter", "correlation_id", "get_logger"]
