"""Tests for the current-script index and edit_python(file=...).

Why (docs/agent-track/LAB-BUGFIX-PLAN.md F1): history is replayed as
role/content only, so on a new turn the model no longer saw the code behind a
figure and rewrote it from memory, losing the title, size, order and file
name. script_index recovers the source that last wrote each output file from
the persisted tool calls; assemble_context shows it and edit_python can target
it by file name.

Run standalone or under pytest:
    python backend/retrieval/tests/test_script_index.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DB_DIR = tempfile.mkdtemp(prefix="munin-test-scriptidx-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

import script_index  # noqa: E402
from mcp.tools import sandbox  # noqa: E402


def _tok(text: str) -> int:
    return max(1, len(text) // 4)


def _run(code, files, error=None, timed_out=False):
    return {"name": "run_python", "arguments": {"code": code},
            "result": {"error": error, "timed_out": timed_out,
                       "artifacts": [{"filename": f} for f in files]}}


def _edit(resulting, files):
    return {"name": "edit_python", "arguments": {"edits": [{"old": "a", "new": "b"}]},
            "result": {"resulting_code": resulting,
                       "artifacts": [{"filename": f} for f in files]}}


PLOT = "fig, ax = plt.subplots(figsize=(3.35, 2.36))\nax.bar(x, y)\nfig.savefig('activity.png')\n"
PROBE = "from PIL import Image\nprint(Image.open('activity.png').size)\n"

HISTORY = [
    {"role": "user", "content": "plot it"},
    {"role": "assistant", "content": "done", "tool_calls": [
        _run("df = pd.read_csv('inputs/activity.csv')", []),
        _run(PLOT, ["activity.png"]),
        _run(PROBE, []),
    ]},
    {"role": "user", "content": "also a table"},
    {"role": "assistant", "content": "done", "tool_calls": [
        _run("df.to_csv('summary.csv')", ["summary.csv"]),
        _run("raise ValueError", ["broken.png"], error="ValueError"),
        {"name": "web_search", "arguments": {"query": "x"}, "result": {}},
    ]},
]


def _restore(saved):
    if saved is not None:
        sys.modules["chat_store"] = saved
    else:
        sys.modules.pop("chat_store", None)


def _cleanup():
    mod = sys.modules.get("chat_store")
    close = getattr(mod, "close_db", None) if mod is not None else None
    if close is not None:
        try:
            asyncio.run(close())
        except Exception:
            pass
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def teardown_module(module=None) -> None:  # noqa: ARG001
    _cleanup()


# --- pure index -------------------------------------------------------------

def test_index_maps_files_to_their_writing_run_newest_first():
    pairs = script_index.scripts_by_file(HISTORY)
    assert [n for n, _ in pairs] == ["summary.csv", "activity.png"], pairs
    assert dict(pairs)["activity.png"] == PLOT, "the probe that only READ the png must not win"


def test_failed_run_is_not_a_current_script():
    assert "broken.png" not in dict(script_index.scripts_by_file(HISTORY))


def test_timed_out_run_keeps_last_good_source():
    hist = HISTORY + [{"role": "assistant", "tool_calls": [
        _run("slow()", ["activity.png"], timed_out=True)]}]
    assert dict(script_index.scripts_by_file(hist))["activity.png"] == PLOT


def test_edit_python_contributes_its_resulting_code():
    hist = HISTORY + [{"role": "assistant", "tool_calls": [
        _edit(PLOT.replace("3.35", "4.0"), ["activity.png"])]}]
    pairs = script_index.scripts_by_file(hist)
    assert pairs[0][0] == "activity.png"
    assert "4.0" in pairs[0][1]


def test_script_for_file_matches_basename():
    hist = [{"role": "assistant", "tool_calls": [_run(PLOT, ["figs/activity.png"])]}]
    assert script_index.script_for_file(hist, "activity.png") == PLOT
    assert script_index.script_for_file(hist, "other.png") is None


def test_block_lists_newest_first_and_groups_shared_source():
    hist = [{"role": "assistant", "tool_calls": [
        _run(PLOT, ["activity.png", "activity.pdf"]),
        _run("df.to_csv('t.csv')", ["t.csv"]),
    ]}]
    block = script_index.current_scripts_block(hist, _tok)
    assert block.startswith(script_index.BLOCK_HEADER)
    assert block.index("### t.csv") < block.index("activity.png")
    assert block.count("fig.savefig") == 1, "one source for two files is shown once"
    assert "### activity.png, activity.pdf" in block or "### activity.pdf, activity.png" in block


def test_block_respects_file_and_token_caps():
    calls = [_run(f"x{i} = 1\n" * 50, [f"f{i}.png"]) for i in range(6)]
    hist = [{"role": "assistant", "tool_calls": calls}]
    block = script_index.current_scripts_block(hist, _tok, max_files=3)
    assert block.count("### ") == 3
    assert "### f5.png" in block and "### f3.png" in block and "### f2.png" not in block
    small = script_index.current_scripts_block(hist, _tok, max_tokens=_tok(script_index.BLOCK_HEADER) + 120)
    assert small.count("### ") == 1
    assert script_index.current_scripts_block(hist, _tok, max_tokens=10) == ""
    assert script_index.current_scripts_block([], _tok) == ""


def test_huge_old_script_does_not_push_out_a_recent_small_one():
    hist = [{"role": "assistant", "tool_calls": [
        _run("big = 1\n" * 4000, ["old.png"]),
        _run("small = 1", ["new.png"]),
    ]}]
    block = script_index.current_scripts_block(hist, _tok, max_tokens=500)
    assert "### new.png" in block and "### old.png" not in block


# --- edit_python(file=...) ----------------------------------------------------

def _stub_run(captured):
    async def _fake(code, timeout_s=30):
        captured["code"] = code
        return {"stdout": "", "artifacts": [{"filename": "activity.png"}]}
    return _fake


def _with_ctx(conv, fn):
    import mcp.context as ctx
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set(conv)
    try:
        return fn()
    finally:
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source(conv)


def test_file_targets_the_script_that_wrote_it_not_the_last_run():
    captured = {}
    orig = sandbox.run_python
    sandbox.run_python = _stub_run(captured)
    try:
        def go():
            sandbox._remember_file_sources("conv-f", PLOT, {"artifacts": [{"filename": "activity.png"}]})
            sandbox._remember_source("conv-f", PROBE)
            res = asyncio.run(sandbox.edit_python(
                [{"old": "figsize=(3.35, 2.36)", "new": "figsize=(4.0, 2.36)"}], file="activity.png"))
            assert "error" not in res, res
            assert "figsize=(4.0, 2.36)" in captured["code"] and "Image.open" not in captured["code"]
        _with_ctx("conv-f", go)
    finally:
        sandbox.run_python = orig


def test_file_falls_back_to_the_transcript():
    captured = {}
    orig = sandbox.run_python
    sandbox.run_python = _stub_run(captured)
    fake = types.SimpleNamespace()

    async def _msgs(conv_id, index):
        return HISTORY
    fake.get_messages_after_index = _msgs
    saved = sys.modules.get("chat_store")
    sys.modules["chat_store"] = fake
    try:
        def go():
            res = asyncio.run(sandbox.edit_python(
                [{"old": "ax.bar(x, y)", "new": "ax.bar(x, y, color='k')"}], file="activity.png"))
            assert "error" not in res, res
            assert "color='k'" in captured["code"]
        _with_ctx("conv-t", go)
    finally:
        _restore(saved)
        sandbox.run_python = orig


def test_unknown_file_is_a_clear_error():
    fake = types.SimpleNamespace()

    async def _msgs(conv_id, index):
        return HISTORY
    fake.get_messages_after_index = _msgs
    saved = sys.modules.get("chat_store")
    sys.modules["chat_store"] = fake
    try:
        def go():
            res = asyncio.run(sandbox.edit_python([{"old": "a", "new": "b"}], file="nope.png"))
            assert "error" in res and "nope.png" in res["error"]
        _with_ctx("conv-u", go)
    finally:
        _restore(saved)


def test_sandbox_reset_forgets_file_sources():
    sandbox._remember_file_sources("conv-r", PLOT, {"artifacts": [{"filename": "a.png"}]})
    sandbox.forget_source("conv-r")
    assert "conv-r" not in sandbox._file_sources


def test_schema_and_dispatcher_accept_file():
    from mcp.schemas import MCP_TOOLS
    props = MCP_TOOLS["edit_python"]["inputSchema"]["properties"]
    assert "file" in props and "file" not in MCP_TOOLS["edit_python"]["inputSchema"]["required"]
    import inspect
    from mcp import dispatchers
    assert "file=" in inspect.getsource(dispatchers._edit_python)

# --- assemble_context shows the block -----------------------------------------

def _conv(messages):
    msgs = []
    for i, m in enumerate(messages):
        mm = dict(m)
        mm.setdefault("content", "")
        mm["id"] = f"m{i}"
        mm["index_in_conversation"] = i
        msgs.append(mm)
    return {"id": "c-scripts", "messages": msgs, "summary": None, "summary_through_index": None}


def test_assemble_context_adds_current_scripts():
    import chat_context
    msgs, info = asyncio.run(chat_context.assemble_context(
        conversation=_conv(HISTORY), new_message={"role": "user", "content": "make it 85 mm wide"},
        system_prompt="sys", ephemeral=False))
    system = msgs[0]["content"]
    assert info is None
    assert script_index.BLOCK_HEADER in system and "### activity.png" in system
    assert "fig.savefig('activity.png')" in system
    assert "Image.open" not in system, "a run that wrote nothing is not a current script"


def test_assemble_context_ephemeral_and_plain_chats_have_no_block():
    import chat_context
    msgs, _ = asyncio.run(chat_context.assemble_context(
        conversation=_conv(HISTORY), new_message={"role": "user", "content": "x"},
        system_prompt="sys", ephemeral=True))
    assert script_index.BLOCK_HEADER not in msgs[0]["content"]
    plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    msgs, _ = asyncio.run(chat_context.assemble_context(
        conversation=_conv(plain), new_message={"role": "user", "content": "x"},
        system_prompt="sys", ephemeral=False))
    assert msgs[0]["content"] == "sys"


def test_block_survives_compaction():
    import chat_context
    huge = "x " * 50_000
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": huge} for i in range(6)]
    hist[1]["tool_calls"] = [_run(PLOT, ["activity.png"])]

    async def fake_summarize(conv_id, existing, dropped):
        return "summary"

    async def fake_update(*a, **k):
        return None
    orig = chat_context.summarize_messages, chat_context.update_summary
    chat_context.summarize_messages, chat_context.update_summary = fake_summarize, fake_update
    try:
        msgs, info = asyncio.run(chat_context.assemble_context(
            conversation=_conv(hist), new_message={"role": "user", "content": "x"},
            system_prompt="sys", ephemeral=False))
    finally:
        chat_context.summarize_messages, chat_context.update_summary = orig
    assert info is not None, "the test must actually compact"
    assert "### activity.png" in msgs[0]["content"]



if __name__ == "__main__":
    passed = 0
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and isinstance(fn, types.FunctionType):
                fn()
                print(f"  ok  {name}")
                passed += 1
        print(f"{passed} tests passed")
    finally:
        _cleanup()
