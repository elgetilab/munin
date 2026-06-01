"""
Tests for Brave Search API integration in web_search (Bug 4a, 2026-06-01).

Brave's scraper engine in SearXNG has been "Suspended: too many
requests" against our IP for months, so we drop it from
`_SEARXNG_ENGINES` and bring Brave back via the official Search API
when `BRAVE_API_KEY` is set. The path is dormant when the key is
unset (the same SearXNG-only fan-out as before).

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_brave_api.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")

from mcp.tools import web as web_module  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _run(coro):
    return asyncio.run(coro)


def test_brave_key_unset_skips_brave() -> bool:
    """No BRAVE_API_KEY in env: web_search runs only SearXNG, no
    Brave call is made."""
    brave_calls: list[str] = []

    async def _stub_searxng(client, q):
        return {
            "results": [
                {
                    "url": f"https://sx.example/{q}",
                    "title": q,
                    "content": "snippet",
                    "engine": "stub",
                }
            ],
            "unresponsive": [],
            "transport_error": None,
        }

    async def _stub_brave(client, q, key):
        brave_calls.append(q)
        return {"results": [], "unresponsive": [], "transport_error": None}

    with (
        patch.dict(os.environ, {"BRAVE_API_KEY": ""}, clear=False),
        patch.object(web_module, "_searxng_one", _stub_searxng),
        patch.object(web_module, "_brave_api_one", _stub_brave),
        patch.object(
            web_module, "expand_queries", AsyncMock(return_value=["q1", "q2"])
        ),
    ):
        result = _run(web_module.web_search(query="anything"))

    if brave_calls:
        return _check(
            "no Brave call when key is unset",
            False,
            f"unexpected brave calls: {brave_calls!r}",
        )
    if not (result.get("results") or []):
        return _check(
            "SearXNG still returns results when Brave is off",
            False,
            f"empty results: {result!r}",
        )
    return _check("BRAVE_API_KEY unset -> Brave skipped", True)


def test_brave_key_set_calls_brave_per_query() -> bool:
    """With the key set, Brave is invoked once per query alongside
    SearXNG. Both engines' results land in the same merged set."""
    sx_calls: list[str] = []
    br_calls: list[str] = []

    async def _stub_searxng(client, q):
        sx_calls.append(q)
        return {
            "results": [
                {
                    "url": f"https://sx.example/{q}",
                    "title": f"sx {q}",
                    "content": "sx snippet",
                    "engine": "stub-sx",
                }
            ],
            "unresponsive": [],
            "transport_error": None,
        }

    async def _stub_brave(client, q, key):
        br_calls.append(q)
        return {
            "results": [
                {
                    "url": f"https://br.example/{q}",
                    "title": f"br {q}",
                    "content": "br snippet",
                    "engine": "brave-api",
                }
            ],
            "unresponsive": [],
            "transport_error": None,
        }

    with (
        patch.dict(os.environ, {"BRAVE_API_KEY": "test-token"}, clear=False),
        patch.object(web_module, "_searxng_one", _stub_searxng),
        patch.object(web_module, "_brave_api_one", _stub_brave),
        patch.object(
            web_module, "expand_queries", AsyncMock(return_value=["q1", "q2"])
        ),
    ):
        result = _run(web_module.web_search(query="anything"))

    if sx_calls != ["q1", "q2"]:
        return _check(
            "SearXNG hit for each query",
            False,
            f"sx_calls={sx_calls!r}",
        )
    if br_calls != ["q1", "q2"]:
        return _check(
            "Brave hit for each query",
            False,
            f"br_calls={br_calls!r}",
        )
    urls = {r.get("url") for r in (result.get("results") or [])}
    expected = {
        "https://sx.example/q1",
        "https://sx.example/q2",
        "https://br.example/q1",
        "https://br.example/q2",
    }
    if urls != expected:
        return _check(
            "merged result set contains both engines' URLs",
            False,
            f"got {urls!r}",
        )
    return _check(
        "BRAVE_API_KEY set -> Brave + SearXNG fan-out and merge", True
    )


def test_brave_unresponsive_surfaced_in_warning() -> bool:
    """When Brave AND SearXNG engines all report unresponsive AND no
    results land, the warning string mentions tool failure (not 'no
    information'). Brave's failure should be counted toward the
    threshold so a partial outage doesn't go unmentioned."""

    async def _stub_searxng(client, q):
        return {
            "results": [],
            "unresponsive": [
                ["startpage", "captcha"],
                ["duckduckgo", "captcha"],
            ],
            "transport_error": None,
        }

    async def _stub_brave(client, q, key):
        return {
            "results": [],
            "unresponsive": [["brave-api", "503"]],
            "transport_error": None,
        }

    with (
        patch.dict(os.environ, {"BRAVE_API_KEY": "test-token"}, clear=False),
        patch.object(web_module, "_searxng_one", _stub_searxng),
        patch.object(web_module, "_brave_api_one", _stub_brave),
        patch.object(
            web_module, "expand_queries", AsyncMock(return_value=["q1"])
        ),
    ):
        result = _run(web_module.web_search(query="anything"))

    if "warning" not in result:
        return _check(
            "all-engines-down emits a warning",
            False,
            f"got {result!r}",
        )
    if "TOOL FAILURE" not in result["warning"]:
        return _check(
            "warning identifies tool failure",
            False,
            f"warning text: {result['warning']!r}",
        )
    return _check("all-engines-down warning mentions tool failure", True)


def main() -> int:
    tests = [
        test_brave_key_unset_skips_brave,
        test_brave_key_set_calls_brave_per_query,
        test_brave_unresponsive_surfaced_in_warning,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(
        f"\n{len(results) - failed}/{len(results)} passed; {failed} failed."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
