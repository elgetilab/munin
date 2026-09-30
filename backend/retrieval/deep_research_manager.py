"""
Deep Research manager: detached-task lifecycle, concurrency, and delivery.

Deep Research is a long-running (many-minute) in-process agent, so it cannot run
inside a chat turn. This manager kicks off a DETACHED asyncio task (survives the
client disconnecting) and streams the job's progress as render-ready EVENTS into
a durable log (``research_store``): plan, a tool_call/tool_result per search and
per paper read, notes, and the final artifact. The frontend reads that log and
renders it inline in the conversation, exactly like normal tool use; because the
log is durable and ordered, a client that disconnects and returns just re-reads
it (every read is a replay). This is the "background work mirrored onto the
stream" model - the job is the source of truth, the UI is a view onto its log.

Concurrency is a configurable cap (default 1) enforced by a semaphore so the
many-minute run does not oversubscribe the vLLM shared with chat. The plan is
also checkpointed (agent side) so a crash resumes rather than restarts.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Optional

import deep_research_agent as _dr
import research_store

MAX_CONCURRENT = int(os.getenv("DEEP_RESEARCH_MAX_CONCURRENT", "1"))

_sem: Optional[asyncio.Semaphore] = None
# job_id -> running asyncio.Task, kept only so cancel_job can interrupt it. The
# durable state lives in research_store, not here.
_tasks: dict[str, asyncio.Task] = {}


class ResumeRejected(ValueError):
    """A client-supplied resume_job_id that may not be resumed by this caller."""


def _get_sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:  # lazily bound to the running loop, not import-time
        _sem = asyncio.Semaphore(MAX_CONCURRENT)
    return _sem


async def check_resumable(job_id: Any, user_email: Optional[str]) -> None:
    """A resume_job_id comes from the client, so it must be in the shape this
    module mints, be the caller's own job, and not still be running here:
    create_job replaces the row, which would otherwise hand another user's job
    (and its event log) to the caller or start a second task on a live one. A
    job left 'running' by a restart has no task, so it can still be resumed."""
    if not isinstance(job_id, str) or not _dr._JOB_ID_RE.fullmatch(job_id):
        raise ResumeRejected("invalid resume_job_id")
    # get_job skips the ownership check for user_email=None, so require one
    if not user_email or await research_store.get_job(job_id, user_email=user_email) is None:
        raise ResumeRejected("unknown or unauthorized job")
    if job_id in _tasks:
        raise ResumeRejected("job is still running")


async def start_job(question: str, *, conversation_id: Optional[str] = None,
                    user_email: Optional[str] = None, depth: str = "normal",
                    resume_job_id: Optional[str] = None, **kw: Any) -> str:
    """Create a durable job and kick off its detached task; return the job_id.
    Raises ResumeRejected for a resume_job_id the caller may not resume."""
    if resume_job_id is not None:
        await check_resumable(resume_job_id, user_email)
    job_id = resume_job_id or ("dr_" + uuid.uuid4().hex[:16])
    await research_store.create_job(job_id, conversation_id, user_email, question)
    _tasks[job_id] = asyncio.create_task(
        _run(job_id, question, depth, conversation_id, user_email, kw))
    return job_id


async def _run(job_id: str, question: str, depth: str,
               conv: Optional[str], user: Optional[str], kw: dict) -> None:
    started = time.time()

    async def _progress(event: str, data: dict) -> None:
        # Every agent milestone becomes a render-ready event in the durable log.
        await research_store.append_event(
            job_id, {"t": round(time.time() - started, 1), "type": event, **data})

    try:
        # Concurrency cap: a second job queues here rather than oversubscribing
        # the shared vLLM. Status stays 'queued' until the slot is free.
        async with _get_sem():
            await research_store.set_status(job_id, "running")
            env = await _dr.deep_research(question, depth=depth, job_id=job_id,
                                          progress=_progress, **kw)
            if env.get("error"):
                await research_store.set_status(job_id, "error", error=env["error"])
                await _progress("error", {"message": env["error"]})
                return
            # Deliver the report as an artifact, then emit the final events so the
            # inline view ends with a link to the report.
            artifact_id = await _deliver(job_id, conv, user, question, env["document"])
            await research_store.set_status(job_id, "done", artifact_id=artifact_id)
            n_resolved = sum(1 for n in env["plan"] if n["status"] == "resolved")
            await _progress("artifact", {"artifact_id": artifact_id,
                                         "title": f"Research: {question[:70]}",
                                         "n_resolved": n_resolved,
                                         "n_sub_questions": len(env["plan"]),
                                         "n_citations": len(env["citations"])})
            await _progress("done", {"artifact_id": artifact_id})
    except asyncio.CancelledError:
        await research_store.set_status(job_id, "cancelled")
        raise
    except Exception as exc:  # noqa: BLE001 - a job failure must not crash the loop
        await research_store.set_status(job_id, "error",
                                        error=f"{type(exc).__name__}: {exc}")
    finally:
        _tasks.pop(job_id, None)


async def _deliver(job_id: str, conv: Optional[str], user: Optional[str],
                   question: str, document: str) -> Optional[str]:
    """Deliver the composite as a markdown artifact + an assistant message.
    Returns the artifact id (or None). Non-fatal."""
    if not conv or not user:
        return None
    try:
        import artifact_store
        art = await artifact_store.create_artifact(
            user_email=user, conversation_id=conv,
            title=f"Research: {question[:70]}", content=document,
            content_type="text/markdown", language="markdown",
            change_summary="Deep Research report")
        try:
            import chat_store
            await chat_store.add_message(
                conv, "assistant",
                f"I've finished the deep research report. See the **Research: "
                f"{question[:70]}** artifact in the panel.")
        except Exception:  # noqa: BLE001
            pass
        return (art or {}).get("id")
    except Exception:  # noqa: BLE001
        return None


async def get_job(job_id: str, user_email: Optional[str] = None) -> Optional[dict]:
    """Full job record + ordered event log (durable across disconnect/restart)."""
    return await research_store.get_job(job_id, user_email)


async def get_job_for_conversation(conversation_id: str,
                                   user_email: str) -> Optional[dict]:
    """The conversation's DR job, for re-loading the inline view on open."""
    return await research_store.get_job_for_conversation(conversation_id, user_email)


async def list_jobs(user_email: str) -> list[dict]:
    return await research_store.list_jobs(user_email)


async def cancel_job(job_id: str, user_email: Optional[str] = None) -> bool:
    job = await research_store.get_job(job_id, user_email)
    if not job or job.get("status") not in ("queued", "running"):
        return False
    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    await research_store.set_status(job_id, "cancelled")
    return True
