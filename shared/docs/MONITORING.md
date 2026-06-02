# Monitoring

## What's running

- **Prometheus** (`munin-prometheus` container, port 127.0.0.1:9090 on
  hugin). Scrapes `munin-retrieval:8080/metrics/` every 15 s. 30-day
  retention. Lives under the `monitoring` compose profile.
- **No Grafana.** Dashboards live in the webui (Admin panel → Metrics
  tab) and query Prometheus via an admin-gated proxy on retrieval.

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

What another admin needs to do for a clean clone:

1. `git clone` the repo onto their cluster.
2. Add to `/opt/munin/docker/.env`:
   - `KB_GATE_TOKEN=<random-32-byte-hex>` — the shared bearer between
     retrieval and auth. Must match the one set on the auth side.
   - `AUTH_CHECK_ROLE_URL=https://auth.<their-domain>/admin/check-role`
     (optional; the default points at `auth.muninai.org`).
3. `sudo ./backend/deploy.sh monitoring` — installs the prometheus
   config and brings the container up.
4. `sudo ./backend/deploy.sh retrieval` — rebuilds retrieval with the
   metrics-proxy env vars wired in.
5. Open the chat UI, sign in as an admin, navigate to Admin →
   Metrics. The 8 default panels should populate within ~30 s.

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

Plus `python_*` and `process_*` from `prometheus_client` defaults.
