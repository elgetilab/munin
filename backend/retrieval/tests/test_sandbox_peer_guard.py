"""
Requests from the sandbox container are refused before routing.

Regression for the 2026-09-30 finding that run_python kernels could reach
retrieval:8080 over sandbox-net and, since retrieval trusts X-Munin-Email,
act as any user or admin. Drives the real `main.app` with httpx's
ASGITransport, which lets the test choose the peer address; SANDBOX_URL
points at a literal IP so no DNS is needed.

    docker exec munin-retrieval python /app/tests/test_sandbox_peer_guard.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SANDBOX_IP = "10.99.0.2"
os.environ["SANDBOX_URL"] = f"http://{SANDBOX_IP}:8090"

import httpx  # noqa: E402

import main  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _get(path: str, peer: str, **headers) -> httpx.Response:
    transport = httpx.ASGITransport(app=main.app, client=(peer, 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path, headers=headers)


async def _main() -> int:
    results = []
    for path in ("/api/chats", "/api/usage/me", "/deepresearch/jobs", "/mcp/tools", "/"):
        r = await _get(path, SANDBOX_IP, **{"X-Munin-Email": "victim@example.org"})
        results.append(_check(f"sandbox peer {path} -> 403", r.status_code == 403,
                              f"{r.status_code} {r.text[:100]}"))
    r = await _get("/", "10.99.0.3")
    results.append(_check("other peers still served", r.status_code == 200, str(r.status_code)))
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
