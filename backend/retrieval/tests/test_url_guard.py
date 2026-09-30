"""
Outbound fetches of untrusted URLs must not reach internal services.

Regression for the 2026-09 review finding S4: source({url}) fetched any URL
with redirects on, and web_fetch's allowlist was a substring test that
"http://127.0.0.1:6333/?x=//doi.org/10.1/x" passed, so a prompt-injected page
could make the retrieval container read Qdrant, Neo4j or the sandbox and put
the response in the chat. Runs offline: literal IPs and /etc/hosts names only,
and redirects are driven through a MockTransport.

Run:
    python backend/retrieval/tests/test_url_guard.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import url_guard  # noqa: E402
from url_guard import BlockedURL, GuardedTransport  # noqa: E402

PUBLIC_IP = "93.184.215.14"  # a literal global address, resolved without DNS


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _blocked(url: str) -> bool:
    try:
        await url_guard.assert_public_url(httpx.URL(url))
    except BlockedURL:
        return True
    return False


async def test_internal_targets_refused() -> list[bool]:
    out = []
    for url in ("http://127.0.0.1:6333/collections", "http://localhost:7474/",
                "http://10.0.0.5/", "http://172.18.0.3:8090/", "http://192.168.1.1/",
                "http://169.254.169.254/latest/meta-data/", "http://[::1]:8080/",
                "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0.0.0.0:8080/",
                "http://100.64.0.1/", "file:///etc/passwd", "gopher://example.org/"):
        out.append(_check(f"refused {url}", await _blocked(url)))
    return out


async def test_public_target_allowed() -> list[bool]:
    return [_check("public literal IP allowed", not await _blocked(f"https://{PUBLIC_IP}/x"))]


async def test_redirect_into_internal_refused() -> list[bool]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:6333/collections"})

    transport = GuardedTransport(inner=httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as c:
        try:
            await c.get(f"http://{PUBLIC_IP}/start")
            refused = False
        except BlockedURL:
            refused = True
    return [_check("public URL redirecting inward is refused at the hop", refused),
            _check("internal hop never sent", seen == [f"http://{PUBLIC_IP}/start"], str(seen))]


async def test_read_capped() -> list[bool]:
    body = b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        async with c.stream("GET", "http://t/") as r:
            data = await url_guard.read_capped(r, max_bytes=1000)
    return [_check("body cut at the cap", len(data) == 1000, str(len(data)))]


def test_canonical_url_is_host_based() -> list[bool]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp" / "tools"))
    from mcp.tools import web
    out = []
    for url in ("http://127.0.0.1:6333/collections?x=//doi.org/10.1234/abc",
                "http://localhost:7474/db/data/?//arxiv.org/abs/2101.00001",
                "http://evil.example/doi.org/10.1234/abc"):
        out.append(_check(f"not canonical: {url}", not web._is_canonical_reference_url(url)))
    for url in ("https://doi.org/10.1038/nature12373",
                "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3159761/"):
        out.append(_check(f"canonical: {url}", web._is_canonical_reference_url(url)))
    return out


async def _main() -> int:
    results: list[bool] = []
    for t in (test_internal_targets_refused, test_public_target_allowed,
              test_redirect_into_internal_refused, test_read_capped):
        results += await t()
    results += test_canonical_url_is_host_based()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
