"""Tests for inlining an attached text document into the user turn.

Guards the fix for the CV-translation failure (2026-07-08): a PDF/DOCX
attached in the composer was uploaded + embedded but its text never reached
the model, because ``_resolve_user_content_images`` only understood image
blocks. It now resolves a ``{type:"document", document_id}`` block to the
document's extracted text as a ``text`` block.

Run standalone or under pytest:
    python backend/retrieval/tests/test_document_attachment.py
    pytest backend/retrieval/tests/test_document_attachment.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_context as cc  # noqa: E402
import chat_service as cs  # noqa: E402
import document_store  # noqa: E402
import vision  # noqa: E402


def _resolve(content, doc_text):
    """Run the resolver with document_store.get_document_text stubbed."""
    orig = document_store.get_document_text

    async def fake(user_email, document_id):
        return doc_text

    document_store.get_document_text = fake
    try:
        return asyncio.run(
            cs._resolve_user_content_images(
                content=content,
                user_email="u@x",
                conversation_id="c1",
                ephemeral=True,
            )
        )
    finally:
        document_store.get_document_text = orig


def _text_of(blocks):
    return "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )


def test_document_text_is_inlined_as_text_block():
    content = [
        {"type": "text", "text": "translate this"},
        {"type": "document", "document_id": "doc_abc", "filename": "CV.pdf"},
    ]
    blocks, attachments = _resolve(content, "Person083\nCurriculum Vitae\n...")
    joined = _text_of(blocks)
    assert "translate this" in joined
    assert "Person083" in joined          # the actual file contents reached the model
    assert "[Attached document: CV.pdf]" in joined
    assert attachments == [
        {
            "document_id": "doc_abc",
            "filename": "CV.pdf",
            "content_type": "text/plain",
            "source": "document",
        }
    ]


def test_oversized_document_is_truncated_with_note():
    big = "A" * (cs.MAX_DOC_INLINE_CHARS + 5_000)
    content = [{"type": "document", "document_id": "doc_big", "filename": "big.txt"}]
    blocks, _ = _resolve(content, big)
    joined = _text_of(blocks)
    assert "[document truncated to fit the context window]" in joined
    # the injected text (minus the header/note) is capped near the limit
    assert len(joined) <= cs.MAX_DOC_INLINE_CHARS + 200


def test_missing_document_raises():
    content = [{"type": "document", "document_id": "doc_gone", "filename": "x.pdf"}]
    raised = False
    try:
        _resolve(content, None)  # get_document_text returns None -> not found
    except vision.VisionError:
        raised = True
    assert raised


def test_empty_document_records_attachment_with_note():
    content = [{"type": "document", "document_id": "doc_scan", "filename": "scan.pdf"}]
    blocks, attachments = _resolve(content, "")  # e.g. a scanned PDF, no text
    joined = _text_of(blocks)
    assert "no extractable text" in joined
    assert len(attachments) == 1 and attachments[0]["document_id"] == "doc_scan"


def test_reload_marker_skips_text_docs_but_keeps_images():
    # On reload, images (one-shot, not in the text) get a "call view_attachment"
    # re-view hint; text docs are already inlined into the content, so they must
    # NOT get the hint (view_attachment is image-only and would error).
    doc_msg = {
        "content": "translate this\n\n[Attached document: CV.pdf]\n\n...cv...",
        "attachments": [
            {"document_id": "doc_abc", "filename": "CV.pdf",
             "content_type": "text/plain", "source": "document"},
        ],
    }
    assert cc._augment_with_attachments(doc_msg) == doc_msg["content"]  # unchanged

    img_msg = {
        "content": "what is this?",
        "attachments": [
            {"document_id": "doc_img", "filename": "chart.png",
             "content_type": "image/png", "source": "inline"},
        ],
    }
    out = cc._augment_with_attachments(img_msg)
    assert "view_attachment" in out and "doc_img" in out

    mixed = {
        "content": "compare",
        "attachments": [
            {"document_id": "doc_txt", "filename": "notes.txt",
             "content_type": "text/plain", "source": "document"},
            {"document_id": "doc_png", "filename": "fig.png",
             "content_type": "image/png", "source": "inline"},
        ],
    }
    out = cc._augment_with_attachments(mixed)
    assert "doc_png" in out and "doc_txt" not in out  # only the image is re-viewable


if __name__ == "__main__":
    import types
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"{passed} tests passed")
