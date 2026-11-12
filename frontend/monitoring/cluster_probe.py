#!/usr/bin/env python3
"""
cluster_probe.py: off-host liveness probe for the cluster, run on the VPS.

The cluster's own check (backend/scripts/vllm/check-vllm-health.sh) cannot
report a cluster that is powered off, has lost its network, or whose reverse
SSH tunnel to the VPS is down: in every one of those cases the box that would
send the alert is the box that is gone. This probe asks the same question the
chat UI asks, from the outside: GET /api/status through the tunnel, the URL
the api-gateway uses (CLUSTER_TUNNEL / BACKEND_URL in docker-compose.yml,
default http://127.0.0.1:18080).

Healthy means HTTP 200, a JSON body, and services.retrieval / services.qdrant
reporting "ok". During announced maintenance (maintenance.active) reachability
alone counts. vLLM is deliberately NOT part of it: vLLM is meant to be down
02:00-06:00 and the cluster-side check already owns that question.

Alerting: one "down" post once the cluster has been unhealthy for
PROBE_DOWN_AFTER_S (default 600 s, five consecutive 2-minute runs), then one
"recovered after N min" post when it answers again. A blip shorter than the
threshold is logged and never posted. State lives in a small JSON file so the
timer can be a oneshot.

Config (environment, normally from /etc/munin/cluster-probe.env):
    MUNIN_ALERT_WEBHOOK  destination; unset = log only
    MUNIN_ALERT_FORMAT   slack|discord|gotify|ntfy|json, overrides detection
    PROBE_URL            default http://127.0.0.1:18080/api/status
    PROBE_DOWN_AFTER_S   default 600
    PROBE_TIMEOUT_S      default 15
    PROBE_STATE_FILE     default /var/lib/munin-cluster-probe/state.json

Usage: cluster_probe.py [--dry-run]
    --dry-run  probe and report the decision; never POSTs, never writes state
Exit: 0 healthy, 1 unhealthy (the unit treats both as success).
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:18080/api/status"
DEFAULT_STATE = "/var/lib/munin-cluster-probe/state.json"
REQUIRED_SERVICES = ("retrieval", "qdrant")
USER_AGENT = "munin-cluster-probe/1.0"

# Same refusal list as check-vllm-health.sh: an example URL pasted verbatim is
# a real destination, and on public ntfy a guessable topic is a broadcast.
PLACEHOLDER_MARKERS = ("CHANGE-ME", "CHANGE_ME", "change-me", "your-topic",
                       "example.com", "EXAMPLE")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + ": " + msg, flush=True)


def probe(url, timeout):
    """Return (healthy, reason)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.status
            body = resp.read()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return False, f"unreachable ({reason})"
    if code != 200:
        return False, f"HTTP {code}"
    try:
        data = json.loads(body)
    except ValueError:
        return False, "status body is not JSON"
    if not isinstance(data, dict):
        return False, "status body is not a JSON object"
    if (data.get("maintenance") or {}).get("active"):
        return True, "maintenance announced; reachable"
    services = data.get("services") or {}
    bad = [f"{s}={services.get(s, 'missing')}" for s in REQUIRED_SERVICES
           if services.get(s) != "ok"]
    if bad:
        return False, "unhealthy: " + ", ".join(bad)
    return True, "ok"


def detect_format(url, override=""):
    if override:
        return override
    if "hooks.slack.com" in url:
        return "slack"
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        return "discord"
    if "ntfy" in url:
        return "ntfy"
    if "gotify" in url or "/message?token=" in url:
        return "gotify"
    return "json"


def build_payload(fmt, host, msg):
    """Return (content_type, body bytes). Mirrors check-vllm-health.sh."""
    msg = msg.replace("\n", " ")
    tagged = f"[munin/{host}] {msg}"
    if fmt == "ntfy":
        return "text/plain", tagged.encode()
    if fmt == "discord":
        obj = {"content": tagged}
    elif fmt == "gotify":
        obj = {"title": f"munin/{host}", "message": msg}
    else:  # slack and the generic fallback
        obj = {"text": tagged}
    return "application/json", json.dumps(obj).encode()


def webhook_target(env):
    """Return (url, placeholder_refused)."""
    url = env.get("MUNIN_ALERT_WEBHOOK", "").strip()
    if url and any(m in url for m in PLACEHOLDER_MARKERS):
        return "", True
    return url, False


def send(msg, env, dry_run, host):
    """Post msg. True when delivered, or when there is nowhere to deliver it
    (so the state machine moves on instead of retrying a no-op forever)."""
    log(("[dry-run] " if dry_run else "") + "ALERT: " + msg)
    url, refused = webhook_target(env)
    if refused:
        log("  WARNING: MUNIN_ALERT_WEBHOOK still holds a PLACEHOLDER; refusing to POST.")
        return True
    if not url:
        log("  (MUNIN_ALERT_WEBHOOK unset; log only)")
        return True
    fmt = detect_format(url, env.get("MUNIN_ALERT_FORMAT", "").strip())
    ctype, body = build_payload(fmt, host, msg)
    if dry_run:
        log(f"  [dry-run] would POST ({fmt}) to $MUNIN_ALERT_WEBHOOK")
        log(f"  [dry-run] Content-Type: {ctype}")
        log(f"  [dry-run] body: {body.decode()}")
        return True
    # An explicit User-Agent: Discord's Cloudflare front answers urllib's
    # default "Python-urllib/3.x" with 403 (error 1010), so without this every
    # Discord post would fail and be retried forever. curl's UA passes.
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype,
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except (urllib.error.URLError, OSError) as e:
        log(f"  WARNING: webhook POST failed ({fmt}): {e}; will retry next run")
        return False
    log(f"  webhook notified ({fmt})")
    return True


def load_state(path):
    try:
        with open(path) as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def run(env=None, dry_run=False, now=None, host=None):
    env = dict(os.environ if env is None else env)
    now = int(time.time() if now is None else now)
    host = host or socket.gethostname().split(".")[0]
    url = env.get("PROBE_URL") or DEFAULT_URL
    down_after = int(env.get("PROBE_DOWN_AFTER_S") or 600)
    timeout = float(env.get("PROBE_TIMEOUT_S") or 15)
    state_path = env.get("PROBE_STATE_FILE") or DEFAULT_STATE

    state = load_state(state_path)
    first_fail = state.get("first_fail")
    alerted = bool(state.get("alerted"))

    healthy, reason = probe(url, timeout)

    if healthy:
        log(f"OK: cluster answering at {url} ({reason})")
        if first_fail is None:
            return 0
        mins = (now - int(first_fail)) // 60
        if alerted:
            if not send(f"cluster reachable again from the VPS, recovered after "
                        f"{mins} min ({url})", env, dry_run, host):
                return 0  # keep the incident so the recovery is retried
        else:
            log(f"  short outage ({mins} min) cleared below the alert threshold")
        new_state = {}
    else:
        if first_fail is None:
            first_fail = now
        elapsed = now - int(first_fail)
        log(f"FAIL: {url}: {reason} (failing for {elapsed // 60} min)")
        new_state = {"first_fail": first_fail, "alerted": alerted,
                     "reason": reason}
        if not alerted and elapsed >= down_after:
            if send(f"cluster unreachable/unhealthy from the VPS for "
                    f"{elapsed // 60} min: {reason}. Chat is down for users. "
                    f"Check the reverse tunnel (munin-tunnel.service on the cluster) and the "
                    f"retrieval container.", env, dry_run, host):
                new_state["alerted"] = True

    if dry_run:
        log(f"  [dry-run] would write state {json.dumps(new_state)} -> {state_path}")
    else:
        save_state(state_path, new_state)
    return 0 if healthy else 1


def main(argv):
    return run(dry_run="--dry-run" in argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
