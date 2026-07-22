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
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400,
                            detail={"error": {"message": "question is required"}})
    # A brand-new chat has no conversation yet. Create one so Deep Research can be
    # launched as the FIRST action in a fresh chat; the report is delivered there
    # as an artifact. The client switches to the returned conversation_id.
    conv = body.get("conversation_id")
    created_conversation = False
    if not conv:
        import chat_store
        c = await chat_store.create_conversation(
            user_email=email, persona="munin", title=question[:80])
        conv = c["id"]
        created_conversation = True
    kw = {k: body[k] for k in ("max_subq", "screen_keep", "read_cap")
          if isinstance(body.get(k), int)}
    job_id = await manager.start_job(
        question, conversation_id=conv, user_email=email,
        depth=body.get("depth", "normal"),
        resume_job_id=body.get("resume_job_id"), **kw)
    return {"job_id": job_id, "conversation_id": conv,
            "created_conversation": created_conversation, "status": "queued"}


@router.get("/status/{job_id}")
async def status(job_id: str, request: Request) -> dict:
    email = _require_email(request)
    job = manager.get_job(job_id, user_email=email)
    if job is None:
        raise HTTPException(status_code=404,
                            detail={"error": {"message": "unknown or unauthorized job"}})
    return job


@router.get("/jobs")
async def jobs(request: Request) -> dict:
    email = _require_email(request)
    return {"jobs": manager.list_jobs(email)}


@router.post("/cancel/{job_id}")
async def cancel(job_id: str, request: Request) -> dict:
    email = _require_email(request)
    if manager.get_job(job_id, user_email=email) is None:
        raise HTTPException(status_code=404,
                            detail={"error": {"message": "unknown or unauthorized job"}})
    return {"cancelled": manager.cancel_job(job_id)}
