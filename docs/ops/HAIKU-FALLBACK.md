# Claude Haiku fallback: keeping Munin answering when the cluster cannot

Status: built and tested on branch `feat/haiku-fallback`, **not deployed**.
Written 2026-11-12. The deploy steps at the end are for a person with root on
hugin and a login on the VPS.

Munin has two ways to go dark, and each gets its own layer:

| | What failed | Who notices | Who answers instead |
|---|---|---|---|
| **Layer 1** (cluster) | vLLM: job ended, model being switched, OOM, nightly stop | `llm-failover`, a proxy between retrieval and the LLM | `llm-claude` on the cluster (Claude Haiku 5.5) |
| **Layer 2** (VPS) | the whole cluster or the tunnel to it | the API gateway on the VPS | the Lite backend on the VPS (its own retrieval + `llm-claude`) |

Under layer 1 everything except the model stays the same: history, paper
search, uploads and the knowledge graph all keep working. Under layer 2 only
chat comes back, on a separate database, so it is a reduced mode.

```
browser -> Caddy -> api-gateway --(normal)--> tunnel :18080 -> cluster retrieval -> llm-failover -> vLLM
                                 |                                                       \-> llm-claude -> Anthropic
                                 \--(reduced)-> Lite retrieval :18081 -> llm-claude -> Anthropic
```

## Layer 1: llm-failover on the cluster

### Why a proxy, not code in retrieval

Retrieval calls the LLM from about 13 places across 10 modules, most with
their own `httpx` client and the URL bound at import time
(`from database import VLLM_URL`). A proxy that speaks the same OpenAI API
needs no change at any of them: retrieval's `LLM_BASE_URL` points at
`http://llm-failover:8000` and that is the whole integration. It also gives
one place for the probe, the metrics and the cost guard, and can be tested
with throwaway containers. Code: `backend/llm-failover/` (`failover.py` is the
state machine with no I/O, `main.py` the proxy).

### Routing

- **Probes**, every 5 s (`FAILOVER_PROBE_INTERVAL_S`), 2 s timeout:
  - vLLM: `GET /v1/models` must answer 200 **and list the model retrieval
    asks for** (`FAILOVER_PRIMARY_MODEL` = retrieval's `VLLM_MODEL_NAME`). A
    vLLM that is up but serving another name, as during a model switch, is
    down for retrieval's purposes and is treated so.
  - llm-claude: `GET /health`. Local, costs nothing. It does not prove the API
    key works; a 402 or 401 on a real request does that (below).
- **Passive detection.** A real request that cannot connect, or gets 502, 503
  or 504, counts like a failed probe, so a dead vLLM is usually noticed by the
  first request after it died rather than up to 5 s later.
- **Hysteresis.** primary -> secondary after `FAILOVER_FAIL_AFTER` (2)
  consecutive failures. secondary -> primary only after `FAILOVER_RECOVER_AFTER`
  (6, so 30 s) consecutive good probes **and** `FAILOVER_MIN_HOLD_S` (120 s in
  compose) on the secondary. A vLLM that answers one probe and dies again
  never takes traffic back. The unit tests pin this
  (`test_flapping_primary_never_takes_traffic_back`).
- **Per-request fallback, only before the first byte.** While the route is
  still primary, a request whose vLLM attempt fails (connection refused,
  timeout before headers, 404 unknown model, 500, 502, 503, 504, or a 200 whose
  body never starts) is sent once to the secondary. The proxy reads the first
  chunk of the upstream body before answering, so "before any token was
  streamed" is literal. 4xx answers (context too long, bad request) pass
  through unchanged and are never retried: they would fail on Claude too, or
  mean something retrieval must see. 500 retries the request but does not
  count toward the route's health, so one malformed request cannot move every
  user to Claude.
- **After the first byte the request is committed.** If vLLM dies
  mid-stream the connection is dropped, which retrieval already handles as a
  dropped stream (`vllm_client.py`: retries before the first line, then
  surfaces the error and the UI offers Continue). The next request goes to
  Claude.
- **Switching back.** Automatic, by the rule above. On the secondary, a
  request is also tried on vLLM if Claude fails before its first byte, but
  only when vLLM's last probe was good, so a broken Claude path does not
  hammer a dead vLLM.

### Claude-safe requests

`llm-claude` already ignores what the Messages API rejects (sampling fields,
`chat_template_kwargs`), and the proxy cleans the body as well, so the
secondary's behaviour does not depend on which vLLM features retrieval uses
next (`claude_safe()` in `main.py`):

- `model` becomes `FAILOVER_SECONDARY_MODEL` (llm-claude uses its own
  `CLAUDE_MODEL` anyway; API users cannot pick a dearer model).
- vLLM-only fields are dropped: `top_k`, `min_p`, `repetition_penalty`,
  `presence_penalty`, guided decoding, `seed`, `logprobs`, `n` and the rest of
  `VLLM_ONLY_FIELDS`. Persona sampling settings therefore have no effect while
  Claude answers.
- **Thinking.** `LLM_THINKING_TOGGLE` is global, so retrieval keeps sending
  `chat_template_kwargs`. The sub-task marker is translated to the one
  llm-claude understands: `enable_thinking: false` stays, and on a gpt-oss
  profile (`LLM_THINKING_MODE=effort_low`) `reasoning_effort: "low"` becomes
  `enable_thinking: false`. The user-turn `reasoning_effort` (default
  `medium`) is dropped; llm-claude's `CLAUDE_EFFORT` applies instead.
- **Model name.** `/v1/models` keeps reporting the vLLM name as `id`, so
  retrieval's `/api/models` stays `served: true` and API clients keep sending
  the same `model`. Completions carry the real Claude name in their own
  `model` field, which is what the gateway's usage log records: admins can
  count fallback traffic from `gateway.db`.
- **Router and tools.** The turn router is embedding based and never sees the
  model. Tool names are snake_case and valid for Claude (the Lite deployment
  has run the same retrieval against llm-claude since 2026-10-21). The Qwen
  and gpt-oss parsing workarounds in `chat_service.py` only trigger on those
  models' markers and are inert on Claude output.
- **Context.** Retrieval budgets prompts for `VLLM_MAX_MODEL_LEN` (64k), far
  inside Claude's window and under the 100k prompt size where Haiku 5.5's
  dearer rate card starts.
- Thinking blocks llm-claude keeps for tool loops are lost when a
  conversation crosses from one upstream to the other. That costs that turn's
  reasoning, nothing else.

### What users see

Retrieval's `/api/status` gains `llm_fallback`
(`{"active": true, "model": "claude-haiku-5-5", "since": ...}`, see
`shared/docs/BACKEND-API.md` 4.1). `vllm.status` stays `running`, so the
sleeping page does not show; the chat UI shows a banner instead
(`frontend/webui/src/components/DegradedBanner.tsx`):

> The local model is offline, so claude-haiku-5-5 (Anthropic API) is
> answering for now. Your messages are sent to Anthropic while this notice is
> shown. Paper search still works.

The second sentence matters: in this state user messages leave the cluster.
If that is not acceptable for some deployment, set
`FAILOVER_SECONDARY_ENABLED=0` and the proxy behaves like a plain pass-through
to vLLM (the sleeping page returns when vLLM is down).

When neither upstream can answer, `/v1/models` returns 503, retrieval reports
`vllm.status: offline` and the UI shows the sleeping page as before.

### What admins see

- Log lines in `docker logs munin-llm-failover`:
  `failover route=secondary model=claude-haiku-5-5 reason='probe ConnectError'`,
  `failover route=primary ...`, `request fallback primary -> secondary (...)`
  and `secondary spend cap reached (402)`.
- `GET http://munin-llm-failover:8000/status` (from inside the compose
  network) with streaks, last errors, the block reason and today's tokens.
- Prometheus at `/metrics`, scraped as job `munin-llm-failover`
  (`backend/docker/prometheus/prometheus.yml`):
  `munin_llm_failover_active`, `munin_llm_failover_route{upstream}`,
  `munin_llm_failover_upstream_up{upstream}`,
  `munin_llm_failover_transitions_total{to}`,
  `munin_llm_failover_requests_total{upstream,outcome}`,
  `munin_llm_failover_secondary_tokens_today`,
  `munin_llm_failover_secondary_blocked{reason}`.
  Worth alerting on: `munin_llm_failover_active == 1` for more than 10 min,
  and `munin_llm_failover_secondary_blocked{reason="spend_cap"} == 1`.

### Cost guard

Browser chat is exempt from the gateway's monthly token quota (it meters only
API-key traffic, `EXEMPT_PREFIXES` in `frontend/gateway/main.py`), and its
`prompt_tokens` count conversation size, not money. So the gateway is not a
cost control for the fallback. Three things are:

1. **A dedicated Anthropic Console workspace with a spend limit** for the
   cluster's key. When the limit is hit the API answers 402 (llm-claude maps
   `enforced_spend_limit_reached` to 402). The proxy then turns the secondary
   off for `FAILOVER_SPEND_CAP_COOLDOWN_S` (1 h) and passes the 402 through;
   requests go to vLLM only, and if vLLM is down too, users get the sleeping
   page.
2. **A daily token budget in the proxy**, `FAILOVER_SECONDARY_DAILY_TOKENS`
   (default 20,000,000 prompt + completion tokens per UTC day, counted from the
   `usage` the secondary returns; retrieval asks for `include_usage` on its
   streams). Exhausted: same behaviour as the spend cap until midnight UTC. At
   Haiku 5.5's list price ($0.10 in / $0.50 out per million tokens, prompts up
   to 100k; check the Console for the current card) that is roughly $2 to $4 a
   day at most, before cache discounts.
3. **`CLAUDE_FALLBACK_MODEL`** in llm-claude (default `claude-sonnet-5`) answers
   requests Haiku refuses, at about 20 times the price. Its tokens count toward
   the same daily budget. Set it empty to turn it off.

Deep Research jobs that run while the fallback is active continue on Claude
and are the most likely thing to use the budget.

## Layer 2: gateway failover on the VPS

### Today's port collision

`munin-tunnel.service` on hugin exposes cluster retrieval on the VPS as
`127.0.0.1:18080`. Lite's retrieval was started with `RETRIEVAL_HOST_PORT=18080`
during the outage, so it owns that port, and the tunnel has been failing
since: `journalctl -u munin-tunnel` on hugin shows
`remote port forwarding failed for listen port 18080` every 16 s (seen
2026-11-12, restart count above 160). **The moment Lite moves to another port,
the tunnel binds 18080 within about 16 s and the old gateway sends every user
to the cluster.** That is the cutover, so it must come after the merge (deploy
steps below).

### Design

Code: `frontend/gateway/backend_failover.py` (state machine and its SQLite
rows) and the wiring in `frontend/gateway/main.py`. Off unless
`BACKEND_STANDBY_URL` is set; then:

- **Probes**, every 5 s: `GET /health` on the cluster (via the tunnel) and on
  the standby, 3 s timeout. With the tunnel down, 18080 has no listener and
  the probe fails at once.
- **Hysteresis.** normal -> reduced after `BACKEND_FAIL_AFTER` (3, so 15 s)
  failed cluster probes, and only if the standby answers. reduced -> normal
  after `BACKEND_RECOVER_AFTER` (12, so 60 s) good probes **and**
  `BACKEND_MIN_HOLD_S` (300 s) in reduced mode. A tunnel that flaps keeps the
  gateway on the standby.
- **One owner of writes.** Every `/api/*` and `/v1/*` request goes to the
  active backend, chosen once per request. Nothing is written to both, and a
  failed request is **not** retried on the other backend: a chat saved on the
  wrong side is worse than one failed request. A stream keeps the backend it
  started on even if the mode changes while it runs.
- **Restarts do not flap.** The mode and the episode's start are kept in
  `gateway.db` (`backend_mode`, `backend_episodes`). A restarted gateway
  resumes reduced mode with its original `since` and must earn the hold again.
- **Failback.** `BACKEND_FAILBACK=auto` (default) returns by the rule above.
  `manual` stays on the standby until an admin sends
  `PUT /api/backend-mode {"mode": "normal"}`, for operators who want the merge
  done before users see the cluster again. Admins can also pin `reduced`
  (planned cluster work) and hand control back with `{"mode": "auto"}`.
- `GET /api/backend-mode`: mode and `since` for any signed-in user; admins
  also get the probe state and the list of episodes. **An episode's
  `started_at` is the `--since` cutoff for its merge.**

### What users see in reduced mode

The gateway adds a `backend` block to `/api/status` while it serves from the
standby, and the same banner component shows its note:

> Reduced mode: the main Munin server cannot be reached, so a standby is
> answering. Chats from before 2026-11-12 09:14 UTC are not shown and will be
> back when the main server returns. Paper search is off, and uploads wait in
> the queue until then.

(`BACKEND_REDUCED_NOTE` overrides the text; `{since}` is filled in.) Login,
API keys, quotas and announcements live on the VPS and are unaffected.

### Uploads, papers and other direct paths

Only the gateway fails over. Two other things use `BACKEND_URL` and stay on
the cluster on purpose:

- **Upload hook** (`CLUSTER_INGEST_URL`). In reduced mode its POST to the
  tunnel fails, the PDF stays in `complete/`, and the retry sweep delivers it
  once the cluster is back. That is the same queueing Lite's own 503
  (`ADMIN_INGEST_TOKEN=` empty in `lite.yml`) would give, without depending on
  Lite. The upload page itself works throughout.
- **Caddy `/paper/*`** (`import backend`). Paper pages need the corpus, which
  Lite does not have; they return 502 in reduced mode.

### Split brain, and the simpler alternative

Routing cannot split: there is one gateway and it alone decides. Data can:

1. **Cluster-side writers keep going during a tunnel outage.** Background
   turns, Deep Research jobs and the nightly jobs on the cluster still write to
   the cluster's `chats.db` while users write to Lite's. Without replication
   these touch different conversations (Lite never sees the cluster's), so the
   merge is a clean insert of new rows.
2. **Flapping.** Each switch is another window in which the two databases
   diverge. Hysteresis (15 s down, 60 s up plus 5 min hold) and the manual
   mode bound this; every window is recorded as an episode.
3. **A conversation continued on both sides.** Impossible without
   replication (Lite does not have the old conversations). With replication it
   becomes the main risk: the same conversation gets message index N on both
   sides, and the merge must refuse or pick a winner.
4. **Resume after a switch.** `GET /api/chat/completions/resume` for a stream
   started on the other backend finds nothing (404). Rare: it needs a switch
   during a running answer.

**Reconciliation** after each reduced episode uses lane A's tool
(`backend/scripts/maintenance/merge_state.py` and `fetch_lite_state.sh` on
branch `feat/lite-merge`; uncommitted in its worktree when this was written,
so check its final interface before use): fetch Lite's state read-only, then
`merge_state.py db --source <lite snapshot> --target /opt/munin/data/chats.db
--since <episode started_at>` (dry run, then `--apply`), the same for `files`
and `qdrant` for uploaded documents. Its default `--on-conflict refuse` is the
right policy here: without replication a conflict means something unexpected
happened.

**The simpler alternative** is periodic cluster -> Lite replication, so
reduced mode shows history up to the last sync instead of none. Replacing
Lite's `chats.db` file is not safe (retrieval holds it open); it would be the
same merge tool run the other way, additively, `--since` the previous sync.

**Recommendation.** Ship the failover as built (no replication) now: outages
are rare (one in months), the banner tells users exactly what is missing, and
reconciliation is a conflict-free insert. Add a nightly cluster -> Lite sync
as a second step only once a few merges back have gone cleanly, because it
turns risk 3 from impossible into routine and needs a conflict policy
(`newer-wins` per row, or manual resolution of refusals). The sync must not
run while an episode is open, or it would interleave with reduced-mode writes;
it can check `gateway.db`'s `backend_mode` row before starting.

## Tests

| What | Where | Result 2026-11-12 |
|---|---|---|
| Layer 1 state machine (thresholds, hysteresis, no flapping, blocks, budget rollover) | `backend/llm-failover/tests/test_failover_state.py` | 15 passed |
| Layer 1 proxy vs fake upstreams (per-request fallback, Claude-safe body, 4xx not retried, 404/500 not counted, stream relay and usage, 402 block, `/v1/models`, metrics) | `backend/llm-failover/tests/test_proxy.py` | 17 passed |
| Layer 1 in containers: real image between two stub servers, stub vLLM **killed mid-test** and restarted | `bash backend/llm-failover/tests/integration_failover.sh` | PASS (fallback on the first request after the kill, route moved, hold respected, one transition each way, 503 with both down) |
| Retrieval `llm_fallback` block | `backend/retrieval/tests/test_llm_fallback_status.py` | 5 passed |
| Layer 2 state machine and gateway end to end (fake backends, no network) | `frontend/gateway/tests/test_backend_failover.py` | 11 passed (all 29 gateway tests pass) |
| Banner | `frontend/webui/src/components/DegradedBanner.test.tsx` | 3 passed (full webui suite 1062 passed) |

The Python suites were also run in a clean `python:3.12-slim` container with
the pinned `constraints.txt` versions; CI runs both (`.github/workflows/ci.yml`).
Not tested: a real vLLM job ending under load, a real Anthropic 402, and the
gateway against the real tunnel and Lite (the VPS was read-only for this work).

## Deploy

### Cluster (hugin), as root

From a checkout of `main` with this branch merged (`<munin checkout>`
below).

1. Add the key and settings to `cluster.env` (by hand, the key is a secret):

   ```
   sudoedit /opt/hugin/config/cluster.env
   ```

   ```
   ANTHROPIC_API_KEY=sk-ant-...        # a dedicated workspace with a spend limit
   CLAUDE_MODEL=claude-haiku-5-5
   CLAUDE_FALLBACK_MODEL=              # empty: no Sonnet 5 retries on refusals (optional)
   FAILOVER_SECONDARY_DAILY_TOKENS=20000000
   ```

2. Start llm-claude and llm-failover. Touches only those two containers; it
   installs the current compose file and prometheus.yml:

   ```
   cd <munin checkout>/backend && sudo ./deploy.sh --dry-run fallback
   cd <munin checkout>/backend && sudo ./deploy.sh fallback
   ```

   It prints the proxy's route. With vLLM not running it reports
   `secondary`, which is correct and costs nothing until retrieval uses it.

3. Point retrieval at the proxy. **This recreates the production retrieval
   container and deploys whatever retrieval code `main` has** (live retrieval
   is 116 commits behind main as of 2026-11-12), so do it as part of the
   planned retrieval upgrade, not on its own:

   ```
   sudoedit /opt/hugin/config/cluster.env     # add: LLM_BASE_URL=http://llm-failover:8000
   cd <munin checkout>/backend && sudo ./deploy.sh retrieval
   ```

   Keep `VLLM_URL` as it is (the real vLLM): it is the proxy's primary.

4. Let Prometheus scrape the proxy. This recreates the `munin-prometheus`
   container (history is kept in its volume):

   ```
   cd <munin checkout>/backend && sudo ./deploy.sh monitoring
   ```

5. Check:

   ```
   docker exec munin-llm-failover python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:8000/status')))"
   curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | grep -A4 llm_fallback
   ```

   To test a failover for real: stop the vLLM job (`vllm-service stop`), send
   one chat, and watch `docker logs -f munin-llm-failover`; start it again and
   the route returns after the hold.

Rollback: remove `LLM_BASE_URL` from `cluster.env` and
`sudo ./deploy.sh retrieval`; then
`cd /opt/munin/docker && sudo docker compose --profile llm-claude --profile llm-failover stop llm-failover llm-claude`.

### VPS (frontend + Lite), by the operator

Nothing here was done; the VPS was read-only for this work. Follow
`docs/LITE-DEPLOY.md` for how files reach the server (compare, back up, no
CRLF). Order matters because of the port collision above:

1. **Merge Lite into the cluster first.** Lite has been the only backend since
   2026-10-21, so its `chats.db`, `user_docs` and Qdrant `user_docs` hold three
   weeks of work. Use lane A's `fetch_lite_state.sh` and `merge_state.py`
   (target: the cluster's `/opt/munin/data/chats.db`, dry run first). Announce a
   short pause, because writes to Lite after the snapshot are not in it; a
   second pass with `--since` the first snapshot's time picks them up.
2. **Move Lite off 18080.** In `~/munin-lite/.env` set
   `RETRIEVAL_HOST_PORT=18081`, then from `~/munin-lite`:
   `docker compose up -d --no-deps --no-build retrieval`.
   About 16 s later the tunnel owns 18080 and the (still old) gateway sends
   everyone to the cluster. Confirm on hugin:
   `journalctl -u munin-tunnel -n 5` shows no new "remote port forwarding
   failed", and on the VPS `curl -s 127.0.0.1:18080/health` answers.
3. **Gateway code.** Upload `frontend/gateway/` (now with
   `backend_failover.py`) to `~/munin/frontend/gateway`, update
   `~/munin/frontend/docker-compose.yml` from this branch, and add to
   `~/munin/frontend/.env`:

   ```
   BACKEND_STANDBY_URL=http://127.0.0.1:18081
   BACKEND_FAILBACK=auto
   ```

   Then from `~/munin/frontend`:
   `docker compose build api-gateway && docker compose up -d --no-deps --no-build api-gateway`.
   The log shows `backend failover on: cluster http://127.0.0.1:18080, standby http://127.0.0.1:18081, mode normal`.
4. **Web UI** with the banner, per `docs/LITE-DEPLOY.md` ("Web UI").
5. **Check** as an admin: `GET https://chat.<domain>/api/backend-mode` shows
   `mode: normal`, `detail.cluster.ok: true`, `detail.standby.ok: true`.
   Optionally rehearse: `PUT /api/backend-mode {"mode": "reduced"}`, see the
   banner, then `{"mode": "auto"}`. A rehearsal is a real episode: chats written
   meanwhile land on Lite and need the merge.
6. **Keep Lite current.** The standby runs whatever code it was last deployed
   with; when retrieval's schema changes, redeploy Lite too, or the merge
   needs `--allow-schema-drift`.

After any reduced episode (the gateway logs `NORMAL mode, cluster serving
again. Merge the standby's chats since ...`): run the merge with that `since`.

## Configuration reference

llm-failover (compose service `llm-failover`, profile `llm-failover`):

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PRIMARY_URL` | `$VLLM_URL`, else `http://host.docker.internal:8000` | vLLM |
| `LLM_SECONDARY_URL` | `http://llm-claude:8000` | llm-claude |
| `FAILOVER_SECONDARY_ENABLED` | `1` | `0`: never use Claude |
| `FAILOVER_SECONDARY_DAILY_TOKENS` | `20000000` | per UTC day, `0` unlimited |
| `FAILOVER_SPEND_CAP_COOLDOWN_S` | `3600` | secondary off after a 402 |
| `FAILOVER_PROBE_INTERVAL_S` | `5` | |
| `FAILOVER_FAIL_AFTER` | `2` | failures before moving to Claude |
| `FAILOVER_RECOVER_AFTER` | `6` | good probes before moving back |
| `FAILOVER_MIN_HOLD_S` | `120` | minimum time on Claude |

The primary's model name comes from `LLM_MODEL_NAME` / `VLLM_MODEL_NAME`, the
same value retrieval sends, and `LLM_THINKING_MODE` from the model profile.

Gateway (`frontend/docker-compose.yml`, `api-gateway`):

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_STANDBY_URL` | empty (off) | the Lite backend, not 18080 |
| `BACKEND_FAILBACK` | `auto` | or `manual` |
| `BACKEND_FAIL_AFTER` | `3` | |
| `BACKEND_RECOVER_AFTER` | `12` | |
| `BACKEND_MIN_HOLD_S` | `300` | |
| `BACKEND_PROBE_INTERVAL_S` | `5` | |
| `BACKEND_REDUCED_NOTE` | see above | banner text, `{since}` filled in |
