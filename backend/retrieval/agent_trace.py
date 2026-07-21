"""
Agent trace spine (append-only JSONL).

Design Principle 6 ("everything emits a trace"): every specialised-agent call
(source / search / compute / deep_research) records ONE structured record of what
it did - resolved sources, LLM calls made, decisions taken, and the abstain or
failure reason. This is the observability substrate the diagnosis found missing:
today the flat tool loop is opaque and intent must be reconstructed from a 5-30
primitive-call sequence. The trace makes an agent call a single legible unit, and
the eval reads it (e.g. the confabulated-DOI audit becomes a grep/query).

Storage is append-only JSONL under `AGENT_TRACE_DIR/<date>.jsonl`, one record per
line. JSONL wins the crash case on legibility: a process dying mid-append leaves a
partial trailing line, which `read_traces` drops and continues. No DuckDB yet - a
grep / `json` read is enough for the first eval read; a DuckDB read-layer is added
only if aggregate SQL is needed later (plan D5).

This is distinct from the SSE progress events emitted via
`mcp.context.current_sse_emitter` (those are for the live UI; these persist for the
eval). Traces are privileged-read (they contain the query, which is competitively
sensitive) - serve them to eval/admin only.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

AGENT_TRACE_DIR = os.getenv("AGENT_TRACE_DIR", "/data/agent_traces")

_write_lock = threading.Lock()


def _request_context() -> dict:
    """Read request-scoped identity for the trace, defensively.

    Imported lazily so the trace spine stays decoupled from the MCP package init
    (and remains testable in isolation). Missing context is not an error - a
    trace written outside a request (e.g. a benchmark) simply has null fields.
    """
    try:
        from mcp.context import (
            current_conversation_id,
            current_persona,
            current_user_email,
        )

        return {
            "conversation_id": current_conversation_id.get(),
            "user": current_user_email.get(),
            "persona": current_persona.get(),
        }
    except Exception:
        return {"conversation_id": None, "user": None, "persona": None}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _path_for(date: str) -> str:
    return os.path.join(AGENT_TRACE_DIR, f"{date}.jsonl")


def emit_trace(record: dict) -> str:
    """Append one trace record as a JSONL line; return its trace_id.

    Stamps `ts` and a `trace_id` if absent, and fills request-scoped context
    (`conversation_id`, `user`, `persona`) when not already set on the record.
    Never raises into the caller: a trace-write failure must not fail an agent
    call the user already paid for - it is logged-and-swallowed.
    """
    rec = dict(record)
    rec.setdefault("trace_id", uuid.uuid4().hex)
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    for k, v in _request_context().items():
        rec.setdefault(k, v)

    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    try:
        with _write_lock:
            os.makedirs(AGENT_TRACE_DIR, exist_ok=True)
            with open(_path_for(_today()), "a", encoding="utf-8") as f:
                f.write(line)
    except OSError as exc:  # disk full, perms, mount missing - never fatal
        import logging

        logging.getLogger(__name__).warning("agent trace write failed: %s", exc)
    return rec["trace_id"]


def read_traces(date: Optional[str] = None,
                where: Optional[dict] = None) -> list[dict]:
    """Read one day's trace records (default: today).

    Skips blank and partial trailing lines (crash tolerance). `where` is an
    optional exact-match filter, e.g. `{"agent": "source", "outcome": "not_found"}`.
    """
    path = _path_for(date or _today())
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # partial trailing line from a crash mid-append
                if where and any(rec.get(k) != v for k, v in where.items()):
                    continue
                out.append(rec)
    except FileNotFoundError:
        return []
    return out


class AgentTrace:
    """Accumulator an agent builds over one call, then flushes with `finish`.

    Usage (in a fat-tool agent)::

        tr = AgentTrace("source", mode="qa", question=question)
        tr.source(ref_resolved)          # each resolved document
        tr.llm()                         # each LLM call made
        tr.decide("chose full-text read", doc=doc_id)
        ...
        envelope["trace"] = tr.finish(outcome="resolved")

    `finish` writes the full record to JSONL and returns a COMPACT dict suitable
    for the caller-facing envelope's `trace` field (the full detail lives in the
    JSONL, not the model's context window).
    """

    def __init__(self, agent: str, *, mode: Optional[str] = None, **extra: Any):
        self.trace_id = uuid.uuid4().hex
        self.agent = agent
        self.mode = mode
        self.extra = extra
        self._t0 = time.time()
        self.sources: list[Any] = []
        self.n_llm_calls = 0
        self.decisions: list[dict] = []

    def source(self, ref: Any) -> None:
        self.sources.append(ref)

    def llm(self, n: int = 1) -> None:
        self.n_llm_calls += n

    def decide(self, message: str, **fields: Any) -> None:
        self.decisions.append({"msg": message, **fields})

    def finish(self, *, outcome: str, reason: Optional[str] = None,
               **fields: Any) -> dict:
        elapsed = round(time.time() - self._t0, 3)
        record = {
            "trace_id": self.trace_id,
            "agent": self.agent,
            "mode": self.mode,
            "outcome": outcome,
            "abstain_reason": reason,
            "resolved_sources": self.sources,
            "n_llm_calls": self.n_llm_calls,
            "decisions": self.decisions,
            "elapsed_s": elapsed,
            **self.extra,
            **fields,
        }
        emit_trace(record)
        # Compact envelope field - full detail stays in the JSONL, off the model's
        # context window.
        return {
            "trace_id": self.trace_id,
            "agent": self.agent,
            "mode": self.mode,
            "outcome": outcome,
            "n_llm_calls": self.n_llm_calls,
            "elapsed_s": elapsed,
        }
