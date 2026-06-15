"""Tests for the tusd pre-create KB gate (P1 #11 commit 4)."""
import asyncio
import json

import httpx
import pytest


def _payload(email: str | None) -> dict:
    """Minimal tusd v2-style pre-create payload."""
    headers: dict[str, list[str]] = {}
    if email is not None:
        headers["X-Munin-Email"] = [email]
    return {
        "Type": "pre-create",
        "Event": {
            "HTTPRequest": {"Header": headers},
            "Upload": {"ID": "tx", "MetaData": {"filename": "paper.pdf"}},
        },
    }


def _mock_transport(role: str | None, status: int = 200):
    """Build an httpx MockTransport that returns the given role payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 404:
            return httpx.Response(404, json={
                "email": request.url.params.get("email", ""),
                "role": None,
                "allowed_kb_contribution": False,
            })
        if status >= 400:
            return httpx.Response(status, text="boom")
        return httpx.Response(200, json={
            "email": request.url.params.get("email"),
            "role": role,
            "group": None,
            "allowed_kb_contribution": role in ("group_leader", "admin"),
        })
    return httpx.MockTransport(handler)


def _patch_http(monkeypatch, hook_env, transport: httpx.MockTransport):
    """Force hook_service.httpx.AsyncClient to use our transport."""
    real_async_client = hook_env.httpx.AsyncClient

    class PatchedClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(hook_env.httpx, "AsyncClient", PatchedClient)


def test_pre_create_allows_group_leader(hook_env, monkeypatch):
    _patch_http(monkeypatch, hook_env, _mock_transport("group_leader"))
    result = asyncio.run(hook_env.handle_pre_create(_payload("leader@e.org")))
    assert result == {"ok": True}


def test_pre_create_allows_admin(hook_env, monkeypatch):
    _patch_http(monkeypatch, hook_env, _mock_transport("admin"))
    result = asyncio.run(hook_env.handle_pre_create(_payload("admin@e.org")))
    assert result == {"ok": True}


def test_pre_create_rejects_user_role(hook_env, monkeypatch):
    _patch_http(monkeypatch, hook_env, _mock_transport("user"))
    result = asyncio.run(hook_env.handle_pre_create(_payload("alice@e.org")))
    # JSONResponse subclass — peek at the body.
    assert result.status_code == 403
    body = json.loads(result.body.decode("utf-8"))
    assert body["RejectUpload"] is True
    assert body["HTTPResponse"]["StatusCode"] == 403


def test_pre_create_rejects_group_leader_without_group(hook_env, monkeypatch):
    """Auth now denies a group leader with no research group; the hook must
    reject and explain the group requirement."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "email": request.url.params.get("email"),
            "role": "group_leader",
            "group": None,
            "allowed_kb_contribution": False,
        })
    _patch_http(monkeypatch, hook_env, httpx.MockTransport(handler))
    result = asyncio.run(hook_env.handle_pre_create(_payload("leader@e.org")))
    assert result.status_code == 403
    body = json.loads(result.body.decode("utf-8"))
    assert "assigned to a research group" in body["HTTPResponse"]["Body"]


def test_pre_create_rejects_unknown_email(hook_env, monkeypatch):
    _patch_http(monkeypatch, hook_env, _mock_transport(None, status=404))
    result = asyncio.run(hook_env.handle_pre_create(_payload("nobody@e.org")))
    assert result.status_code == 403


def test_pre_create_rejects_missing_email(hook_env):
    result = asyncio.run(hook_env.handle_pre_create(_payload(None)))
    assert result.status_code == 403


def test_pre_create_fails_closed_when_auth_unreachable(hook_env, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("auth down")
    _patch_http(monkeypatch, hook_env, httpx.MockTransport(boom))
    result = asyncio.run(hook_env.handle_pre_create(_payload("alice@e.org")))
    assert result.status_code == 403


def test_pre_create_fails_open_when_configured(hook_env, monkeypatch):
    monkeypatch.setattr(hook_env, "KB_GATE_FAIL_OPEN", True)
    def boom(request):
        raise httpx.ConnectError("auth down")
    _patch_http(monkeypatch, hook_env, httpx.MockTransport(boom))
    result = asyncio.run(hook_env.handle_pre_create(_payload("alice@e.org")))
    assert result == {"ok": True}


def test_pre_create_no_token_fails_closed(hook_env, monkeypatch):
    monkeypatch.setattr(hook_env, "KB_GATE_TOKEN", "")
    result = asyncio.run(hook_env.handle_pre_create(_payload("alice@e.org")))
    assert result.status_code == 403


def test_check_helper_passes_bearer_token(hook_env, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["email"] = request.url.params.get("email")
        return httpx.Response(200, json={
            "email": "alice@e.org", "role": "group_leader",
            "group": None, "allowed_kb_contribution": True,
        })

    _patch_http(monkeypatch, hook_env, httpx.MockTransport(handler))
    asyncio.run(hook_env.check_kb_contribution_allowed("alice@e.org"))
    assert captured["auth"] == "Bearer test-token"
    assert captured["email"] == "alice@e.org"
