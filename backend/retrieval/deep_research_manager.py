"""
Deep Research manager: detached-task lifecycle, concurrency, and delivery.

Deep Research is a long-running (many-minute) in-process agent, so it cannot run
inside a chat turn. This manager owns its lifecycle (AGENT-IMPLEMENTATION-PLAN.md
D6): a request kicks off a DETACHED asyncio task (survives the client
disconnecting), progress accrues to a durable per-job record that a reconnect/poll
reads, the plan is checkpointed so a crash RESUMES rather than restarts, and the
final composite is delivered as a markdown ARTIFACT. Concurrency is a configurable
cap (default 1, D6c) enforced by a semaphore so the many-minute run does not
oversubscribe the 2-slot vLLM shared with chat.

This is the integration core. What still needs chat_service surgery (deploy-gated,
not verifiable from the host): streaming the job's progress over SSE while the
client is connected + stream_registry replay on reconnect, and the request-body
on/off toggle that calls `start_job`. Those bridge this manager's durable progress
record to the live SSE stream; the manager itself is loop-testable in isolation.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Optional

import deep_research_agent as _dr

MAX_CONCURRENT = int(os.getenv("DEEP_RESEARCH_MAX_CONCURRENT", "1"))

_sem: Optional[asyncio.Semaphore] = None
_jobs: dict[str, dict] = {}


def _get_sem() -> asyncio.Semaphore:
    # Lazily bound so it attaches to the running loop, not import-time.
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONCURRENT)
    return _sem


async def start_job(question: str, *, conversation_id: Optional[str] = None,
                    user_email: Optional[str] = None, depth: str = "normal",
                    resume_job_id: Optional[str] = None, **kw: Any) -> str:
    """Kick off a detached Deep Research job; return its job_id immediately.

    `resume_job_id` reuses an existing job's checkpoint (the agent skips
    already-resolved sub-questions). Extra kwargs (max_subq/screen_keep/read_cap)
    pass through to the agent."""
    job_id = resume_job_id or ("dr_" + uuid.uuid4().hex[:16])
    _jobs[job_id] = {
        "job_id": job_id, "question": question, "status": "queued",
        "progress": [], "conversation_id": conversation_id,
        "user_email": user_email, "created": time.time(),
        "document": None, "citations": None, "error": None}
    asyncio.create_task(_run(job_id, question, depth, conversation_id, user_email, kw))
    return job_id


async def _run(job_id: str, question: str, depth: str,
               conv: Optional[str], user: Optional[str], kw: dict) -> None:
    rec = _jobs[job_id]
    try:
        # Concurrency cap (default 1): a second job queues here rather than
        # oversubscribing the shared vLLM.
        async with _get_sem():
            rec["status"] = "running"
            rec["started"] = time.time()

            def _progress(event: str, data: dict) -> None:
                rec["progress"].append({"t": round(time.time() - rec["started"], 1),
                                        "event": event, **data})

            env = await _dr.deep_research(question, depth=depth, job_id=job_id,
                                          progress=_progress, **kw)
            if env.get("error"):
                rec.update(status="error", error=env["error"], finished=time.time())
                return
            rec.update(status="done", document=env["document"],
                       citations=env["citations"], plan=env["plan"],
                       finished=time.time())
            await _deliver(job_id, conv, user, question, env["document"])
    except asyncio.CancelledError:
        rec.update(status="cancelled", finished=time.time())
        raise
    except Exception as exc:  # noqa: BLE001 - a job failure must not crash the loop
        rec.update(status="error", error=f"{type(exc).__name__}: {exc}",
                   finished=time.time())


async def _deliver(job_id: str, conv: Optional[str], user: Optional[str],
                   question: str, document: str) -> None:
    """Deliver the composite as a markdown artifact (D6a). Non-fatal: the job is
    still 'done' and pollable even if artifact write fails."""
    if not conv or not user:
        return
    try:
        import artifact_store
        art = await artifact_store.create_artifact(
            user_email=user, conversation_id=conv,
            title=f"Research: {question[:70]}", content=document,
            content_type="text/markdown", language="markdown",
            change_summary="Deep Research report")
        _jobs[job_id]["artifact_id"] = (art or {}).get("id")
        # Leave an assistant message so the conversation reads question -> report
        # on reload (the artifact itself renders in the side panel).
        try:
            import chat_store
            await chat_store.add_message(
                conv, "assistant",
                f"I've finished the deep research report. See the **Research: "
                f"{question[:70]}** artifact in the panel.")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        _jobs[job_id]["delivery_error"] = f"{type(exc).__name__}: {exc}"


_POLL_KEYS = ("job_id", "question", "status", "progress", "document",
              "citations", "error", "artifact_id", "delivery_error", "created",
              "started", "finished")


def get_job(job_id: str, user_email: Optional[str] = None) -> Optional[dict]:
    """Poll a job's current state (durable across client disconnect). If
    `user_email` is given, returns None unless it owns the job (handles are
    unguessable + private, design §4.3)."""
    rec = _jobs.get(job_id)
    if rec is None:
        return None
    if user_email is not None and rec.get("user_email") != user_email:
        return None
    return {k: rec.get(k) for k in _POLL_KEYS}


def list_jobs(user_email: Optional[str] = None) -> list[dict]:
    out = []
    for rec in _jobs.values():
        if user_email and rec.get("user_email") != user_email:
            continue
        out.append({k: rec.get(k) for k in
                    ("job_id", "question", "status", "created", "finished")})
    return sorted(out, key=lambda r: r.get("created") or 0, reverse=True)


def cancel_job(job_id: str) -> bool:
    rec = _jobs.get(job_id)
    if rec and rec.get("status") in ("queued", "running"):
        rec["status"] = "cancelled"
        return True
    return False
