"""Tests for the contributors.yml seed + /admin/contributors.yaml export."""
import textwrap

from tests.conftest import make_user, session_cookie


SAMPLE_YAML = textwrap.dedent("""\
    contributors:
      - email: contributor-a@example.org
        username: corzilius
        display_name: Contributor A
        research_group: corzilius
        research_group_display_name: Corzilius Lab (Rostock)

      - emails:
          - user038@example.org
          - user037@example.org
        username: elgeti
        display_name: Contributor D
        research_group: elgeti
        research_group_display_name: Elgeti Lab (Leipzig)
""")


def _write_csv(path, rows):
    path.write_text(
        "email,name,role\n" + "\n".join(",".join(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_seed_creates_new_users_when_emails_absent(auth_env):
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    summary = auth_env.seed_contributors_from_yaml()
    assert summary["created"] == 2
    assert summary["groups"] == 2
    assert auth_env.lookup_user("contributor-a@example.org")["role"] == "group_leader"
    elgeti = auth_env.lookup_user("user038@example.org")
    assert elgeti["role"] == "group_leader"
    assert elgeti["group"] == "elgeti"
    assert elgeti["username"] == "elgeti"
    # Multi-email entry: both emails resolve to the same user.
    assert auth_env.lookup_user("user037@example.org")["id"] == elgeti["id"]


def test_seed_promotes_existing_user_to_group_leader(auth_env):
    _write_csv(auth_env.WHITELIST_PATH, [
        ("contributor-a@example.org", "Bjoern", "user"),
    ])
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    auth_env.seed_users_from_whitelist()
    summary = auth_env.seed_contributors_from_yaml()
    assert summary["promoted"] == 1
    assert summary["created"] == 1  # elgeti is new

    user = auth_env.lookup_user("contributor-a@example.org")
    assert user["role"] == "group_leader"
    assert user["group"] == "corzilius"
    assert user["username"] == "corzilius"


def test_seed_does_not_overwrite_admin_role(auth_env):
    _write_csv(auth_env.WHITELIST_PATH, [
        ("contributor-a@example.org", "Bjoern", "admin"),
    ])
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    auth_env.seed_users_from_whitelist()
    auth_env.seed_contributors_from_yaml()
    user = auth_env.lookup_user("contributor-a@example.org")
    assert user["role"] == "admin"  # NOT demoted to group_leader
    # But research_group + username still backfilled (they were empty)
    assert user["group"] == "corzilius"
    assert user["username"] == "corzilius"


def test_seed_is_idempotent(auth_env):
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    first = auth_env.seed_contributors_from_yaml()
    assert first["created"] == 2

    second = auth_env.seed_contributors_from_yaml()
    assert second["already_run"] is True
    assert second["created"] == 0


def test_seed_retries_when_yaml_appears_later(auth_env):
    auth_env.init_db()
    summary = auth_env.seed_contributors_from_yaml()
    assert summary["missing_yaml"] is True
    # Missing-YAML must NOT mark the migration applied; a later
    # boot with the file in place should still seed.
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    again = auth_env.seed_contributors_from_yaml()
    assert again["created"] == 2
    assert not again.get("already_run")


def test_admin_contributors_yaml_requires_auth(client):
    r = client.get("/admin/contributors.yaml")
    assert r.status_code == 401


def test_admin_contributors_yaml_bearer_token(auth_env, monkeypatch):
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    auth_env.seed_contributors_from_yaml()

    monkeypatch.setattr(auth_env, "CONTRIBUTORS_SYNC_TOKEN", "secret-token-xyz")
    from fastapi.testclient import TestClient
    with TestClient(auth_env.app) as c:
        c.cookies.clear()
        # No auth at all -> 401
        assert c.get("/admin/contributors.yaml").status_code == 401
        # Wrong token -> still tries cookie path, no cookie -> 401
        r = c.get("/admin/contributors.yaml",
                  headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        # Right token -> 200
        r = c.get("/admin/contributors.yaml",
                  headers={"Authorization": "Bearer secret-token-xyz"})
        assert r.status_code == 200
        body = r.text
        assert "contributors:" in body
        assert "contributor-a@example.org" in body
        assert "user038@example.org" in body
        assert "user037@example.org" in body


def test_admin_contributors_yaml_admin_session(client, auth_env):
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.seed_contributors_from_yaml()
    make_user(auth_env, "admin@e.org", "Admin", role="admin")
    cookies = {"munin_session": session_cookie(auth_env, "admin@e.org", "Admin")}
    r = client.get("/admin/contributors.yaml", cookies=cookies)
    assert r.status_code == 200
    assert "research_group: corzilius" in r.text


def test_contributors_yaml_round_trips_through_loader(auth_env):
    """Emitted YAML must be parseable by the same loader pattern the
    cluster's retrieval service uses (yaml.safe_load + email/emails)."""
    import yaml
    auth_env.CONTRIBUTORS_PATH.write_text(SAMPLE_YAML, encoding="utf-8")
    auth_env.init_db()
    auth_env.seed_contributors_from_yaml()
    out = auth_env.emit_contributors_yaml()
    doc = yaml.safe_load(out)
    entries = doc["contributors"]
    assert len(entries) == 2
    # Both email forms must round-trip.
    emails_in_entries = []
    for e in entries:
        if "email" in e:
            emails_in_entries.append(e["email"])
        if "emails" in e:
            emails_in_entries.extend(e["emails"])
    assert "contributor-a@example.org" in emails_in_entries
    assert "user038@example.org" in emails_in_entries
    assert "user037@example.org" in emails_in_entries


def test_emit_excludes_users_without_group(auth_env):
    auth_env.init_db()
    # Add a user with group_leader role but no group.
    make_user(auth_env, "lonely@e.org", "Lonely", role="group_leader",
              group=None, username="lonely")
    body = auth_env.emit_contributors_yaml()
    assert "lonely@e.org" not in body


def test_emit_excludes_user_role(auth_env):
    auth_env.init_db()
    # Set up a group + a regular user assigned to it.
    conn = auth_env.get_db()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO groups (slug, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("rg", "RG Lab", now, now),
    )
    conn.commit()
    conn.close()
    make_user(auth_env, "regular@e.org", "Regular", role="user",
              group="rg", username="regular")
    body = auth_env.emit_contributors_yaml()
    assert "regular@e.org" not in body
