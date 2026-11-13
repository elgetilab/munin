"""Tests for the lab-analysis plumbing on the retrieval side
(docs/agent-track/LAB-ANALYSIS-PLAN.md, Phase 0).

- Any file type can be uploaded; non-document types are DATA files: stored as
  uploaded, never embedded and never inlined into the prompt.
- Attaching a file to a message copies it into the conversation's sandbox
  inputs, and the model is told where code finds it. A sandbox problem
  becomes a note, never a failed turn.
- Tool results give the model a short figure_summary instead of the full
  figure description, and plot feedback covers every code tool, not only
  run_python (before 2026-11 a plot changed with edit_python was never shown
  back to the model).

Run standalone or under pytest:
    python backend/retrieval/tests/test_lab_plumbing.py
    pytest backend/retrieval/tests/test_lab_plumbing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="munin-test-lab-")
os.environ["USER_DOCS_DIR"] = os.path.join(_TMP, "user_docs")

import chat_service as cs  # noqa: E402
import document_store  # noqa: E402
import vision  # noqa: E402
from mcp.tools import sandbox  # noqa: E402


def _text_of(blocks):
    return "\n".join(b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text")


def _resolve(content, *, stored_name, data=b"x", put_result=None, doc_text="", ephemeral=False):
    """Run the resolver with the stored file, the sandbox and text extraction stubbed."""
    stored = os.path.join(_TMP, "stored", stored_name)
    os.makedirs(os.path.dirname(stored), exist_ok=True)
    with open(stored, "wb") as f:
        f.write(data)
    calls = []

    async def fake_put(cid, name, payload):
        calls.append((cid, name, payload))
        return put_result if put_result is not None else {"paths": [f"inputs/{name}"]}

    async def fake_text(user_email, document_id):
        return doc_text

    saved = (document_store.get_document_file_path, document_store.get_document_text,
             sandbox.put_input)
    document_store.get_document_file_path = lambda user_email, doc_id: stored
    document_store.get_document_text = fake_text
    sandbox.put_input = fake_put
    try:
        blocks, atts = asyncio.run(cs._resolve_user_content_images(
            content=content, user_email="u@x", conversation_id="conv-1", ephemeral=ephemeral))
    finally:
        (document_store.get_document_file_path, document_store.get_document_text,
         sandbox.put_input) = saved
    return blocks, atts, calls


def test_any_extension_is_a_data_file_except_documents_and_images():
    for name in ("plate.xlsx", "nanodrop.tsv", "fid", "exp1.zip", "run.itc", "spectrum.jws",
                 "gel.tif", "data.csv"):
        assert document_store.is_data_file(name), name
    for name in ("paper.pdf", "notes.txt", "README.md", "cv.docx", "a.png", "b.JPG"):
        assert not document_store.is_data_file(name), name


def test_upload_stores_a_data_file_without_embedding():
    res = asyncio.run(document_store.upload_document(
        filename="plate reader.xlsx", file_bytes=b"PK\x03\x04fake", user_email="u@x"))
    assert res["status"] == "stored" and res["kind"] == "data" and res["chunks"] == 0
    assert res["size_bytes"] == 8
    path = document_store.get_document_file_path("u@x", res["document_id"])
    assert path and open(path, "rb").read() == b"PK\x03\x04fake"


def test_data_file_is_described_not_inlined_and_reaches_the_sandbox():
    content = [{"type": "text", "text": "fit this"},
               {"type": "document", "document_id": "doc_1", "filename": "plate.xlsx"}]
    blocks, atts, calls = _resolve(content, stored_name="plate.xlsx", data=b"SECRET-BYTES" * 10)
    joined = _text_of(blocks)
    assert "[Attached data file: plate.xlsx (120 bytes)" in joined
    assert "'inputs/plate.xlsx'" in joined and "read-only" in joined
    assert "SECRET-BYTES" not in joined          # never inlined
    assert calls == [("conv-1", "plate.xlsx", b"SECRET-BYTES" * 10)]
    assert atts[0]["kind"] == "data" and atts[0]["document_id"] == "doc_1"


def test_text_document_is_inlined_and_also_given_to_the_sandbox():
    content = [{"type": "document", "document_id": "doc_2", "filename": "cd_export.txt"}]
    blocks, _, calls = _resolve(content, stored_name="cd_export.txt",
                                doc_text="XUNITS NANOMETERS\n250 -3.1")
    joined = _text_of(blocks)
    assert "XUNITS NANOMETERS" in joined
    assert "Code can read it at 'inputs/cd_export.txt'" in joined
    assert len(calls) == 1


def test_zip_lists_the_unpacked_paths():
    content = [{"type": "document", "document_id": "doc_3", "filename": "bruker.zip"}]
    paths = [f"inputs/bruker/exp{i}/fid" for i in range(25)]
    blocks, _, _ = _resolve(content, stored_name="bruker.zip", put_result={"paths": paths})
    joined = _text_of(blocks)
    assert "unpacked for code under inputs/" in joined
    assert "'inputs/bruker/exp0/fid'" in joined and "and 5 more" in joined


def test_sandbox_failure_is_a_note_not_an_error():
    content = [{"type": "document", "document_id": "doc_4", "filename": "big.h5"}]
    blocks, _, _ = _resolve(content, stored_name="big.h5",
                            put_result={"error": "sandbox refused the file: too large"})
    joined = _text_of(blocks)
    assert "could not be copied to the code sandbox" in joined and "too large" in joined


def test_temporary_chat_does_not_touch_the_sandbox():
    content = [{"type": "document", "document_id": "doc_5", "filename": "plate.xlsx"}]
    blocks, _, calls = _resolve(content, stored_name="plate.xlsx", ephemeral=True)
    assert calls == []
    assert "Attached data file: plate.xlsx" in _text_of(blocks)


def test_put_input_refuses_temporary_chats_without_a_request():
    res = asyncio.run(sandbox.put_input("ephemeral-abc", "x.csv", b"1"))
    assert "temporary" in res["error"]


def test_model_gets_the_figure_summary_not_the_full_description():
    payload = {"artifacts": [{
        "id": "a1", "filename": "fit.png", "content_type": "image/png", "size_bytes": 10,
        "sha256": "0" * 64, "version": 2, "previous_id": "a0", "kind": "file",
        "mtime_ns": 123, "figure": {"axes": [{"series": [{"x": list(range(5000))}]}]},
        "figure_summary": "85x60 mm, 300 dpi, png, 1 axes",
    }], "warnings": []}
    sandbox._slim_artifacts(payload)
    art = payload["artifacts"][0]
    assert "figure" not in art and "mtime_ns" not in art
    assert art["figure_summary"].startswith("85x60 mm") and art["version"] == 2
    assert "figure_hint" in payload and "warnings" not in payload


def test_plot_feedback_covers_every_code_tool():
    img = {"id": "a1", "content_type": "image/png"}
    for name in ("run_python", "edit_python", "compute"):
        tr = {"name": name, "result": {"artifacts": [img]}}
        assert vision._image_artifacts_from_tool_result(tr) == [img], name
    assert vision._image_artifacts_from_tool_result(
        {"name": "web_search", "result": {"artifacts": [img]}}) == []
    assert set(vision.PLOT_FEEDBACK_TOOLS) <= set(cs.SANDBOX_ARTIFACT_TOOLS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
