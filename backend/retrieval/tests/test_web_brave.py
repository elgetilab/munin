"""
Standalone unit tests for the Brave Search API path in web_search.

Brave (api.search.brave.com, keyed JSON API) became the primary web
source on 2026-07-23 after the SearXNG scraper engines were all
CAPTCHA'd / access-denied from the cluster's datacenter IP. SearXNG
stays as a keyless supplement. These tests cover:

- key unset -> Brave never called, SearXNG-only behaviour unchanged
- key set -> Brave results merged + deduped by URL with SearXNG's
- Brave fan-out capped at BRAVE_MAX_QUERIES
- degradation: Brave down too -> TOOL FAILURE warning includes brave;
  Brave up -> zero hits is a real "no info found", no warning, even
  when SearXNG is fully suspended or unreachable
- _brave_one: response normalization and one-shot 429 retry

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_web_brave.py

Exit 0 = pass, non-zero = fail. No network required.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, "/app")

from mcp.tools import web as web_module  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _run(coro):
    return asyncio.run(coro)


def _searxng_mock(per_query_responses: list[dict]):
    iterator = iter(per_query_responses)

    async def _fake(client, q):
        return next(iterator)

    return _fake


def _brave_mock(per_query_responses: list[dict], calls: list | None = None):
    iterator = iter(per_query_responses)

    async def _fake(client, q, count=10):
        if calls is not None:
            calls.append(q)
        return next(iterator)

    return _fake


def _searxng_empty(n: int) -> list[dict]:
    return [
        {"results": [], "unresponsive": [], "transport_error": None}
        for _ in range(n)
    ]


def _hit(url: str, engine: str) -> dict:
    return {"title": f"t {url}", "url": url, "content": "...", "engine": engine}


# ---------------------------------------------------------------------------
# Key unset -> Brave never called
# ---------------------------------------------------------------------------

def test_key_unset_brave_not_called() -> bool:
    async def _explode(client, q, count=10):
        raise AssertionError("_brave_one called without BRAVE_API_KEY")

    searxng = [
        {"results": [_hit("https://ex.org/1", "duckduckgo")],
         "unresponsive": [], "transport_error": None}
    ]
    with patch.object(web_module, "BRAVE_API_KEY", ""), \
         patch.object(web_module, "_searxng_one", _searxng_mock(searxng)), \
         patch.object(web_module, "_brave_one", _explode):
        out = _run(web_module.web_search(queries=["q"]))
    ok = out.get("total_hits") == 1 and len(out.get("results") or []) == 1
    return _check("key unset -> brave not called, searxng-only", ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# Key set -> Brave results merged, deduped by URL against SearXNG
# ---------------------------------------------------------------------------

def test_brave_results_merged_and_deduped() -> bool:
    searxng = [
        {"results": [_hit("https://ex.org/shared", "duckduckgo")],
         "unresponsive": [], "transport_error": None}
    ]
    brave = [
        {"results": [_hit("https://ex.org/shared", "brave"),
                     _hit("https://ex.org/brave-only", "brave")],
         "unresponsive": [], "transport_error": None}
    ]
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "_searxng_one", _searxng_mock(searxng)), \
         patch.object(web_module, "_brave_one", _brave_mock(brave)):
        out = _run(web_module.web_search(queries=["q"]))
    results = out.get("results") or []
    by_url = {r["url"]: r for r in results}
    shared = by_url.get("https://ex.org/shared") or {}
    ok = (
        out.get("total_hits") == 3
        and len(results) == 2
        and shared.get("matched_by") == 2
        and by_url.get("https://ex.org/brave-only", {}).get("engine") == "brave"
        and "warning" not in out
    )
    return _check("brave merged + deduped by url", ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# Brave fan-out capped at BRAVE_MAX_QUERIES
# ---------------------------------------------------------------------------

def test_brave_query_cap() -> bool:
    calls: list[str] = []
    queries = ["a", "b", "c", "d", "e"]
    brave = [
        {"results": [], "unresponsive": [], "transport_error": None}
        for _ in range(2)
    ]
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "BRAVE_MAX_QUERIES", 2), \
         patch.object(web_module, "_searxng_one", _searxng_mock(_searxng_empty(5))), \
         patch.object(web_module, "_brave_one", _brave_mock(brave, calls)):
        _run(web_module.web_search(queries=queries))
    ok = calls == ["a", "b"]
    return _check("brave capped at BRAVE_MAX_QUERIES", ok, f"calls={calls!r}")


# ---------------------------------------------------------------------------
# Brave down + all SearXNG engines down -> warning includes brave
# ---------------------------------------------------------------------------

def test_brave_and_searxng_down_warning() -> bool:
    searxng = [
        {"results": [],
         "unresponsive": [["startpage", "CAPTCHA"], ["duckduckgo", "CAPTCHA"],
                          ["qwant", "denied"], ["mojeek", "denied"]],
         "transport_error": None}
    ]
    brave = [
        {"results": [], "unresponsive": [["brave", "HTTP 401"]],
         "transport_error": None}
    ]
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "_searxng_one", _searxng_mock(searxng)), \
         patch.object(web_module, "_brave_one", _brave_mock(brave)):
        out = _run(web_module.web_search(queries=["q"]))
    engines = {p[0] for p in out.get("engines_unresponsive") or []}
    ok = (
        out.get("total_hits") == 0
        and "brave" in engines
        and "TOOL FAILURE" in (out.get("warning") or "")
    )
    return _check("brave + searxng all down -> warning", ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# Brave answered cleanly (0 hits) + SearXNG suspended -> NO warning
# ---------------------------------------------------------------------------

def test_brave_ok_suppresses_warning_when_searxng_suspended() -> bool:
    searxng = [
        {"results": [],
         "unresponsive": [["startpage", "CAPTCHA"], ["duckduckgo", "CAPTCHA"],
                          ["qwant", "denied"], ["mojeek", "denied"]],
         "transport_error": None}
    ]
    brave = [{"results": [], "unresponsive": [], "transport_error": None}]
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "_searxng_one", _searxng_mock(searxng)), \
         patch.object(web_module, "_brave_one", _brave_mock(brave)):
        out = _run(web_module.web_search(queries=["q"]))
    ok = (
        out.get("total_hits") == 0
        and "warning" not in out
        and "engines_unresponsive" in out
    )
    return _check("brave ok -> no warning despite searxng suspended", ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# SearXNG unreachable (transport) + Brave returns hits -> results, no warning
# ---------------------------------------------------------------------------

def test_searxng_unreachable_brave_carries() -> bool:
    searxng = [
        {"results": [], "unresponsive": [],
         "transport_error": "ConnectError: refused"}
    ]
    brave = [
        {"results": [_hit("https://ex.org/b1", "brave")],
         "unresponsive": [], "transport_error": None}
    ]
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "_searxng_one", _searxng_mock(searxng)), \
         patch.object(web_module, "_brave_one", _brave_mock(brave)):
        out = _run(web_module.web_search(queries=["q"]))
    ok = (
        out.get("total_hits") == 1
        and (out.get("results") or [{}])[0].get("engine") == "brave"
        and "warning" not in out
    )
    return _check("searxng unreachable -> brave results carry, no warning", ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# _brave_one: normalization of the Brave API response shape
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self._iter = iter(responses)
        self.calls: list[dict] = []

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return next(self._iter)


def test_brave_one_normalizes_response() -> bool:
    body = {
        "web": {
            "results": [
                {"title": "T1", "url": "https://ex.org/1", "description": "D1"},
                {"title": "no-url dropped", "description": "D2"},
            ]
        }
    }
    client = _FakeClient([_FakeResponse(200, body)])
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "BRAVE_SEARCH_QPS", 1000.0):
        out = _run(web_module._brave_one(client, "q", count=7))
    results = out.get("results") or []
    call = client.calls[0]
    ok = (
        len(results) == 1
        and results[0] == {"title": "T1", "url": "https://ex.org/1",
                           "content": "D1", "engine": "brave"}
        and out.get("unresponsive") == []
        and out.get("transport_error") is None
        and call["params"]["count"] == 7
        and call["headers"]["X-Subscription-Token"] == "k"
    )
    return _check("_brave_one normalizes web.results", ok, f"out={out!r} call={call!r}")


def test_brave_one_retries_once_on_429() -> bool:
    body = {"web": {"results": [{"title": "T", "url": "https://ex.org/r",
                                 "description": "D"}]}}
    client = _FakeClient([
        _FakeResponse(429, {}, headers={"Retry-After": "0"}),
        _FakeResponse(200, body),
    ])
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "BRAVE_SEARCH_QPS", 1000.0):
        out = _run(web_module._brave_one(client, "q"))
    ok = len(client.calls) == 2 and len(out.get("results") or []) == 1
    return _check("_brave_one retries once on 429", ok, f"out={out!r}")


def test_brave_one_error_reported_as_unresponsive() -> bool:
    client = _FakeClient([_FakeResponse(401, {})])
    with patch.object(web_module, "BRAVE_API_KEY", "k"), \
         patch.object(web_module, "BRAVE_SEARCH_QPS", 1000.0):
        out = _run(web_module._brave_one(client, "q"))
    unresp = out.get("unresponsive") or []
    ok = (
        out.get("results") == []
        and len(unresp) == 1
        and unresp[0][0] == "brave"
        and out.get("transport_error") is None
    )
    return _check("_brave_one failure -> unresponsive entry, not transport_error",
                  ok, f"out={out!r}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_key_unset_brave_not_called,
    test_brave_results_merged_and_deduped,
    test_brave_query_cap,
    test_brave_and_searxng_down_warning,
    test_brave_ok_suppresses_warning_when_searxng_suspended,
    test_searxng_unreachable_brave_carries,
    test_brave_one_normalizes_response,
    test_brave_one_retries_once_on_429,
    test_brave_one_error_reported_as_unresponsive,
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
