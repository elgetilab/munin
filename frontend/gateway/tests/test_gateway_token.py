"""The gateway sends MUNIN_GATEWAY_TOKEN to the backend on every request,
and sends nothing when it is unset (the backend then does not check)."""
import sys


def _load(monkeypatch, gw_env_unused=None, **env):
    monkeypatch.delenv("MUNIN_GATEWAY_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("main", None)
    import main
    return main


def test_token_rides_on_every_backend_request(gw_env, monkeypatch):
    m = _load(monkeypatch, MUNIN_GATEWAY_TOKEN="s3cret")
    req = m.http_client.build_request("GET", "/api/chats",
                                      headers={"X-Munin-Email": "a@b.org"})
    assert req.headers["X-Munin-Gateway-Token"] == "s3cret"
    assert req.headers["X-Munin-Email"] == "a@b.org"


def test_unset_token_sends_no_header(gw_env, monkeypatch):
    m = _load(monkeypatch)
    req = m.http_client.build_request("GET", "/api/chats")
    assert "X-Munin-Gateway-Token" not in req.headers
