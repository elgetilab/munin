"""
Standalone tests for tool-result truncation (tool_result.py, P1 #15).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_tool_result.py
Or locally:
    python backend/retrieval/tests/test_tool_result.py

The load-bearing guarantee: truncate_tool_result ALWAYS returns valid
JSON within the char limit. Every case round-trips the output through
json.loads to prove it.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool_result import truncate_tool_result  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _valid_json_within(s: str, limit: int) -> bool:
    """Output must parse as JSON and respect the budget."""
    try:
        json.loads(s)
    except Exception:
        return False
    return len(s) <= limit


# ---------------------------------------------------------------------------
# Tier 1: fits unchanged
# ---------------------------------------------------------------------------

def test_small_result_passes_through() -> bool:
    result = {"papers": [{"title": "A short paper", "doi": "10.1/x"}]}
    out = truncate_tool_result(result, limit=8000)
    return _check(
        "small result serialised unchanged",
        out == json.dumps(result, default=str),
    )


def test_scalars_handled() -> bool:
    ok = True
    for scalar in (42, None, "a short string", True, 3.14):
        out = truncate_tool_result(scalar, limit=8000)
        if not _valid_json_within(out, 8000) or json.loads(out) != scalar:
            ok = False
    return _check("scalar results (int/None/str/bool/float) round-trip", ok)


# ---------------------------------------------------------------------------
# Tier 2: trim the dominant list
# ---------------------------------------------------------------------------

def test_dict_with_list_trims_items() -> bool:
    # 200 papers, each padded so the whole thing far exceeds the limit.
    result = {
        "query": "polymer crystallization",
        "papers": [
            {"title": f"Paper {i}", "abstract": "x" * 200, "doi": f"10.1/{i}"}
            for i in range(200)
        ],
    }
    limit = 8000
    out = truncate_tool_result(result, limit=limit)
    if not _valid_json_within(out, limit):
        return _check("dict-with-list trims items", False, "invalid/oversized")
    parsed = json.loads(out)
    kept = len(parsed.get("papers", []))
    return _check(
        "dict-with-list trims items, keeps valid JSON + _truncated note",
        0 < kept < 200
        and "_truncated" in parsed
        and parsed.get("query") == "polymer crystallization",
        f"kept={kept}, keys={list(parsed.keys())}",
    )


def test_bare_list_trims_items() -> bool:
    result = [{"n": i, "pad": "y" * 200} for i in range(200)]
    limit = 8000
    out = truncate_tool_result(result, limit=limit)
    if not _valid_json_within(out, limit):
        return _check("bare list trims items", False, "invalid/oversized")
    parsed = json.loads(out)
    # Last element is the truncation note string.
    has_note = isinstance(parsed[-1], str) and "omitted" in parsed[-1]
    return _check(
        "bare list trims items and appends a note element",
        isinstance(parsed, list) and 0 < len(parsed) < 201 and has_note,
        f"len={len(parsed)}",
    )


# ---------------------------------------------------------------------------
# Tier 3: generic preview wrap
# ---------------------------------------------------------------------------

def test_huge_string_field_wraps() -> bool:
    """A dict with no trimmable list, dominated by one giant string,
    falls through to the generic preview wrap."""
    result = {"status": "ok", "body": "z" * 50000}
    limit = 8000
    out = truncate_tool_result(result, limit=limit)
    if not _valid_json_within(out, limit):
        return _check("huge string field wraps", False, "invalid/oversized")
    parsed = json.loads(out)
    return _check(
        "huge non-list result falls back to generic preview wrap",
        parsed.get("_truncated") is True
        and "preview" in parsed
        and isinstance(parsed["preview"], str),
        f"keys={list(parsed.keys())}",
    )


def test_oversized_single_item_dropped_to_empty_list() -> bool:
    """A list whose single item alone exceeds the limit is dropped,
    leaving an empty list + note — still valid, correctly-typed JSON,
    and a better outcome than the generic wrap (the dict shape and the
    omission count survive)."""
    result = {"matches": [{"huge": "q" * 50000}]}
    limit = 8000
    out = truncate_tool_result(result, limit=limit)
    if not _valid_json_within(out, limit):
        return _check(
            "oversized single item dropped", False, "invalid/oversized"
        )
    parsed = json.loads(out)
    return _check(
        "a single over-budget list item is dropped, leaving [] + note",
        parsed.get("matches") == []
        and "_truncated" in parsed
        and "1 of 1" in parsed["_truncated"],
        f"parsed={parsed}",
    )


def test_emptied_list_still_oversized_wraps() -> bool:
    """When even an emptied list can't save the structure (a huge
    non-list sibling field), the helper falls through to the generic
    preview wrap."""
    result = {"giant": "g" * 50000, "items": [{"a": 1}, {"b": 2}]}
    limit = 8000
    out = truncate_tool_result(result, limit=limit)
    if not _valid_json_within(out, limit):
        return _check("emptied list still oversized wraps", False, "bad")
    parsed = json.loads(out)
    return _check(
        "huge non-list sibling forces the generic wrap even with a list present",
        parsed.get("_truncated") is True and "preview" in parsed,
        f"keys={list(parsed.keys())}",
    )


def test_huge_bare_string_wraps() -> bool:
    out = truncate_tool_result("w" * 50000, limit=8000)
    parsed_ok = _valid_json_within(out, 8000)
    return _check(
        "a huge bare string result wraps to valid JSON within limit",
        parsed_ok and json.loads(out).get("_truncated") is True,
    )


# ---------------------------------------------------------------------------
# Invariant sweep
# ---------------------------------------------------------------------------

def test_all_outputs_are_valid_json_within_limit() -> bool:
    """The core guarantee, swept across a range of shapes and limits."""
    cases = [
        {"a": 1},
        [1, 2, 3],
        {"results": [{"k": "v" * 100} for _ in range(500)]},
        {"text": "t" * 99999},
        ["item" * 5000 for _ in range(10)],
        "plain",
        12345,
    ]
    ok = True
    for c in cases:
        for limit in (500, 2000, 8000):
            out = truncate_tool_result(c, limit=limit)
            if not _valid_json_within(out, limit):
                ok = False
                print(f"  FAIL: shape={type(c).__name__} limit={limit} "
                      f"len(out)={len(out)}")
    return _check(
        "every output is valid JSON within the limit (shape x limit sweep)",
        ok,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_small_result_passes_through,
    test_scalars_handled,
    test_dict_with_list_trims_items,
    test_bare_list_trims_items,
    test_huge_string_field_wraps,
    test_oversized_single_item_dropped_to_empty_list,
    test_emptied_list_still_oversized_wraps,
    test_huge_bare_string_wraps,
    test_all_outputs_are_valid_json_within_limit,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
