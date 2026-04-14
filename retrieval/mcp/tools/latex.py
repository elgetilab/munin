"""
MCP tool: compile_latex (§18).

Thin proxy over the sandbox sidecar's ``/latex/{conversation_id}``
endpoint. Mirrors the shape of ``run_python`` in ``sandbox.py``:

- Refuses ephemeral conversations (no persistent scratch dir).
- Registers both the ``.tex`` source and the compiled ``.pdf`` in
  the unified ``artifact_store`` so they show up in the side panel
  alongside model-written documents (§22 Stage C).
- Always returns the .tex artifact, so the user can grab the source
  even if the compile blew up. The .pdf artifact is only set on
  success.
- Returns structured ``errors`` / ``warnings`` / ``log_tail`` so the
  model can read its own compile errors and iterate within the
  existing tool loop.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from ..context import current_user_email, current_conversation_id
from .sandbox import SANDBOX_URL, SANDBOX_TIMEOUT_S


_MAX_SOURCE_CHARS = 500_000
_MAX_BIBLIOGRAPHY_CHARS = 500_000
_MAX_EXTRA_FILES = 20


def _require_persistent_user_and_conv() -> tuple[
    Optional[str], Optional[str], Optional[dict]
]:
    """Same contract as sandbox.run_python: authenticated user + non-ephemeral conv."""
    user_email = current_user_email.get()
    if not user_email:
        return None, None, {
            "error": "compile_latex requires an authenticated user context"
        }
    conv_id = current_conversation_id.get()
    if not conv_id:
        return None, None, {
            "error": "compile_latex requires an active conversation context"
        }
    if conv_id.startswith("ephemeral-"):
        return None, None, {
            "error": (
                "compile_latex is unavailable in ephemeral chats by design. "
                "Switch to a regular (persistent) chat to compile LaTeX."
            )
        }
    return user_email, conv_id, None


async def _register_artifact(
    user_email: str,
    conv_id: str,
    art: dict,
) -> dict:
    """
    Wrap artifact_store.register_sandbox_artifact and enrich the
    response with display_url / external_url / registered_artifact_id
    so chat_service can emit artifact_created events the same way it
    does for run_python outputs.
    """
    import artifact_store  # lazy to avoid circular init
    art = dict(art)
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
        print(f"[WARNING] register_sandbox_artifact (latex) failed: {e}")
    return art


async def compile_latex(
    source: str,
    bibliography: Optional[str] = None,
    extra_files: Optional[dict[str, str]] = None,
    timeout_s: int = 60,
) -> dict:
    """
    Compile ``source`` via the sandbox's pdflatex pipeline and return
    both the .tex source and (on success) the compiled .pdf as
    registered artifacts.
    """
    if not isinstance(source, str) or not source.strip():
        return {"error": "source must be a non-empty string"}
    if len(source) > _MAX_SOURCE_CHARS:
        return {
            "error": (
                f"source too large: {len(source)} chars "
                f"(limit {_MAX_SOURCE_CHARS})"
            )
        }
    if bibliography is not None:
        if not isinstance(bibliography, str):
            return {"error": "bibliography must be a string or null"}
        if len(bibliography) > _MAX_BIBLIOGRAPHY_CHARS:
            return {
                "error": (
                    f"bibliography too large: {len(bibliography)} chars "
                    f"(limit {_MAX_BIBLIOGRAPHY_CHARS})"
                )
            }
    if extra_files is not None:
        if not isinstance(extra_files, dict):
            return {"error": "extra_files must be a dict of filename -> content"}
        if len(extra_files) > _MAX_EXTRA_FILES:
            return {
                "error": (
                    f"too many extra_files: {len(extra_files)} "
                    f"(limit {_MAX_EXTRA_FILES})"
                )
            }
        for k, v in extra_files.items():
            if not isinstance(k, str) or not k:
                return {"error": "extra_files keys must be non-empty strings"}
            if not isinstance(v, str):
                return {"error": f"extra_files[{k!r}] must be a string"}

    try:
        timeout_s = int(timeout_s)
    except (TypeError, ValueError):
        timeout_s = 60
    timeout_s = max(5, min(timeout_s, 120))

    user_email, conv_id, refusal = _require_persistent_user_and_conv()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None

    body: dict[str, Any] = {
        "source": source,
        "bibliography": bibliography,
        "extra_files": extra_files,
        "timeout_s": float(timeout_s),
    }

    try:
        async with httpx.AsyncClient(timeout=SANDBOX_TIMEOUT_S) as client:
            r = await client.post(
                f"{SANDBOX_URL}/latex/{conv_id}",
                json=body,
            )
    except httpx.RequestError as e:
        return {"error": f"sandbox unreachable: {type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {
            "error": f"sandbox returned {r.status_code}: {r.text[:300]}"
        }
    payload = r.json()

    # §22 Stage C: register each returned artifact (.tex and maybe
    # .pdf) in the unified artifacts table so the side panel picks
    # them up. Both are registered as sandbox-generated; the frontend
    # already routes `source: "sandbox_generated"` to a downloadable
    # chip rendered via `external_url`.
    registered_artifacts: list[dict] = []
    tex_art = payload.get("tex_artifact")
    if tex_art:
        enriched = await _register_artifact(user_email, conv_id, tex_art)
        registered_artifacts.append(enriched)
        payload["tex_artifact"] = enriched
    pdf_art = payload.get("pdf_artifact")
    if pdf_art:
        enriched = await _register_artifact(user_email, conv_id, pdf_art)
        registered_artifacts.append(enriched)
        payload["pdf_artifact"] = enriched

    payload["artifacts"] = registered_artifacts
    payload["conversation_id"] = conv_id
    return payload
