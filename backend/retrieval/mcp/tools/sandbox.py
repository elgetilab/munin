"""
MCP tools: run_python and sandbox_reset.

Thin proxies in front of the sandbox sidecar service. The sidecar lives in
its own docker container on a private internal network and runs Jupyter
kernels under firejail. We do not run any user code in this process.

Per-conversation kernel binding:
  - The sidecar keys kernels by ``conversation_id`` so variables, imports,
    and data in /scratch persist between turns of the same chat.
  - Ephemeral chats (``conversation_id`` starts with ``ephemeral-``) are
    refused at this layer because the synthetic id changes every request
    and the user explicitly opted out of any persistent state.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from ..context import current_user_email, current_conversation_id

logger = logging.getLogger(__name__)


def _require_persistent_user_and_conv() -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Same as _require_persistent_conversation below but also returns
    the user_email so the caller can register unified artifacts
    (§22 Stage C)."""
    user_email = current_user_email.get()
    if not user_email:
        return None, None, {"error": "sandbox tools require an authenticated user context"}
    conv_id = current_conversation_id.get()
    if not conv_id:
        return None, None, {"error": "sandbox tools require an active conversation context"}
    if conv_id.startswith("ephemeral-"):
        return None, None, {
            "error": (
                "sandbox is unavailable in ephemeral chats by design. "
                "Switch to a regular (persistent) chat to run code."
            )
        }
    return user_email, conv_id, None


SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:8090")
SANDBOX_TIMEOUT_S = float(os.environ.get("SANDBOX_HTTP_TIMEOUT_S", "180"))


def _require_persistent_conversation() -> tuple[Optional[str], Optional[dict]]:
    """
    Return ``(conversation_id, None)`` if the request can use the sandbox,
    or ``(None, error_response)`` if it cannot. Centralises the ephemeral
    refusal so both run_python and sandbox_reset share one rule.
    """
    user_email = current_user_email.get()
    if not user_email:
        return None, {
            "error": "sandbox tools require an authenticated user context",
        }
    conv_id = current_conversation_id.get()
    if not conv_id:
        return None, {
            "error": "sandbox tools require an active conversation context",
        }
    if conv_id.startswith("ephemeral-"):
        return None, {
            "error": (
                "sandbox is unavailable in ephemeral chats by design. "
                "Switch to a regular (persistent) chat to run code."
            ),
        }
    return conv_id, None


async def run_python(code: str, timeout_s: int = 30) -> dict:
    if not isinstance(code, str) or not code.strip():
        return {"error": "code must be a non-empty string"}

    try:
        timeout_s = int(timeout_s)
    except (TypeError, ValueError):
        timeout_s = 30
    timeout_s = max(1, min(timeout_s, 120))

    user_email, conv_id, refusal = _require_persistent_user_and_conv()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None

    try:
        async with httpx.AsyncClient(timeout=SANDBOX_TIMEOUT_S) as client:
            r = await client.post(
                f"{SANDBOX_URL}/exec/{conv_id}",
                json={"code": code, "timeout_s": timeout_s},
            )
    except httpx.RequestError as e:
        return {
            "error": f"sandbox unreachable: {type(e).__name__}: {e}",
        }
    if r.status_code != 200:
        return {
            "error": f"sandbox returned {r.status_code}: {r.text[:300]}",
        }

    payload = r.json()
    # §22 Stage C: register each sandbox-produced artifact in the
    # unified artifacts table so it shows up alongside model-written
    # documents in the side panel and in the model's list_artifacts
    # tool. Each artifact dict gets a new ``registered_artifact_id``
    # field (the new art_* id) added to its metadata; the legacy
    # ``display_url`` still points at the sandbox proxy endpoint so
    # back-compat is intact. Registration failures are logged but
    # don't fail the tool call - the bytes are still on disk.
    import artifact_store  # lazy to avoid circular init
    for art in payload.get("artifacts") or []:
        art["display_url"] = f"/api/artifacts/{conv_id}/{art['id']}"
        try:
            registered = await artifact_store.register_sandbox_artifact(
                user_email=user_email,
                conversation_id=conv_id,
                sandbox_artifact_id=art.get("id") or "",
                filename=art.get("filename") or "unnamed",
                content_type=art.get("content_type") or "application/octet-stream",
                size_bytes=int(art.get("size_bytes") or 0),
            )
            art["registered_artifact_id"] = registered.get("id")
            art["source"] = registered.get("source")
            art["external_url"] = registered.get("external_url")
        except Exception as e:
            logger.warning("register_sandbox_artifact failed: %s", e)
    payload["conversation_id"] = conv_id
    return payload


async def sandbox_reset() -> dict:
    conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    try:
        async with httpx.AsyncClient(timeout=SANDBOX_TIMEOUT_S) as client:
            r = await client.post(f"{SANDBOX_URL}/reset/{conv_id}")
    except httpx.RequestError as e:
        return {"error": f"sandbox unreachable: {type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {
            "error": f"sandbox returned {r.status_code}: {r.text[:300]}",
        }
    return r.json()


async def sandbox_shutdown(conversation_id: str) -> dict:
    """
    Internal helper used by api_delete_chat to release a kernel when its
    conversation row is deleted. Not exposed as an MCP tool — it would let
    the model nuke its own kernel by accident.
    """
    if not conversation_id or conversation_id.startswith("ephemeral-"):
        return {"shutdown": False, "reason": "no kernel for this conversation"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.delete(f"{SANDBOX_URL}/kernels/{conversation_id}")
    except httpx.RequestError as e:
        return {"shutdown": False, "error": f"{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"shutdown": False, "error": f"sandbox returned {r.status_code}"}
    return r.json()
