"""Tests for /admin/groups/* endpoints."""
from tests.conftest import make_user, session_cookie


def _admin_cookies(auth_env, email="admin@example.org"):
    make_user(auth_env, email, "Admin", role="admin")
    return {"munin_session": session_cookie(auth_env, email, "Admin")}


def test_groups_require_admin(client):
    assert client.get("/admin/groups").status_code == 401


def test_create_and_list_groups(client, auth_env):
    cookies = _admin_cookies(auth_env)
    r = client.post("/admin/groups", cookies=cookies,
                    json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    assert r.status_code == 201
    assert r.json()["slug"] == "elgeti"
    assert r.json()["member_count"] == 0

    listed = client.get("/admin/groups", cookies=cookies).json()["groups"]
    assert any(g["slug"] == "elgeti" for g in listed)


def test_create_group_rejects_invalid_slug(client, auth_env):
    cookies = _admin_cookies(auth_env)
    r = client.post("/admin/groups", cookies=cookies,
                    json={"slug": "Has Space", "display_name": "X"})
    assert r.status_code == 400


def test_create_group_duplicate_409(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    r = client.post("/admin/groups", cookies=cookies,
                    json={"slug": "elgeti", "display_name": "Other Name"})
    assert r.status_code == 409


def test_patch_group_rename(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    r = client.patch("/admin/groups/elgeti", cookies=cookies,
                     json={"display_name": "Elgeti Lab (Leipzig)"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Elgeti Lab (Leipzig)"


def test_assign_group_to_user(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.patch(f"/admin/users/{uid}", cookies=cookies,
                     json={"group": "elgeti"})
    assert r.status_code == 200
    assert r.json()["group"] == "elgeti"
    groups = client.get("/admin/groups", cookies=cookies).json()["groups"]
    elgeti = next(g for g in groups if g["slug"] == "elgeti")
    assert elgeti["member_count"] == 1


def test_delete_group_with_members_409(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice", group="elgeti")
    r = client.delete("/admin/groups/elgeti", cookies=cookies)
    assert r.status_code == 409


def test_delete_empty_group(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    r = client.delete("/admin/groups/elgeti", cookies=cookies)
    assert r.status_code == 204
    assert all(g["slug"] != "elgeti"
               for g in client.get("/admin/groups", cookies=cookies).json()["groups"])


def test_group_survives_when_member_deleted(client, auth_env):
    """Per design: deleting a user does NOT delete their group."""
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice", group="elgeti")
    client.delete(f"/admin/users/{uid}", cookies=cookies)
    groups = client.get("/admin/groups", cookies=cookies).json()["groups"]
    assert any(g["slug"] == "elgeti" for g in groups)


# ── Group membership ─────────────────────────────────────────────────────────

def test_add_and_list_group_members(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice")

    r = client.post("/admin/groups/elgeti/members", cookies=cookies,
                    json={"user_id": uid})
    assert r.status_code == 201
    members = r.json()["members"]
    assert [m["id"] for m in members] == [uid]

    listed = client.get("/admin/groups/elgeti/members", cookies=cookies).json()["members"]
    assert listed[0]["primary_email"] == "alice@example.org"


def test_add_member_sets_primary_group_when_unset(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice")
    client.post("/admin/groups/elgeti/members", cookies=cookies, json={"user_id": uid})

    user = next(u for u in client.get("/admin/users", cookies=cookies).json()["users"]
                if u["id"] == uid)
    assert user["group"] == "elgeti"


def test_add_member_keeps_existing_primary_group(client, auth_env):
    """A user can belong to several groups; adding a second one must
    not change their primary group."""
    cookies = _admin_cookies(auth_env)
    for slug in ("elgeti", "smith"):
        client.post("/admin/groups", cookies=cookies,
                    json={"slug": slug, "display_name": slug.title()})
    uid = make_user(auth_env, "alice@example.org", "Alice", group="elgeti")

    client.post("/admin/groups/smith/members", cookies=cookies, json={"user_id": uid})

    user = next(u for u in client.get("/admin/users", cookies=cookies).json()["users"]
                if u["id"] == uid)
    assert user["group"] == "elgeti"  # primary unchanged
    groups = {g["slug"]: g["member_count"]
              for g in client.get("/admin/groups", cookies=cookies).json()["groups"]}
    assert groups["elgeti"] == 1 and groups["smith"] == 1


def test_remove_member_repoints_primary_group(client, auth_env):
    cookies = _admin_cookies(auth_env)
    for slug in ("elgeti", "smith"):
        client.post("/admin/groups", cookies=cookies,
                    json={"slug": slug, "display_name": slug.title()})
    uid = make_user(auth_env, "alice@example.org", "Alice", group="elgeti")
    client.post("/admin/groups/smith/members", cookies=cookies, json={"user_id": uid})

    # Remove the primary group; primary should repoint to the remaining one.
    r = client.delete(f"/admin/groups/elgeti/members/{uid}", cookies=cookies)
    assert r.status_code == 200
    user = next(u for u in client.get("/admin/users", cookies=cookies).json()["users"]
                if u["id"] == uid)
    assert user["group"] == "smith"


def test_remove_last_member_clears_primary_group(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice", group="elgeti")

    client.delete(f"/admin/groups/elgeti/members/{uid}", cookies=cookies)
    user = next(u for u in client.get("/admin/users", cookies=cookies).json()["users"]
                if u["id"] == uid)
    assert user["group"] is None


def test_add_member_missing_group_404(client, auth_env):
    cookies = _admin_cookies(auth_env)
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.post("/admin/groups/nope/members", cookies=cookies, json={"user_id": uid})
    assert r.status_code == 404


def test_remove_non_member_404(client, auth_env):
    cookies = _admin_cookies(auth_env)
    client.post("/admin/groups", cookies=cookies,
                json={"slug": "elgeti", "display_name": "Elgeti Lab"})
    uid = make_user(auth_env, "alice@example.org", "Alice")
    r = client.delete(f"/admin/groups/elgeti/members/{uid}", cookies=cookies)
    assert r.status_code == 404


def test_members_require_admin(client):
    assert client.get("/admin/groups/elgeti/members").status_code == 401
