"""Forward-auth headers must survive names that are not plain ASCII.

Companion to the gateway's tests/test_header_safe.py. Two encoding
boundaries sit between a user's profile name and the cluster:

  1. Starlette encodes response headers as latin-1 -> a name outside that
     range 500s /auth/check, which fails forward-auth and locks the user
     out of every protected route.
  2. httpx encodes request headers as ASCII -> a latin-1 accent clears
     boundary 1 but kills the gateway proxy one hop later (the 2026-08
     "Person115" outage).

Stripping to ASCII here clears both. Display fidelity is unaffected:
the UI reads names from /auth/me as JSON, which is UTF-8.
"""
from tests.conftest import make_user, session_cookie


def _profile(auth_env, email, full_name=None, nickname=None):
    auth_env.set_user_profile(email, full_name, nickname, None)


def test_ascii_name_forwarded_verbatim(client, auth_env):
    make_user(auth_env, "alice@e.org", "Alice", last_name="Smith")
    cookies = {"munin_session": session_cookie(auth_env, "alice@e.org", "Alice Smith")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Name"] == "Alice Smith"


def test_umlaut_name_is_stripped_not_fatal(client, auth_env):
    make_user(auth_env, "klaus@e.org", "Klaus", last_name="Völkel")
    _profile(auth_env, "klaus@e.org", full_name="Person115")
    cookies = {"munin_session": session_cookie(auth_env, "klaus@e.org", "Person115")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Name"] == "Person115"
    assert r.headers["X-Munin-Email"] == "klaus@e.org"


def test_name_beyond_latin1_does_not_500(client, auth_env):
    """This one never even reached the gateway; it died in Starlette."""
    make_user(auth_env, "luk@e.org", "Łukasz")
    _profile(auth_env, "luk@e.org", nickname="Łukasz")
    cookies = {"munin_session": session_cookie(auth_env, "luk@e.org", "Łukasz")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Name"] == "ukasz"


def test_name_stripped_to_empty_still_authenticates(client, auth_env):
    """A wholly non-ASCII name leaves an empty header. That is fine: no
    upstream code reads the name; identity rides on X-Munin-Email."""
    make_user(auth_env, "cjk@e.org", "大変")
    _profile(auth_env, "cjk@e.org", nickname="大変")
    cookies = {"munin_session": session_cookie(auth_env, "cjk@e.org", "大変")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Name"] == ""
    assert r.headers["X-Munin-Email"] == "cjk@e.org"


def test_non_ascii_group_is_stripped(client, auth_env):
    conn = auth_env.get_db()
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT OR IGNORE INTO groups (slug, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("lab-fürth", "Lab Fürth", now, now),
    )
    conn.commit()
    conn.close()
    make_user(auth_env, "g@e.org", "Gerd", group="lab-fürth")
    cookies = {"munin_session": session_cookie(auth_env, "g@e.org", "Gerd")}
    r = client.get("/auth/check", cookies=cookies)
    assert r.status_code == 200
    assert r.headers["X-Munin-Group"] == "lab-frth"


def test_profile_name_still_reads_back_intact_over_json(client, auth_env):
    """The strip is transport-only. /auth/me must keep the real spelling,
    otherwise we would be silently renaming people in the UI."""
    make_user(auth_env, "klaus@e.org", "Klaus", last_name="Völkel")
    _profile(auth_env, "klaus@e.org", full_name="Person115")
    cookies = {"munin_session": session_cookie(auth_env, "klaus@e.org", "Person115")}
    r = client.get("/auth/me", cookies=cookies)
    assert r.status_code == 200
    assert r.json()["name"] == "Person115"
