"""Per-user rows here are keyed by the user key ("u12", X-Munin-User), not
an address, and the backend is told both: X-Munin-User to store under,
X-Munin-Email (the primary address) for display and attribution.

API keys store their owner's user key; the owner's address, name and role
come from munin-auth's check-role, so quotas.yml overrides and ADMIN_EMAILS
keep matching by address for API traffic too.
"""
import httpx
import pytest

from tests.test_resolve_auth import _add_key


@pytest.fixture
def upstream(gw_env, monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k: request.headers.get(k) for k in ("X-Munin-User", "X-Munin-Email", "X-Munin-Name")})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(gw_env, "http_client", httpx.AsyncClient(
        base_url=gw_env.CLUSTER_TUNNEL, transport=httpx.MockTransport(handler)))
    return seen


@pytest.fixture
def auth(gw_env, monkeypatch):
    """Stand-in for munin-auth's check-role; records the lookups."""
    users = {"u7": {"user": "u7", "email": "ann@gmx.de", "name": "Ann K", "role": "user"}}
    calls = []

    async def ask(owner):
        calls.append(owner)
        if owner in users:
            return httpx.Response(200, json=users[owner])
        return httpx.Response(404, json={"role": None})

    monkeypatch.setattr(gw_env, "_ask_auth", ask)
    gw_env._owner_cache.clear()
    return {"users": users, "calls": calls}


BROWSER = {"X-Munin-User": "u7", "X-Munin-Email": "ann@gmx.de", "X-Munin-Name": "Ann K"}


def test_browser_request_forwards_key_and_address(client, gw_env, upstream):
    assert client.get("/api/chats", headers=BROWSER).status_code == 200
    assert upstream == {"X-Munin-User": "u7", "X-Munin-Email": "ann@gmx.de", "X-Munin-Name": "Ann K"}
    conn = gw_env.get_db()
    row = conn.execute("SELECT email, address FROM user_activity").fetchone()
    conn.close()
    assert (row["email"], row["address"]) == ("u7", "ann@gmx.de")


def test_caller_without_user_key_is_keyed_by_address(client, upstream):
    """Scripts and benchmarks that predate user keys keep working."""
    client.get("/api/chats", headers={"X-Munin-Email": "bench@example.org"})
    assert upstream["X-Munin-User"] == "bench@example.org"
    assert upstream["X-Munin-Email"] == "bench@example.org"


def test_keys_are_stored_and_listed_by_user_key(client, gw_env):
    r = client.post("/api/keys", json={"name": "laptop"}, headers=BROWSER)
    assert r.status_code == 200
    conn = gw_env.get_db()
    assert conn.execute("SELECT user_email FROM api_keys").fetchone()[0] == "u7"
    conn.close()
    # Same person after a primary change: same key, same keys.
    other_address = {**BROWSER, "X-Munin-Email": "ann@med.de"}
    assert [k["name"] for k in client.get("/api/keys", headers=other_address).json()["keys"]] == ["laptop"]


def test_api_key_owner_is_resolved_through_auth(client, gw_env, upstream, auth):
    raw = _add_key(gw_env, "u7")
    r = client.get("/api/chats", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert upstream == {"X-Munin-User": "u7", "X-Munin-Email": "ann@gmx.de", "X-Munin-Name": "Ann K"}
    # Cached: a second request does not ask again.
    client.get("/api/chats", headers={"Authorization": f"Bearer {raw}"})
    assert auth["calls"] == ["u7"]


def test_api_key_of_a_deleted_user_stops_working(client, gw_env, upstream, auth):
    raw = _add_key(gw_env, "u404")
    assert client.get("/api/chats", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_api_key_still_works_when_auth_is_down(client, gw_env, upstream, monkeypatch):
    async def down(owner):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(gw_env, "_ask_auth", down)
    gw_env._owner_cache.clear()
    raw = _add_key(gw_env, "u7")
    assert client.get("/api/chats", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert upstream["X-Munin-User"] == "u7"


def test_quota_override_matches_the_owners_address(client, gw_env, upstream, auth):
    gw_env.quotas_config.setdefault("overrides", {})["ann@gmx.de"] = {"tokens_per_month": 0}
    raw = _add_key(gw_env, "u7")
    r = client.post("/api/search", headers={"Authorization": f"Bearer {raw}"}, json={})
    assert r.status_code == 429


def test_admin_views_show_addresses(client, gw_env, upstream):
    client.get("/api/chats", headers=BROWSER)
    gw_env.log_usage("u7", "browser", None, "/api/x", tokens_total=5)
    admin = {"X-Munin-User": "u1", "X-Munin-Email": "boss@x.org", "X-Munin-Role": "admin"}
    act = client.get("/api/usage/admin/activity", headers=admin).json()
    assert {(u["email"], u["user"]) for u in act["all_users"]} >= {("ann@gmx.de", "u7")}
    top = client.get("/api/usage/admin", headers=admin).json()["top_users"]
    assert top[0]["email"] == "ann@gmx.de" and top[0]["user"] == "u7"
