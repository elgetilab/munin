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
    inline_content: Optional[str] = None,
) -> dict:
    """
    Wrap artifact_store.register_sandbox_artifact and enrich the
    response with display_url / external_url / registered_artifact_id
    so chat_service can emit artifact_created events the same way it
    does for run_python outputs.

    ``inline_content`` is the actual textual payload (e.g. the .tex
    source for a compile_latex result). Pass it for text artifacts so
    read_artifact and compile_latex(artifact_id=...) can see the real
    content; pass None for binaries (PDFs, plot PNGs).
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
            inline_content=inline_content,
        )
        art["registered_artifact_id"] = registered.get("id")
        art["source"] = registered.get("source")
        art["external_url"] = registered.get("external_url")
    except Exception as e:
        print(f"[WARNING] register_sandbox_artifact (latex) failed: {e}")
    return art


async def compile_latex(
    source: Optional[str] = None,
    artifact_id: Optional[str] = None,
    diff: Optional[str] = None,
    bibliography: Optional[str] = None,
    extra_files: Optional[dict[str, str]] = None,
    timeout_s: int = 60,
) -> dict:
    """
    Compile a LaTeX document via the sandbox's pdflatex pipeline.

    Three input modes:

    - ``source``: full LaTeX text. The standard path for a brand-new
      document.
    - ``artifact_id``: id of a previously-registered .tex artifact.
      Reads its latest version content as the source. Use this when
      the user asks for an unchanged recompile.
    - ``artifact_id`` + ``diff``: read the artifact, apply a unified
      diff, then compile. The big iteration win — a one-line edit
      ("aspectratio=169") becomes ~150 emitted tokens instead of
      re-emitting the full ~9KB source. Diff format mirrors
      update_artifact's is_diff mode (strict matching, no fuzz).

    Exactly one of ``source`` / ``artifact_id`` must be set. ``diff``
    is only valid alongside ``artifact_id``.
    """
    has_source = isinstance(source, str) and source.strip()
    has_artifact = isinstance(artifact_id, str) and artifact_id.strip()
    has_diff = isinstance(diff, str) and diff.strip()

    if has_source and has_artifact:
        return {
            "error": (
                "pass either 'source' or 'artifact_id', not both. Use "
                "'source' for new documents and 'artifact_id' "
                "(optionally with 'diff') for iterating on an existing "
                ".tex artifact."
            )
        }
    if not has_source and not has_artifact:
        return {
            "error": (
                "must pass either 'source' (full LaTeX) or "
                "'artifact_id' (id of an existing .tex artifact, "
                "optionally with 'diff' to apply a unified diff before "
                "compiling)."
            )
        }
    if has_diff and not has_artifact:
        return {
            "error": (
                "'diff' is only valid with 'artifact_id'. To compile a "
                "new document pass full LaTeX in 'source' instead."
            )
        }

    if has_source:
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

    # Resolve effective source for artifact_id mode. Read the latest
    # version from artifact_store (compile_latex now stores .tex
    # source inline at registration time, so the round-trip is
    # in-process — no sandbox HTTP fetch needed). Reject pre-fix
    # placeholder content with a clear error so the model knows to
    # fall back to passing source explicitly.
    if has_artifact:
        import artifact_store  # lazy to avoid circular init
        try:
            art = await artifact_store.get_artifact_version(
                user_email=user_email,
                conversation_id=conv_id,
                artifact_id=artifact_id,
            )
        except Exception as e:
            return {
                "error": (
                    f"failed to read artifact {artifact_id}: "
                    f"{type(e).__name__}: {e}"
                )
            }
        if art is None:
            return {
                "error": (
                    f"artifact {artifact_id!r} not found in this "
                    f"conversation. Pass a valid id from a prior "
                    f"compile_latex / create_artifact result, or use "
                    f"'source' to compile fresh LaTeX."
                )
            }
        base_source = art.get("content") or ""
        if base_source.startswith("[Sandbox-generated file:"):
            return {
                "error": (
                    f"artifact {artifact_id!r} was registered before "
                    f"inline-source storage was enabled, so its "
                    f"content is just a placeholder. Pass full LaTeX "
                    f"in 'source' for this turn; future compile_latex "
                    f"calls on the resulting artifact will support "
                    f"artifact_id mode."
                )
            }
        if has_diff:
            try:
                resolved_source, _stats = artifact_store.apply_unified_diff(
                    base_source, diff
                )
            except artifact_store.ArtifactError as e:
                return {
                    "error": (
                        f"diff application failed: {e}. The diff must "
                        f"be a standard unified diff (@@ -old,len "
                        f"+new,len @@) with context and removal lines "
                        f"matching the artifact source exactly. Read "
                        f"the artifact again with read_artifact and "
                        f"rebuild the diff against the latest version."
                    )
                }
        else:
            resolved_source = base_source
        if len(resolved_source) > _MAX_SOURCE_CHARS:
            return {
                "error": (
                    f"resolved source too large: "
                    f"{len(resolved_source)} chars "
                    f"(limit {_MAX_SOURCE_CHARS})"
                )
            }
        source = resolved_source

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
    # chip rendered via `external_url`. The .tex artifact carries the
    # resolved source inline so subsequent compile_latex(artifact_id=
    # ..., diff=...) calls can fetch and patch it without re-emitting
    # the full body.
    registered_artifacts: list[dict] = []
    tex_art = payload.get("tex_artifact")
    if tex_art:
        enriched = await _register_artifact(
            user_email, conv_id, tex_art, inline_content=source
        )
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
