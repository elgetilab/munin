"""Non-ASCII display names must not take down a user's whole session.

Regression for the 2026-08 outage: munin-auth forwards X-Munin-Name from
the user's profile, and httpx encodes request headers as ASCII. A user
with an accented name (say "Erika Mösermann") hit UnicodeEncodeError inside every
authenticated /api/* proxy call, which the blanket handler reported as
502 "Backend unavailable.", including on /api/status, so the entire UI
read as down for that user while every ASCII-named user was fine.
"""
import httpx
import pytest


# ── header_safe() ────────────────────────────────────────────────────────────

def test_ascii_passes_through_unchanged(gw_env):
    assert gw_env.header_safe("Erika Moesermann") == "Erika Moesermann"


def test_latin1_accent_is_stripped(gw_env):
    # The character that caused the outage (U+00F6).
    assert gw_env.header_safe("Erika Mösermann") == "Erika Msermann"


def test_beyond_latin1_is_stripped(gw_env):
    # Would have died at the auth layer's latin-1 boundary, one hop earlier.
    assert gw_env.header_safe("Łukasz") == "ukasz"
    assert gw_env.header_safe("大変") == ""


def test_empty_string_is_safe(gw_env):
    assert gw_env.header_safe("") == ""


@pytest.mark.parametrize("value", ["Erika Mösermann", "Łukasz", "大変", "Jiří"])
def test_result_is_always_httpx_encodable(gw_env, value):
    """The property that actually matters: whatever comes back must survive
    httpx's header normalisation, which is where the original crash was."""
    httpx.Headers({"X-Munin-Name": gw_env.header_safe(value)})


# ── End-to-end through the proxy ─────────────────────────────────────────────

def _mock_upstream(gw_env, monkeypatch, seen: dict):
    """Swap the upstream client for one that records the forwarded headers.

    Uses a real httpx.AsyncClient so the genuine header-encoding path runs;
    a hand-rolled fake would not reproduce the bug.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        gw_env,
        "http_client",
        httpx.AsyncClient(
            base_url=gw_env.CLUSTER_TUNNEL,
            transport=httpx.MockTransport(handler),
        ),
    )
    return seen


def test_umlaut_name_does_not_502(client, gw_env, monkeypatch):
    seen = _mock_upstream(gw_env, monkeypatch, {})
    # Headers go on the wire as latin-1 bytes (that is how Caddy copies the
    # value out of munin-auth, and how Starlette hands it to the app). Sending
    # a str here would fail inside the test client itself before reaching us.
    r = client.get(
        "/api/status",
        headers=[
            (b"X-Munin-Email", b"erika.moesermann@example.org"),
            (b"X-Munin-Name", "Erika Mösermann".encode("latin-1")),
        ],
    )
    assert r.status_code == 200, "umlaut in display name must not surface as a 502"
    assert seen["headers"]["X-Munin-Name"] == "Erika Msermann"
    # The identity that upstream actually keys on must arrive intact.
    assert seen["headers"]["X-Munin-Email"] == "erika.moesermann@example.org"


def test_ascii_name_still_forwarded_verbatim(client, gw_env, monkeypatch):
    seen = _mock_upstream(gw_env, monkeypatch, {})
    r = client.get(
        "/api/status",
        headers={
            "X-Munin-Email": "alice@example.org",
            "X-Munin-Name": "Alice Smith",
        },
    )
    assert r.status_code == 200
    assert seen["headers"]["X-Munin-Name"] == "Alice Smith"


def test_missing_name_header_falls_back_to_email_local_part(client, gw_env, monkeypatch):
    seen = _mock_upstream(gw_env, monkeypatch, {})
    r = client.get("/api/status", headers={"X-Munin-Email": "alice@example.org"})
    assert r.status_code == 200
    assert seen["headers"]["X-Munin-Name"] == "alice"
