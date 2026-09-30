"""
Refuse every request that comes from the sandbox container.

The retrieval service trusts X-Munin-Email, and the sandbox shares a docker
network with it (sandbox-net, so retrieval can reach the sandbox). run_python
kernels run inside the sandbox container, and until kernels get their own
network namespace they could open http://retrieval:8080 and act as any user
or admin (2026-09-30 review, S6). The sandbox itself never calls retrieval,
so any request whose peer address is the sandbox's is refused before routing.

The sandbox's address is resolved from SANDBOX_URL's host and re-resolved at
most every REFRESH_S seconds, so a recreated sandbox container with a new IP
is picked up. A failed lookup keeps the last known addresses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

REFRESH_S = 10.0


def _sandbox_host() -> str:
    url = os.environ.get("SANDBOX_URL", "http://sandbox:8090")
    return urlparse(url).hostname or "sandbox"


class SandboxPeerGuard:
    def __init__(self, app, host: str | None = None, refresh_s: float = REFRESH_S) -> None:
        self.app = app
        self.host = host or _sandbox_host()
        self.refresh_s = refresh_s
        self._addrs: frozenset[str] = frozenset()
        self._resolved_at = float("-inf")

    async def _sandbox_addrs(self) -> frozenset[str]:
        now = time.monotonic()
        if now - self._resolved_at >= self.refresh_s:
            self._resolved_at = now
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    self.host, None, type=socket.SOCK_STREAM)
                self._addrs = frozenset(info[4][0] for info in infos)
            except OSError:
                pass  # sandbox not running: keep the last known addresses
        return self._addrs

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if client and client[0] in await self._sandbox_addrs():
                logger.warning("refused %s %s from the sandbox container (%s)",
                               scope.get("method", scope["type"]), scope.get("path"), client[0])
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps({"error": {"message": "forbidden"}}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
