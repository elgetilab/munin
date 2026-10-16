"""
Tests for search/replace `edits` on update_artifact and compile_latex (v1.1).

Why: update_artifact's unified-diff mode (is_diff) was used in 1 of 66 calls,
and compile_latex's `diff` shares the same format. Line numbers and hunk
lengths are what models get wrong; a verbatim snippet is what they get right.
edit_python already switched to search/replace, so both artifact tools now
take `edits=[{old_str, new_str}]` through the same core (text_edits.py). The
diff paths still work for one release and return a `deprecation` note.

Everything runs through the real executor (alias repair, coercion, schema
validation, dispatcher) against a throwaway SQLite DB. The sandbox HTTP call
in compile_latex is replaced by a fake that records the source it was sent.
All artifacts and LaTeX here are synthetic.

    python backend/retrieval/tests/test_search_replace_edits.py
    pytest backend/retrieval/tests/test_search_replace_edits.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before chat_store is imported: it reads the path once and
# otherwise defaults to the production database.
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-sredits-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

import artifact_store  # noqa: E402
import chat_store  # noqa: E402
import text_edits  # noqa: E402
from mcp import executor  # noqa: E402
from mcp.context import current_conversation_id, current_user_email  # noqa: E402
from mcp.tools import latex  # noqa: E402

USER = "tester@example.org"

DOC = """# Draft

The results were significant.

## Methods

We used a sample of 40 participants.
"""

TEX = r"""\documentclass{beamer}
\title{Synthetic Deck}
\begin{document}
\frame{\titlepage}
\begin{frame}{One}
Hello world.
\end{frame}
\end{document}
"""

UNIFIED = """@@ -3,1 +3,1 @@
-The results were significant.
+The results were not significant.
"""


# --- harness -------------------------------------------------------------------

async def _conversation() -> str:
    conv = await chat_store.create_conversation(user_email=USER, persona="chat")
    current_user_email.set(USER)
    current_conversation_id.set(conv["id"])
    return conv["id"]


async def _artifact(content: str, title: str = "draft.md") -> tuple[str, str]:
    conv_id = await _conversation()
    art = await artifact_store.create_artifact(
        user_email=USER, conversation_id=conv_id, title=title,
        content=content, content_type="text/markdown",
    )
    return conv_id, art["id"]


async def _latest(conv_id: str, art_id: str) -> dict:
    return await artifact_store.get_artifact_version(
        user_email=USER, conversation_id=conv_id, artifact_id=art_id,
    )


def _call(tool: str, args: dict) -> dict:
    return asyncio.run(executor._dispatch_mcp_tool(tool, args))


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return dict(self._payload)


class _FakeClient:
    """Stands in for httpx.AsyncClient in latex.py; records each POST body."""
    sent: list[dict] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        _FakeClient.sent.append(json)
        return _FakeResponse({"success": True, "errors": [], "warnings": []})


def _with_fake_sandbox(fn):
    real = latex.httpx.AsyncClient
    latex.httpx.AsyncClient = _FakeClient  # type: ignore[assignment]
    _FakeClient.sent = []
    try:
        return fn()
    finally:
        latex.httpx.AsyncClient = real  # type: ignore[assignment]


# --- text_edits core -------------------------------------------------------------

def test_core_old_str_keys_and_messages():
    out, stats = text_edits.apply_edits(
        DOC, [{"old_str": "40 participants", "new_str": "42 participants"}],
        old_key="old_str", new_key="new_str",
    )
    assert "42 participants" in out and stats["edits_applied"] == 1
    try:
        text_edits.apply_edits(DOC, [{"old_str": "nope", "new_str": "x"}],
                               old_key="old_str", new_key="new_str")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "edit 1: 'old_str' not found" in str(e), e


def test_core_ambiguous_names_count():
    try:
        text_edits.apply_edits("a b a", [{"old_str": "a", "new_str": "c"}],
                               old_key="old_str", new_key="new_str")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "matches 2 places" in str(e), e


def test_edit_python_core_unchanged():
    """edit_python still speaks {old, new} through the shared helper."""
    from mcp.tools import sandbox
    out, stats = sandbox.apply_edits("x = 1\n", [{"old": "x = 1", "new": "x = 2"}])
    assert out == "x = 2\n" and stats["edits_applied"] == 1


# --- update_artifact ---------------------------------------------------------------

def test_update_artifact_edits_happy_path():
    async def go():
        conv_id, art_id = await _artifact(DOC)
        res = await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "base_version": 1,
            "edits": [
                {"old_str": "were significant", "new_str": "were not significant"},
                {"old_str": "40 participants", "new_str": "42 participants"},
            ],
        })
        return res, await _latest(conv_id, art_id)
    res, latest = asyncio.run(go())
    assert not res.get("error"), res
    assert res["version"] == 2 and res["edits_applied"] == 2
    assert res["applied_hunks"] is None and "deprecation" not in res
    assert "were not significant" in latest["content"]
    assert "42 participants" in latest["content"]


def test_update_artifact_edit_miss_saves_nothing_and_names_edit():
    async def go():
        conv_id, art_id = await _artifact(DOC)
        res = await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id,
            "edits": [
                {"old_str": "40 participants", "new_str": "42 participants"},
                {"old_str": "The result was significant", "new_str": "x"},
            ],
        })
        return res, await _latest(conv_id, art_id)
    res, latest = asyncio.run(go())
    err = res.get("error") or ""
    assert "edit 2: 'old_str' not found" in err, err
    assert '"old_str"' in err and '"new_str"' in err, err      # shape example
    assert "The results were significant." in err              # near miss
    assert latest["version"] == 1 and latest["content"] == DOC


def test_update_artifact_ambiguous_edit_rejected():
    async def go():
        _, art_id = await _artifact("same\nsame\n")
        return await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "edits": [{"old_str": "same", "new_str": "x"}],
        })
    err = asyncio.run(go()).get("error") or ""
    assert "edit 1: 'old_str' matches 2 places" in err, err


def test_update_artifact_content_and_edits_together_rejected():
    async def go():
        _, art_id = await _artifact(DOC)
        return await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "content": "whole new text",
            "edits": [{"old_str": "Draft", "new_str": "Final"}],
        })
    err = asyncio.run(go()).get("error") or ""
    assert "not both" in err, err


def test_update_artifact_neither_content_nor_edits_rejected():
    async def go():
        _, art_id = await _artifact(DOC)
        return await executor._dispatch_mcp_tool(
            "update_artifact", {"artifact_id": art_id})
    err = asyncio.run(go()).get("error") or ""
    assert "'edits'" in err and "'content'" in err, err


def test_update_artifact_empty_content_still_clears():
    """An explicit empty `content` is a full-content update, as before."""
    async def go():
        conv_id, art_id = await _artifact(DOC)
        res = await executor._dispatch_mcp_tool(
            "update_artifact", {"artifact_id": art_id, "content": ""})
        return res, await _latest(conv_id, art_id)
    res, latest = asyncio.run(go())
    assert not res.get("error"), res
    assert latest["version"] == 2 and latest["content"] == ""


def test_update_artifact_noop_edit_rejected():
    async def go():
        _, art_id = await _artifact(DOC)
        return await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "edits": [{"old_str": "Draft", "new_str": "Draft"}],
        })
    assert "unchanged" in (asyncio.run(go()).get("error") or "")


def test_update_artifact_stale_base_applies_to_edits():
    async def go():
        _, art_id = await _artifact(DOC)
        return await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "base_version": 7,
            "edits": [{"old_str": "Draft", "new_str": "Final"}],
        })
    assert "stale" in (asyncio.run(go()).get("error") or "")


def test_update_artifact_diff_still_works_with_deprecation():
    async def go():
        conv_id, art_id = await _artifact(DOC)
        res = await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "is_diff": True, "content": UNIFIED,
        })
        return res, await _latest(conv_id, art_id)
    res, latest = asyncio.run(go())
    assert not res.get("error"), res
    assert res["applied_hunks"] == 1 and res["edits_applied"] is None
    assert "deprecated" in res.get("deprecation", ""), res
    assert '"edits"' in res["deprecation"]
    assert "were not significant" in latest["content"]


def test_update_artifact_edits_and_is_diff_rejected():
    async def go():
        _, art_id = await _artifact(DOC)
        return await executor._dispatch_mcp_tool("update_artifact", {
            "artifact_id": art_id, "is_diff": True,
            "edits": [{"old_str": "Draft", "new_str": "Final"}],
        })
    assert "not both" in (asyncio.run(go()).get("error") or "")


def test_update_artifact_repairs_other_pair_spellings():
    """{old, new} learned from edit_python, a top-level old_str/new_str pair,
    and a `replacements` list all reach the tool as edits=[{old_str, new_str}]."""
    shapes = [
        lambda a: {"artifact_id": a, "edits": [{"old": "Draft", "new": "Final"}]},
        lambda a: {"artifact_id": a, "old_str": "Draft", "new_str": "Final"},
        lambda a: {"artifact_id": a, "replacements": [{"find": "Draft", "replace": "Final"}]},
        lambda a: {"id": a, "edits": {"old_string": "Draft", "new_string": "Final"}},
    ]
    for shape in shapes:
        async def go():
            conv_id, art_id = await _artifact(DOC)
            res = await executor._dispatch_mcp_tool("update_artifact", shape(art_id))
            return res, await _latest(conv_id, art_id)
        res, latest = asyncio.run(go())
        assert not res.get("error"), (shape(""), res)
        assert latest["content"].startswith("# Final"), latest["content"]


def test_update_artifact_top_level_pair_not_folded_when_content_sent():
    args = executor._normalize_aliases("update_artifact", {
        "artifact_id": "art_x", "content": "full", "old_str": "a", "new_str": "b"})
    assert "edits" not in args, args


def test_update_artifact_schema_error_shows_shape():
    err = _call("update_artifact", {"edits": [{"old_str": "a"}]}).get("error") or ""
    assert err.startswith("invalid arguments"), err
    assert '"edits": [{"old_str"' in err, err


# --- compile_latex ---------------------------------------------------------------------

def test_compile_latex_edits_resolve_before_compile():
    async def go():
        conv_id, art_id = await _artifact(TEX, title="deck.tex")
        res = await executor._dispatch_mcp_tool("compile_latex", {
            "artifact_id": art_id,
            "edits": [{"old_str": r"\documentclass{beamer}",
                       "new_str": r"\documentclass[aspectratio=169]{beamer}"}],
        })
        return res
    res = _with_fake_sandbox(lambda: asyncio.run(go()))
    assert not res.get("error"), res
    assert "deprecation" not in res
    sent = _FakeClient.sent[-1]["source"]
    assert sent.startswith(r"\documentclass[aspectratio=169]{beamer}"), sent
    assert r"\title{Synthetic Deck}" in sent


def test_compile_latex_edit_miss_compiles_nothing():
    async def go():
        _, art_id = await _artifact(TEX, title="deck.tex")
        return await executor._dispatch_mcp_tool("compile_latex", {
            "artifact_id": art_id,
            "edits": [{"old_str": r"\title{Other Deck}", "new_str": "x"}],
        })
    res = _with_fake_sandbox(lambda: asyncio.run(go()))
    err = res.get("error") or ""
    assert "edit 1: 'old_str' not found" in err, err
    assert r"\title{Synthetic Deck}" in err, err                # near miss
    assert '"old_str"' in err, err                              # shape example
    assert _FakeClient.sent == []


def test_compile_latex_diff_still_works_with_deprecation():
    diff = (
        "@@ -2,1 +2,1 @@\n"
        "-\\title{Synthetic Deck}\n"
        "+\\title{Renamed Deck}\n"
    )

    async def go():
        _, art_id = await _artifact(TEX, title="deck.tex")
        return await executor._dispatch_mcp_tool(
            "compile_latex", {"artifact_id": art_id, "diff": diff})
    res = _with_fake_sandbox(lambda: asyncio.run(go()))
    assert not res.get("error"), res
    assert "deprecated" in res.get("deprecation", ""), res
    assert r"\title{Renamed Deck}" in _FakeClient.sent[-1]["source"]


def test_compile_latex_edits_need_artifact_id():
    res = asyncio.run(latex.compile_latex(
        source=TEX, edits=[{"old_str": "a", "new_str": "b"}]))
    assert "'edits' is only valid with 'artifact_id'" in (res.get("error") or ""), res


def test_compile_latex_edits_and_diff_rejected():
    res = asyncio.run(latex.compile_latex(
        artifact_id="art_x", diff="@@ -1,1 +1,1 @@\n-a\n+b\n",
        edits=[{"old_str": "a", "new_str": "b"}]))
    assert "not both" in (res.get("error") or ""), res


def test_compile_latex_repairs_old_new_pairs():
    args = executor._normalize_aliases("compile_latex", {
        "artifact_id": "art_x", "edits": [{"old": "a", "new": "b"}]})
    assert args["edits"] == [{"old_str": "a", "new_str": "b"}], args
    untouched = executor._normalize_aliases("compile_latex", {
        "source": TEX, "old_str": "a", "new_str": "b"})
    assert "edits" not in untouched


# --- runner ------------------------------------------------------------------------------

def _cleanup() -> None:
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        traceback.print_exc()
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def teardown_module(module=None) -> None:  # noqa: ARG001
    _cleanup()


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"[PASS] {name}")
            except Exception:
                failed += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    finally:
        _cleanup()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
