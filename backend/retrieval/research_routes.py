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
    conv = body.get("conversation_id")
    if not conv:
        raise HTTPException(status_code=400,
                            detail={"error": {"message": "conversation_id is required "
                                    "(the report is delivered as an artifact in it)"}})
    kw = {k: body[k] for k in ("max_subq", "screen_keep", "read_cap")
          if isinstance(body.get(k), int)}
    job_id = await manager.start_job(
        question, conversation_id=conv, user_email=email,
        depth=body.get("depth", "normal"),
        resume_job_id=body.get("resume_job_id"), **kw)
    return {"job_id": job_id, "status": "queued"}


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
