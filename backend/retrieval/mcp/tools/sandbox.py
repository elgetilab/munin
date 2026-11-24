"""
MCP tools: run_python, edit_python and sandbox_reset.

Thin proxies in front of the sandbox sidecar service. The sidecar lives in
its own docker container on a private internal network and runs Jupyter
kernels in isolated namespaces as unprivileged uids. We do not run any user code in this process.

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

import labkit_hints
import results_outline
import retype_check
from text_edits import apply_edits as _apply_edits, near_misses

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

# Which labkit pointers each conversation has had (labkit_hints.py).
_labkit_hints = labkit_hints.HintState()

# Numeric columns of each conversation's uploads, for the retyping hint
# (retype_check.py, LABKIT-018-PLAN.md change 1). In-process like the two
# caches above: after a restart it is empty and the hint does not fire.
_retype = retype_check.RetypeState()


def remember_input(conv_id: str, filename: str, data: bytes, paths: list[str]) -> None:
    """Keep an uploaded file's numeric columns for the retyping hint. Never raises."""
    try:
        _retype.remember(conv_id, filename, data, paths)
    except Exception as e:  # a parse problem only silences the hint
        logger.info("retype: columns of %s not kept: %s", filename, e)


def new_turn(conv_id: str) -> None:
    """A new user message: per-turn hint limits start over."""
    _retype.new_turn(conv_id)


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
    _file_sources.pop(conv_id, None)


# Source that last wrote each output file, per conversation, so edit_python
# can patch the plot script rather than whatever ran last (often a small size
# check). In-process for the current turn; earlier turns come from the
# persisted transcript via script_index (LAB-BUGFIX-PLAN.md F1).
_file_sources: dict[str, dict[str, str]] = {}


def _remember_file_sources(conv_id: str, code: str, payload: dict) -> None:
    if not conv_id or not isinstance(code, str) or payload.get("error") or payload.get("timed_out"):
        return
    names = [a.get("filename") for a in payload.get("artifacts") or [] if isinstance(a, dict)]
    names = [n for n in names if isinstance(n, str) and n]
    if not names:
        return
    if conv_id not in _file_sources and len(_file_sources) >= _LAST_SOURCE_MAX:
        _file_sources.pop(next(iter(_file_sources)), None)
    per = _file_sources.setdefault(conv_id, {})
    for n in names:
        per.pop(n, None)
        per[n] = code


async def _file_source(conv_id: str, filename: str) -> Optional[str]:
    """Source of the last successful run that wrote ``filename``."""
    per = _file_sources.get(conv_id) or {}
    if filename in per:
        return per[filename]
    base = filename.rsplit("/", 1)[-1]
    for name in reversed(list(per)):
        if name.rsplit("/", 1)[-1] == base:
            return per[name]
    try:
        import chat_store  # lazy: avoids a circular import at module init
        from script_index import script_for_file
        messages = await chat_store.get_messages_after_index(conv_id, -1)
    except Exception as e:
        logger.warning("edit_python: transcript fallback failed: %s", e)
        return None
    return script_for_file(messages, filename)


async def _user_text(conv_id: str) -> str:
    """The user's messages in this conversation (last 4, 4000 chars), for the
    hints that need what the question says, not only the code (LABKIT-019).
    Best effort: empty on any problem."""
    try:
        import asyncio
        import chat_store  # lazy: avoids a circular import at module init
        messages = await asyncio.wait_for(chat_store.get_messages_after_index(conv_id, -1), timeout=2.0)
    except Exception as e:
        logger.info("hint context skipped: %s", e)
        return ""
    texts = [m.get("content") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return "\n".join(texts[-4:])[-4000:]


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


# The search/replace core is shared with update_artifact and compile_latex
# (text_edits.py); edit_python keeps its original {old, new} key names.
_near_misses = near_misses


def apply_edits(source: str, edits: list) -> tuple[str, dict]:
    """Apply ordered {old, new} replacements. Raises ValueError on any miss.
    See text_edits.apply_edits for why this is search/replace."""
    return _apply_edits(source, edits, old_key="old", new_key="new")


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
                sha256=art.get("sha256"),
                version=art.get("version"),
            )
            art["registered_artifact_id"] = registered.get("id")
            art["source"] = registered.get("source")
            art["external_url"] = registered.get("external_url")
        except Exception as e:
            logger.warning("register_sandbox_artifact failed: %s", e)
    payload["conversation_id"] = conv_id
    _slim_artifacts(payload)
    await _outline_json(conv_id, payload)
    _remember_source(conv_id, code)
    _remember_file_sources(conv_id, code, payload)
    # Adoption hint. The equivalent affordance for artifacts (update_artifact's
    # is_diff mode) sits unused at 2% because nothing points at it when the
    # model is deciding what to do next. This is that pointer, and it is the
    # cheapest part of the whole change.
    payload["edit_hint"] = (
        "To change part of this code, call edit_python with the lines to "
        "replace. Do not re-send the whole script; the kernel state persists. "
        "To change a file made by an earlier run, pass file=<its name>."
    )
    # A hand-rolled fit of a model labkit has (LAB-ADOPTION-PLAN.md), once
    # per conversation and hint, never after the conversation used labkit.
    context = await _user_text(conv_id) if labkit_hints.wants_context(code) else ""
    hint = _labkit_hints.hint_for(conv_id, code, context=context)
    if hint:
        payload["labkit_hint"] = hint[1]
        # One line per hint so an eval can count them (LABKIT-011-PLAN.md).
        logger.info("labkit_hint kind=%s conv=%s", hint[0], conv_id)
    try:
        out_hint = _labkit_hints.output_hint_for(conv_id, code, str(payload.get("stdout") or ""))
    except Exception as e:
        logger.info("output hint skipped: %s", e)
        out_hint = None
    if out_hint:
        payload["labkit_hint"] = (payload["labkit_hint"] + " " + out_hint[1]) if hint else out_hint[1]
        logger.info("labkit_hint kind=%s conv=%s", out_hint[0], conv_id)
    # An array typed into the code that starts like an uploaded column and
    # then departs from it (advisory; once per file and column per turn).
    try:
        retype = _retype.hint_for(conv_id, code)
    except Exception as e:
        logger.info("retype check skipped: %s", e)
        retype = None
    if retype:
        payload["data_hint"] = retype
        logger.info("data_hint kind=retype conv=%s", conv_id)
    return payload


async def _outline_json(conv_id: str, payload: dict) -> None:
    """Add `json_outline`: what each JSON file this run wrote holds, key by
    key (LABKIT-013-PLAN.md; results_outline.py says why). Best effort: a
    fetch problem leaves the result as it was."""
    arts = [a for a in payload.get("artifacts") or []
            if str(a.get("filename") or "").lower().endswith(".json") and a.get("id")
            and int(a.get("size_bytes") or 0) <= results_outline.MAX_BYTES]
    if not arts:
        return
    lines = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for a in arts[:3]:
                r = await client.get(f"{SANDBOX_URL}/artifacts/{conv_id}/{a['id']}")
                if r.status_code != 200:
                    continue
                line = results_outline.outline(a["filename"], r.content)
                if line:
                    lines.append(line)
    except Exception as e:  # never fail the run over an outline
        logger.info("json outline skipped: %s", e)
        return
    if lines:
        payload["json_outline"] = lines
        payload["json_outline_hint"] = (
            "json_outline shows what each JSON file you wrote holds. Check it against "
            "what the user asked for before answering.")


# Keys of a sandbox artifact the model gets. The full figure description
# (per-point data) stays in the sandbox and the download bundle; the model
# reads the short figure_summary to check its plot against the request.
_MODEL_ARTIFACT_KEYS = (
    "id", "filename", "content_type", "size_bytes", "version", "previous_id",
    "kind", "sha256", "figure_summary", "display_url", "registered_artifact_id",
    "source", "external_url",
)


def _slim_artifacts(payload: dict) -> None:
    arts = payload.get("artifacts")
    if not isinstance(arts, list):
        return
    payload["artifacts"] = [
        {k: a[k] for k in _MODEL_ARTIFACT_KEYS if k in a} for a in arts if isinstance(a, dict)
    ]
    if not payload.get("warnings"):
        payload.pop("warnings", None)
    if any(a.get("figure_summary") for a in payload["artifacts"]):
        payload["figure_hint"] = (
            "figure_summary describes what was actually drawn (axes, scales, "
            "series, error bars, size, layout problems). Check it against what "
            "the user asked for before answering."
        )


async def put_input(conversation_id: str, filename: str, data: bytes) -> dict:
    """Hand an uploaded file to the conversation's sandbox as a read-only
    input under ``inputs/``. A .zip is extracted into ``inputs/<name>/``.
    Returns ``{"paths": [...]}`` or ``{"error": ...}``."""
    if not conversation_id or conversation_id.startswith("ephemeral-"):
        return {"error": "the code sandbox is not available in temporary chats"}
    try:
        async with httpx.AsyncClient(timeout=SANDBOX_TIMEOUT_S) as client:
            r = await client.post(
                f"{SANDBOX_URL}/inputs/{conversation_id}",
                params={"name": filename}, content=data,
            )
    except httpx.RequestError as e:
        return {"error": f"sandbox unreachable: {type(e).__name__}"}
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        return {"error": f"sandbox refused the file: {detail}"}
    added = r.json().get("added") or []
    return {"paths": [f"inputs/{a['path']}" for a in added if isinstance(a, dict)]}


async def edit_python(edits: list, timeout_s: int = 30, file: Optional[str] = None) -> dict:
    """Patch the last executed source, or with ``file`` the source that last
    wrote that output file, and run the result in the same kernel."""
    user_email, conv_id, refusal = _require_persistent_user_and_conv()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None

    if isinstance(file, str) and file.strip():
        source = await _file_source(conv_id, file.strip())
        if not source:
            return {
                "error": (
                    f"no successful run in this conversation wrote {file.strip()!r}. "
                    "Check the file name, or call edit_python without file to "
                    "patch the code you last ran."
                )
            }
    else:
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


async def sandbox_shutdown(conversation_id: str, purge: bool = False) -> dict:
    """
    Internal helper used by api_delete_chat to release a kernel when its
    conversation row is deleted. Not exposed as an MCP tool — it would let
    the model nuke its own kernel by accident.
    """
    if not conversation_id or conversation_id.startswith("ephemeral-"):
        return {"shutdown": False, "reason": "no kernel for this conversation"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # purge: the chat itself was deleted, so its sandbox files,
            # artifacts and inputs (persistent volumes since 2026-11) go too.
            path = "conversations" if purge else "kernels"
            r = await client.delete(f"{SANDBOX_URL}/{path}/{conversation_id}")
    except httpx.RequestError as e:
        return {"shutdown": False, "error": f"{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"shutdown": False, "error": f"sandbox returned {r.status_code}"}
    return r.json()
