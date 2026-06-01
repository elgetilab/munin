"""Tests for /admin/users/* endpoints."""
from tests.conftest import make_user, session_cookie


def _admin_cookies(auth_env, email="admin@example.org"):
    make_user(auth_env, email, "Admin", role="admin")
    return {"munin_session": session_cookie(auth_env, email, "Admin")}


def test_unauthenticated_returns_401(client):
    r = client.get("/admin/users")
    assert r.status_code == 401


def test_non_admin_returns_403(client, auth_env):
    make_user(auth_env, "alice@example.org", "Alice", role="user")
    cookies = {"munin_session": session_cookie(auth_env, "alice@example.org", "Alice")}
    r = client.get("/admin/users", cookies=cookies)
    assert r.status_code == 403


def test_group_leader_is_not_admin(client, auth_env):
    make_user(auth_env, "leader@example.org", "Leader", role="group_leader")
    cookies = {"munin_session": session_cookie(auth_env, "leader@example.org", "Leader")}
    r = client.get("/admin/users", cookies=cookies)
    assert r.status_code == 403


def test_list_users_returns_all(client, auth_env):
    cookies = _admin_cookies(auth_env)
    make_user(auth_env, "alice@example.org", "Alice")
    make_user(auth_env, "bob@example.org", "Bob")
    r = client.get("/admin/users", cookies=cookies)
    assert r.status_code == 200
    users = r.json()["users"]
    assert {u["name"] for u in users} == {"Admin", "Alice", "Bob"}
    alice = next(u for u in users if u["name"] == "Alice")
    assert alice["role"] == "user"
    assert alice["primary_email"] == "alice@example.org"
    assert alice["emails"] == ["alice@example.org"]


def test_create_user_succeeds(client, auth_env):
    cookies = _admin_cookies(auth_env)
    r = client.post("/admin/users", cookies=cookies,
                    json={"name": "Charlie", "email": "charlie@example.org",
                          "role": "user"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Charlie"
    assert body["role"] == "user"
    assert body["primary_email"] == "charlie@example.org"


def test_create_user_duplicate_email_409(client, auth_env):
    cookies = _admin_cookies(auth_env)
    make_user(auth_env, "alice@example.org", "Alice")
    r = client.post("/admin/users", cookies=cookies,
                    json={"name": "Alice2", "email": "alice@example.org"})
    assert r.status_code == 409


def test_create_user_invalid_role_400(client, auth_env):
    cookies = _admin_cookies(auth_env)
    r = client.post("/admin/users", cookies=cookies,
                    json={"name": "X", "email": "x@e.org", "role": "wizard"})
    assert r.status_code == 400


def test_create_user_with_unknown_group_400(client, auth_env):
    cookies = _admin_cookies(auth_env)
    r = client.post("/admin/users", cookies=cookies,
                    json={"name": "X", "email": "x@e.org", "group": "nope"})
    assert r.status_code == 400


def test_patch_user_updates_fields(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.patch(f"/admin/users/{uid}", cookies=cookies,
                     json={"name": "Alice X", "role": "group_leader",
                           "username": "alicex"})
    assert r.status_code == 200
    assert r.json()["name"] == "Alice X"
    assert r.json()["role"] == "group_leader"
    assert r.json()["username"] == "alicex"


def test_patch_cannot_demote_last_admin(client, auth_env):
    cookies = _admin_cookies(auth_env, email="admin@example.org")
    # admin is the only admin
    admin_id = auth_env.lookup_user("admin@example.org")["id"]
    r = client.patch(f"/admin/users/{admin_id}", cookies=cookies,
                     json={"role": "user"})
    assert r.status_code == 409


def test_patch_can_demote_when_other_admin_exists(client, auth_env):
    cookies = _admin_cookies(auth_env)
    make_user(auth_env, "other@example.org", "Other", role="admin")
    admin_id = auth_env.lookup_user("admin@example.org")["id"]
    r = client.patch(f"/admin/users/{admin_id}", cookies=cookies,
                     json={"role": "user"})
    assert r.status_code == 200


def test_delete_user(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.delete(f"/admin/users/{uid}", cookies=cookies)
    assert r.status_code == 204
    assert auth_env.lookup_user("alice@example.org") is None


def test_delete_user_cascades_emails_and_sessions(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    # Add an alias and a session.
    client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                json={"email": "alice@alias.org"})
    auth_env.create_session("alice@example.org", "Alice")
    auth_env.create_session("alice@alias.org", "Alice")
    # Delete.
    r = client.delete(f"/admin/users/{uid}", cookies=cookies)
    assert r.status_code == 204
    conn = auth_env.get_db()
    assert conn.execute("SELECT COUNT(*) AS n FROM user_emails").fetchone()["n"] == 1  # admin's
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE email IN (?,?)",
        ("alice@example.org", "alice@alias.org"),
    ).fetchone()["n"] == 0
    conn.close()


def test_delete_cannot_delete_self(client, auth_env):
    cookies = _admin_cookies(auth_env)
    admin_id = auth_env.lookup_user("admin@example.org")["id"]
    r = client.delete(f"/admin/users/{admin_id}", cookies=cookies)
    assert r.status_code == 409


def test_delete_cannot_delete_last_admin(client, auth_env):
    # Two admins: A (acting) and B. Drop B's role to user first, then try to delete A.
    cookies = _admin_cookies(auth_env)
    bid = make_user(auth_env, "b@example.org", "B", role="admin")
    client.patch(f"/admin/users/{bid}", cookies=cookies, json={"role": "user"})
    admin_id = auth_env.lookup_user("admin@example.org")["id"]
    r = client.delete(f"/admin/users/{admin_id}", cookies=cookies)
    # 409 because admin is the last admin (also caught by "cannot delete yourself")
    assert r.status_code == 409


def test_add_email_alias(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                    json={"email": "alice@alias.org"})
    assert r.status_code == 201
    body = r.json()
    assert "alice@alias.org" in body["emails"]
    assert body["primary_email"] == "alice@example.org"
    # alias resolves to same user
    assert auth_env.lookup_user("alice@alias.org")["id"] == uid


def test_add_email_duplicate_409(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    make_user(auth_env, "bob@example.org", "Bob")
    r = client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                    json={"email": "bob@example.org"})
    assert r.status_code == 409


def test_remove_non_primary_email(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                json={"email": "alice@alias.org"})
    r = client.delete(f"/admin/users/{uid}/emails/alice@alias.org", cookies=cookies)
    assert r.status_code == 200
    assert auth_env.lookup_user("alice@alias.org") is None
    assert auth_env.lookup_user("alice@example.org")["primary_email"] == "alice@example.org"


def test_remove_primary_promotes_next(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                json={"email": "alice@alias.org"})
    r = client.delete(f"/admin/users/{uid}/emails/alice@example.org", cookies=cookies)
    assert r.status_code == 200
    user = auth_env.lookup_user("alice@alias.org")
    assert user["primary_email"] == "alice@alias.org"


def test_remove_last_email_409(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.delete(f"/admin/users/{uid}/emails/alice@example.org", cookies=cookies)
    assert r.status_code == 409


def test_set_primary_email(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    client.post(f"/admin/users/{uid}/emails", cookies=cookies,
                json={"email": "alice@alias.org"})
    r = client.put(f"/admin/users/{uid}/emails/alice@alias.org/primary", cookies=cookies)
    assert r.status_code == 200
    assert r.json()["primary_email"] == "alice@alias.org"


def test_export_users_csv(client, auth_env):
    cookies = _admin_cookies(auth_env)
    make_user(auth_env, "alice@example.org", "Alice")
    r = client.get("/admin/users.csv", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert body.startswith("email,name,role")
    assert "alice@example.org,Alice,user" in body
    assert "admin@example.org,Admin,admin" in body
