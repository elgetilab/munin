"""
MCP tool: transcribe_equation (§11).

Takes a document_id pointing at an image the user already uploaded
and returns a LaTeX transcription via vLLM's multimodal endpoint.
Every researcher has hand-copied an equation from a PDF at some
point; this tool makes that step a one-shot call.

Design notes:

- We deliberately drop the spec's optional ``confidence`` field. The
  model has no honest way to produce a calibrated confidence score
  for OCR output - self-reported confidences are basically random,
  and returning one would mislead callers into trusting them.
- The vLLM call disables Qwen3's default reasoning phase via
  ``chat_template_kwargs.enable_thinking=False``. OCR is a mechanical
  transcription task; letting the model ramble through ``<think>``
  would burn the whole token budget before any LaTeX appeared. Same
  trick used by ``llm_summarize`` and ``chat_context._call_vllm``.
- Only image document types (png/jpeg/webp) are accepted. Non-image
  docs return an explicit error so the model can apologise to the
  user rather than feed unusable input to vLLM.
"""

from __future__ import annotations

import os

import httpx

from database import VLLM_URL, VLLM_MODEL_NAME

from ..context import current_user_email


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_TRANSCRIBE_SYSTEM = (
    "You are an expert at reading mathematical notation from images "
    "and transcribing it as LaTeX source. Output ONLY the LaTeX "
    "source for the equation(s) you see - nothing else. No prose, "
    "no commentary, no surrounding $$ delimiters, no code fences. "
    "If multiple equations are visible, separate them with '\\\\\\\\'. "
    "Use standard LaTeX commands (\\int, \\sum, \\frac, \\sqrt, "
    "subscripts, superscripts, Greek letters). If the image contains "
    "no mathematics, reply with exactly: NO_EQUATION"
)

_TRANSCRIBE_USER_PROMPT = "Transcribe this equation as LaTeX."


async def transcribe_equation(image_ref: str) -> dict:
    """
    Resolve ``image_ref`` to a stored image document and return its
    LaTeX transcription.

    Args:
        image_ref: A ``document_id`` of a previously-uploaded image.
            The model typically pulls this id from the inline
            ``[Attachments on this message: ...]`` marker that
            chat_context splices into past-turn content, the same way
            ``view_attachment`` does.

    Returns:
        ``{"latex": "<source>", "image_ref": "<doc_id>"}`` on success.
        ``{"error": "..."}`` on any failure path - missing doc,
        wrong content type, vLLM unreachable, empty output, etc.
    """
    import document_store  # lazy to avoid circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "error": "transcribe_equation requires an authenticated user context",
        }
    if not isinstance(image_ref, str) or not image_ref.strip():
        return {"error": "image_ref must be a non-empty document_id string"}

    path = document_store.get_document_file_path(user_email, image_ref)
    if not path:
        return {"error": f"document not found: {image_ref!r}"}

    ext = os.path.splitext(path)[1].lower()
    if ext not in _IMAGE_EXTS:
        return {
            "error": (
                f"transcribe_equation only accepts image documents; "
                f"{os.path.basename(path)} is {ext!r}"
            ),
        }

    import base64
    try:
        with open(path, "rb") as f:
            image_bytes = f.read()
    except OSError as e:
        return {"error": f"failed to read document: {e}"}

    subtype = "jpeg" if ext in (".jpg", ".jpeg") else ext.lstrip(".")
    data_url = f"data:image/{subtype};base64," + base64.b64encode(image_bytes).decode("ascii")

    messages = [
        {"role": "system", "content": _TRANSCRIBE_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _TRANSCRIBE_USER_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{VLLM_URL}/v1/chat/completions",
                json={
                    "model": VLLM_MODEL_NAME,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.2,
                    "stream": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
    except httpx.TimeoutException:
        return {"error": "vLLM request timed out"}
    except httpx.RequestError as e:
        return {"error": f"vLLM unreachable: {type(e).__name__}: {e}"}
    if response.status_code != 200:
        return {
            "error": (
                f"vLLM returned {response.status_code}: "
                f"{response.text[:200]}"
            ),
        }

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return {"error": "vLLM returned no choices"}
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return {"error": "vLLM returned empty content"}

    # Strip any wrapping the model slipped in despite the system prompt.
    stripped = content
    for fence in ("```latex", "```tex", "```"):
        if stripped.startswith(fence):
            stripped = stripped[len(fence):].lstrip("\n")
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")].rstrip("\n")
    # Drop leading/trailing $$ / $ pairs if the model still used them.
    for delim in ("$$", "$"):
        if stripped.startswith(delim) and stripped.endswith(delim) and len(stripped) > 2 * len(delim):
            stripped = stripped[len(delim) : -len(delim)].strip()
            break

    if stripped == "NO_EQUATION":
        return {
            "latex": None,
            "image_ref": image_ref,
            "message": "no equation detected in the image",
        }

    return {"latex": stripped, "image_ref": image_ref}
