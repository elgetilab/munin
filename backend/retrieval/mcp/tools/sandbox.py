"""
MCP tools: run_python, edit_python and sandbox_reset.

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

import difflib
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


# --- Base source tracking for edit_python -----------------------------------
#
# The model resends a median 91-line script to change 3 lines: 56% of
# successive run_python pairs in a turn are >80% identical, and 35% of all
# Python source in the system is a verbatim copy of the previous call. See
# docs/agent-track/CODE-EDIT-TOOL-PLAN.md. edit_python gives it a way to say
# "change these three lines" instead, which needs the previous source.
#
# In-process, per-conversation, and deliberately NOT in the sandbox sidecar:
# that is a separate container with its own deploy, and nothing here needs
# kernel-side state. Edits land seconds after the run they patch, so this
# almost always hits; _base_source() falls back to the persisted transcript so
# a restart or a different worker degrades to a slower path, not an error.
_last_source: dict[str, str] = {}
_LAST_SOURCE_MAX = 512


def _remember_source(conv_id: str, code: str) -> None:
    if not conv_id or not isinstance(code, str):
        return
    if conv_id not in _last_source and len(_last_source) >= _LAST_SOURCE_MAX:
        # Bounded: drop an arbitrary entry rather than grow without limit.
        # Losing one only costs a fallback read.
        _last_source.pop(next(iter(_last_source)), None)
    _last_source[conv_id] = code


def forget_source(conv_id: str) -> None:
    """Drop the cached base source. Called on sandbox_reset: the kernel state
    it was written against is gone, so patching it would be misleading."""
    _last_source.pop(conv_id, None)


async def _base_source(conv_id: str) -> Optional[str]:
    """Most recent source executed in this conversation, or None."""
    cached = _last_source.get(conv_id)
    if cached:
        return cached
    try:
        import chat_store  # lazy: avoids a circular import at module init
        messages = await chat_store.get_messages_after_index(conv_id, -1)
    except Exception as e:
        logger.warning("edit_python: transcript fallback failed: %s", e)
        return None
    for msg in reversed(messages):
        for call in reversed(msg.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            if call.get("name") not in ("run_python", "edit_python"):
                continue
            args = call.get("arguments")
            if not isinstance(args, dict):
                continue
            # run_python carries the source directly. edit_python records the
            # source it produced under `resulting_code` (see below) precisely
            # so this fallback can chain across edits.
            code = args.get("code") or (call.get("result") or {}).get("resulting_code")
            if isinstance(code, str) and code.strip():
                _remember_source(conv_id, code)
                return code
    return None


def _near_misses(source: str, needle: str, limit: int = 3) -> list[str]:
    """Closest lines to an unmatched `old`, so the error is actionable.

    A bare "not found" is the fastest route back to re-pasting the whole
    script, which is the behaviour this tool exists to remove.
    """
    first = (needle.strip().splitlines() or [""])[0].strip()
    if not first:
        return []
    lines = source.splitlines()
    scored = sorted(
        ((difflib.SequenceMatcher(None, first, ln.strip()).ratio(), ln) for ln in lines),
        key=lambda t: -t[0],
    )
    return [ln.strip()[:160] for score, ln in scored[:limit] if score > 0.5]


def apply_edits(source: str, edits: list) -> tuple[str, dict]:
    """Apply ordered {old, new} replacements. Raises ValueError on any miss.

    Search/replace rather than unified diff, by design: update_artifact already
    ships a correct unified-diff mode and the model used it in 1 of 66 calls
    (2%). Line numbers and hunk lengths are the arithmetic models are worst at.
    Reproducing a snippet verbatim is something they do constantly.

    Each `old` must match EXACTLY ONCE. Ambiguity is an error rather than a
    first-match guess: silently patching the wrong occurrence produces code
    that runs and is subtly wrong, which is the worst failure available here.
    """
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list of {old, new} objects")

    out = source
    added = removed = 0
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"edit {i + 1}: must be an object with 'old' and 'new'")
        old = edit.get("old")
        new = edit.get("new")
        if not isinstance(old, str) or not old:
            raise ValueError(f"edit {i + 1}: 'old' must be a non-empty string")
        if not isinstance(new, str):
            raise ValueError(f"edit {i + 1}: 'new' must be a string (use \"\" to delete)")

        count = out.count(old)
        if count == 0:
            hint = _near_misses(out, old)
            msg = (
                f"edit {i + 1}: 'old' not found in the current source. It must match "
                f"character-for-character, including indentation."
            )
            if hint:
                msg += " Closest lines in the source: " + " | ".join(hint)
            raise ValueError(msg)
        if count > 1:
            raise ValueError(
                f"edit {i + 1}: 'old' matches {count} places; include more "
                f"surrounding lines so it identifies exactly one."
            )

        out = out.replace(old, new, 1)
        added += len(new.splitlines())
        removed += len(old.splitlines())

    return out, {
        "edits_applied": len(edits),
        "lines_added": added,
        "lines_removed": removed,
    }


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
    _remember_source(conv_id, code)
    # Adoption hint. The equivalent affordance for artifacts (update_artifact's
    # is_diff mode) sits unused at 2% because nothing points at it when the
    # model is deciding what to do next. This is that pointer, and it is the
    # cheapest part of the whole change.
    payload["edit_hint"] = (
        "To change part of this code, call edit_python with the lines to "
        "replace. Do not re-send the whole script; the kernel state persists."
    )
    return payload


async def edit_python(edits: list, timeout_s: int = 30) -> dict:
    """Patch the last executed source and run the result in the same kernel."""
    user_email, conv_id, refusal = _require_persistent_user_and_conv()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None

    source = await _base_source(conv_id)
    if not source:
        return {
            "error": (
                "no code has been run in this conversation yet, so there is "
                "nothing to edit. Call run_python first."
            )
        }

    try:
        new_source, stats = apply_edits(source, edits)
    except ValueError as e:
        # A failed edit must leave the model able to retry the edit rather than
        # fall back to re-sending everything, so the message says what to fix.
        return {"error": str(e)}

    if new_source == source:
        return {"error": "the edits leave the source unchanged; nothing to run"}

    result = await run_python(new_source, timeout_s=timeout_s)
    if isinstance(result, dict):
        result.update(stats)
        # Recorded so _base_source can chain across consecutive edits when the
        # in-process cache misses, and so a transcript replay sees what ran.
        result["resulting_code"] = new_source
        result.pop("edit_hint", None)
    return result


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
    forget_source(conv_id)
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
