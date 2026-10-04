"""GatewayTokenGuard: forwarded identity needs the gateway token when one is set."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway_token_guard import GatewayTokenGuard  # noqa: E402


def _call(token: str, headers: dict) -> int:
    reached = []

    async def app(scope, receive, send):
        reached.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": "/api/chats",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(GatewayTokenGuard(app, token=token)(scope, None, send))
    return sent[0]["status"]


def test_unset_token_changes_nothing():
    assert _call("", {"X-Munin-Email": "a@b.org"}) == 200


def test_identity_without_token_is_refused():
    assert _call("s3cret", {"X-Munin-Email": "a@b.org"}) == 401
    assert _call("s3cret", {"X-Munin-Role": "admin"}) == 401


def test_wrong_token_is_refused_and_right_token_passes():
    assert _call("s3cret", {"X-Munin-Email": "a@b.org",
                            "X-Munin-Gateway-Token": "nope"}) == 401
    assert _call("s3cret", {"X-Munin-Email": "a@b.org",
                            "X-Munin-Gateway-Token": "s3cret"}) == 200


def test_requests_without_identity_are_untouched():
    assert _call("s3cret", {}) == 200
