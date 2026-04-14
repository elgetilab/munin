"""
Image handling for multimodal chat (§5) and the plot-critique feedback
loop (§3 deferred).

Responsibilities:

1. Turn whatever shape the frontend hands us for an image (inline
   ``data:...`` URL, ``document:<id>`` reference, or sandbox artifact
   URL) into a normalised ``data:image/<subtype>;base64,...`` string
   ready to drop into a vLLM ``image_url`` content block.
2. Enforce server-side size caps so a pathological 50 MB PNG cannot
   DoS the chat endpoint.
3. Synthesise the follow-up user message chat_service needs after a
   ``run_python`` tool_result that produced image artifacts, so the
   model sees its own plot on the next tool-loop iteration.

Design notes:

- We cap inline + document-backed images at **5 MB per image**, **3
  images per turn**. vLLM's vision layer resizes internally; the cap
  is just to keep memory and base64 overhead bounded here. A 5 MB PNG
  base64-encodes to ~6.7 MB of ASCII, which is fine to stream through
  one request.
- Vision is always **one-shot across turns**: the image only reaches
  the model on the turn it was sent or produced. Storage persistence
  (funnelled to the documents table) is the escape hatch for the
  model to request the image again later - see the Stage B deferred
  note in ``docs/future_features.md`` §5.
- ``build_tool_result_followup`` is pure bytes-in / message-out: it
  takes the tool_result payload the chat loop already has, fetches
  the artifact bytes from the sandbox over its HTTP surface, and
  returns a dict in OpenAI-compatible multimodal format. No chat
  state is touched.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any, Optional

import httpx


MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB per image
MAX_IMAGES_PER_TURN = 3                    # cap on attachments[]
SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:8090")

# Supported image subtypes. vLLM / Qwen3 vision accepts png, jpeg, and
# webp. Anything else we refuse at the boundary so the frontend gets a
# clear 400 rather than a mysterious model error.
_ALLOWED_IMAGE_SUBTYPES = {"png", "jpeg", "jpg", "webp"}


class VisionError(ValueError):
    """Raised for any unusable image input — size cap, bad shape, missing.
    Callers convert to HTTP 400 at the request boundary."""


# ----------------------------------------------------------------------------
# Data URL construction
# ----------------------------------------------------------------------------

def _normalise_subtype(subtype: str) -> str:
    subtype = subtype.lower().strip()
    if subtype == "jpg":
        return "jpeg"
    return subtype


def _build_data_url(image_bytes: bytes, subtype: str) -> str:
    subtype = _normalise_subtype(subtype)
    if subtype not in _ALLOWED_IMAGE_SUBTYPES:
        raise VisionError(f"unsupported image subtype: {subtype!r}")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"image exceeds {MAX_IMAGE_BYTES} byte cap "
            f"(got {len(image_bytes)} bytes)"
        )
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/{subtype};base64,{b64}"


# ----------------------------------------------------------------------------
# Inline data URL parsing
# ----------------------------------------------------------------------------

_DATA_URL_RE = re.compile(
    r"^data:image/(?P<subtype>[a-z0-9+.\-]+)(?:;[^,]*)?,(?P<payload>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_inline_data_url(url: str) -> tuple[bytes, str]:
    """
    Decode a ``data:image/<subtype>;base64,...`` URL into (bytes, subtype).

    Raises ``VisionError`` on malformed input or size cap.
    """
    m = _DATA_URL_RE.match(url)
    if not m:
        raise VisionError("content is not a data:image/... URL")
    subtype = _normalise_subtype(m.group("subtype"))
    if subtype not in _ALLOWED_IMAGE_SUBTYPES:
        raise VisionError(f"unsupported image subtype: {subtype!r}")
    payload = m.group("payload")
    # We only accept base64-encoded data URLs, not URL-encoded raw bytes.
    # vLLM / Qwen want the bytes to be base64 anyway so this saves a
    # re-encode on the way out.
    if ";base64" not in url.split(",", 1)[0]:
        raise VisionError(
            "only base64-encoded data URLs are accepted for images"
        )
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise VisionError(f"invalid base64 payload: {exc}") from exc
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"image exceeds {MAX_IMAGE_BYTES} byte cap "
            f"(got {len(image_bytes)} bytes)"
        )
    return image_bytes, subtype


# ----------------------------------------------------------------------------
# Reference resolvers
# ----------------------------------------------------------------------------

async def fetch_sandbox_artifact(
    conversation_id: str,
    artifact_id: str,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[bytes, str]:
    """
    Pull an artifact from sandbox-svc over the internal docker network
    and return (image_bytes, subtype). The sandbox serves artifacts
    with their original Content-Type from the manifest; we parse the
    subtype out of that header.

    Accepts an optional shared client so callers inside a tight loop
    don't re-establish the connection for every artifact.
    """
    url = f"{SANDBOX_URL}/artifacts/{conversation_id}/{artifact_id}"
    close_after = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_after = True
    try:
        r = await client.get(url)
    except httpx.RequestError as exc:
        raise VisionError(f"sandbox unreachable: {exc}") from exc
    finally:
        if close_after:
            await client.aclose()
    if r.status_code != 200:
        raise VisionError(
            f"sandbox artifact fetch returned {r.status_code} for "
            f"{conversation_id}/{artifact_id}"
        )
    ctype = (r.headers.get("content-type") or "").lower()
    if "/" in ctype:
        subtype = ctype.split("/", 1)[1].split(";", 1)[0].strip()
    else:
        subtype = "png"  # best-effort default
    return r.content, subtype


def read_user_document_image(path: str) -> tuple[bytes, str]:
    """
    Read an image previously stored via the documents API. ``path`` is
    the absolute on-disk path already scoped to the requesting user.
    Subtype is inferred from the filename extension.
    """
    if not os.path.isfile(path):
        raise VisionError(f"document file missing: {path}")
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        raise VisionError(
            f"document image exceeds {MAX_IMAGE_BYTES} byte cap "
            f"(got {size} bytes)"
        )
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return data, _normalise_subtype(ext or "png")


# ----------------------------------------------------------------------------
# Chat content helpers
# ----------------------------------------------------------------------------

def image_url_block(data_url: str) -> dict:
    """Return an OpenAI-style ``image_url`` content block."""
    return {"type": "image_url", "image_url": {"url": data_url}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def multimodal_user_content(text: str, data_urls: list[str]) -> list[dict]:
    """
    Package a plain-text message plus zero or more image data URLs into
    the ``content`` list shape vLLM expects for multimodal user turns.
    The text block always leads so the model has the instruction before
    the attached images.
    """
    parts: list[dict] = []
    if text:
        parts.append(text_block(text))
    for url in data_urls:
        parts.append(image_url_block(url))
    return parts


# ----------------------------------------------------------------------------
# view_attachment follow-up synthesis (§5 re-view)
# ----------------------------------------------------------------------------

_VIEW_ATTACHMENT_LEADER = (
    "[view_attachment] The attachment(s) you requested are shown below. "
    "Use them to answer the user's question; if you can no longer tell "
    "what the user was asking about, say so honestly."
)


def _view_attachment_requests_from_results(
    tool_results: list[dict],
) -> list[tuple[str, str]]:
    """
    Walk tool_results for view_attachment calls that succeeded and
    return ``[(document_id, filename)]`` so the caller can resolve
    each to bytes. Failed calls (``error`` key set) are skipped.
    """
    out: list[tuple[str, str]] = []
    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        if tr.get("name") != "view_attachment":
            continue
        result = tr.get("result") or {}
        if not isinstance(result, dict):
            continue
        if result.get("error"):
            continue
        doc_id = result.get("document_id")
        filename = result.get("filename") or ""
        if isinstance(doc_id, str) and doc_id:
            out.append((doc_id, filename))
    return out


def build_view_attachment_followup(
    tool_results: list[dict],
    user_email: str,
) -> Optional[dict]:
    """
    Synthesise the multimodal user message that carries images the
    model asked to see via ``view_attachment``. Mirrors
    ``build_tool_result_followup`` but reads bytes from the local
    documents store instead of fetching from the sandbox sidecar.

    Returns ``None`` when there are no successful view_attachment
    calls in this batch, so the chat loop can skip injecting the
    message entirely.
    """
    import document_store  # lazy - same rationale as elsewhere

    requests = _view_attachment_requests_from_results(tool_results)
    if not requests:
        return None
    if not user_email:
        return None

    data_urls: list[str] = []
    for doc_id, _filename in requests:
        if len(data_urls) >= MAX_IMAGES_PER_TURN:
            break
        path = document_store.get_document_file_path(user_email, doc_id)
        if not path:
            continue
        try:
            image_bytes, subtype = read_user_document_image(path)
            data_urls.append(_build_data_url(image_bytes, subtype))
        except VisionError:
            continue
    if not data_urls:
        return None
    return {
        "role": "user",
        "content": multimodal_user_content(_VIEW_ATTACHMENT_LEADER, data_urls),
    }


# ----------------------------------------------------------------------------
# Feedback-loop synthesis
# ----------------------------------------------------------------------------

# Images this loop injects. We don't include the stdout/stderr from the
# run_python result - the tool_result message already carries those, and
# duplicating the text would waste tokens.
_FEEDBACK_LOOP_LEADER = (
    "The plot you just generated (shown to you so you can judge whether "
    "axes, legends, colours, and scale match what the user asked for - "
    "if not, call run_python again with corrections; otherwise describe "
    "it in your answer):"
)


def _image_artifacts_from_tool_result(tool_result: dict) -> list[dict]:
    if not isinstance(tool_result, dict):
        return []
    if tool_result.get("name") != "run_python":
        return []
    result = tool_result.get("result") or {}
    if not isinstance(result, dict):
        return []
    artifacts = result.get("artifacts") or []
    return [
        art for art in artifacts
        if isinstance(art, dict)
        and (art.get("content_type") or "").lower().startswith("image/")
    ]


async def build_tool_result_followup(
    tool_results: list[dict],
    conversation_id: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict]:
    """
    Construct the synthetic follow-up user message the tool loop should
    append after ``run_python`` tool_results that produced image
    artifacts. Returns ``None`` if there were no image artifacts and
    the loop should just proceed as before.

    Output shape is OpenAI-compatible so vLLM / Qwen will treat it as a
    normal multimodal user turn::

        {
          "role": "user",
          "content": [
            {"type": "text", "text": "<leader sentence>"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            ...
          ]
        }
    """
    if not tool_results:
        return None

    data_urls: list[str] = []
    close_after = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_after = True
    try:
        for tr in tool_results:
            for art in _image_artifacts_from_tool_result(tr):
                if len(data_urls) >= MAX_IMAGES_PER_TURN:
                    break
                try:
                    image_bytes, subtype = await fetch_sandbox_artifact(
                        conversation_id=conversation_id,
                        artifact_id=art["id"],
                        client=client,
                    )
                    data_urls.append(_build_data_url(image_bytes, subtype))
                except VisionError:
                    # A single artifact failure should not block the
                    # feedback loop - log and keep going. chat_service
                    # will still see any other successful artifacts.
                    continue
    finally:
        if close_after:
            await client.aclose()

    if not data_urls:
        return None
    return {
        "role": "user",
        "content": multimodal_user_content(_FEEDBACK_LOOP_LEADER, data_urls),
    }
