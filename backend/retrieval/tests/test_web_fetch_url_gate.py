"""
Tests for the web_fetch URL allowlist (Bug 4b, 2026-06-01).

Regression guard for chat d3b4c98b: the model invented URLs
(`dein-felix.de/leipzig/geschenkgutschein`, `syndeo-leipzig.de/...`)
based on prior knowledge instead of fetching URLs that surfaced in
recent web_search results, and both calls 404'd. The fix routes
every web_fetch_content call through a per-request URL allowlist
seeded from the conversation's prior tool_call results AND from the
current user message's text. URLs not in the set get a synthetic
error that nudges the model to web_search first.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_web_fetch_url_gate.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
import types
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")


# `trafilatura` is a heavy dep that ships with the retrieval container
# but isn't always installed on dev laptops. Inject a stub so the
# import inside web_fetch_content succeeds; the real summary path is
# stubbed via `llm_summarize` anyway, so the only thing trafilatura
# does in these tests is `extract(html)`, which we make a passthrough.
if "trafilatura" not in sys.modules:
    _stub_trafilatura = types.ModuleType("trafilatura")
    _stub_trafilatura.extract = lambda html, **kwargs: "stub extracted text"
    sys.modules["trafilatura"] = _stub_trafilatura

from mcp.context import current_search_urls  # noqa: E402
from mcp.tools import web as web_module  # noqa: E402
from chat_service import _extract_urls, _seed_search_urls  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# ─── _extract_urls / _seed_search_urls ────────────────────────────────────


def test_extract_urls_from_string() -> bool:
    text = (
        "I found two pages: https://example.com/page-a and "
        "https://example.com/page-b. Also see https://other.example.org/x."
    )
    urls = _extract_urls(text)
    expected = {
        "https://example.com/page-a",
        "https://example.com/page-b",
        "https://other.example.org/x",
    }
    return _check(
        "_extract_urls finds 3 URLs from prose",
        urls == expected,
        f"got {urls!r}",
    )


def test_extract_urls_strips_trailing_punctuation() -> bool:
    text = "Check https://example.com/foo, https://example.com/bar."
    urls = _extract_urls(text)
    return _check(
        "_extract_urls strips trailing comma/period",
        urls == {"https://example.com/foo", "https://example.com/bar"},
        f"got {urls!r}",
    )


def test_extract_urls_walks_dict_and_list() -> bool:
    payload = {
        "results": [
            {"url": "https://a.example", "title": "A"},
            {"url": "https://b.example", "title": "B"},
        ],
        "warning": "see https://c.example for context",
    }
    urls = _extract_urls(payload)
    return _check(
        "_extract_urls walks nested dict/list",
        urls == {
            "https://a.example",
            "https://b.example",
            "https://c.example",
        },
        f"got {urls!r}",
    )


def test_seed_search_urls_from_user_message() -> bool:
    bucket: set[str] = set()
    _seed_search_urls(
        prior_messages=[],
        user_text="Summarize https://example.com/article please",
        bucket=bucket,
    )
    return _check(
        "_seed_search_urls picks up URL in user message",
        bucket == {"https://example.com/article"},
        f"got {bucket!r}",
    )


def test_seed_search_urls_from_prior_tool_call() -> bool:
    bucket: set[str] = set()
    prior = [
        {
            "role": "assistant",
            "content": "Found two pages.",
            "tool_calls": [
                {
                    "name": "web_search",
                    "arguments": {"query": "foo"},
                    "result": {
                        "results": [
                            {"url": "https://search-hit-1.example"},
                            {"url": "https://search-hit-2.example"},
                        ]
                    },
                }
            ],
        }
    ]
    _seed_search_urls(prior, user_text="next question", bucket=bucket)
    return _check(
        "_seed_search_urls picks up URLs from prior tool_calls",
        bucket == {
            "https://search-hit-1.example",
            "https://search-hit-2.example",
        },
        f"got {bucket!r}",
    )


def test_seed_search_urls_noop_when_bucket_none() -> bool:
    # When the ContextVar is unbound, _seed_search_urls must not raise.
    _seed_search_urls(
        prior_messages=[{"role": "user", "content": "https://x.example"}],
        user_text="https://y.example",
        bucket=None,
    )
    return _check(
        "_seed_search_urls is a no-op when bucket is None", True
    )


# ─── web_fetch_content gating behaviour ──────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_web_fetch_blocks_url_not_in_allowlist() -> bool:
    """The smoking-gun chat shape: model invents
    `dein-felix.de/leipzig/geschenkgutschein` after a search that
    never surfaced it. With the gate, the fetch is short-circuited
    with the nudge instead of 404'ing on the real network."""
    bucket: set[str] = {"https://example.com/seen"}
    token = current_search_urls.set(bucket)
    try:
        result = _run(
            web_module.web_fetch_content(
                "https://dein-felix.de/leipzig/geschenkgutschein"
            )
        )
    finally:
        current_search_urls.reset(token)

    if "error" not in result:
        return _check(
            "hallucinated URL is rejected",
            False,
            f"expected error, got {result!r}",
        )
    if "not from any recent search result" not in result["error"]:
        return _check(
            "rejection nudges the model toward web_search",
            False,
            f"unexpected error text: {result['error']!r}",
        )
    return _check("hallucinated URL is rejected with search-first nudge", True)


def test_web_fetch_allows_url_added_by_web_search() -> bool:
    """An URL in the allowlist must pass the gate and reach the
    network layer (which we stub to assert it was called)."""
    bucket: set[str] = {"https://example.com/article"}
    token = current_search_urls.set(bucket)
    fetched: list[str] = []

    class _StubResponse:
        text = "<html><body><p>hello world</p></body></html>"

        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            fetched.append(url)
            return _StubResponse()

    try:
        with (
            patch.object(web_module.httpx, "AsyncClient", _StubClient),
            patch.object(
                web_module,
                "llm_summarize",
                AsyncMock(return_value={"summary": "stub summary"}),
            ),
        ):
            result = _run(
                web_module.web_fetch_content("https://example.com/article")
            )
    finally:
        current_search_urls.reset(token)

    if fetched != ["https://example.com/article"]:
        return _check(
            "allowed URL reaches httpx.get",
            False,
            f"fetched={fetched!r} result={result!r}",
        )
    return _check("URL in allowlist passes the gate", True)


def test_web_fetch_open_mode_when_bucket_unbound() -> bool:
    """If chat_service hasn't bound `current_search_urls`, the gate is
    fully open (back-compat). The fetch reaches httpx and runs."""
    fetched: list[str] = []

    class _StubResponse:
        text = "<html><body><p>open</p></body></html>"

        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            fetched.append(url)
            return _StubResponse()

    # Ensure the ContextVar is at its default (None) for this test.
    # No `current_search_urls.set(...)` call here.
    with (
        patch.object(web_module.httpx, "AsyncClient", _StubClient),
        patch.object(
            web_module,
            "llm_summarize",
            AsyncMock(return_value={"summary": "stub summary"}),
        ),
    ):
        _run(web_module.web_fetch_content("https://anything.example/whatever"))

    return _check(
        "open mode: unbound ContextVar means no gating",
        fetched == ["https://anything.example/whatever"],
        f"fetched={fetched!r}",
    )


def test_web_search_records_result_urls() -> bool:
    """`web_search` must register every dedup'd result URL with the
    per-request allowlist so a follow-up web_fetch on one of them
    succeeds. We bypass the SearXNG round-trip with a stub."""
    bucket: set[str] = set()
    token = current_search_urls.set(bucket)

    async def _fake_searxng_one(client, q):
        return {
            "results": [
                {
                    "url": "https://hit-a.example",
                    "title": "A",
                    "content": "snippet a",
                    "engine": "stub",
                },
                {
                    "url": "https://hit-b.example",
                    "title": "B",
                    "content": "snippet b",
                    "engine": "stub",
                },
            ],
            "unresponsive": [],
            "transport_error": None,
        }

    try:
        with (
            patch.object(web_module, "_searxng_one", _fake_searxng_one),
            patch.object(
                web_module,
                "expand_queries",
                AsyncMock(return_value=["q1"]),
            ),
        ):
            _run(web_module.web_search(query="anything"))
    finally:
        current_search_urls.reset(token)

    expected = {"https://hit-a.example", "https://hit-b.example"}
    return _check(
        "web_search registers result URLs with the allowlist",
        bucket == expected,
        f"got {bucket!r}",
    )


def main() -> int:
    tests = [
        test_extract_urls_from_string,
        test_extract_urls_strips_trailing_punctuation,
        test_extract_urls_walks_dict_and_list,
        test_seed_search_urls_from_user_message,
        test_seed_search_urls_from_prior_tool_call,
        test_seed_search_urls_noop_when_bucket_none,
        test_web_fetch_blocks_url_not_in_allowlist,
        test_web_fetch_allows_url_added_by_web_search,
        test_web_fetch_open_mode_when_bucket_unbound,
        test_web_search_records_result_urls,
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
