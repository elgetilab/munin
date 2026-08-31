# Monitoring

## What's running

- **Prometheus** (`munin-prometheus` container, port 127.0.0.1:9090 on
  hugin). Scrapes `munin-retrieval:8080/metrics/` every 15 s. 30-day
  retention. Lives under the `monitoring` compose profile.
- **No Grafana.** Dashboards live in the webui (Admin panel → Metrics
  tab) and query Prometheus via an admin-gated proxy on retrieval.

## Cluster liveness (vLLM health check)

Prometheus watches the *application*; this watches whether there is a
cluster underneath it. The two do not overlap:
`munin_vllm_request_total` only moves when chat traffic flows, so a
vLLM that never started at 06:00 with no users online emits nothing at
all. That is exactly what happened on 2026-08-31, when a GPU-less boot
took chat down from 05:02 and nothing surfaced it until a human asked
at 10:44.

- **Unit**: `munin-vllm-health.timer` -> `munin-vllm-health.service`,
  hourly at `:15`. Installed and enabled by
  `sudo ./deploy.sh vllm`.
- **Script**: `scripts/vllm/check-vllm-health.sh`, deployed to
  `/opt/cluster/scripts/llm/`.
- **Log**: `/var/log/cluster-admin/vllm-health.log`.

A systemd timer rather than an `/etc/cron.d` entry on purpose: the
cron file is written by `HuginSLURM/setup/07-scheduling-setup.sh`,
which runs once per node, so a cron entry would never reach an
already-provisioned cluster.

### What it does

1. **Skips 02:00-06:00.** vLLM is deliberately down then and the GPUs
   belong to batch work. Restarting in that window would steal the
   slot the chunk-index embed pass runs in.
2. **Probes** `http://127.0.0.1:8000/v1/models`. Answering means
   healthy; it clears the restart stamp and exits.
3. **Diagnoses before acting.** If the SLURM node is
   `DOWN`/`DRAIN`/`NOT_RESPONDING` it alerts and does *not* resubmit,
   because a job would only pend. If a vLLM job already exists it
   alerts without resubmitting, since it may still be loading (~2 min).
4. **Restarts once** when the node is healthy and no job exists, via
   `schedule-vllm.sh start`, then alerts regardless of outcome so the
   event is never silent. A one-hour cooldown stamp stops a
   crashlooping vLLM being fed back to SLURM every hour.

### Alert channels

Always logs, and calls `wall`. `wall` reaches only logged-in
terminals, which is why it did not help in the incident above, so for
anything off-machine set `MUNIN_ALERT_WEBHOOK` in
`/opt/hugin/config/cluster.env` to an endpoint accepting a JSON POST
of `{"text": "..."}` (Slack, Discord, ntfy, Gotify). Unset is a silent
no-op, so other sites deploy this without inventing a notification
stack. Note `deploy.sh config` does not overwrite an existing
`cluster.env`, so on an existing install add the line by hand.

**Pick the destination carefully.** On public `ntfy.sh` there is no
auth: the topic name IS the secret, and anyone who subscribes to it
receives every alert. Alert bodies name the host and describe cluster
state (`vLLM is down AND the SLURM node is DOWN+DRAIN...`), so use a
long random topic, or prefer a Slack/Discord webhook, a self-hosted
ntfy, or Gotify where the URL carries a real credential.

The script refuses to POST when the value looks like a placeholder
(`CHANGE-ME`, `your-topic`, `example.com`), logging a warning instead.
An example URL pasted verbatim is a real, and in ntfy's case guessable,
destination.

### Operating it

```bash
# see what it would do, changing nothing
sudo /opt/cluster/scripts/llm/check-vllm-health.sh --dry-run

# pause during planned maintenance (skips check AND restart)
touch /opt/munin/logs/vllm-health.hold
rm    /opt/munin/logs/vllm-health.hold

systemctl list-timers munin-vllm-health.timer
journalctl -u munin-vllm-health.service --since today
```

Exit 1 means "reported a problem", and the unit sets
`SuccessExitStatus=0 1` so a degraded cluster does not also show up as
a failed unit in `systemctl --failed`.

## Why no Grafana

Decided 2026-06-02 (see commit history). At single-cluster, ~150-user
scale the lock-in / styling / SSO costs outweighed the convenience.
The in-house dashboard reuses the existing React + Tailwind + admin
auth stack and moves in lockstep with schema changes: adding a metric
is one PR that touches `metrics.py` (emit), `MetricsTab.tsx`
(display), and possibly `prometheus.yml` (scrape).

If Munin grows beyond what one dashboard fits, Grafana can come back
later. The metrics-emission code is unchanged either way.

## How metrics flow

```
chat code (chat_service, mcp/executor, ...)
    └─> metrics.py (prometheus_client counters/histograms)
            └─> /metrics/ on retrieval:8080
                    └─> prometheus container (every 15s)
                            └─> 30-day TSDB on disk

admin opens https://chat.muninai.org → Admin → Metrics
    └─> /api/admin/metrics/query_range on retrieval
            └─> metrics_proxy.py
                    ├─> role check via auth.muninai.org/admin/check-role
                    │       (bearer KB_GATE_TOKEN, cached 30 s per email)
                    └─> proxy to prometheus:9090/api/v1/query_range
                            └─> JSON back to MetricsTab → recharts
```

## Adding a new metric end-to-end

1. **Emit it.** Add the metric to `retrieval/metrics.py` (counter /
   histogram / gauge). Instrument the call sites. Use the
   `munin_` prefix and keep label cardinality low.
2. **(Optional) Add a panel.** Append a `PanelDef` to the `PANELS`
   array in `webui/src/components/admin/MetricsTab.tsx`. PromQL goes
   in the same file. Use `$WINDOW` for the rate window placeholder
   so the same panel works at any zoom level.
3. **No Prometheus restart needed** — the scrape config already
   captures `munin_*` series by job, not by name. Restart retrieval
   so the new counter starts incrementing.

## Reproducibility (other sites)

The dashboard works on a fresh clone if both sides of the VPS↔cluster
pair share the same `KB_GATE_TOKEN`. That's the only piece of cross-
site coordination the Metrics tab needs; everything else lives in the
repo and is picked up by `deploy.sh`.

### One-time setup (fresh deploy)

The standard cluster + VPS bootstrap (`SETUP-CLUSTER.md`,
`SETUP-VPS.md`) already covers generating `KB_GATE_TOKEN` and
declaring it on both sides. If you followed those, you're done --
`sudo ./backend/deploy.sh monitoring && sudo ./backend/deploy.sh retrieval`
on hugin brings up Prometheus + the metrics proxy.

### Existing deploys: adding KB_GATE_TOKEN on the cluster side

If you already have a working VPS that issued a `KB_GATE_TOKEN` for
the tusd KB-upload gate, the cluster needs the SAME value (the
metrics proxy reuses the auth service's `/admin/check-role` endpoint,
which gates on this bearer).

> **The shared secret has two independent copies and nothing syncs
> them.** The VPS holds it in `~/munin/frontend/.env`; the cluster
> holds it in `/opt/hugin/config/cluster.env`. They drift silently:
> a value added to one side is never propagated to the other, and a
> mismatch surfaces only as a broken Metrics tab (proxy returns 502,
> `check-role` 401). `cluster.env` is the cluster's source of truth —
> `deploy.sh` symlinks `/opt/munin/docker/.env -> cluster.env` on
> every run (`ln -snf`). **Always edit `cluster.env` directly; never
> `mv`/write a file over the `/opt/munin/docker/.env` symlink.**
> Replacing the symlink with a regular copy freezes a stale token
> that "works" until the next deploy restores the symlink to the
> divergent `cluster.env` and recreates retrieval — which is exactly
> how this broke once (2026-06-23: a deploy relinked `.env` to a
> `cluster.env` whose token never matched the VPS).

Copy the VPS-side value into the cluster's `cluster.env` from a shell
that has SSH to the VPS:

```
TOKEN=$(ssh <admin>@<vps> 'docker exec frontend-munin-auth-1 sh -c "printf %s \"\$KB_GATE_TOKEN\""')
# Edit cluster.env IN PLACE — do not replace the /opt/munin/docker/.env symlink.
sudo sh -c "grep -v '^KB_GATE_TOKEN=' /opt/hugin/config/cluster.env > /opt/hugin/config/cluster.env.new && \
            echo 'KB_GATE_TOKEN=$TOKEN' >> /opt/hugin/config/cluster.env.new && \
            chmod 600 /opt/hugin/config/cluster.env.new && \
            mv /opt/hugin/config/cluster.env.new /opt/hugin/config/cluster.env"
sudo sh -c 'cd /opt/munin/docker && docker compose --profile rag up -d --force-recreate retrieval'
```

To verify both sides match without exposing the token, run this on
the cluster:

```
sudo grep '^KB_GATE_TOKEN=' /opt/munin/docker/.env | \
  sed -E 's|^KB_GATE_TOKEN=(.{6}).*(.{4})$|first6=\1 last4=\2|'
```

And on the VPS:

```
ssh <admin>@<vps> 'docker exec frontend-munin-auth-1 sh -c \
  "echo first6=\$(printf %s \"\$KB_GATE_TOKEN\" | head -c 6); \
   echo last4=\$(printf %s \"\$KB_GATE_TOKEN\" | tail -c 4)"'
```

The `first6=/last4=` outputs should match exactly. If they differ,
copy one side's value to the other.

### Optional overrides

- `AUTH_CHECK_ROLE_URL` — only set if your auth service hostname
  isn't `auth.muninai.org`. Default (`docker-compose.yml`) is
  `https://auth.muninai.org/admin/check-role`.
- `PROMETHEUS_URL` — only set if you've moved Prometheus off its
  default docker service name. Default is `http://prometheus:9090`.

No site-specific values are hardcoded in checked-in files. The
defaults in `metrics_proxy.py` are valid for this site; everything
overridable lives in `.env`.

## Operating notes

- **Wipe Prometheus storage:** `docker volume rm
  frontend_prometheus_data` (the named volume; rebuild on next deploy).
  Use this if a series goes bad and you want a clean slate.
- **Reach Prometheus's own UI:** `ssh -L 9090:127.0.0.1:9090 hugin`
  then `http://localhost:9090`. Useful for ad-hoc PromQL outside the
  dashboard.
- **Adding a panel that needs ad-hoc PromQL:** prototype it in the
  Prometheus UI, copy-paste into `MetricsTab.tsx`.
- **`$WINDOW` substitution** is the only macro the dashboard does.
  Everything else is raw PromQL.

## Endpoint reference

### `POST /api/admin/metrics/query`
Instant query.

Request body:
```json
{ "query": "<promql>", "time": "2026-06-02T12:00:00Z" }
```
`time` is optional; defaults to now.

Response: Prometheus's `/api/v1/query` body verbatim.

### `POST /api/admin/metrics/query_range`
Range query.

Request body:
```json
{
  "query": "<promql>",
  "start": "2026-06-02T11:00:00Z",
  "end":   "2026-06-02T12:00:00Z",
  "step":  "15s"
}
```

Response: Prometheus's `/api/v1/query_range` body verbatim.

### Auth on both
- Caller's `X-Munin-Email` (set by Caddy forward-auth) must resolve to
  `role == "admin"` in the auth service.
- Lookup is cached 30 s per email so a dashboard tick (~8 panels)
  costs one auth round-trip.
- Errors:
  - `401` — missing or empty `X-Munin-Email`
  - `403` — caller is not admin
  - `502` — auth or prometheus unreachable
  - `503` — `KB_GATE_TOKEN` unset (proxy not configured)

## Metrics currently emitted

| Family | Labels | What it tracks |
|---|---|---|
| `munin_vllm_request_total` | `purpose`, `outcome` | vLLM HTTP calls by call site + final outcome |
| `munin_vllm_request_duration_seconds` | `purpose` | wall time of one successful vLLM call |
| `munin_vllm_tokens_total` | `purpose`, `direction` | tokens billed per call site |
| `munin_mcp_tool_total` | `name`, `outcome` | MCP tool dispatches |
| `munin_mcp_tool_duration_seconds` | `name` | wall time of one MCP tool dispatch |
| `munin_chat_turns_total` | `persona`, `terminal_reason` | chat turn outcomes |
| `munin_phantom_url_total` | `kind` | hallucinated artifact / paper URLs caught by post-turn audit |
| `munin_citation_claims_total` | `outcome` | "X et al." attributions per turn, `grounded` when the surname appears in a tool result from that turn, `ungrounded` when it does not. Watch the ratio, not the raw ungrounded count: it is only interpretable against how many attributions were made. |

Plus `python_*` and `process_*` from `prometheus_client` defaults.
