"""AUTH_DEV_ECHO_OTP: the demo-login escape hatch.

OTP delivery was SMTP-only, which meant a fresh instance could not complete a
single login without a working mail relay. This flag logs the code instead.

Two properties matter and both are asserted here: it is OFF unless explicitly
enabled (a deployment that forgets to set it keeps emailing), and when ON it
returns before touching SMTP (so a host with no relay and no credentials still
works rather than hanging on a connect timeout).
"""

import asyncio
import importlib
import os

import pytest


def _load_auth(monkeypatch, flag=None):
    """Import auth.main under a given AUTH_DEV_ECHO_OTP value."""
    monkeypatch.delenv("AUTH_DEV_ECHO_OTP", raising=False)
    if flag is not None:
        monkeypatch.setenv("AUTH_DEV_ECHO_OTP", flag)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    import main
    return importlib.reload(main)


@pytest.mark.parametrize("flag", [None, "0", "false", "no", "off", ""])
def test_disabled_by_default_and_for_falsey_values(monkeypatch, flag):
    """The dangerous direction. A deployment that never sets this, or sets it
    to something falsey, must keep using real email."""
    assert _load_auth(monkeypatch, flag).DEV_ECHO_OTP is False


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on"])
def test_enabled_for_truthy_values(monkeypatch, flag):
    assert _load_auth(monkeypatch, flag).DEV_ECHO_OTP is True


def test_enabled_logs_the_code_and_never_calls_smtp(monkeypatch, caplog):
    m = _load_auth(monkeypatch, "1")

    called = False

    async def _explode(*a, **kw):
        nonlocal called
        called = True
        raise AssertionError("SMTP must not be contacted when echoing is on")

    monkeypatch.setattr(m.aiosmtplib, "send", _explode)

    with caplog.at_level("WARNING"):
        asyncio.run(m.send_otp_email("someone@example.org", "123456", "Someone"))

    assert called is False
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "123456" in joined, "the code must actually be recoverable from the log"
    assert "someone@example.org" in joined


def test_disabled_still_goes_through_smtp(monkeypatch):
    """Guards against the branch swallowing real delivery."""
    m = _load_auth(monkeypatch, "0")

    sent = []

    async def _capture(msg, **kw):
        sent.append(msg)

    monkeypatch.setattr(m.aiosmtplib, "send", _capture)
    asyncio.run(m.send_otp_email("someone@example.org", "123456", "Someone"))
    assert len(sent) == 1
    assert sent[0]["To"] == "someone@example.org"


def teardown_module():
    os.environ.pop("AUTH_DEV_ECHO_OTP", None)
