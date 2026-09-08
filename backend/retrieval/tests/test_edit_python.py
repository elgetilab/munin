"""Tests for edit_python, the patch-style code edit tool.

Why the tool exists (docs/agent-track/CODE-EDIT-TOOL-PLAN.md): the model
resends a median 91-line script to change 3 lines. 56% of successive
run_python pairs inside one turn are >80% identical, and 35% of all Python
source in the system is a verbatim copy of the call immediately before it.
That repetition was the largest single component of the prompts that
overflowed the context window in August 2026.

Why search/replace and not unified diff: update_artifact already ships a
correct unified-diff mode and the model used it in 1 of 66 calls (2%). Exact
line numbers and hunk lengths are the arithmetic models are worst at, so the
format here asks only for a verbatim snippet.

Run standalone or under pytest:
    python backend/retrieval/tests/test_edit_python.py
    pytest backend/retrieval/tests/test_edit_python.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set before anything can import chat_store (sandbox imports it lazily inside
# _base_source). chat_store reads this once at module import and otherwise
# defaults to /data/chats.db, i.e. the production database.
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-editpy-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

from mcp.tools import sandbox  # noqa: E402


def _restore_chat_store(saved) -> None:
    """Put back whatever was in sys.modules["chat_store"] before a test
    installed its fake.

    This used to be `sys.modules.pop("chat_store", None)`, which is wrong in
    a way that only shows up at exit. Other tests in this file import the
    REAL chat_store, which opens an aiosqlite connection; popping the entry
    left that module object unreachable by name while its NON-daemon worker
    thread stayed alive, so the interpreter hung in `threading._shutdown`
    after printing "18 tests passed". Worse, the orphan could not be closed:
    a later `import chat_store` builds a FRESH module whose `_db` is None,
    so calling close_db() on it closes nothing. Restoring the original entry
    keeps the connection reachable for _cleanup() below.
    """
    if saved is not None:
        sys.modules["chat_store"] = saved
    else:
        sys.modules.pop("chat_store", None)


def _cleanup() -> None:
    """Close the real chat_store connection, if one was ever opened, and drop
    the throwaway DB. See tests/README.md, "Two ways a test lies about
    itself"."""
    mod = sys.modules.get("chat_store")
    close = getattr(mod, "close_db", None) if mod is not None else None
    if close is not None:
        try:
            asyncio.run(close())
        except Exception:
            import traceback
            traceback.print_exc()
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def teardown_module(module=None) -> None:  # noqa: ARG001
    _cleanup()

SCRIPT = """import numpy as np

def simulate(n, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(size=n)

data = simulate(100)
print(data.mean())
"""


# --- apply_edits: the pure core -------------------------------------------

def test_single_edit_applies():
    out, stats = sandbox.apply_edits(SCRIPT, [{"old": "seed=0", "new": "seed=42"}])
    assert "seed=42" in out and "seed=0" not in out
    assert stats["edits_applied"] == 1


def test_multiple_edits_apply_in_order():
    """The median re-paste has 2 edit sites, so one-edit-per-call would only
    halve the round trips. Multi-edit is the common case, not an extra."""
    out, stats = sandbox.apply_edits(SCRIPT, [
        {"old": "simulate(100)", "new": "simulate(500)"},
        {"old": "print(data.mean())", "new": "print(data.mean(), data.std())"},
    ])
    assert "simulate(500)" in out and "data.std()" in out
    assert stats["edits_applied"] == 2


def test_edit_can_delete():
    out, _ = sandbox.apply_edits(SCRIPT, [{"old": "import numpy as np\n\n", "new": ""}])
    assert not out.startswith("import numpy")


def test_unmatched_old_is_an_error_with_a_hint():
    """A bare 'not found' is the fastest route back to re-pasting the whole
    script, which is the behaviour this tool exists to remove."""
    try:
        sandbox.apply_edits(SCRIPT, [{"old": "def simulate(n, seed=1):", "new": "x"}])
    except ValueError as e:
        msg = str(e)
        assert "not found" in msg
        assert "def simulate" in msg, "error should name the closest source line"
        return
    raise AssertionError("expected ValueError")


def test_ambiguous_old_is_an_error():
    """Patching the wrong occurrence yields code that runs and is subtly
    wrong, which is the worst failure available here. Refuse instead."""
    src = "x = 1\ny = 1\n"
    try:
        sandbox.apply_edits(src, [{"old": "= 1", "new": "= 2"}])
    except ValueError as e:
        assert "matches 2" in str(e)
        return
    raise AssertionError("expected ValueError")


def test_later_edit_sees_earlier_result():
    out, _ = sandbox.apply_edits("a = 1\n", [
        {"old": "a = 1", "new": "a = 2"},
        {"old": "a = 2", "new": "a = 3"},
    ])
    assert out.strip() == "a = 3"


def test_malformed_edits_rejected():
    for bad in (None, [], "nope", [{"old": "", "new": "x"}], [{"new": "x"}],
                [{"old": "a", "new": None}], ["notadict"]):
        try:
            sandbox.apply_edits(SCRIPT, bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_indentation_must_match_exactly():
    try:
        sandbox.apply_edits(SCRIPT, [{"old": "return rng.normal(size=n)", "new": "return None"}])
    except ValueError:
        raise AssertionError("a correctly-indented snippet should match")
    # ...but a dedented version of the same line must not silently match.
    out, _ = sandbox.apply_edits(SCRIPT, [{"old": "    return rng.normal(size=n)",
                                           "new": "    return None"}])
    assert "return None" in out


# --- base-source tracking --------------------------------------------------

def test_remember_and_forget_source():
    sandbox._remember_source("conv-a", "print(1)")
    assert asyncio.run(sandbox._base_source("conv-a")) == "print(1)"
    sandbox.forget_source("conv-a")
    # Cache cleared; the fallback will look at the transcript and find nothing
    # for this synthetic id.
    assert asyncio.run(sandbox._base_source("conv-a")) in (None, "")


def test_cache_is_bounded():
    for i in range(sandbox._LAST_SOURCE_MAX + 20):
        sandbox._remember_source(f"c{i}", "x = 1")
    assert len(sandbox._last_source) <= sandbox._LAST_SOURCE_MAX


def test_transcript_fallback_reads_last_run_python(monkeypatch=None):
    """A restart or a different worker must degrade to a slower path, not an
    error, so the cache miss falls back to the persisted transcript."""
    import types
    fake = types.SimpleNamespace()

    async def _msgs(conv_id, index):
        return [
            {"role": "assistant", "tool_calls": [
                {"name": "run_python", "arguments": {"code": "old = 1"}}]},
            {"role": "assistant", "tool_calls": [
                {"name": "web_search", "arguments": {"query": "irrelevant"}}]},
            {"role": "assistant", "tool_calls": [
                {"name": "run_python", "arguments": {"code": "newest = 2"}}]},
        ]

    fake.get_messages_after_index = _msgs
    _saved_chat_store = sys.modules.get("chat_store")
    sys.modules["chat_store"] = fake
    try:
        sandbox.forget_source("conv-fallback")
        got = asyncio.run(sandbox._base_source("conv-fallback"))
        assert got == "newest = 2", got
    finally:
        _restore_chat_store(_saved_chat_store)
        sandbox.forget_source("conv-fallback")


def test_fallback_chains_across_edits():
    """edit_python records the source it produced, so a cache miss after a
    chain of edits still resolves to what actually ran."""
    import types
    fake = types.SimpleNamespace()

    async def _msgs(conv_id, index):
        return [
            {"role": "assistant", "tool_calls": [
                {"name": "run_python", "arguments": {"code": "a = 1"}}]},
            {"role": "assistant", "tool_calls": [
                {"name": "edit_python", "arguments": {"edits": [{"old": "1", "new": "2"}]},
                 "result": {"resulting_code": "a = 2"}}]},
        ]

    fake.get_messages_after_index = _msgs
    _saved_chat_store = sys.modules.get("chat_store")
    sys.modules["chat_store"] = fake
    try:
        sandbox.forget_source("conv-chain")
        assert asyncio.run(sandbox._base_source("conv-chain")) == "a = 2"
    finally:
        _restore_chat_store(_saved_chat_store)
        sandbox.forget_source("conv-chain")


# --- edit_python end to end (sandbox stubbed) ------------------------------

def _stub_run(captured):
    async def _fake_run_python(code, timeout_s=30):
        captured["code"] = code
        captured["timeout_s"] = timeout_s
        return {"stdout": "ok", "artifacts": [], "edit_hint": "should be dropped"}
    return _fake_run_python


def test_no_base_source_is_a_clear_error():
    """Without a prior run there is nothing to patch, and the message must say
    what to do instead."""
    import mcp.context as ctx
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-empty")
    try:
        sandbox.forget_source("conv-empty")
        import types
        fake = types.SimpleNamespace()

        async def _none(conv_id, index):
            return []
        fake.get_messages_after_index = _none
        _saved_chat_store = sys.modules.get("chat_store")
        sys.modules["chat_store"] = fake
        try:
            res = asyncio.run(sandbox.edit_python([{"old": "a", "new": "b"}]))
        finally:
            _restore_chat_store(_saved_chat_store)
        assert "error" in res and "run_python first" in res["error"]
    finally:
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)


def test_edit_python_executes_result_and_reports_stats():
    import mcp.context as ctx
    captured = {}
    orig = sandbox.run_python
    sandbox.run_python = _stub_run(captured)
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-run")
    try:
        sandbox._remember_source("conv-run", SCRIPT)
        res = asyncio.run(sandbox.edit_python([{"old": "seed=0", "new": "seed=7"}]))
        assert "seed=7" in captured["code"], "the PATCHED source must be what runs"
        assert res["edits_applied"] == 1
        assert res["resulting_code"] == captured["code"]
        assert "edit_hint" not in res, "the re-send hint is for run_python only"
    finally:
        sandbox.run_python = orig
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source("conv-run")


def test_noop_edit_is_refused():
    import mcp.context as ctx
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-noop")
    try:
        sandbox._remember_source("conv-noop", "a = 1\n")
        res = asyncio.run(sandbox.edit_python([{"old": "a = 1", "new": "a = 1"}]))
        assert "error" in res and "unchanged" in res["error"]
    finally:
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source("conv-noop")


# --- registration ----------------------------------------------------------

def test_tool_is_registered_and_resident_on_code_only():
    from mcp.schemas import MCP_TOOLS, CORE_TOOLS
    assert "edit_python" in MCP_TOOLS
    spec = MCP_TOOLS["edit_python"]
    assert spec["is_concurrency_safe"] is False, "it mutates kernel state"
    assert "edits" in spec["inputSchema"]["properties"]
    assert spec["inputSchema"]["required"] == ["edits"]
    # Resident on the code profile, not core: run_python is already core and
    # tool_search can reach this elsewhere, so every non-coding turn should not
    # pay for its description.
    assert "edit_python" not in CORE_TOOLS


def test_dispatcher_registered():
    import mcp.dispatchers  # noqa: F401  (import registers every tool)
    from mcp._dispatch import registered_names
    assert "edit_python" in registered_names()


def test_run_python_description_leads_with_incremental_use():
    """Adoption is the risk, not correctness: the equivalent affordance for
    artifacts sits unused at 2% because nothing points at it."""
    from mcp.schemas import MCP_TOOLS
    d = MCP_TOOLS["run_python"]["description"]
    assert "edit_python" in d
    assert d.index("PERSISTENT") < d.index("pre-installed packages")


if __name__ == "__main__":
    import types as _t
    passed = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and isinstance(fn, _t.FunctionType):
                fn()
                print(f"  ok  {name}")
                passed += 1
        print(f"{passed} tests passed")
    finally:
        _cleanup()
