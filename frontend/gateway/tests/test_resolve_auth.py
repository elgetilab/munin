"""A client-sent X-Munin-Email must never outrank a Bearer token.

Regression for the 2026-09 review finding: api.muninai.org has no
forward-auth, and resolve_auth() checked X-Munin-Email before the Bearer
key, so any request carrying that header was served as that user. Caddy now
strips the header on the API host; these tests pin the gateway's own half.
"""
import httpx


def _add_key(gw_env, email: str) -> str:
    raw = "sk-munin-" + "ab" * 16
    conn = gw_env.get_db()
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, key_prefix, user_email, name, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("key_test", gw_env.hash_key(raw), raw[:16], email, "t", "2026-09-30T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return raw


def _mock_upstream(gw_env, monkeypatch, seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["email"] = request.headers.get("X-Munin-Email")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        gw_env, "http_client",
        httpx.AsyncClient(base_url=gw_env.CLUSTER_TUNNEL,
                          transport=httpx.MockTransport(handler)),
    )
    return seen


def test_bearer_wins_over_spoofed_email(client, gw_env, monkeypatch):
    raw = _add_key(gw_env, "owner@example.org")
    seen = _mock_upstream(gw_env, monkeypatch, {})
    r = client.get("/api/chats", headers={
        "Authorization": f"Bearer {raw}",
        "X-Munin-Email": "victim@example.org",
    })
    assert r.status_code == 200
    assert seen["email"] == "owner@example.org"


def test_invalid_bearer_does_not_fall_back_to_header(client, gw_env, monkeypatch):
    seen = _mock_upstream(gw_env, monkeypatch, {})
    r = client.get("/api/chats", headers={
        "Authorization": "Bearer sk-munin-not-a-real-key",
        "X-Munin-Email": "victim@example.org",
    })
    assert r.status_code == 401
    assert "email" not in seen


def test_browser_header_still_resolves(client, gw_env, monkeypatch):
    seen = _mock_upstream(gw_env, monkeypatch, {})
    r = client.get("/api/chats", headers={"X-Munin-Email": "alice@example.org"})
    assert r.status_code == 200
    assert seen["email"] == "alice@example.org"


# ── /api/X -> /X fallback is limited to the search page's routes ────────────

def _mock_root_only(gw_env, monkeypatch, seen: list):
    """Upstream that 404s every /api/ path and answers every root path."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.startswith("/api/"):
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        gw_env, "http_client",
        httpx.AsyncClient(base_url=gw_env.CLUSTER_TUNNEL,
                          transport=httpx.MockTransport(handler)),
    )


def test_fallback_reaches_search_routes(client, gw_env, monkeypatch):
    seen: list = []
    _mock_root_only(gw_env, monkeypatch, seen)
    r = client.get("/api/sources", headers={"X-Munin-Email": "alice@example.org"})
    assert r.status_code == 200
    assert seen == ["/api/sources", "/sources"]


def test_fallback_does_not_reach_other_root_routes(client, gw_env, monkeypatch):
    for path in ("deepresearch/jobs", "mcp/tools", "graph/stats"):
        seen: list = []
        _mock_root_only(gw_env, monkeypatch, seen)
        r = client.get(f"/api/{path}", headers={"X-Munin-Email": "alice@example.org"})
        assert r.status_code == 404, path
        assert seen == [f"/api/{path}"], path
