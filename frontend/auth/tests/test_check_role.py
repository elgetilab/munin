"""Tests for /admin/check-role (P1 #11 commit 4)."""
from fastapi.testclient import TestClient

from tests.conftest import make_user, session_cookie


def test_check_role_requires_auth(client):
    r = client.get("/admin/check-role", params={"email": "x@e.org"})
    assert r.status_code == 401


def test_check_role_bearer_token_user(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    auth_env.init_db()
    make_user(auth_env, "alice@e.org", "Alice", role="user")
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        r = c.get("/admin/check-role",
                  params={"email": "alice@e.org"},
                  headers={"Authorization": "Bearer k-tok"})
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "user"
        assert body["allowed_kb_contribution"] is False


def test_check_role_bearer_token_group_leader(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    auth_env.init_db()
    make_user(auth_env, "leader@e.org", "Leader", role="group_leader")
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        r = c.get("/admin/check-role",
                  params={"email": "leader@e.org"},
                  headers={"Authorization": "Bearer k-tok"})
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "group_leader"
        assert body["allowed_kb_contribution"] is True


def test_check_role_admin_allowed(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    auth_env.init_db()
    make_user(auth_env, "admin@e.org", "Admin", role="admin")
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        r = c.get("/admin/check-role",
                  params={"email": "admin@e.org"},
                  headers={"Authorization": "Bearer k-tok"})
        assert r.json()["allowed_kb_contribution"] is True


def test_check_role_unknown_email_404(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    auth_env.init_db()
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        r = c.get("/admin/check-role",
                  params={"email": "nobody@e.org"},
                  headers={"Authorization": "Bearer k-tok"})
        assert r.status_code == 404
        assert r.json()["allowed_kb_contribution"] is False


def test_check_role_alias_email_resolves_to_same_user(client, auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    uid = make_user(auth_env, "alice@primary.org", "Alice", role="group_leader")
    # Add an alias.
    cookies = {"munin_session": session_cookie(auth_env, "admin@e.org", "Admin")}
    make_user(auth_env, "admin@e.org", "Admin", role="admin")
    client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                json={"email": "alice@alias.org"})
    # The alias should resolve to the same role.
    r = client.get("/admin/check-role",
                   params={"email": "alice@alias.org"},
                   headers={"Authorization": "Bearer k-tok"})
    assert r.status_code == 200
    assert r.json()["role"] == "group_leader"
    assert r.json()["email"] == "alice@primary.org"  # primary, not alias


def test_check_role_wrong_token_falls_back_to_cookie(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    auth_env.init_db()
    make_user(auth_env, "x@e.org", "X", role="user")
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        # Wrong token -> falls through to cookie check -> no cookie -> 401
        r = c.get("/admin/check-role",
                  params={"email": "x@e.org"},
                  headers={"Authorization": "Bearer WRONG"})
        assert r.status_code == 401


def test_check_role_admin_session_works(client, auth_env):
    make_user(auth_env, "admin@e.org", "Admin", role="admin")
    make_user(auth_env, "x@e.org", "X", role="group_leader")
    cookies = {"munin_session": session_cookie(auth_env, "admin@e.org", "Admin")}
    r = client.get("/admin/check-role",
                   params={"email": "x@e.org"},
                   cookies=cookies)
    assert r.status_code == 200
    assert r.json()["allowed_kb_contribution"] is True


def test_check_role_requires_email_param(client, auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "k-tok")
    r = client.get("/admin/check-role",
                   headers={"Authorization": "Bearer k-tok"})
    assert r.status_code == 400
