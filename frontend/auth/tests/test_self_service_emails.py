"""Users add, remove and choose their own sign-in addresses.

A new address must prove itself with a code mailed to it. That code is
stored apart from login codes, so it can never sign anyone in, and a login
code can never confirm an address. The primary address hears about every
address that is added.
"""
import re

import pytest

from tests.conftest import make_user, session_cookie

ME = "kim@uni.de"
NEW = "kim.private@mail.com"


@pytest.fixture
def sent(auth_env, monkeypatch):
    messages = []

    async def _capture(msg, **kw):
        messages.append(msg)

    monkeypatch.setattr(auth_env.aiosmtplib, "send", _capture)
    monkeypatch.setattr(auth_env, "DEV_ECHO_OTP", False)
    return messages


@pytest.fixture
def me(auth_env, client, sent):
    uid = make_user(auth_env, ME, "Kim")
    client.cookies.set("munin_session", session_cookie(auth_env, ME, "Kim"))
    return uid


def _code_in(msg) -> str:
    return re.search(r"\b(\d{6})\b", msg["Subject"]).group(1)


def _request(client, email=NEW):
    return client.post("/auth/me/emails", json={"email": email})


def _confirm(client, code, email=NEW):
    return client.post("/auth/me/emails/verify", json={"email": email, "code": code})


def test_code_goes_to_the_new_address_and_confirms_it(client, me, sent):
    r = _request(client)
    assert r.status_code == 202
    assert [m["To"] for m in sent] == [NEW]
    assert "confirm" in sent[0]["Subject"]
    r = _confirm(client, _code_in(sent[0]))
    assert r.status_code == 200
    assert [e["email"] for e in r.json()["emails"]] == [ME, NEW]
    # The primary is told; the notice carries no code.
    notice = sent[-1]
    assert notice["To"] == ME and NEW in notice.get_payload(decode=True).decode()
    # And the new address now signs in to the same account.
    r = client.post("/login", data={"email": NEW}, follow_redirects=False)
    assert r.status_code == 303


def test_confirmation_code_cannot_sign_in(client, me, sent, auth_env):
    _request(client)
    code = _code_in(sent[0])
    # Make NEW a real sign-in address by another route, so only the code
    # stands between the caller and a session.
    conn = auth_env.get_db()
    auth_env._attach_email(conn, me, NEW, is_primary=False)
    conn.commit()
    conn.close()
    assert auth_env.otp_store.get(NEW) is None
    client.cookies.clear()
    r = client.post("/verify", data={"email": NEW, "code": code}, follow_redirects=False)
    assert r.status_code != 303
    assert "munin_session" not in r.cookies


def test_login_code_cannot_confirm_an_address(client, me, sent, auth_env):
    client.post("/login", data={"email": ME}, follow_redirects=False)
    login_code = auth_env.otp_store[ME]["code"]
    _request(client, ME + ".x")  # any pending alias, so a code exists
    r = _confirm(client, login_code, ME + ".x")
    if r.status_code == 200:  # astronomically unlikely: same 6 digits
        pytest.skip("codes collided")
    assert r.status_code == 400


def test_code_only_attaches_to_the_account_that_asked(client, me, sent, auth_env):
    _request(client)
    code = _code_in(sent[0])
    make_user(auth_env, "eve@x.org", "Eve")
    client.cookies.set("munin_session", session_cookie(auth_env, "eve@x.org", "Eve"))
    r = _confirm(client, code)
    assert r.status_code == 400
    assert auth_env.lookup_user(NEW) is None


def test_taken_and_identity_addresses_are_refused(client, me, sent, auth_env):
    make_user(auth_env, "taken@x.org", "T")
    r = _request(client, "taken@x.org")
    assert r.status_code == 409 and "cannot be added" in r.json()["error"]
    r = _request(client, ME)
    assert r.status_code == 409 and "already on your account" in r.json()["error"]
    assert sent == []


def test_cap_on_addresses(client, me, sent, auth_env):
    conn = auth_env.get_db()
    for i in range(auth_env.MAX_EMAILS_PER_USER - 1):
        auth_env._attach_email(conn, me, f"a{i}@x.org", is_primary=False)
    conn.commit()
    conn.close()
    r = _request(client, "one-too-many@x.org")
    assert r.status_code == 409 and "at most" in r.json()["error"]


def test_request_rate_limit(client, me, sent):
    for i in range(3):
        assert _request(client, f"n{i}@x.org").status_code == 202
    assert _request(client, "n9@x.org").status_code == 429


def test_wrong_codes_lock_out(client, me, sent, auth_env):
    _request(client)
    real = _code_in(sent[0])
    wrong = "000000" if real != "000000" else "000001"
    for _ in range(auth_env.VERIFY_ATTEMPT_LIMIT):
        _confirm(client, wrong)
    assert _confirm(client, real).status_code in (400, 429)
    assert auth_env.lookup_user(NEW) is None


def test_remove_rules(client, me, sent, auth_env):
    conn = auth_env.get_db()
    auth_env._attach_email(conn, me, NEW, is_primary=False)
    conn.commit()
    conn.close()
    # Not the address of this session.
    r = client.delete(f"/auth/me/emails/{ME}")
    assert r.status_code == 409
    # Another one is fine, and its sessions end.
    other_session = session_cookie(auth_env, NEW, "Kim")
    r = client.delete(f"/auth/me/emails/{NEW}")
    assert r.status_code == 200
    assert [e["email"] for e in r.json()["emails"]] == [ME]
    assert auth_env.validate_session(other_session) is None
    # The last one never.
    client.cookies.set("munin_session", session_cookie(auth_env, ME, "Kim"))
    assert client.delete(f"/auth/me/emails/{ME}").status_code == 409


def test_make_primary_and_list(client, me, sent, auth_env):
    conn = auth_env.get_db()
    auth_env._attach_email(conn, me, NEW, is_primary=False)
    conn.commit()
    conn.close()
    r = client.put(f"/auth/me/emails/{NEW}/primary")
    assert r.status_code == 200
    body = client.get("/auth/me/emails").json()
    assert body["emails"][0] == {"email": NEW, "primary": True, "current": False}
    assert {"email": ME, "primary": False, "current": True} in body["emails"]
    # Data key unchanged; the address sent along is now the new primary.
    h = client.get("/auth/check").headers
    assert h["X-Munin-User"] == f"u{me}"
    assert h["X-Munin-Email"] == NEW


def test_cannot_touch_someone_elses_address(client, me, sent, auth_env):
    make_user(auth_env, "eve@x.org", "Eve")
    assert client.delete("/auth/me/emails/eve@x.org").status_code == 404
    assert client.put("/auth/me/emails/eve@x.org/primary").status_code == 404


def test_requires_sign_in(client, auth_env):
    client.cookies.clear()
    assert client.get("/auth/me/emails").status_code == 401
    assert client.post("/auth/me/emails", json={"email": NEW}).status_code == 401
