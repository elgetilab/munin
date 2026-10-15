"""
Regression tests: every URL-bearing tool feeds the web_fetch allow-set
in the same turn (v1.1 tool-bug F4).

Before the fix only `web_search` and `web_fetch` added URLs to the
per-request allow-set, so a URL returned by `semantic_scholar_search`,
the s2 citation tools, `paper_lookup` or the `search` agent was
rejected by the web_fetch gate until the next request. The executor
(`chat_service._run_tool_calls`) now records every URL in a tool's
result as it returns. Invented URLs, and URLs a tool merely echoed back
from its own arguments or an error, stay rejected.

All inputs are synthetic (example.org hosts, made-up titles).

Run inside a throwaway retrieval container:
    python /app/tests/test_web_fetch_allowset_all_tools.py
Exit 0 = pass, non-zero = fail. Also pytest-collectable.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
import types
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if "trafilatura" not in sys.modules:
    _stub_trafilatura = types.ModuleType("trafilatura")
    _stub_trafilatura.extract = lambda html, **kwargs: "stub extracted text"
    sys.modules["trafilatura"] = _stub_trafilatura

import chat_service  # noqa: E402
import provenance  # noqa: E402
from mcp.context import current_search_urls  # noqa: E402
from mcp.tools import web as web_module  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    # Raise so a failure also fails under pytest; main() counts it.
    assert ok, f"{name}: {detail}"
    return ok


S2_URL = "https://www.example.org/paper/abc123"
DOI_URL = "https://doi.example.org/10.9999/synthetic.0001"
INVENTED_URL = "https://invented.example.org/never/returned"


def _fake_results() -> dict:
    """Synthetic tool results shaped like the real non-web tools."""
    return {
        "semantic_scholar_search": {
            "results": [
                {"title": "A synthetic paper", "year": 2024, "url": S2_URL},
            ],
        },
        "paper_lookup": {
            "found": True,
            "paper": {"title": "Another synthetic paper", "url": DOI_URL},
        },
        # A tool that fails and echoes back the URL it was given must not
        # whitelist that URL.
        "s2_get_references": {
            "error": "not found",
            "url": INVENTED_URL,
        },
    }


async def _run_batch(tool_calls: list[dict], bucket: set) -> list[dict]:
    results = _fake_results()

    async def _fake_execute(name: str, args: dict) -> dict:
        return results.get(name, {"ok": True, "echo": args})

    real = chat_service.execute_mcp_tool
    chat_service.execute_mcp_tool = _fake_execute  # type: ignore[assignment]
    token = current_search_urls.set(bucket)
    try:
        return await chat_service._run_tool_calls(tool_calls)
    finally:
        current_search_urls.reset(token)
        chat_service.execute_mcp_tool = real  # type: ignore[assignment]


def _gate(url: str, bucket: set) -> dict:
    """Run web_fetch_content only as far as the gate: a URL that passes
    reaches a stub network layer, which records the call and fails."""
    reached: list[bool] = []

    def _client(*a, **kw):
        reached.append(True)
        raise RuntimeError("stub network")

    async def _go() -> dict:
        token = current_search_urls.set(bucket)
        real_client = web_module.guarded_client
        real_may = provenance.may_fetch
        web_module.guarded_client = _client  # type: ignore[assignment]
        provenance.may_fetch = lambda *_a, **_k: True  # type: ignore[assignment]
        try:
            out = await web_module.web_fetch_content(url)
        except RuntimeError:
            out = {}
        finally:
            web_module.guarded_client = real_client  # type: ignore[assignment]
            provenance.may_fetch = real_may  # type: ignore[assignment]
            current_search_urls.reset(token)
        if reached:
            return {"passed_gate": True}
        return out

    return asyncio.run(_go())


def test_non_web_tool_urls_become_fetchable_same_turn() -> bool:
    bucket: set[str] = set()
    asyncio.run(_run_batch([
        {"id": "c1", "name": "semantic_scholar_search",
         "arguments": {"query": "synthetic topic"}},
        {"id": "c2", "name": "paper_lookup",
         "arguments": {"identifier": "10.9999/synthetic.0001"}},
    ], bucket))
    ok = S2_URL in bucket and DOI_URL in bucket
    if not ok:
        return _check("non-web tool URLs recorded in allow-set", False,
                      f"bucket={bucket!r}")
    r1 = _gate(S2_URL, bucket)
    r2 = _gate(DOI_URL, bucket)
    return _check(
        "URL returned by a non-web tool passes the web_fetch gate same turn",
        r1.get("passed_gate") is True and r2.get("passed_gate") is True,
        f"r1={r1!r} r2={r2!r}",
    )


def test_invented_url_still_rejected() -> bool:
    bucket: set[str] = set()
    asyncio.run(_run_batch([
        {"id": "c1", "name": "semantic_scholar_search",
         "arguments": {"query": "synthetic topic"}},
    ], bucket))
    r = _gate(INVENTED_URL, bucket)
    return _check("invented URL is still rejected", "error" in r, f"r={r!r}")


def test_error_and_argument_echo_not_recorded() -> bool:
    bucket: set[str] = set()
    asyncio.run(_run_batch([
        {"id": "c1", "name": "s2_get_references",
         "arguments": {"paper_id": INVENTED_URL}},
        {"id": "c2", "name": "some_echo_tool",
         "arguments": {"url": INVENTED_URL}},
    ], bucket))
    return _check(
        "URLs echoed from arguments or error results are not recorded",
        INVENTED_URL not in bucket,
        f"bucket={bucket!r}",
    )


def test_rejection_message_points_to_search() -> bool:
    r = _gate(INVENTED_URL, set())
    msg = r.get("error", "")
    ok = (
        "web_search" not in msg
        and "`search`" in msg
        and "prior tool result" in msg
    )
    return _check("rejection message points to `search` and prior tool results",
                  ok, f"msg={msg!r}")


def main() -> int:
    tests = [
        test_non_web_tool_urls_become_fetchable_same_turn,
        test_invented_url_still_rejected,
        test_error_and_argument_echo_not_recorded,
        test_rejection_message_points_to_search,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
