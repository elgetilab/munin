"""Signing in with any of a user's addresses lands in the same account.

Downstream services key all user data (chats, uploads, API keys, usage)
on X-Munin-Email. It used to carry the address typed at login, so an
alias opened an empty account (2026-11: a user given a second address
saw none of her chats). X-Munin-Email now carries users.identity_email:
set once from the primary address, never changed afterwards.
"""
from tests.conftest import make_user, session_cookie
from tests.test_admin_users import _admin_cookies


def _add_alias(auth_env, user_id, email):
    conn = auth_env.get_db()
    auth_env._attach_email(conn, user_id, email, is_primary=False)
    conn.commit()
    conn.close()


def test_alias_login_forwards_identity(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    cookies = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Email"] == "k@uni.de"


def test_auth_me_reports_identity_and_login_address(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    cookies = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    body = client.get("/auth/me", cookies=cookies).json()
    assert body["email"] == "k@uni.de"
    assert body["login_email"] == "k.private@mail.com"


def test_profile_saved_under_alias_login_is_shared(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    alias = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    primary = {"munin_session": session_cookie(auth_env, "k@uni.de", "Kim")}
    r = client.patch("/auth/me", json={"nickname": "Kimmy"}, cookies=alias)
    assert r.status_code == 200, r.text
    assert client.get("/auth/me", cookies=primary).json()["name"] == "Kimmy"


def test_changing_primary_keeps_identity(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    r = client.put(f"/admin/users/{uid}/emails/k.private@mail.com/primary",
                   cookies=_admin_cookies(auth_env))
    assert r.status_code == 200
    cookies = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    assert client.get("/auth/check", cookies=cookies).headers["X-Munin-Email"] == "k@uni.de"


def test_removing_identity_address_keeps_identity(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    r = client.delete(f"/admin/users/{uid}/emails/k@uni.de", cookies=_admin_cookies(auth_env))
    assert r.status_code == 200
    cookies = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    assert client.get("/auth/check", cookies=cookies).headers["X-Munin-Email"] == "k@uni.de"
    # Role lookups by the identity (upload hook, retrieval admin gate) still resolve.
    assert auth_env.lookup_user("k@uni.de")["id"] == uid


def test_removed_identity_address_cannot_go_to_another_user(client, auth_env):
    """Otherwise the other user would be sent Kim's identity and see her chats."""
    admin = _admin_cookies(auth_env)
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    client.delete(f"/admin/users/{uid}/emails/k@uni.de", cookies=admin)
    other = make_user(auth_env, "o@uni.de", "Otto")
    r = client.post(f"/admin/users/{other}/emails", json={"email": "k@uni.de"}, cookies=admin)
    assert r.status_code == 409
    r = client.post("/admin/users", json={"first_name": "New", "email": "k@uni.de",
                                          "role": "user"}, cookies=admin)
    assert r.status_code == 409


def test_migration_backfills_and_moves_alias_profiles(auth_env):
    auth_env.init_db()
    conn = auth_env.get_db()
    uid = auth_env._insert_user(conn, first_name="Ann", last_name=None, role="user")
    conn.execute(
        "INSERT INTO user_emails (email, user_id, is_primary, added_at) VALUES "
        "('ann@gmx.de', ?, 1, 'x'), ('ann@med.de', ?, 0, 'x')", (uid, uid))
    conn.execute("INSERT INTO user_profiles (email, nickname) VALUES ('ann@med.de', 'Annie')")
    conn.commit()
    conn.close()
    summary = auth_env.migrate_identity_email_v1()
    assert summary["rows_backfilled"] == 1
    assert summary["profiles_moved"] == 1
    assert auth_env.lookup_user("ann@med.de")["identity_email"] == "ann@gmx.de"
    assert auth_env.get_user_profile("ann@gmx.de")["nickname"] == "Annie"
    assert auth_env.migrate_identity_email_v1()["already_run"] is True
