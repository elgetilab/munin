"""Layer-2 failover: the gateway serves from the standby (Lite) backend while
the cluster is unreachable. State machine with a fake clock, then the gateway
end to end against two in-process fake backends (no network, no VPS)."""
import sqlite3
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

import backend_failover as bf


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def machine(**kw):
    clock, wall = Clock(), Clock(1_800_000_000.0)
    s = bf.BackendFailover(clock=clock, wall=wall, **kw)
    s.observe_standby(True)
    return s, clock, wall


# ── state machine ────────────────────────────────────────────────────────────

def test_needs_fail_after_consecutive_failures():
    s, _, _ = machine(fail_after=3)
    s.observe_primary(False)
    s.observe_primary(False)
    assert s.mode == bf.NORMAL
    assert s.observe_primary(False, "ConnectError") == bf.REDUCED
    assert s.use_standby()
    assert s.status()["since"].startswith("2027-01-15T08:00")


def test_no_failover_to_a_dead_standby():
    s, _, _ = machine(fail_after=1)
    s.observe_standby(False)
    s.observe_primary(False)
    assert s.mode == bf.NORMAL


def test_failback_needs_streak_and_hold():
    s, clock, _ = machine(fail_after=1, recover_after=3, min_hold_s=300)
    s.observe_primary(False)
    for _ in range(5):
        s.observe_primary(True)
    assert s.mode == bf.REDUCED
    clock.t += 301
    assert s.tick() == bf.NORMAL


def test_flapping_tunnel_does_not_flap_the_mode():
    s, clock, _ = machine(fail_after=3, recover_after=4, min_hold_s=0)
    for _ in range(3):
        s.observe_primary(False)
    assert s.mode == bf.REDUCED
    for _ in range(20):  # up for 3 probes, down for 1, repeatedly
        s.observe_primary(True)
        s.observe_primary(True)
        s.observe_primary(True)
        s.observe_primary(False)
        clock.t += 20
    assert s.mode == bf.REDUCED
    assert [k for k, _ in s.events] == [bf.REDUCED]


def test_manual_failback_waits_for_admin():
    s, clock, _ = machine(fail_after=1, recover_after=1, min_hold_s=0, auto_failback=False)
    s.observe_primary(False)
    for _ in range(50):
        s.observe_primary(True)
    clock.t += 10_000
    assert s.mode == bf.REDUCED
    assert s.force(bf.NORMAL, "admin") == bf.NORMAL
    assert s.pinned
    s.observe_primary(False)
    s.observe_primary(False)
    assert s.mode == bf.NORMAL, "pinned: probes do not move it"
    s.release()
    assert s.mode == bf.REDUCED


def test_persistence_round_trip_keeps_since(tmp_path):
    conn = sqlite3.connect(tmp_path / "g.db")
    bf.init_schema(conn)
    s, _, wall = machine(fail_after=1)
    s.observe_primary(False)
    bf.save(conn, s)
    eps = bf.episodes(conn)
    assert len(eps) == 1 and eps[0]["ended_at"] is None
    restored, _, _ = machine()
    bf.load(conn, restored)
    assert restored.mode == bf.REDUCED
    assert restored.since_wall == s.since_wall
    # A restart restarts the hold, so it cannot fail back immediately.
    restored.recover_after, restored.min_hold_s = 1, 300
    restored.observe_primary(True)
    assert restored.mode == bf.REDUCED
    s.min_hold_s, s.recover_after = 0, 1
    s.observe_primary(True)
    bf.save(conn, s)
    assert bf.episodes(conn)[0]["ended_at"] is not None


# ── gateway end to end ───────────────────────────────────────────────────────

class Backends:
    def __init__(self):
        self.cluster_up = True
        self.standby_up = True
        self.hits = []

    def handler(self, name):
        def handle(request: httpx.Request) -> httpx.Response:
            up = self.cluster_up if name == "cluster" else self.standby_up
            if not up:
                raise httpx.ConnectError("refused", request=request)
            self.hits.append((name, request.url.path))
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "healthy"})
            if request.url.path == "/api/status":
                return httpx.Response(200, json={"vllm": {"status": "running", "model": "m"}, "services": {}})
            if request.url.path == "/api/chat/completions":
                body = f'data: {{"choices": [{{"delta": {{"content": "{name}"}}}}]}}\n\ndata: [DONE]\n\n'
                return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"from": name})
        return handle


def load_gateway(tmp_path, monkeypatch, standby=True, **env):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "gateway.db"))
    q = tmp_path / "quotas.yml"
    q.write_text("defaults:\n  requests_per_minute: 1000\n")
    monkeypatch.setenv("QUOTAS_PATH", str(q))
    monkeypatch.setenv("BACKEND_PROBE_DISABLED", "1")
    if standby:
        monkeypatch.setenv("BACKEND_STANDBY_URL", "http://standby")
    else:
        monkeypatch.delenv("BACKEND_STANDBY_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("main", None)
    import main
    main.DB_PATH = tmp_path / "gateway.db"
    main.QUOTAS_PATH = q
    b = Backends()
    main.http_client = httpx.AsyncClient(base_url="http://cluster", transport=httpx.MockTransport(b.handler("cluster")))
    if standby:
        main.standby_client = httpx.AsyncClient(base_url="http://standby", transport=httpx.MockTransport(b.handler("standby")))
    return main, b


USER = {"X-Munin-Email": "u@example.org"}
ADMIN = {"X-Munin-Email": "a@example.org", "X-Munin-Role": "admin"}


def probe(c, main, n=1):
    for _ in range(n):
        c.portal.call(main.backend_probe_once)


def test_gateway_without_standby_is_unchanged(tmp_path, monkeypatch):
    main, b = load_gateway(tmp_path, monkeypatch, standby=False)
    with TestClient(main.app) as c:
        assert c.get("/api/chats", headers=USER).json() == {"from": "cluster"}
        assert c.get("/api/backend-mode", headers=USER).json() == {"mode": "normal", "standby_configured": False}
        assert "backend" not in c.get("/api/status", headers=USER).json()


def test_gateway_fails_over_and_back(tmp_path, monkeypatch):
    main, b = load_gateway(tmp_path, monkeypatch, BACKEND_FAIL_AFTER="3",
                           BACKEND_RECOVER_AFTER="2", BACKEND_MIN_HOLD_S="0")
    with TestClient(main.app) as c:
        probe(c, main)
        assert c.get("/api/chats", headers=USER).json() == {"from": "cluster"}

        b.cluster_up = False  # tunnel down
        probe(c, main, 2)
        assert c.get("/api/backend-mode", headers=USER).json()["mode"] == "normal"
        r = c.get("/api/chats", headers=USER)
        assert r.status_code == 502, "no silent retry on the standby before the switch"
        probe(c, main)
        assert c.get("/api/chats", headers=USER).json() == {"from": "standby"}

        status = c.get("/api/status", headers=USER).json()
        assert status["backend"]["mode"] == "reduced"
        assert "not shown" in status["backend"]["history_note"]
        assert status["vllm"]["status"] == "running"

        with c.stream("POST", "/api/chat/completions", headers={**USER, "Content-Type": "application/json"},
                      json={"stream": True, "messages": []}) as r:
            assert "standby" in b"".join(r.iter_bytes()).decode()

        mode = c.get("/api/backend-mode", headers=ADMIN).json()
        assert mode["mode"] == "reduced" and mode["since"]
        assert mode["episodes"][0]["ended_at"] is None
        assert "detail" not in c.get("/api/backend-mode", headers=USER).json()

        b.cluster_up = True
        probe(c, main, 2)
        assert c.get("/api/chats", headers=USER).json() == {"from": "cluster"}
        assert "backend" not in c.get("/api/status", headers=USER).json()
        assert c.get("/api/backend-mode", headers=ADMIN).json()["episodes"][0]["ended_at"]


def test_admin_override_and_manual_failback(tmp_path, monkeypatch):
    main, b = load_gateway(tmp_path, monkeypatch, BACKEND_FAIL_AFTER="1",
                           BACKEND_RECOVER_AFTER="1", BACKEND_MIN_HOLD_S="0", BACKEND_FAILBACK="manual")
    with TestClient(main.app) as c:
        b.cluster_up = False
        probe(c, main)
        b.cluster_up = True
        probe(c, main, 5)
        assert c.get("/api/chats", headers=USER).json() == {"from": "standby"}, "manual: stays reduced"
        assert c.put("/api/backend-mode", headers=USER, json={"mode": "normal"}).status_code == 403
        assert c.put("/api/backend-mode", headers=ADMIN, json={"mode": "bogus"}).status_code == 400
        r = c.put("/api/backend-mode", headers=ADMIN, json={"mode": "normal"}).json()
        assert r["mode"] == "normal" and r["pinned"] is True
        assert c.get("/api/chats", headers=USER).json() == {"from": "cluster"}
        r = c.put("/api/backend-mode", headers=ADMIN, json={"mode": "auto"}).json()
        assert r["pinned"] is False


def test_v1_models_follows_the_active_backend(tmp_path, monkeypatch):
    main, b = load_gateway(tmp_path, monkeypatch, BACKEND_FAIL_AFTER="1")
    with TestClient(main.app) as c:
        b.cluster_up = False
        probe(c, main)
        b.hits.clear()
        c.get("/v1/models", headers=USER)
        assert ("standby", "/api/models") in b.hits


def test_restart_resumes_reduced_mode(tmp_path, monkeypatch):
    main, b = load_gateway(tmp_path, monkeypatch, BACKEND_FAIL_AFTER="1")
    with TestClient(main.app) as c:
        b.cluster_up = False
        probe(c, main)
        since = c.get("/api/backend-mode", headers=USER).json()["since"]
    main2, b2 = load_gateway(tmp_path, monkeypatch, BACKEND_FAIL_AFTER="1")
    with TestClient(main2.app) as c:
        body = c.get("/api/backend-mode", headers=USER).json()
        assert body["mode"] == "reduced" and body["since"] == since
        assert c.get("/api/chats", headers=USER).json() == {"from": "standby"}
