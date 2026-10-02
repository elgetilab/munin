"""
HTTP surface for the Deep Research agent (kick-off -> poll -> document).

A self-contained APIRouter included by main.py with one line, so it adds the
detached-job delivery model without touching the rest of the app. This is the
poll-based half of the D6 hybrid: `start` returns a job_id immediately, `status`
is durable across client disconnect (progress + the final markdown document +
the delivered artifact id), `jobs` lists the caller's runs. The SSE-streaming
half (live progress bridged onto the chat stream + stream_registry resume) is the
remaining chat_service integration.

Auth follows the app convention: X-Munin-Email (set by Caddy forward-auth);
errors use the uniform {"error": {"message": ...}} envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import deep_research_manager as manager
from maintenance import read_maintenance

# Prefix MUST start with /api/: the frontend Caddy only forwards /api/* and
# /paper/* to the cluster, and the gateway proxies /api/{path} -> cluster
# /api/{path} (prefix preserved). A bare /research would be cluster-internal only.
router = APIRouter(prefix="/api/research", tags=["deep-research-agent"])


def _require_email(request: Request) -> str:
    email = (request.headers.get("X-Munin-Email") or "").strip()
    if not email:
        raise HTTPException(status_code=401,
                            detail={"error": {"message": "Missing X-Munin-Email header"}})
    return email


@router.post("/start")
async def start(request: Request) -> dict:
    email = _require_email(request)
    # Maintenance mode stops vLLM, so a job started now could only fail at
    # its first model call. Same flag /api/status reports to the UI.
    if read_maintenance().get("active"):
        raise HTTPException(status_code=503, detail={"error": {
            "message": "Munin is in maintenance; Deep Research is unavailable until it ends."}})
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400,
                            detail={"error": {"message": "question is required"}})
    # Both ids come from the client, so check them before anything is written:
    # a resume id must be the caller's own idle job, and an existing
    # conversation must be the caller's, or the question (and later the report
    # message) lands in someone else's chat.
    resume_job_id = body.get("resume_job_id")
    if resume_job_id is not None:
        try:
            await manager.check_resumable(resume_job_id, email)
        except manager.ResumeRejected as exc:
            raise HTTPException(status_code=400,
                                detail={"error": {"message": str(exc)}})
    import chat_store
    conv = body.get("conversation_id")
    if conv and await chat_store._get_conversation_meta(conv, email) is None:
        raise HTTPException(status_code=404,
                            detail={"error": {"message": "unknown or unauthorized conversation"}})
    # A brand-new chat has no conversation yet. Create one so Deep Research can be
    # launched as the FIRST action in a fresh chat; the report is delivered there
    # as an artifact. The client switches to the returned conversation_id.
    created_conversation = False
    if not conv:
        c = await chat_store.create_conversation(
            user_email=email, persona="munin", title=question[:80])
        conv = c["id"]
        created_conversation = True
    # Record the research question as a user message so the conversation view is
    # populated - a fresh DR chat would otherwise open empty (the report is a
    # background job, not a chat turn). Non-fatal if it fails.
    try:
        await chat_store.add_message(conv, "user", question)
    except Exception:  # noqa: BLE001
        pass
    kw = {k: body[k] for k in ("max_subq", "screen_keep", "read_cap")
          if isinstance(body.get(k), int)}
    try:
        job_id = await manager.start_job(
            question, conversation_id=conv, user_email=email,
            depth=body.get("depth", "deep"),  # include the web tier (lever 1)
            resume_job_id=resume_job_id, **kw)
    except manager.ResumeRejected as exc:  # lost a race with a concurrent resume
        raise HTTPException(status_code=400,
                            detail={"error": {"message": str(exc)}})
    return {"job_id": job_id, "conversation_id": conv,
            "created_conversation": created_conversation, "status": "queued"}


@router.get("/status/{job_id}")
async def status(job_id: str, request: Request) -> dict:
    """Full job state + ordered event log. The client renders `events` inline and
    re-reads on reconnect (every read is a replay)."""
    email = _require_email(request)
    job = await manager.get_job(job_id, user_email=email)
    if job is None:
        raise HTTPException(status_code=404,
                            detail={"error": {"message": "unknown or unauthorized job"}})
    return job


@router.get("/for-conversation/{conversation_id}")
async def for_conversation(conversation_id: str, request: Request) -> dict:
    """The conversation's most recent DR job (+event log), or {job: null}. Lets the
    client re-load and render the inline research view when a chat is re-opened."""
    email = _require_email(request)
    job = await manager.get_job_for_conversation(conversation_id, email)
    return {"job": job}


@router.get("/jobs")
async def jobs(request: Request) -> dict:
    email = _require_email(request)
    return {"jobs": await manager.list_jobs(email)}


@router.post("/cancel/{job_id}")
async def cancel(job_id: str, request: Request) -> dict:
    email = _require_email(request)
    cancelled = await manager.cancel_job(job_id, user_email=email)
    if not cancelled and await manager.get_job(job_id, user_email=email) is None:
        raise HTTPException(status_code=404,
                            detail={"error": {"message": "unknown or unauthorized job"}})
    return {"cancelled": cancelled}
