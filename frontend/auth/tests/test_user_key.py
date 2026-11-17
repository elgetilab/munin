"""Every service stores a user's data under X-Munin-User: "u" + users.id.

History: data used to be keyed on X-Munin-Email, which carried the address
typed at login, so an alias opened an empty account (2026-11). A frozen
users.identity_email fixed that; the numeric key replaced it so addresses
are only sign-in and contact details: they can be added, removed, made
primary and even handed to another person without moving anyone's data.
X-Munin-Email now carries the primary address, for display and mail.
"""
from tests.conftest import make_user, session_cookie
from tests.test_admin_users import _admin_cookies


def _add_alias(auth_env, user_id, email):
    conn = auth_env.get_db()
    auth_env._attach_email(conn, user_id, email, is_primary=False)
    conn.commit()
    conn.close()


def _check(client, auth_env, login):
    cookies = {"munin_session": session_cookie(auth_env, login, "Kim")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    return r.headers


def test_any_address_gives_the_same_key(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    a = _check(client, auth_env, "k@uni.de")
    b = _check(client, auth_env, "k.private@mail.com")
    assert a["X-Munin-User"] == b["X-Munin-User"] == f"u{uid}"
    assert a["X-Munin-Email"] == b["X-Munin-Email"] == "k@uni.de"


def test_auth_me_reports_key_primary_and_login(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    cookies = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    body = client.get("/auth/me", cookies=cookies).json()
    assert body["user"] == f"u{uid}"
    assert body["email"] == "k@uni.de"
    assert body["login_email"] == "k.private@mail.com"


def test_profile_saved_under_alias_login_is_shared(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    alias = {"munin_session": session_cookie(auth_env, "k.private@mail.com", "Kim")}
    primary = {"munin_session": session_cookie(auth_env, "k@uni.de", "Kim")}
    assert client.patch("/auth/me", json={"nickname": "Kimmy"}, cookies=alias).status_code == 200
    assert client.get("/auth/me", cookies=primary).json()["name"] == "Kimmy"


def test_changing_primary_keeps_the_key_and_changes_the_address(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, uid, "k.private@mail.com")
    r = client.put(f"/admin/users/{uid}/emails/k.private@mail.com/primary",
                   cookies=_admin_cookies(auth_env))
    assert r.status_code == 200
    h = _check(client, auth_env, "k@uni.de")
    assert h["X-Munin-User"] == f"u{uid}"
    assert h["X-Munin-Email"] == "k.private@mail.com"


def test_removed_address_can_go_to_another_user_without_their_data(client, auth_env):
    admin = _admin_cookies(auth_env)
    kim = make_user(auth_env, "k@uni.de", "Kim")
    _add_alias(auth_env, kim, "k.private@mail.com")
    assert client.delete(f"/admin/users/{kim}/emails/k@uni.de", cookies=admin).status_code == 200
    otto = make_user(auth_env, "o@uni.de", "Otto")
    r = client.post(f"/admin/users/{otto}/emails", json={"email": "k@uni.de"}, cookies=admin)
    assert r.status_code == 201
    assert _check(client, auth_env, "k@uni.de")["X-Munin-User"] == f"u{otto}"
    assert _check(client, auth_env, "k.private@mail.com")["X-Munin-User"] == f"u{kim}"


def test_check_role_resolves_a_user_key(client, auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "KB_GATE_TOKEN", "t")
    uid = make_user(auth_env, "k@uni.de", "Kim", role="admin")
    r = client.get(f"/admin/check-role?email=u{uid}", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    body = r.json()
    assert (body["user"], body["email"], body["role"]) == (f"u{uid}", "k@uni.de", "admin")
    assert client.get("/admin/check-role?email=u999999",
                      headers={"Authorization": "Bearer t"}).status_code == 404


def test_session_of_a_deleted_user_ends(client, auth_env):
    uid = make_user(auth_env, "k@uni.de", "Kim")
    cookie = session_cookie(auth_env, "k@uni.de", "Kim")
    client.delete(f"/admin/users/{uid}", cookies=_admin_cookies(auth_env))
    assert auth_env.validate_session(cookie) is None


def test_identity_migration_still_backfills(auth_env):
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
    assert summary["rows_backfilled"] == 1 and summary["profiles_moved"] == 1
    assert auth_env.migrate_identity_email_v1()["already_run"] is True


def test_user_key_migration_moves_profiles(auth_env):
    auth_env.init_db()
    conn = auth_env.get_db()
    uid = auth_env._insert_user(conn, first_name="Ann", last_name=None, role="user")
    auth_env._attach_email(conn, uid, "ann@gmx.de", is_primary=True)
    conn.execute("INSERT INTO user_profiles (email, nickname) VALUES ('ann@gmx.de', 'Annie')")
    conn.execute("INSERT INTO display_names (email, name) VALUES ('ann@gmx.de', 'Ann G')")
    conn.commit()
    conn.close()
    summary = auth_env.migrate_user_key_v1()
    assert summary["profiles_moved"] == 2
    assert auth_env.get_user_profile(f"u{uid}")["nickname"] == "Annie"
    assert auth_env.get_display_name(f"u{uid}") == "Ann G"
    assert auth_env.get_user_profile("ann@gmx.de") is None
    assert auth_env.migrate_user_key_v1()["already_run"] is True
