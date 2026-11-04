"""The sign-in flow: code reuse, lockout, the page states, and the login email.

The properties that matter most here are the security ones. Re-requesting a
code must never buy more time or more guesses, and a new code must not lift the
per-email verify limit, or the limit stops bounding the guessing rate.
"""

import asyncio
import email
import email.utils
import logging
import re
import time

import pytest

from tests.conftest import make_user

EMAIL = "someone@example.org"
LOCKOUT = "Too many failed attempts. Please wait 15 minutes, then request a new code."


@pytest.fixture
def sent(auth_env, monkeypatch):
    """Stub SMTP and collect what would have been sent."""
    messages = []

    async def _capture(msg, **kw):
        messages.append(msg)

    monkeypatch.setattr(auth_env.aiosmtplib, "send", _capture)
    monkeypatch.setattr(auth_env, "DEV_ECHO_OTP", False)
    return messages


@pytest.fixture
def user(auth_env, client, sent):
    make_user(auth_env, EMAIL, "Someone")
    return EMAIL


def _wrong_code(auth_env, email=EMAIL):
    """A 6-digit code that is not the live one."""
    return "000000" if auth_env.otp_store[email]["code"] != "000000" else "000001"


def _post_verify(client, code, email=EMAIL, redirect=""):
    return client.post(
        "/verify",
        data={"email": email, "code": code, "redirect": redirect},
        follow_redirects=False,
    )


def _post_login(client, email=EMAIL, redirect=""):
    return client.post(
        "/login",
        data={"email": email, "redirect": redirect},
        follow_redirects=False,
    )


def _lock_out(auth_env, client):
    """Spend all five attempts on wrong codes."""
    auth_env.generate_otp(EMAIL)
    wrong = _wrong_code(auth_env)
    for _ in range(5):
        response = _post_verify(client, wrong)
    return response


# ── Code reuse ───────────────────────────────────────────────────────────────

def test_reuse_keeps_code_expiry_and_attempts(auth_env):
    code = auth_env.generate_otp(EMAIL)
    entry = auth_env.otp_store[EMAIL]
    entry["attempts"] = 2
    # Partway through its life, so re-stamping the expiry would show.
    expires = time.time() + 300
    entry["expires"] = expires

    assert auth_env.generate_otp(EMAIL) == code
    assert auth_env.otp_store[EMAIL]["expires"] == expires
    assert auth_env.otp_store[EMAIL]["attempts"] == 2


def test_code_with_exactly_the_minimum_left_is_reused(auth_env, monkeypatch):
    code = auth_env.generate_otp(EMAIL)
    now = auth_env.otp_store[EMAIL]["expires"] - 120
    monkeypatch.setattr(auth_env.time, "time", lambda: now)

    assert auth_env.generate_otp(EMAIL) == code


def test_near_expiry_code_is_replaced(auth_env, monkeypatch):
    # The fixed draw makes "replaced" observable: a random one could repeat.
    monkeypatch.setattr(auth_env.secrets, "randbelow", lambda n: 111111)
    auth_env.otp_store[EMAIL] = {
        "code": "222222",
        "expires": time.time() + 119,
        "attempts": 3,
        "sends": 1,
    }

    assert auth_env.generate_otp(EMAIL) == "111111"
    entry = auth_env.otp_store[EMAIL]
    assert entry["attempts"] == 0
    assert entry["expires"] > time.time() + 119


def test_expired_code_is_replaced(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env.secrets, "randbelow", lambda n: 111111)
    auth_env.otp_store[EMAIL] = {
        "code": "222222",
        "expires": time.time() - 1,
        "attempts": 0,
        "sends": 1,
    }

    assert auth_env.generate_otp(EMAIL) == "111111"


def test_re_request_mails_the_same_code_and_still_counts_against_rate_send(auth_env, user, client, sent):
    _post_login(client)
    _post_login(client)

    subjects = [m["Subject"] for m in sent]
    assert len(subjects) == 2
    assert subjects[0] == subjects[1]
    assert len(auth_env.rate_send[EMAIL]) == 2


def test_verify_page_says_same_code_only_when_it_was(auth_env, user, client):
    first = client.get(_post_login(client).headers["location"])
    assert "We resent the same code" not in first.text

    second = client.get(_post_login(client).headers["location"])
    assert "We resent the same code" in second.text

    # Near expiry the next request issues a new code, so the claim goes away.
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 60
    third = client.get(_post_login(client).headers["location"])
    assert "We resent the same code" not in third.text


def test_bare_verify_page_does_not_reveal_a_sign_in_in_progress(auth_env, user, client):
    """GET /verify needs no login, so it must not read the address's otp_store entry."""
    untouched = client.get("/verify", params={"email": EMAIL}).text

    _post_login(client)
    _post_login(client)
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 125

    assert client.get("/verify", params={"email": EMAIL}).text == untouched


def test_reused_code_is_mailed_with_the_time_it_has_left(auth_env, user, client, sent):
    _post_login(client)
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 125

    response = _post_login(client)

    msg = email.message_from_bytes(sent[-1].as_bytes())
    plain, html = [p.get_payload(decode=True).decode(p.get_content_charset()) for p in msg.get_payload()]
    assert "It expires in 2 minutes." in plain
    assert "This code expires in 2 minutes." in html
    assert "It expires in 2 minutes." in client.get(response.headers["location"]).text


def test_fresh_code_is_mailed_with_its_full_lifetime(auth_env, user, client, sent):
    response = _post_login(client)

    msg = email.message_from_bytes(sent[-1].as_bytes())
    plain = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "It expires in 10 minutes." in plain
    assert "minutes=" not in response.headers["location"]
    assert "It expires in 10 minutes." in client.get(response.headers["location"]).text


def test_verify_page_says_one_minute_in_the_singular(client):
    page = client.get("/verify", params={"email": EMAIL, "minutes": 1}).text

    assert "It expires in 1 minute." in page


@pytest.mark.parametrize("minutes", [0, -5, 9999])
def test_verify_page_ignores_a_forged_minutes_value(client, minutes):
    page = client.get("/verify", params={"email": EMAIL, "minutes": minutes}).text

    assert "It expires in 10 minutes." in page


def test_wrong_code_page_does_not_restate_a_stale_expiry(auth_env, user, client):
    auth_env.generate_otp(EMAIL)
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 125

    page = _post_verify(client, _wrong_code(auth_env)).text

    assert "Invalid code." in page
    assert "It expires in" not in page


# ── Lockout ──────────────────────────────────────────────────────────────────

def test_wrong_codes_show_attempts_left_from_rate_verify(auth_env, user, client):
    auth_env.generate_otp(EMAIL)
    wrong = _wrong_code(auth_env)

    assert "Invalid code. 4 attempts left." in _post_verify(client, wrong).text
    assert "Invalid code. 3 attempts left." in _post_verify(client, wrong).text
    assert "Invalid code. 2 attempts left." in _post_verify(client, wrong).text
    assert "Invalid code. 1 attempt left." in _post_verify(client, wrong).text


def test_attempts_left_counts_failures_from_an_earlier_code(auth_env, user, client):
    """The count comes from rate_verify, not from the current code's entry."""
    auth_env.generate_otp(EMAIL)
    _post_verify(client, _wrong_code(auth_env))
    _post_verify(client, _wrong_code(auth_env))
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 60
    auth_env.generate_otp(EMAIL)

    assert "Invalid code. 2 attempts left." in _post_verify(client, _wrong_code(auth_env)).text


def test_lockout_wording_and_disabled_form(auth_env, user, client):
    response = _lock_out(auth_env, client)

    assert LOCKOUT in response.text
    assert re.search(r'<input id="verify-code"[^>]* disabled', response.text)
    assert re.search(r'<button id="verify-btn"[^>]* disabled', response.text)
    assert re.search(r'<button id="resend-btn"[^>]* disabled', response.text)
    assert 'role="alert"' in response.text


def test_locked_page_labels_the_way_out(auth_env, user, client):
    page = _lock_out(auth_env, client).text

    assert re.search(r'<a href="/login\?redirect=[^"]*">Request a new code</a>', page)
    assert "Use a different email" not in page


def test_verify_otp_uses_the_same_lockout_wording(auth_env):
    auth_env.generate_otp(EMAIL)
    wrong = _wrong_code(auth_env)
    for _ in range(4):
        auth_env.verify_otp(EMAIL, wrong)

    assert auth_env.verify_otp(EMAIL, wrong) == (False, LOCKOUT)


def test_get_verify_shows_the_lockout_too(auth_env, user, client):
    _lock_out(auth_env, client)

    page = client.get("/verify", params={"email": EMAIL})
    assert LOCKOUT in page.text
    assert re.search(r'<input id="verify-code"[^>]* disabled', page.text)


def test_correct_code_is_refused_while_locked(auth_env, user, client):
    _lock_out(auth_env, client)
    code = auth_env.generate_otp(EMAIL)

    response = _post_verify(client, code)

    assert response.status_code == 200
    assert LOCKOUT in response.text
    assert "munin_session" not in response.headers.get("set-cookie", "")
    assert auth_env.otp_store[EMAIL]["code"] == code, "a refused try must not consume the code"


def test_new_code_does_not_reset_rate_verify(auth_env, user, client):
    auth_env.generate_otp(EMAIL)
    _post_verify(client, _wrong_code(auth_env))
    _post_verify(client, _wrong_code(auth_env))
    before = list(auth_env.rate_verify[EMAIL])

    # Both ways of getting a code: a reused one and a replaced one.
    _post_login(client)
    assert auth_env.rate_verify[EMAIL] == before
    auth_env.otp_store[EMAIL]["expires"] = time.time() + 60
    _post_login(client)
    assert auth_env.rate_verify[EMAIL] == before


def test_lockout_ends_when_the_window_passes(auth_env, user, client, monkeypatch):
    _lock_out(auth_env, client)
    code = auth_env.generate_otp(EMAIL)
    later = time.time() + 901
    auth_env.otp_store[EMAIL]["expires"] = later + 600
    monkeypatch.setattr(auth_env.time, "time", lambda: later)

    assert _post_verify(client, code).status_code == 303


def test_success_clears_the_limit(auth_env, user, client):
    auth_env.generate_otp(EMAIL)
    wrong = _wrong_code(auth_env)
    _post_verify(client, wrong)
    _post_verify(client, wrong)
    code = auth_env.otp_store[EMAIL]["code"]

    assert _post_verify(client, code).status_code == 303
    assert EMAIL not in auth_env.rate_verify


# ── POST /verify and GET /verify ─────────────────────────────────────────────

@pytest.mark.parametrize("template", ["{} {}", " {}-{} ", "{} {}"])
def test_verify_strips_non_digits(auth_env, user, client, template):
    code = auth_env.generate_otp(EMAIL)

    response = _post_verify(client, template.format(code[:3], code[3:]))

    assert response.status_code == 303


def test_non_ascii_digits_are_not_treated_as_the_code(auth_env, user, client, monkeypatch):
    monkeypatch.setattr(auth_env.secrets, "randbelow", lambda n: 123456)
    auth_env.generate_otp(EMAIL)

    assert _post_verify(client, "١٢٣٤٥٦").status_code == 200


def test_verify_without_email_redirects_to_login(client):
    response = client.get("/verify", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_verify_without_email_keeps_redirect(client):
    response = client.get(
        "/verify",
        params={"redirect": "https://app.example.org/a?b=c&d=e"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/login?redirect=https%3A%2F%2Fapp.example.org%2Fa%3Fb%3Dc%26d%3De"
    )


def test_verify_page_has_the_next_steps(auth_env, client, monkeypatch):
    monkeypatch.setitem(auth_env.templates.globals, "sender_address", "noreply@example.org")

    page = client.get("/verify", params={"email": EMAIL, "redirect": "https://x.example.org/"}).text

    assert "Usually arrives within a minute. It expires in 10 minutes." in page
    assert "Check spam for mail from noreply@example.org." in page
    assert "Use a different email" in page
    assert "/login?redirect=https%3A//x.example.org/" in page
    assert "Resend code" in page
    assert "Request a new code" not in page
    assert 'maxlength' not in page
    assert 'placeholder' not in page
    assert '<label for="verify-code">' in page


def test_code_pattern_lets_a_pasted_space_through(client):
    """The pattern attribute is what applies with JavaScript off."""
    page = client.get("/verify", params={"email": EMAIL}).text
    pattern = re.search(r'pattern="([^"]+)"', page).group(1)

    assert re.fullmatch(pattern, "482 913")
    assert re.fullmatch(pattern, "482913")
    assert not re.fullmatch(pattern, "48291")


# ── POST /login errors ───────────────────────────────────────────────────────

def test_unknown_email_is_kept_and_named(client, sent, auth_env, monkeypatch):
    monkeypatch.setitem(auth_env.templates.globals, "support_email", "help@example.org")

    page = _post_login(client, "Nobody@Example.org ").text

    assert 'value="Nobody@Example.org"' in page
    assert "We don't have an account for Nobody@Example.org." in page
    assert 'ask <a href="mailto:help@example.org">help@example.org</a> for access' in page
    assert 'role="alert"' in page
    assert sent == []


def test_unknown_email_wording_without_a_support_address(client, sent, auth_env, monkeypatch):
    monkeypatch.setitem(auth_env.templates.globals, "support_email", "")

    page = _post_login(client, "nobody@example.org").text

    assert "ask your Munin administrator for access." in page
    assert "mailto:" not in page


def test_typed_email_is_escaped(client, sent):
    hostile = '"><script>alert(1)</script>@example.org'

    page = _post_login(client, hostile).text

    assert "<script>alert(1)" not in page
    assert "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;@example.org" in page


def test_rate_limit_error_keeps_the_typed_email(auth_env, user, client):
    for _ in range(3):
        _post_login(client)

    page = _post_login(client).text

    assert "Too many code requests" in page
    assert f'value="{EMAIL}"' in page


def test_send_failure_keeps_the_typed_email(auth_env, user, client, monkeypatch):
    async def _fail(*a, **kw):
        raise OSError("relay down")

    monkeypatch.setattr(auth_env, "send_otp_email", _fail)

    page = _post_login(client).text

    assert "Failed to send email" in page
    assert f'value="{EMAIL}"' in page


def test_login_page_label_and_footer_support_link(auth_env, client, monkeypatch):
    monkeypatch.setitem(auth_env.templates.globals, "support_email", "help@example.org")
    page = client.get("/login").text
    assert '<label for="login-email">' in page
    assert 'autocomplete="email"' in page
    assert 'href="mailto:help@example.org"' in page

    monkeypatch.setitem(auth_env.templates.globals, "support_email", "")
    assert "mailto:" not in client.get("/login").text


# ── The email ────────────────────────────────────────────────────────────────

def test_message_has_date_and_message_id_from_the_sender_domain(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "SMTP_SENDER", "Munin <noreply@mail.example.org>")

    msg = auth_env.build_otp_message(EMAIL, "123456", "Someone")

    assert email.utils.parsedate_to_datetime(msg["Date"]) is not None
    assert re.fullmatch(r"<[^@<>]+@mail\.example\.org>", msg["Message-ID"])


@pytest.mark.parametrize("sender, domain", [
    ("noreply@münchen.example", r"xn--mnchen-3ya\.example"),
    # A label over 63 characters has no IDNA form.
    (f"noreply@{'ü' * 64}.example", "localhost"),
    ("foo@", "localhost"),
])
def test_message_id_domain_is_ascii(auth_env, monkeypatch, sender, domain):
    monkeypatch.setattr(auth_env, "SMTP_SENDER", sender)

    msg = email.message_from_bytes(auth_env.build_otp_message(EMAIL, "123456", "Someone").as_bytes())

    assert re.fullmatch(rf"<[^@<>]+@{domain}>", msg["Message-ID"])


def test_message_plain_text_says_what_the_code_is_for(auth_env, monkeypatch):
    monkeypatch.setattr(auth_env, "SUPPORT_EMAIL", "help@example.org")

    msg = email.message_from_bytes(
        auth_env.build_otp_message(EMAIL, "123456", "Søren").as_bytes()
    )
    plain = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    text = plain.get_payload(decode=True).decode(plain.get_content_charset())

    assert "123456" in text
    assert "Søren" in text
    assert "Munin sign-in page" in text
    assert "expires in 10 minutes" in text
    assert "help@example.org" in text


def test_message_html_escapes_the_name_and_names_support(auth_env, monkeypatch):
    monkeypatch.setitem(auth_env.templates.globals, "support_email", "help@example.org")

    msg = auth_env.build_otp_message(EMAIL, "123456", "<b>x</b>")
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html")
    text = html.get_payload(decode=True).decode(html.get_content_charset())

    assert "<b>x</b>" not in text
    assert "Enter this code on the Munin sign-in page you just opened" in text
    assert "mailto:help@example.org" in text
    assert 'max-width:440px' in text


def test_send_logs_duration_and_attempt_on_success(auth_env, sent, caplog):
    with caplog.at_level(logging.INFO, logger="munin-auth"):
        asyncio.run(auth_env.send_otp_email(EMAIL, "123456", "Someone"))

    assert len(sent) == 1
    assert re.search(r"sent in \d+\.\ds on attempt 1/\d+", caplog.text)
