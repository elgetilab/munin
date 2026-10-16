"""cluster_probe: one down post after the threshold, one recovery post with
the duration, silence in between and for blips below the threshold.

A local http.server on 127.0.0.1 plays both the cluster's /api/status and
the webhook receiver; nothing leaves the machine.
"""
import http.server
import importlib.util
import json
import pathlib
import threading

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "cluster_probe.py"

HEALTHY = {"maintenance": {"active": False}, "vllm": {"status": "offline"},
           "services": {"retrieval": "ok", "qdrant": "ok", "neo4j": "ok"}}


class _Stub:
    status_code = 200
    status_body = json.dumps(HEALTHY).encode()
    posts = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(_Stub.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_Stub.status_body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _Stub.posts.append((self.path, self.headers.get("Content-Type"),
                            self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Stub.status_code, _Stub.posts = 200, []
    _Stub.status_body = json.dumps(HEALTHY).encode()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("cluster_probe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(server, tmp_path, **extra):
    env = {"PROBE_URL": server + "/api/status",
           "PROBE_STATE_FILE": str(tmp_path / "state.json"),
           "PROBE_TIMEOUT_S": "2",
           "MUNIN_ALERT_WEBHOOK": server + "/api/webhooks/1/tok",
           "MUNIN_ALERT_FORMAT": "discord"}
    env.update(extra)
    return env


def _contents(posts):
    return [json.loads(body)["content"] for _, _, body in posts]


def test_down_once_after_threshold_then_one_recovery(server, probe, tmp_path):
    env = _env(server, tmp_path)
    _Stub.status_code = 503
    t0 = 1_000_000
    for i in range(5):                        # 0, 2, 4, 6, 8 min: below 10
        assert probe.run(env, now=t0 + 120 * i, host="vps") == 1
    assert _Stub.posts == []
    probe.run(env, now=t0 + 600, host="vps")  # 10 min: alert
    probe.run(env, now=t0 + 720, host="vps")  # still down: silent
    probe.run(env, now=t0 + 840, host="vps")
    msgs = _contents(_Stub.posts)
    assert len(msgs) == 1
    assert msgs[0].startswith("[munin/vps] cluster unreachable/unhealthy")
    assert "HTTP 503" in msgs[0]
    assert _Stub.posts[0][1] == "application/json"

    _Stub.status_code = 200
    assert probe.run(env, now=t0 + 1500, host="vps") == 0
    probe.run(env, now=t0 + 1620, host="vps")  # healthy again: quiet
    msgs = _contents(_Stub.posts)
    assert len(msgs) == 2
    assert "recovered after 25 min" in msgs[1]
    assert json.loads((tmp_path / "state.json").read_text()) == {}


def test_blip_below_threshold_is_never_posted(server, probe, tmp_path):
    env = _env(server, tmp_path)
    _Stub.status_code = 502
    probe.run(env, now=0)
    probe.run(env, now=480)
    _Stub.status_code = 200
    probe.run(env, now=600)
    _Stub.status_code = 502                   # a fresh failure restarts the clock
    probe.run(env, now=720)
    probe.run(env, now=1200)
    assert _Stub.posts == []


def test_unreachable_counts_as_down(server, probe, tmp_path):
    env = _env(server, tmp_path, PROBE_URL="http://127.0.0.1:9/api/status")
    probe.run(env, now=0)
    probe.run(env, now=600)
    msgs = _contents(_Stub.posts)
    assert len(msgs) == 1 and "unreachable" in msgs[0]


def test_unhealthy_service_and_maintenance(server, probe):
    bad = dict(HEALTHY, services={"retrieval": "ok", "qdrant": "error"})
    _Stub.status_body = json.dumps(bad).encode()
    assert probe.probe(server + "/api/status", 2) == (False, "unhealthy: qdrant=error")
    bad["maintenance"] = {"active": True}
    _Stub.status_body = json.dumps(bad).encode()
    assert probe.probe(server + "/api/status", 2)[0] is True
    _Stub.status_body = b"<html>bad gateway</html>"
    assert probe.probe(server + "/api/status", 2) == (False, "status body is not JSON")


def test_dry_run_never_posts_or_writes_state(server, probe, tmp_path):
    env = _env(server, tmp_path)
    _Stub.status_code = 503
    probe.run(env, now=0)                     # real run opens the clock
    before = (tmp_path / "state.json").read_text()
    probe.run(env, dry_run=True, now=900)     # would alert
    assert _Stub.posts == []
    assert (tmp_path / "state.json").read_text() == before


def test_placeholder_webhook_is_refused(server, probe, tmp_path):
    env = _env(server, tmp_path,
               MUNIN_ALERT_WEBHOOK=server + "/api/webhooks/CHANGE-ME")
    _Stub.status_code = 503
    probe.run(env, now=0)
    probe.run(env, now=600)
    assert _Stub.posts == []


def test_failed_post_is_retried(server, probe, tmp_path):
    env = _env(server, tmp_path, MUNIN_ALERT_WEBHOOK="http://127.0.0.1:9/hook")
    _Stub.status_code = 503
    probe.run(env, now=0)
    probe.run(env, now=600)
    assert json.loads((tmp_path / "state.json").read_text())["alerted"] is False
    env["MUNIN_ALERT_WEBHOOK"] = server + "/api/webhooks/1/tok"
    probe.run(env, now=720)
    assert len(_Stub.posts) == 1


@pytest.mark.parametrize("url,fmt", [
    ("https://hooks.slack.com/services/x", "slack"),
    ("https://discord.com/api/webhooks/1/x", "discord"),
    ("https://discordapp.com/api/webhooks/1/x", "discord"),
    ("https://ntfy.sh/long-random-topic", "ntfy"),
    ("https://push.host/message?token=abc", "gotify"),
    ("https://alerts.internal/hook", "json"),
])
def test_format_detection_and_payloads(probe, url, fmt):
    assert probe.detect_format(url) == fmt
    ctype, body = probe.build_payload(fmt, "vps", 'down "now"\nreally')
    if fmt == "ntfy":
        assert ctype == "text/plain" and body == b'[munin/vps] down "now" really'
    else:
        obj = json.loads(body)
        key = {"discord": "content", "gotify": "message"}.get(fmt, "text")
        assert 'down "now" really' in obj[key]
