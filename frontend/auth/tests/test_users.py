"""Tests for the users / user_emails / groups schema + whitelist seed."""


def _write_csv(path, rows):
    path.write_text(
        "email,name,role\n" + "\n".join(",".join(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_init_db_creates_new_tables(auth_env, tmp_path):
    auth_env.init_db()
    conn = auth_env.get_db()
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    for required in ("users", "user_emails", "groups",
                     "sessions", "display_names", "user_profiles"):
        assert required in tables, f"missing table {required}"


def test_seed_imports_whitelist_once(auth_env, tmp_path):
    _write_csv(auth_env.WHITELIST_PATH, [
        ("admin@example.org", "Admin", "admin"),
        ("alice@example.org", "Alice", "user"),
    ])
    auth_env.init_db()

    summary = auth_env.seed_users_from_whitelist()
    assert summary == {"imported": 2, "skipped": 0, "missing_csv": False}

    # Re-running is a no-op (every email already present).
    again = auth_env.seed_users_from_whitelist()
    assert again == {"imported": 0, "skipped": 2, "missing_csv": False}

    assert auth_env.count_users() == 2


def test_lookup_user_resolves_email_to_role(auth_env, tmp_path):
    _write_csv(auth_env.WHITELIST_PATH, [
        ("admin@example.org", "Admin", "admin"),
        ("alice@example.org", "Alice", "user"),
    ])
    auth_env.init_db()
    auth_env.seed_users_from_whitelist()

    alice = auth_env.lookup_user("alice@example.org")
    assert alice is not None
    assert alice["name"] == "Alice"
    assert alice["role"] == "user"
    assert alice["group"] is None
    assert alice["primary_email"] == "alice@example.org"
    assert alice["emails"] == ["alice@example.org"]

    admin = auth_env.lookup_user("ADMIN@example.org")  # case-insensitive
    assert admin is not None and admin["role"] == "admin"


def test_lookup_user_returns_none_for_unknown_email(auth_env, tmp_path):
    auth_env.init_db()
    assert auth_env.lookup_user("nobody@example.org") is None


def test_invalid_role_in_csv_falls_back_to_user(auth_env, tmp_path):
    _write_csv(auth_env.WHITELIST_PATH, [
        ("nobody@example.org", "Nobody", "wizard"),
    ])
    auth_env.init_db()
    auth_env.seed_users_from_whitelist()
    user = auth_env.lookup_user("nobody@example.org")
    assert user["role"] == "user"


def test_seed_is_additive_not_destructive(auth_env, tmp_path):
    # First seed with alice only.
    _write_csv(auth_env.WHITELIST_PATH, [
        ("alice@example.org", "Alice", "user"),
    ])
    auth_env.init_db()
    auth_env.seed_users_from_whitelist()

    # Manually attach a second email to alice (simulating a future admin action).
    conn = auth_env.get_db()
    alice_id = conn.execute(
        "SELECT user_id FROM user_emails WHERE email = 'alice@example.org'"
    ).fetchone()["user_id"]
    auth_env._attach_email(conn, alice_id, "alice@alias.org", is_primary=False)
    conn.commit()
    conn.close()

    # Re-write CSV with alice DELETED and bob ADDED.
    _write_csv(auth_env.WHITELIST_PATH, [
        ("bob@example.org", "Bob", "user"),
    ])
    summary = auth_env.seed_users_from_whitelist()
    assert summary["imported"] == 1  # only bob

    # Alice and her alias survive.
    alice = auth_env.lookup_user("alice@example.org")
    assert alice is not None
    assert "alice@alias.org" in alice["emails"]
    # Alias also resolves to the same user.
    alias = auth_env.lookup_user("alice@alias.org")
    assert alias["id"] == alice["id"]


def test_missing_csv_is_handled(auth_env, tmp_path):
    # No CSV present at all.
    auth_env.WHITELIST_PATH.unlink(missing_ok=True)
    auth_env.init_db()
    summary = auth_env.seed_users_from_whitelist()
    assert summary == {"imported": 0, "skipped": 0, "missing_csv": True}
    assert auth_env.count_users() == 0
