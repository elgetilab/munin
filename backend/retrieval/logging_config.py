"""
Structured logging for the Munin retrieval service.

Before this module existed, every diagnostic was ``print(f"[WARNING] ...")``
straight to stdout: no correlation id, no log level, no structured fields,
no way to filter. Closing P1 #6 from the 2026-05 internal harness audit.

Shape: one JSON object per line on stdout, suitable for journald / Loki /
``jq``. Each line carries the standard fields (``ts``, ``level``,
``logger``, ``msg``) plus whatever request-context fields are bound in
ContextVars at the moment the log call fires (``user_email``,
``conversation_id``, ``persona``). The filter pulls those automatically
so call sites don't need to thread anything through.

Usage:

    from logging_config import configure_logging
    import logging

    configure_logging()                           # once, at process start
    logger = logging.getLogger(__name__)          # per module

    logger.info("hello")                          # JSON line to stdout
    logger.warning("retry %d/%d: %s", n, m, e)    # lazy-formatted
    logger.exception("kaboom")                    # captures traceback

Log level: env ``LOG_LEVEL`` (default ``INFO``). The previous
``print(f"[ERROR] ...")`` / ``[WARNING]`` / ``[INFO]`` prefixes map onto
``logger.error`` / ``warning`` / ``info`` respectively; bare ``print``
calls without a prefix are info-level.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from mcp.context import (
    current_conversation_id,
    current_persona,
    current_user_email,
)

# Standard LogRecord attributes — we strip these out when serialising the
# `extra={...}` payload so callers that pass arbitrary fields don't have
# them silently overwritten.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",
        # Filter-injected fields handled explicitly below.
        "user_email", "conversation_id", "persona",
    }
)


class RequestContextFilter(logging.Filter):
    """Stamp each LogRecord with whatever request-scoped ContextVars are
    set. Missing vars are simply not attached (avoids spamming ``null``
    fields when a log line fires outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.user_email = current_user_email.get()
        record.conversation_id = current_conversation_id.get()
        record.persona = current_persona.get()
        return True


class JsonFormatter(logging.Formatter):
    """Serialise each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # ContextVar fields land via the filter; only include them when
        # they're actually set.
        for f in ("user_email", "conversation_id", "persona"):
            v = getattr(record, f, None)
            if v is not None:
                obj[f] = v
        # Anything else passed via ``extra={...}`` rides along.
        for k, v in record.__dict__.items():
            if k in _RESERVED or k in obj:
                continue
            try:
                json.dumps(v)
                obj[k] = v
            except (TypeError, ValueError):
                obj[k] = repr(v)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


_CONFIGURED = False


def configure_logging() -> None:
    """Install the JSON handler + ContextVar filter on the root logger.

    Idempotent — safe to call from main.py startup, test setup, or both.
    Honours ``LOG_LEVEL`` env (default INFO).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any default handlers (uvicorn installs one before we get a
    # chance) so our JSON format wins on every line.
    root.handlers = [handler]
    _CONFIGURED = True
