"""
Standalone unit tests for §28 tag-scoped search.

Verifies the deterministic plumbing that connects:
    request body `tags` field
    → chat_service._normalize_query_tags
    → mcp.context.current_query_tags ContextVar
    → mcp.tools.papers._build_tag_filter
    → Qdrant Filter object

PLUS the system-prompt block that makes active tags visible to the
model so it can honestly answer "what knowledge is attached".

Runs in-process inside the retrieval container (no live service
required) via:

    docker exec munin-retrieval python /app/tests/test_query_tags.py

Exit code 0 = pass, non-zero = fail.

These are pure-function tests — nothing hits Qdrant, vLLM, or the
network. The companion behavioural test (model actually reports
attached scope to the user) is not deterministic and lives in
scripts/flakiness-suite.py.
"""

from __future__ import annotations

import sys
import traceback

# Retrieval code lives at /app inside the container.
sys.path.insert(0, "/app")

from chat_service import _normalize_query_tags, build_active_tags_block  # noqa: E402
from mcp.context import current_query_tags  # noqa: E402
from mcp.tools.papers import _build_tag_filter  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal assertion helpers
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = PASS if ok else FAIL
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# 1. _normalize_query_tags — input sanitisation
# ---------------------------------------------------------------------------

def test_normalize_drops_garbage() -> bool:
    raw = [
        {"kind": "group", "value": "zeitler"},        # keep
        {"kind": "GROUP", "value": "ZEITLER"},        # keep, lowercased
        {"kind": "topic", "value": "  nmr  "},        # keep, stripped
        {"kind": "unknown", "value": "x"},            # drop, bad kind
        {"kind": "group", "value": ""},               # drop, empty
        {"kind": "group", "value": None},             # drop, None
        {"kind": "group"},                            # drop, missing value
        {"value": "zeitler"},                         # drop, missing kind
        "not-a-dict",                                 # drop, wrong type
        None,                                         # drop, None
    ]
    out = _normalize_query_tags(raw)
    expected = [
        {"kind": "group", "value": "zeitler"},
        {"kind": "group", "value": "zeitler"},
        {"kind": "topic", "value": "nmr"},
    ]
    return _check(
        "normalize keeps valid tags + drops garbage",
        out == expected,
        f"got {out!r}",
    )


def test_normalize_empty_returns_none() -> bool:
    cases = [
        (None, "None input"),
        ([], "empty list"),
        ("zeitler", "string input"),
        ({"kind": "group", "value": "zeitler"}, "single dict (not list)"),
        ([{"kind": "bad", "value": "x"}], "list with only bad entries"),
    ]
    for inp, desc in cases:
        if _normalize_query_tags(inp) is not None:
            return _check(
                "normalize returns None on garbage / empty",
                False,
                f"{desc}: expected None",
            )
    return _check("normalize returns None on garbage / empty", True)


# ---------------------------------------------------------------------------
# 2. _build_tag_filter — Qdrant filter shape
# ---------------------------------------------------------------------------

def test_filter_none_for_empty_tags() -> bool:
    return _check(
        "_build_tag_filter returns None for empty input",
        _build_tag_filter(None) is None and _build_tag_filter([]) is None,
    )


def test_filter_group_tag_targets_array_member() -> bool:
    """A `group` tag must filter on the array-member key
    `contributors[].group_slug`, not the scalar `contributor_username`
    or anything else. This is what makes the same-DOI-multi-contributor
    payload merge actually queryable."""
    f = _build_tag_filter([{"kind": "group", "value": "zeitler"}])
    if f is None:
        return _check("group tag → array-member filter", False, "got None")
    must = getattr(f, "must", None) or []
    if len(must) != 1:
        return _check(
            "group tag → array-member filter",
            False,
            f"expected 1 condition, got {len(must)}",
        )
    cond = must[0]
    key_ok = getattr(cond, "key", None) == "contributors[].group_slug"
    match = getattr(cond, "match", None)
    val_ok = getattr(match, "value", None) == "zeitler"
    return _check(
        "group tag → array-member filter on contributors[].group_slug",
        key_ok and val_ok,
        f"key={getattr(cond, 'key', None)!r} value={getattr(match, 'value', None)!r}",
    )


def test_filter_topic_tag_targets_topic_slug() -> bool:
    f = _build_tag_filter([{"kind": "topic", "value": "nmr-of-membrane-proteins"}])
    cond = (getattr(f, "must", None) or [None])[0]
    key_ok = getattr(cond, "key", None) == "topic_slug"
    match = getattr(cond, "match", None)
    val_ok = getattr(match, "value", None) == "nmr-of-membrane-proteins"
    return _check(
        "topic tag → topic_slug filter",
        key_ok and val_ok,
        f"key={getattr(cond, 'key', None)!r}",
    )


def test_filter_contributor_tag_targets_username() -> bool:
    f = _build_tag_filter([{"kind": "contributor", "value": "zeitler"}])
    cond = (getattr(f, "must", None) or [None])[0]
    key_ok = getattr(cond, "key", None) == "contributors[].username"
    return _check(
        "contributor tag → contributors[].username filter",
        key_ok,
        f"key={getattr(cond, 'key', None)!r}",
    )


def test_filter_multiple_tags_and_combine() -> bool:
    """Two tags must produce a Qdrant `must` with two conditions —
    semantically AND. If we ever switch to `should` (OR) by accident,
    this test catches it."""
    f = _build_tag_filter([
        {"kind": "group", "value": "zeitler"},
        {"kind": "topic", "value": "nmr-of-membrane-proteins"},
    ])
    must = getattr(f, "must", None) or []
    return _check(
        "multiple tags AND-combine in Qdrant `must`",
        len(must) == 2,
        f"expected 2 must-conditions, got {len(must)}",
    )


def test_filter_unknown_kind_silently_ignored() -> bool:
    """An unknown kind should yield no conditions for that entry. This
    is the second line of defence after _normalize_query_tags — if a
    bypass path ever passes raw tags directly to _build_tag_filter,
    we still don't blow up."""
    f = _build_tag_filter([{"kind": "doi", "value": "10.1234/x"}])
    return _check(
        "unknown tag kind silently ignored at filter layer",
        f is None,
        f"expected None, got {f!r}",
    )


# ---------------------------------------------------------------------------
# 3. ContextVar handoff — the bridge between chat_service and paper_search
# ---------------------------------------------------------------------------

def test_contextvar_default_none() -> bool:
    return _check(
        "current_query_tags defaults to None outside any request",
        current_query_tags.get() is None,
    )


def test_contextvar_roundtrip() -> bool:
    """Set the ContextVar, read it back, reset it. paper_search relies
    on this being a clean .set() / .get() pair."""
    token = current_query_tags.set([{"kind": "group", "value": "zeitler"}])
    try:
        got = current_query_tags.get()
    finally:
        current_query_tags.reset(token)
    return _check(
        "current_query_tags round-trips a tag list",
        got == [{"kind": "group", "value": "zeitler"}]
        and current_query_tags.get() is None,
    )


# ---------------------------------------------------------------------------
# 4. System-prompt block — model awareness of active scope
# ---------------------------------------------------------------------------

def test_active_tags_block_none_for_empty() -> bool:
    return _check(
        "active-tags block is None when no tags are set",
        build_active_tags_block(None) is None
        and build_active_tags_block([]) is None,
    )


def test_active_tags_block_mentions_each_tag_value() -> bool:
    """Every tag value must appear literally in the block — that's the
    contract that lets the model name the active scope back to the
    user when asked. If we ever start abbreviating or hashing values,
    this test catches it."""
    block = build_active_tags_block([
        {"kind": "group", "value": "zeitler"},
        {"kind": "topic", "value": "nmr-of-membrane-proteins"},
        {"kind": "contributor", "value": "alice"},
    ])
    must_contain = [
        "ACTIVE SCOPE TAGS",
        "zeitler",
        "nmr-of-membrane-proteins",
        "alice",
        "research-group",
        "topic-cluster",
        "individual-contributor",
    ]
    missing = [s for s in must_contain if s not in (block or "")]
    return _check(
        "active-tags block names every tag value + kind",
        not missing,
        f"missing in block: {missing!r}",
    )


def test_active_tags_block_tells_model_to_acknowledge_scope() -> bool:
    """The persona prompts already cover this, but the block restates
    it inline so a thin / non-research persona that doesn't carry the
    scope-acknowledgement rule still gets the hint."""
    block = build_active_tags_block([{"kind": "group", "value": "zeitler"}])
    return _check(
        "active-tags block instructs the model to acknowledge scope",
        "tell them about these active scope tags" in (block or "")
        or "acknowledg" in (block or "").lower(),
        f"block was: {block!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_normalize_drops_garbage,
    test_normalize_empty_returns_none,
    test_filter_none_for_empty_tags,
    test_filter_group_tag_targets_array_member,
    test_filter_topic_tag_targets_topic_slug,
    test_filter_contributor_tag_targets_username,
    test_filter_multiple_tags_and_combine,
    test_filter_unknown_kind_silently_ignored,
    test_contextvar_default_none,
    test_contextvar_roundtrip,
    test_active_tags_block_none_for_empty,
    test_active_tags_block_mentions_each_tag_value,
    test_active_tags_block_tells_model_to_acknowledge_scope,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    total = len(TESTS)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
