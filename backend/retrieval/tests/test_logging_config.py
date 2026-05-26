"""
Standalone tests for the structured-logging configuration
(logging_config.py, P1 #6 from munin-audit.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_logging_config.py
Or locally:
    python backend/retrieval/tests/test_logging_config.py

Captures each log line into an in-memory StringIO so we can assert on
the JSON shape without writing to real stdout. Resets the root logger
between cases so handler installation is hermetic.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.context import (  # noqa: E402
    current_conversation_id,
    current_persona,
    current_user_email,
)
from logging_config import JsonFormatter, RequestContextFilter  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _fresh_logger_with_capture(level: int = logging.INFO):
    """Install a fresh handler on a uniquely-named logger that writes JSON
    into a StringIO. Returns (logger, buffer). Hermetic: each test gets a
    distinct logger instance so handlers don't leak."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    h.addFilter(RequestContextFilter())
    name = f"test_logging_config.{id(buf)}"
    logger = logging.getLogger(name)
    logger.handlers = [h]
    logger.setLevel(level)
    logger.propagate = False  # don't bubble into other test loggers
    return logger, buf


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------

def test_emits_valid_json_per_line() -> bool:
    logger, buf = _fresh_logger_with_capture()
    logger.info("hello %s", "world")
    logger.warning("careful")
    records = _lines(buf)
    return _check(
        "every emitted line is valid JSON",
        len(records) == 2
        and records[0]["msg"] == "hello world"
        and records[1]["msg"] == "careful",
        f"records={records}",
    )


def test_standard_fields_present() -> bool:
    logger, buf = _fresh_logger_with_capture()
    logger.info("x")
    rec = _lines(buf)[0]
    return _check(
        "standard fields present: ts, level, logger, msg",
        all(k in rec for k in ("ts", "level", "logger", "msg"))
        and rec["level"] == "INFO",
    )


# ---------------------------------------------------------------------------
# ContextVar enrichment
# ---------------------------------------------------------------------------

def test_context_vars_attached_when_set() -> bool:
    logger, buf = _fresh_logger_with_capture()
    tok_email = current_user_email.set("alice@munin.test")
    tok_conv = current_conversation_id.set("conv-123")
    tok_persona = current_persona.set("code")
    try:
        logger.info("enriched")
    finally:
        current_user_email.reset(tok_email)
        current_conversation_id.reset(tok_conv)
        current_persona.reset(tok_persona)
    rec = _lines(buf)[0]
    return _check(
        "request-scoped ContextVars are attached to the record",
        rec.get("user_email") == "alice@munin.test"
        and rec.get("conversation_id") == "conv-123"
        and rec.get("persona") == "code",
        f"rec={rec}",
    )


def test_context_vars_absent_when_unset() -> bool:
    """When ContextVars are unset (e.g. log line outside a request),
    the keys must be absent from the JSON, not present with null."""
    logger, buf = _fresh_logger_with_capture()
    logger.info("unenriched")
    rec = _lines(buf)[0]
    return _check(
        "ContextVar keys absent (not null) when unset",
        "user_email" not in rec
        and "conversation_id" not in rec
        and "persona" not in rec,
        f"rec={rec}",
    )


# ---------------------------------------------------------------------------
# Exception capture
# ---------------------------------------------------------------------------

def test_exception_captures_traceback() -> bool:
    logger, buf = _fresh_logger_with_capture()
    try:
        raise ValueError("kaboom")
    except ValueError:
        logger.exception("something failed")
    rec = _lines(buf)[0]
    return _check(
        "logger.exception captures exc field with traceback",
        rec["level"] == "ERROR"
        and rec["msg"] == "something failed"
        and "exc" in rec
        and "ValueError" in rec["exc"]
        and "kaboom" in rec["exc"],
        f"rec={rec}",
    )


# ---------------------------------------------------------------------------
# Log level
# ---------------------------------------------------------------------------

def test_debug_suppressed_at_info_level() -> bool:
    logger, buf = _fresh_logger_with_capture(level=logging.INFO)
    logger.debug("invisible")
    logger.info("visible")
    return _check(
        "DEBUG suppressed when level is INFO",
        len(_lines(buf)) == 1 and _lines(buf)[0]["msg"] == "visible",
    )


def test_debug_visible_at_debug_level() -> bool:
    logger, buf = _fresh_logger_with_capture(level=logging.DEBUG)
    logger.debug("now visible")
    return _check(
        "DEBUG visible when level is DEBUG",
        len(_lines(buf)) == 1 and _lines(buf)[0]["level"] == "DEBUG",
    )


# ---------------------------------------------------------------------------
# Filter robustness
# ---------------------------------------------------------------------------

def test_filter_handles_non_string_context() -> bool:
    """If someone sticks a weird value in a ContextVar (e.g. None,
    an int), the filter must not crash. None stays absent, primitives
    serialise."""
    logger, buf = _fresh_logger_with_capture()
    tok = current_user_email.set(None)  # explicit None
    try:
        logger.info("a")
    finally:
        current_user_email.reset(tok)
    rec = _lines(buf)[0]
    return _check(
        "None ContextVar value treated as absent",
        "user_email" not in rec,
    )


def test_extra_fields_ride_along() -> bool:
    """Anything passed via ``extra={...}`` on a log call should appear
    in the JSON, as long as it serialises."""
    logger, buf = _fresh_logger_with_capture()
    logger.info("with extras", extra={"tool_name": "paper_search", "n_results": 5})
    rec = _lines(buf)[0]
    return _check(
        "extra={...} fields land in the JSON record",
        rec.get("tool_name") == "paper_search"
        and rec.get("n_results") == 5,
        f"rec={rec}",
    )


def test_extra_non_serialisable_falls_back_to_repr() -> bool:
    """A non-JSON-serialisable extra value should appear as repr() string
    instead of crashing the formatter."""
    logger, buf = _fresh_logger_with_capture()

    class Weird:
        def __repr__(self):
            return "<Weird>"

    logger.info("x", extra={"thing": Weird()})
    rec = _lines(buf)[0]
    return _check(
        "non-serialisable extras fall back to repr()",
        rec.get("thing") == "<Weird>",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_emits_valid_json_per_line,
    test_standard_fields_present,
    test_context_vars_attached_when_set,
    test_context_vars_absent_when_unset,
    test_exception_captures_traceback,
    test_debug_suppressed_at_info_level,
    test_debug_visible_at_debug_level,
    test_filter_handles_non_string_context,
    test_extra_fields_ride_along,
    test_extra_non_serialisable_falls_back_to_repr,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
