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


def _with_env(**env):
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items()})
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_text_only_model_is_detected():
    cases = [({"LLM_VISION": "", "VLLM_EXTRA_ARGS": "--language-model-only"}, False),
             ({"LLM_VISION": "", "VLLM_EXTRA_ARGS": ""}, True),
             ({"LLM_VISION": "0", "VLLM_EXTRA_ARGS": ""}, False),
             ({"LLM_VISION": "1", "VLLM_EXTRA_ARGS": "--language-model-only"}, True)]
    for env, expected in cases:
        saved = _with_env(**env)
        try:
            assert vision.model_sees_images() is expected, env
        finally:
            _restore_env(saved)


def test_text_only_model_gets_no_plot_image():
    # Sage-A1.5 (text-only) answered the plot image with a 400 that ended every
    # plotting turn (production, 2026-11-13). The figure_summary is the feedback.
    saved = _with_env(LLM_VISION="0")
    try:
        tr = [{"name": "run_python", "result": {"artifacts": [{"id": "a1", "content_type": "image/png"}]}}]
        assert asyncio.run(vision.build_tool_result_followup(tr, "conv-1")) is None
    finally:
        _restore_env(saved)


def test_text_only_model_gets_a_note_instead_of_an_attached_image():
    import base64
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    content = [{"type": "text", "text": "quantify this gel"},
               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}]
    calls = []

    async def fake_put(cid, name, payload):
        calls.append(name)
        return {"paths": [f"inputs/{name}"]}

    saved_env = _with_env(LLM_VISION="0")
    saved_put = sandbox.put_input
    sandbox.put_input = fake_put
    try:
        blocks, atts = asyncio.run(cs._resolve_user_content_images(
            content=content, user_email="u@x", conversation_id="conv-1", ephemeral=False))
    finally:
        sandbox.put_input = saved_put
        _restore_env(saved_env)
    assert not any(b.get("type") == "image_url" for b in blocks)
    joined = _text_of(blocks)
    assert "cannot view images" in joined and "inputs/pasted-" in joined
    assert len(calls) == 1 and atts and atts[0]["source"] == "inline"



# --- 2026-11-21: numeric text is a data file (LABKIT-016-PLAN.md) -------------

_DEFECT = "Index\tField [G]\tIntensity []\n" + "".join(
    f"{i + 1}\t{3300 + i * 0.1:.3f}\t{(-1) ** i * 1.139363:e}\n" for i in range(1400))


def test_numeric_table_detection():
    assert document_store.looks_like_numeric_table(_DEFECT)
    csv = "# exported\nwavelength,abs\n" + "".join(f"{200 + i},{0.01 * i:.3f}\n" for i in range(50))
    assert document_store.looks_like_numeric_table(csv)
    prose = "\n".join(f"Line {i} of a letter about the 2024 budget, page {i}." for i in range(60))
    assert not document_store.looks_like_numeric_table(prose)
    assert not document_store.looks_like_numeric_table("1 2\n3 4\n")   # too short to tell
    mixed = "\n".join(["Results: 1 2 3"] * 30 + ["1 2 3"] * 30)
    assert not document_store.looks_like_numeric_table(mixed)


def test_numeric_txt_is_a_data_file_with_a_preview():
    content = [{"type": "document", "document_id": "doc_n", "filename": "defect.txt"}]
    blocks, atts, calls = _resolve(content, stored_name="defect.txt",
                                   data=_DEFECT.encode(), doc_text=_DEFECT)
    joined = _text_of(blocks)
    assert "[Attached data file: defect.txt" in joined
    assert ("It has 1401 lines; the first 8 lines (the first 7 of 1400 data rows):" in joined
            and "Field [G]" in joined)
    assert "3300.000" in joined and "3399.900" not in joined     # head only
    assert len(joined) < 2500
    assert atts[0]["kind"] == "data" and len(calls) == 1


def test_numeric_txt_in_a_temporary_chat_is_still_inlined_but_capped():
    # No sandbox in a temporary chat, so the text is the only way in; the
    # token cap keeps it well below the window.
    content = [{"type": "document", "document_id": "doc_t", "filename": "defect.txt"}]
    saved = cs.chat_context.approx_tokens
    cs.chat_context.approx_tokens = lambda text: len(text)        # ~1 token per char
    try:
        blocks, _, calls = _resolve(content, stored_name="defect.txt", data=_DEFECT.encode(),
                                    doc_text=_DEFECT, ephemeral=True)
    finally:
        cs.chat_context.approx_tokens = saved
    joined = _text_of(blocks)
    assert calls == []
    assert "[document truncated to fit the context window" in joined
    assert len(joined) <= cs._inline_cap_tokens() + 500


def test_csv_data_file_note_has_a_preview():
    data = b"t_s,signal\n" + b"".join(f"{i},{i * 2.5:.4f}\n".encode() for i in range(300))
    content = [{"type": "document", "document_id": "doc_c", "filename": "decay.csv"}]
    blocks, _, _ = _resolve(content, stored_name="decay.csv", data=data)
    joined = _text_of(blocks)
    assert "It has 301 lines; the first 8 lines (the first 7 of 300 data rows):" in joined
    assert "t_s,signal" in joined and "6,15.0000" in joined and "7,17.5000" not in joined


# 2026-11-22 (LABKIT-018-PLAN.md, change 2): the model copied the 8 rows the
# preview showed and invented the rest. A file that fits the preview is shown
# whole, and a cut preview says how many data rows it holds of how many.
_THURBER_HEAD = "x,y\n-3.067E0,80.574E0\n-2.981E0,84.248E0\n"


def test_small_file_is_shown_whole():
    data = (_THURBER_HEAD + "".join(f"{-2.9 + i * 0.05:.3f}E0,{90 + i:.3f}E0\n" for i in range(35))).encode()
    assert len(data) < cs.PREVIEW_MAX_CHARS
    content = [{"type": "document", "document_id": "doc_w", "filename": "thurber.csv"}]
    blocks, _, _ = _resolve(content, stored_name="thurber.csv", data=data)
    joined = _text_of(blocks)
    assert "It has 38 lines, 37 data rows, shown whole:" in joined
    assert "-1.200E0,124.000E0" in joined          # the last row is there
    assert "the first" not in joined


def test_row_count_ignores_comments_and_a_missing_last_newline():
    data = b"# exported by the reader\nconc,abs\n" + b"\n".join(
        f"{i},{0.1 * i:.2f}".encode() for i in range(400))
    content = [{"type": "document", "document_id": "doc_r", "filename": "std.csv"}]
    blocks, _, _ = _resolve(content, stored_name="std.csv", data=data)
    joined = _text_of(blocks)
    assert "It has 402 lines; the first 8 lines (the first 6 of 400 data rows):" in joined, joined


def test_binary_data_file_gets_no_preview():
    content = [{"type": "document", "document_id": "doc_b", "filename": "spec.DTA"}]
    blocks, _, _ = _resolve(content, stored_name="spec.DTA", data=b"\x00\x01\x02" * 100)
    assert "lines; the first" not in _text_of(blocks)


def test_long_prose_is_capped_in_tokens():
    prose = "The committee met on Tuesday and discussed the budget. " * 4000   # ~220K chars
    content = [{"type": "document", "document_id": "doc_p", "filename": "minutes.txt"}]
    blocks, atts, _ = _resolve(content, stored_name="minutes.txt", doc_text=prose)
    joined = _text_of(blocks)
    assert "The committee met" in joined and atts[0].get("kind") != "data"
    assert "[document truncated to fit the context window; the whole file is in the code sandbox" in joined
    assert cs.chat_context.approx_tokens(joined) <= cs._inline_cap_tokens() + 300


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
