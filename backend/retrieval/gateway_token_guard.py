"""
Refuse forwarded identity that did not come through the gateway.

The retrieval service takes the user's identity from headers the gateway sets
(X-Munin-Email, -Role, -Group, -Name). That is safe only while nobody else can
reach this port, which the reference deployment guarantees by binding loopback
and arriving over an SSH tunnel. A split install that gets the transport wrong
(retrieval bound to a LAN or VPN address anyone on that network can reach)
would let any caller act as any user.

With MUNIN_GATEWAY_TOKEN set, a request carrying any of those headers must also
carry X-Munin-Gateway-Token with the same value, or it gets a 401 before
routing. The gateway and Caddy send it. Requests with no identity header are
untouched: endpoints that need a user already refuse them.

Unset means the old behaviour, so a deployment changes nothing until both
sides are given the token; the cluster-side scripts that call this API
directly with an identity header (benchmarks, smoke tests, deploy.sh verify)
must then send it too.
"""

from __future__ import annotations

import hmac
import json
import logging
import os

logger = logging.getLogger(__name__)

HEADER = b"x-munin-gateway-token"
IDENTITY_HEADERS = frozenset({b"x-munin-email", b"x-munin-role",
                              b"x-munin-group", b"x-munin-name"})


class GatewayTokenGuard:
    def __init__(self, app, token: str | None = None) -> None:
        self.app = app
        self.token = (os.environ.get("MUNIN_GATEWAY_TOKEN", "")
                      if token is None else token).strip()
        if not self.token:
            logger.info("MUNIN_GATEWAY_TOKEN unset: forwarded identity headers "
                        "are trusted from any caller that can reach this port")

    async def __call__(self, scope, receive, send) -> None:
        if self.token and scope["type"] in ("http", "websocket"):
            headers = scope.get("headers") or []
            if any(k in IDENTITY_HEADERS for k, _ in headers):
                sent = next((v for k, v in headers if k == HEADER), b"")
                if not hmac.compare_digest(sent, self.token.encode()):
                    logger.warning("refused %s %s: identity header without a valid "
                                   "gateway token", scope.get("method", scope["type"]),
                                   scope.get("path"))
                    if scope["type"] == "websocket":
                        await send({"type": "websocket.close", "code": 1008})
                        return
                    body = json.dumps({"error": {"message": "gateway token required"}}).encode()
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)
