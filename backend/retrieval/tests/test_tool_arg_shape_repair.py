"""
Regression tests for the two argument-shape repairs in mcp/executor.py that
the v1.1 tool review found (docs/v1.1-review/01-tools-agents.md section 4,
items 3 and 5).

F5. `edit_python` failed 81% of the time because the model reaches for the
argument names it knows from other editors: `old_str`/`new_str`,
`old_string`/`new_string` (+ `replace_all`), `find`/`replace`, or a
`replacements` list of such pairs. Each is an unambiguous spelling of one
`edits` entry, so the executor maps it onto `edits=[{old, new}]` before
validation. Ambiguous shapes (a bare `code`, or `code`/`new_code`) are NOT
guessed at; they still fail, but the error now shows the correct shape.

F6. Defensive hardening, not a measured fix: the failures the review counted
mostly predate the coercion deploy (248a419, 2026-08-27). `coerce_argument_types`
already rescued `top_k="5"` and `queries='["a", "b"]'`; it now also reads a
quoted number (`'"5"'`), a Python list repr with single quotes, a trailing
comma, or a JSON string encoded twice, via json first and ast.literal_eval
second (never eval). The result is still accepted only when it has the
declared type.

All inputs here are synthetic. Runs in-process, no network, no sandbox:

    python backend/retrieval/tests/test_tool_arg_shape_repair.py
    pytest backend/retrieval/tests/test_tool_arg_shape_repair.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import executor  # noqa: E402
from mcp._dispatch import _REGISTRY  # noqa: E402
from mcp.executor import coerce_argument_types as coerce  # noqa: E402


# --- helpers -----------------------------------------------------------------

def _dispatch_edit_python(arguments: dict) -> dict:
    """Run edit_python through the real alias + coercion + validation path,
    with the dispatcher swapped for a probe that records what it was given.
    Returns {"error": ...} on a validation failure, else {"captured": args}."""
    captured: dict = {}

    async def _probe(args: dict) -> dict:
        captured.update(args)
        return {"ok": True}

    saved = _REGISTRY.get("edit_python")
    _REGISTRY["edit_python"] = _probe
    try:
        result = asyncio.run(executor._dispatch_mcp_tool("edit_python", arguments))
    finally:
        if saved is not None:
            _REGISTRY["edit_python"] = saved
    if result.get("error"):
        return result
    return {"captured": captured}


def _validates(tool: str, arguments: dict) -> bool:
    args = coerce(tool, executor._normalize_aliases(tool, arguments))
    return not list(executor._VALIDATORS[tool].iter_errors(args))


ONE = [{"old": "x = 1", "new": "x = 2"}]


# --- F5: edit_python argument shapes -----------------------------------------

def test_old_str_new_str_becomes_edits():
    out = _dispatch_edit_python({"old_str": "x = 1", "new_str": "x = 2"})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_old_string_new_string_replace_all_becomes_edits():
    out = _dispatch_edit_python(
        {"old_string": "x = 1", "new_string": "x = 2", "replace_all": False})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_find_replace_becomes_edits():
    out = _dispatch_edit_python({"find": "x = 1", "replace": "x = 2"})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_flat_old_new_becomes_edits():
    out = _dispatch_edit_python({"old": "x = 1", "new": "x = 2"})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_empty_new_is_kept_as_a_delete():
    out = _dispatch_edit_python({"old_str": "print(x)\n", "new_str": ""})
    assert out.get("captured", {}).get("edits") == [{"old": "print(x)\n", "new": ""}], out


def test_replacements_list_becomes_edits():
    out = _dispatch_edit_python({"replacements": [
        {"old_str": "x = 1", "new_str": "x = 2"},
        {"find": "y = 3", "replace": "y = 4"},
    ]})
    assert out.get("captured", {}).get("edits") == [
        {"old": "x = 1", "new": "x = 2"}, {"old": "y = 3", "new": "y = 4"}], out


def test_edits_items_with_alias_keys_are_normalised():
    out = _dispatch_edit_python({"edits": [
        {"old_string": "x = 1", "new_string": "x = 2"},
        {"old": "y = 3", "new": "y = 4"},
    ]})
    assert out.get("captured", {}).get("edits") == [
        {"old": "x = 1", "new": "x = 2"}, {"old": "y = 3", "new": "y = 4"}], out


def test_single_edit_object_is_wrapped():
    out = _dispatch_edit_python({"edits": {"old": "x = 1", "new": "x = 2"}})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_canonical_edits_win_over_aliases():
    out = _dispatch_edit_python({"edits": ONE, "old_str": "zzz", "new_str": "qqq"})
    assert out.get("captured", {}).get("edits") == ONE, out


def test_bare_code_is_not_guessed_and_error_shows_shape():
    out = _dispatch_edit_python({"code": "x = 2\nprint(x)\n"})
    err = out.get("error") or ""
    assert err.startswith("invalid arguments"), out
    assert '"edits": [{"old":' in err, err


def test_code_new_code_is_not_guessed():
    out = _dispatch_edit_python({"code": "x = 1", "new_code": "x = 2"})
    err = out.get("error") or ""
    assert err.startswith("invalid arguments") and '"edits": [{"old":' in err, out


def test_half_pair_is_not_guessed():
    out = _dispatch_edit_python({"old_str": "x = 1"})
    assert (out.get("error") or "").startswith("invalid arguments"), out


def test_other_tools_get_no_edit_aliases():
    args = {"code": "print(1)", "old_str": "a", "new_str": "b"}
    assert executor._normalize_aliases("run_python", args) is args


# --- F6: stringified scalars and arrays --------------------------------------

def test_quoted_number_top_k_is_coerced():
    out = coerce("search_user_docs", {"query": "q", "top_k": '"5"'})
    assert out["top_k"] == 5 and isinstance(out["top_k"], int), out


def test_single_quoted_number_top_k_is_coerced():
    out = coerce("search_user_docs", {"query": "q", "top_k": " '5'\n"})
    assert out["top_k"] == 5 and isinstance(out["top_k"], int), out


def test_python_repr_queries_is_coerced():
    out = coerce("web_search", {"queries": "['topic alpha review', 'topic beta 2024']"})
    assert out["queries"] == ["topic alpha review", "topic beta 2024"], out


def test_mixed_quote_queries_with_apostrophe_is_coerced():
    out = coerce("semantic_scholar_search",
                 {"queries": "\n['alpha beta', \"Smith's law\"]\n"})
    assert out["queries"] == ["alpha beta", "Smith's law"], out


def test_trailing_comma_queries_is_coerced():
    out = coerce("web_search", {"queries": '["alpha", "beta",]'})
    assert out["queries"] == ["alpha", "beta"], out


def test_double_encoded_queries_is_coerced():
    out = coerce("web_search", {"queries": '"[\\"alpha\\", \\"beta\\"]"'})
    assert out["queries"] == ["alpha", "beta"], out


def test_python_repr_set_plan_items_validates():
    args = {"items": "[{'title': 'Step one', 'status': 'in_progress'}, "
                     "{'title': 'Step two'}]"}
    out = coerce("set_plan", args)
    assert isinstance(out["items"], list) and out["items"][0]["title"] == "Step one", out
    assert _validates("set_plan", args)


# --- F6: what must still NOT be coerced --------------------------------------

def test_non_literal_string_still_rejected():
    out = coerce("search", {"query": "x", "filters": "year:2023"})
    assert out["filters"] == "year:2023", out


def test_expression_is_never_evaluated():
    for s in ("__import__('os').getcwd()", "[1+1]", "1+2", "len('abc')"):
        out = coerce("web_search", {"queries": s})
        assert out["queries"] == s, (s, out)
        out = coerce("search_user_docs", {"query": "q", "top_k": s})
        assert out["top_k"] == s, (s, out)


def test_wrong_literal_type_still_rejected():
    assert coerce("web_search", {"queries": "('a', 'b')"})["queries"] == "('a', 'b')"
    assert coerce("web_search", {"queries": "{'a': 1}"})["queries"] == "{'a': 1}"
    assert coerce("search_user_docs", {"query": "q", "top_k": "'5.5'"})["top_k"] == "'5.5'"
    assert coerce("search_user_docs", {"query": "q", "top_k": "True"})["top_k"] == "True"
    assert coerce("search_user_docs", {"query": "q", "top_k": "'five'"})["top_k"] == "'five'"


def test_deeply_nested_string_is_left_for_the_validator():
    # json.loads raises RecursionError (not ValueError) on deep nesting; it
    # must not escape coercion, or the caller sees a crash, not a rejection.
    for s in ("[" * 5000, "[" * 5000 + "]" * 5000, '{"a":' * 5000):
        out = coerce("web_search", {"queries": s})
        assert out["queries"] == s
        out = coerce("search_user_docs", {"query": "q", "top_k": s})
        assert out["top_k"] == s
        assert not _validates("web_search", {"queries": s})


def test_wellformed_args_unchanged():
    args = {"queries": ["alpha", "beta"], "top_k": 5}
    assert coerce("web_search", args) is args


# --- runner ------------------------------------------------------------------

def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception:
            failed += 1
            print(f"[FAIL] {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
