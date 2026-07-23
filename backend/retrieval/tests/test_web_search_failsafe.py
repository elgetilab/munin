"""
Standalone unit tests for web_search's degraded-backend handling
(`engines_unresponsive` + `warning` fields).

Regression guard for the failure pattern observed in chat
689f8df3 on 2026-05-06: SearXNG's brave/duckduckgo/startpage were
all suspended (rate-limited, access-denied, CAPTCHA), so every
web_search call returned `total_hits=0`. The model concluded the
user's topic ('OpenClaw', 'SOUL prompts') was obscure or
non-existent, when in reality the search backend was broken.

After the fix, web_search aggregates SearXNG's `unresponsive_engines`
across queries and emits an explicit `warning` field when every
configured engine failed. The model is told (via the schema
description) to treat this as TOOL FAILURE, not 'no info found'.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_web_search_failsafe.py

Exit 0 = pass, non-zero = fail.

Tests mock `_searxng_one` so no network is required. Behavioural
coverage of the same failure shape would need an end-to-end test
with SearXNG faked at the HTTP layer, which is out of scope here.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, "/app")

from mcp.tools import web as web_module  # noqa: E402

# This suite covers the SearXNG-only degradation path. Force the Brave
# API off so the tests stay hermetic when run inside the production
# container (where BRAVE_API_KEY is set and web_search would otherwise
# fan out real API calls). Brave-path coverage: test_web_brave.py.
web_module.BRAVE_API_KEY = ""


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _run(coro):
    return asyncio.run(coro)


def _make_mock(per_query_responses: list[dict]):
    """Return an async mock for `_searxng_one` that yields the given
    list of responses in order, one per call."""
    iterator = iter(per_query_responses)

    async def _fake(client, q):  # signature matches _searxng_one
        return next(iterator)

    return _fake


# ---------------------------------------------------------------------------
# Healthy path: at least one engine returned hits, no warning emitted
# ---------------------------------------------------------------------------

def test_healthy_run_no_warning() -> bool:
    responses = [
        {
            "results": [
                {
                    "title": "Python language",
                    "url": "https://example.com/python",
                    "content": "Python is a programming language.",
                    "engine": "duckduckgo",
                }
            ],
            "unresponsive": [],
            "transport_error": None,
        }
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["python"]))
    ok = (
        out.get("total_hits") == 1
        and len(out.get("results") or []) == 1
        and "warning" not in out
        and "engines_unresponsive" not in out
    )
    return _check(
        "healthy run -> no warning, no engines_unresponsive",
        ok,
        f"out={out!r}",
    )


# ---------------------------------------------------------------------------
# Partial degradation: one engine down, others returned hits -> no warning
# ---------------------------------------------------------------------------

def test_partial_degradation_logs_engine_no_warning() -> bool:
    """One engine throttled but others worked. Result should include
    `engines_unresponsive` (so the caller knows coverage is reduced)
    but NOT the `warning` field, because there are still hits and
    other engines worked."""
    responses = [
        {
            "results": [
                {
                    "title": "Hit 1",
                    "url": "https://example.com/1",
                    "content": "...",
                    "engine": "duckduckgo",
                }
            ],
            "unresponsive": [["brave", "Suspended: too many requests"]],
            "transport_error": None,
        }
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["python"]))
    ok = (
        out.get("total_hits") == 1
        and "warning" not in out
        and out.get("engines_unresponsive") == [
            ["brave", "Suspended: too many requests"]
        ]
    )
    return _check(
        "partial degradation -> engines_unresponsive set, no warning",
        ok,
        f"out={out!r}",
    )


# ---------------------------------------------------------------------------
# THE REGRESSION CASE - all engines down, total_hits=0 -> warning emitted
# ---------------------------------------------------------------------------

def test_689f8df3_all_engines_suspended_warning_emitted() -> bool:
    """Reported-chat failure shape (689f8df3, 2026-05-06), updated to
    the current engine roster (brave was dropped from _SEARXNG_ENGINES
    on 2026-06-01; qwant/mojeek replaced it). Every engine is suspended,
    web_search returns 0 hits, and the result must carry a `warning`
    field telling the model this is tool failure, not 'no info found'."""
    responses = [
        {
            "results": [],
            "unresponsive": [
                ["startpage", "Suspended: CAPTCHA"],
                ["duckduckgo", "Suspended: access denied"],
                ["qwant", "Suspended: access denied"],
                ["mojeek", "Suspended: too many requests"],
            ],
            "transport_error": None,
        }
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["openclaw"]))

    if out.get("total_hits") != 0:
        return _check(
            "689f8df3 all-engines-suspended -> warning",
            False,
            f"expected total_hits=0, got {out.get('total_hits')!r}",
        )
    if "engines_unresponsive" not in out:
        return _check(
            "689f8df3 all-engines-suspended -> warning",
            False,
            "expected engines_unresponsive list to be present",
        )
    engines = {pair[0] for pair in out["engines_unresponsive"]}
    if engines != {"startpage", "duckduckgo", "qwant", "mojeek"}:
        return _check(
            "689f8df3 all-engines-suspended -> warning",
            False,
            f"engines_unresponsive mismatch: {out['engines_unresponsive']!r}",
        )
    warning = out.get("warning") or ""
    if not warning:
        return _check(
            "689f8df3 all-engines-suspended -> warning",
            False,
            "expected `warning` field to be emitted",
        )
    if "TOOL FAILURE" not in warning:
        return _check(
            "689f8df3 all-engines-suspended -> warning",
            False,
            f"warning missing 'TOOL FAILURE' phrase: {warning!r}",
        )
    return _check(
        "689f8df3 all-engines-suspended -> warning emitted with TOOL FAILURE phrasing",
        True,
    )


# ---------------------------------------------------------------------------
# All engines down BUT some other call returned a hit somehow -> no warning
# ---------------------------------------------------------------------------

def test_engines_down_but_some_results_no_warning() -> bool:
    """Defensive: if `unresponsive` reports every engine but some
    `results` slipped through anyway (maybe from a cached result or
    a partial response), suppress the warning. The `warning` is
    triggered ONLY by the conjunction (all engines down) AND
    (total_hits == 0)."""
    responses = [
        {
            "results": [
                {
                    "title": "Cached hit",
                    "url": "https://cache/x",
                    "content": "...",
                    "engine": "cache",
                }
            ],
            "unresponsive": [
                ["brave", "down"],
                ["duckduckgo", "down"],
                ["startpage", "down"],
            ],
            "transport_error": None,
        }
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["x"]))
    ok = (
        out.get("total_hits") == 1
        and "warning" not in out
        and "engines_unresponsive" in out
    )
    return _check(
        "all engines down but >0 hits -> engines listed, no warning",
        ok,
        f"out={out!r}",
    )


# ---------------------------------------------------------------------------
# Transport error on every query -> warning emitted with backend phrasing
# ---------------------------------------------------------------------------

def test_transport_error_total_warning_emitted() -> bool:
    """SearXNG itself is unreachable (DNS, timeout, connection refused).
    Every per-query call sets `transport_error`. Treat as total
    degradation: warning emitted, with backend-unreachable phrasing."""
    responses = [
        {
            "results": [],
            "unresponsive": [],
            "transport_error": "ConnectError: [Errno 111] Connection refused",
        },
        {
            "results": [],
            "unresponsive": [],
            "transport_error": "ConnectError: [Errno 111] Connection refused",
        },
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["a", "b"]))
    warning = out.get("warning") or ""
    ok = (
        out.get("total_hits") == 0
        and "TOOL FAILURE" in warning
        and "search backend" in warning.lower()
    )
    return _check(
        "transport error on all queries -> warning emitted, mentions backend",
        ok,
        f"out={out!r}",
    )


# ---------------------------------------------------------------------------
# Defensive: SearXNG dict-shaped unresponsive entries don't crash
# ---------------------------------------------------------------------------

def test_unresponsive_dict_shape_accepted() -> bool:
    """Some SearXNG versions return unresponsive_engines as a list of
    dicts {name, reason} instead of [name, reason] tuples. Aggregator
    must accept both."""
    responses = [
        {
            "results": [],
            "unresponsive": [
                {"name": "startpage", "reason": "CAPTCHA"},
                {"engine": "duckduckgo", "error": "blocked"},
                {"name": "qwant", "reason": "rate-limited"},
                {"engine": "mojeek", "error": "denied"},
            ],
            "transport_error": None,
        }
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["x"]))
    engines = {pair[0] for pair in out.get("engines_unresponsive") or []}
    return _check(
        "dict-shaped unresponsive entries -> accepted",
        engines == {"startpage", "duckduckgo", "qwant", "mojeek"}
        and "warning" in out,
        f"engines={engines!r}, warning={'warning' in out}",
    )


# ---------------------------------------------------------------------------
# Multi-query: failures aggregate across queries (deduped)
# ---------------------------------------------------------------------------

def test_multi_query_aggregates_unresponsive() -> bool:
    """Different engines fail on different queries; aggregator should
    union them (deduped by engine name)."""
    responses = [
        {
            "results": [],
            "unresponsive": [["startpage", "CAPTCHA"], ["mojeek", "denied"]],
            "transport_error": None,
        },
        {
            "results": [],
            "unresponsive": [["duckduckgo", "blocked"]],
            "transport_error": None,
        },
        {
            "results": [],
            "unresponsive": [["qwant", "rate-limited"]],
            "transport_error": None,
        },
    ]
    with patch.object(web_module, "_searxng_one", _make_mock(responses)):
        out = _run(web_module.web_search(queries=["a", "b", "c"]))
    engines = {pair[0] for pair in out.get("engines_unresponsive") or []}
    return _check(
        "multi-query aggregates unresponsive engines",
        engines == {"startpage", "duckduckgo", "qwant", "mojeek"}
        and "warning" in out,
        f"engines={engines!r}",
    )


# ---------------------------------------------------------------------------
# Empty queries input rejected as before
# ---------------------------------------------------------------------------

def test_empty_queries_returns_error() -> bool:
    out = _run(web_module.web_search(queries=[]))
    return _check(
        "no query and no queries -> error envelope",
        isinstance(out, dict) and "error" in out,
        f"out={out!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_healthy_run_no_warning,
    test_partial_degradation_logs_engine_no_warning,
    test_689f8df3_all_engines_suspended_warning_emitted,
    test_engines_down_but_some_results_no_warning,
    test_transport_error_total_warning_emitted,
    test_unresponsive_dict_shape_accepted,
    test_multi_query_aggregates_unresponsive,
    test_empty_queries_returns_error,
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
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
