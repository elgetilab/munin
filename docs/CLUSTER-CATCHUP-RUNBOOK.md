# Cluster catch-up runbook (2026-11-12)

Brings the cluster (hugin) from the code it runs today up to `main`. Written
2026-11-12, after the PSU outage of 2026-10-21. Nothing in this file has been
run yet. Every step that needs root is written out exactly.

Users are on the Lite backend on the VPS while this runs (see
[CLUSTER-OUTAGE-FALLBACK.md](CLUSTER-OUTAGE-FALLBACK.md)). So none of the
downtime below is seen by users until the VPS `BACKEND_URL` points back at the
tunnel. That switch is a frontend step and is not covered here.

## Where the cluster stands

Audited 2026-11-12 by comparing every deployed path with the repo.

| Area | Deployed | Gap to `main` |
|---|---|---|
| retrieval code (`/opt/munin/services/retrieval`) and running image `munin-retrieval:latest` (`dce7654fbe5b`, built 2026-10-16) | main at `bc1ee62` (2026-10-16), byte for byte, plus two empty root-owned dirs `backend/` and `personas/` | 7 backend commits: `f080ef9`, `fcc00bd` (llm-claude), `2df732b` (Lite: `TORCH_INDEX_URL` build arg, no-corpus mode), `03512e4` (`show_protein` tool and `GET /api/chats/{cid}/artifacts/{aid}/structure`), `a4aa87d` (DR planner token limit), `2e5dfaa` (MiroThinker retirement), `95279af` (upload cancel route). **Rebuild needed.** |
| sandbox code and image `munin-sandbox:latest` (`4938fe326281`, 2026-10-04) | main at `bc1ee62` | `INSTALL_TEX` build arg (default 1 = same packages) and `SANDBOX_KERNEL_NPROC` (default 256 = same limit). No change in behaviour. Rebuilding is optional and keeps deployed equal to `main`. |
| `/opt/munin/docker/docker-compose.yml` | main at `bc1ee62` | With `rag` and `monitoring` rendered, the only differences are in retrieval's environment: `DEEPRESEARCH_ENABLED` is gone and `OPENALEX_API_KEY` is new (optional). The new `llm-claude` service sits behind its own profile and is not started on the cluster. No new mounts, no new ports. |
| grobid.yaml, prometheus.yml, searxng settings, agents.yml, faq.yml, munin.env.template, model profiles, active-model.env, personas | same as `main` | none |
| pipeline, knowledge and run-daily scripts, all systemd units, tunnel unit | same as `main` | none |
| `/opt/cluster/scripts/llm/start-vllm-service{,-tp2}.sh`, `maintenance.sh` | older | comments only (the word "deepresearch") |
| Legacy MiroThinker leftovers | `deepresearch-daemon.service` (disabled, inactive), `deepresearch-daemon.py` and `deepresearch-job.sh` in `/opt/cluster/scripts/llm`, weights in `/opt/munin/data/models/mirothinker-v1.5-30b` (17 GB) | removed from the repo by `2e5dfaa`. Removing them on the host is optional (step 9). |
| cluster.env (`/opt/hugin/config/cluster.env`, 0600 root, not read for this audit) | every key the compose file interpolates resolved in the running container; only `MUNIN_GATEWAY_TOKEN` and `LLM_API_KEY` are empty | see step 2 |

Already deployed, so nothing to do: the hardened sandbox (`4d20370`; the live
`/healthz` reports `isolation.ok: true`, uid 20128, service not reachable, app
not writable), `MUNIN_DOMAIN` (`a1ce168`, live value set), the gateway-token
guard (`c2ff610`; code is live, the token is empty, so the guard is off),
`ROUTER_ENABLED=true`.

Frontend only, so these happen on the VPS: the artifact work (`a702931`
artifacts in messages, `528943f` artifact panel, the webui half of `03512e4`
with the widget frame and protein viewer), the Caddyfile change in `95279af`,
and the switch of `BACKEND_URL` back to the tunnel. The backend half the
cluster needs for them is the retrieval rebuild in step 6: without it a chat
holding an `application/vnd.munin.widget+json` artifact (Lite has persisted 3)
gets a 404 from the `/structure` route, and the model has no `show_protein`
tool.

Chat history: the cluster and Lite `chats.db` snapshots of 2026-11-12 have the
same schema, table for table and column for column. Moving Lite's chats into
the cluster database is a separate task; do it only after step 6.

## Before you start

- Merge `chore/cluster-catchup` into `main` and deploy from the main checkout
  (`<munin checkout>` below). Every command below uses
  `R=<munin checkout>/backend`.
- Never run `docker compose up/down` from the repo. Compose commands in this
  runbook run in `/opt/munin/docker` only, which is the live project.
- The retrieval image build re-downloads torch and the CUDA wheels (about
  4 GB). `main` added a `RUN` step before the pip layer, which invalidates the
  Docker cache. Over hugin's link that ran at 0.2 to 0.4 MB/s on 2026-11-12,
  so a cold build takes about an hour. A test build of the same Dockerfile
  (`munin-retrieval:laneb-catchup-fcc4dd1`) warms the BuildKit cache. Do not
  prune the builder cache before step 6, and the production build then reuses
  those layers.
- Keep vLLM in whatever state the GPU experiments need. None of these steps
  starts or stops it. The `vllm` step only installs scripts.

## Steps

Downtime means downtime of the cluster backend, which no user sees while
traffic goes to Lite.

### 1. Snapshot for rollback (no downtime)

```bash
D=2026-11-12
sudo docker tag munin-retrieval:latest munin-retrieval:pre-catchup-$D
sudo docker tag munin-sandbox:latest   munin-sandbox:pre-catchup-$D
sudo tar -C /opt/munin -czf /opt/munin/pre-catchup-$D.tgz \
    docker/docker-compose.yml services/retrieval services/sandbox
sudo python3 -c "import sqlite3; s=sqlite3.connect('/opt/munin/data/chats.db'); d=sqlite3.connect('/opt/munin/data/chats.db.pre-catchup-$D'); s.backup(d)"
sudo cp -a /opt/hugin/config/cluster.env /opt/hugin/config/cluster.env.bak-$D
```

Verify: `docker image ls | grep pre-catchup` shows both tags with the image IDs
`dce7654fbe5b` and `4938fe326281`.

### 2. cluster.env (no downtime)

Check the required keys (prints counts, not values):

```bash
sudo grep -cE '^(MUNIN_DOMAIN|MUNIN_CLUSTER_NAME|NEO4J_PASSWORD|SEARXNG_SECRET|MUNIN_VPS_HOST)=.+' /opt/hugin/config/cluster.env   # expect 5
sudo grep -nE '^(DEEPRESEARCH_ENABLED|OPENALEX_API_KEY|MUNIN_GATEWAY_TOKEN)=' /opt/hugin/config/cluster.env | cut -d= -f1
```

- `DEEPRESEARCH_ENABLED`: no longer read by anything. Delete the line if it is
  there (`sudo sed -i '/^DEEPRESEARCH_ENABLED=/d' /opt/hugin/config/cluster.env`).
- `OPENALEX_API_KEY` (new, optional): raises the OpenAlex rate limit for the
  paper tools and `paper_cleanup.py`. Leave unset to keep today's behaviour.
- `MUNIN_GATEWAY_TOKEN` (optional, empty today): with it set, retrieval
  refuses identity headers that do not carry it. Set it only together with
  the same value in the VPS `frontend/.env` (frontend step), or every request
  through the gateway fails.

### 3. compose, personas, agents (no downtime)

```bash
sudo $R/deploy.sh --dry-run all      # read the plan first
sudo $R/deploy.sh compose
sudo $R/deploy.sh personas
sudo $R/deploy.sh agents
```

`compose` only installs files. personas and agents are already identical and
are re-run so deployed equals `main`.

Verify: `cmp $R/docker/docker-compose.yml /opt/munin/docker/docker-compose.yml`.

### 4. vllm and maintenance scripts (no downtime)

```bash
sudo $R/deploy.sh vllm
sudo $R/deploy.sh maintenance
```

`vllm` installs scripts and profiles and runs
`systemctl enable --now munin-vllm-health.timer`. That timer is already
enabled, and it restarts vLLM within the hour whenever no job is queued
(it did so at 13:15 on 2026-11-12, after a manual `scancel`). While GPU
experiments need the cards, pause it first with
`sudo touch /opt/munin/logs/vllm-health.hold`, and remove the file afterwards.

Verify: `sudo $R/deploy.sh verify` (later, step 7) reports
`vLLM scripts in sync`.

### 5. sandbox (optional; 10 to 30 s of run_python/compile_latex downtime)

```bash
sudo $R/deploy.sh sandbox
```

Running kernels are lost. The step ends with `up -d retrieval`, which
recreates retrieval on the OLD image because the compose environment changed
(`DEEPRESEARCH_ENABLED` out, `OPENALEX_API_KEY` in). That is harmless and is
superseded by step 6. Run step 6 first to skip it.

Verify: `docker exec munin-sandbox python -c "import httpx;print(httpx.get('http://localhost:8090/healthz').text)"`
shows `"isolation":{"ok":true,...}`.

### 6. retrieval (rebuild plus restart; about 1 to 2 min of downtime once the build is done)

```bash
sudo $R/deploy.sh retrieval
```

It stages the tokenizer, rsyncs the code (which also removes the stray empty
`backend/` and `personas/` dirs), builds the image and recreates the
container, then runs `verify`. The build happens while the old container keeps
serving. Downtime starts at the recreate. Model loading at boot was not
measured for this runbook.

Verify (all read-only):

```bash
sudo $R/deploy.sh verify
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | head -30
docker exec munin-retrieval python -c "import sys; sys.path.insert(0,'/app'); from mcp.schemas import MCP_TOOLS; assert 'show_protein' in MCP_TOOLS; print('show_protein registered')"
docker exec munin-retrieval sh -c 'test -f /app/upload_tracker.py && test -f /app/mcp/tools/structures.py && echo new code in image'
```

`verify` must print `paper space - bge-large (1024d) / papers_bge (1024d)` and
`/api/research/jobs - deep research router mounted`.

### 7. Final verify (read-only)

```bash
sudo $R/deploy.sh verify
for f in $R/retrieval $R/sandbox; do sudo diff -rq --exclude=__pycache__ $f /opt/munin/services/$(basename $f); done   # expect no output
```

### 8. Optional: monitoring, searxng, pipeline, knowledge, tunnel

They are already in sync. Re-running them restarts services (searxng restart,
pipeline daemons, the tunnel), so skip them in this catch-up. `deploy.sh all`
would run them, which is why this runbook goes mode by mode.

### 9. Optional: remove the retired MiroThinker path (no downtime)

From `shared/docs/DECISIONS.md` (2026-11 entry):

```bash
sudo systemctl disable --now deepresearch-daemon
sudo rm /etc/systemd/system/deepresearch-daemon.service && sudo systemctl daemon-reload
sudo rm /opt/cluster/scripts/llm/deepresearch-daemon.py /opt/cluster/scripts/llm/deepresearch-job.sh
sudo rm -r /opt/munin/data/models/mirothinker-v1.5-30b   # 17 GB
```

Other leftovers that nothing reads: `/opt/munin/docker/docker-compose.shadow.yml`
(retired override, replaced by `deploy.sh instance up --corpus shadow`) and
`/opt/munin/docker/grafana/`. Delete them only if wanted. Keep
`/opt/munin/deepresearch`: past reports there are user data.

## Rollback

Retrieval and sandbox (about 1 min):

```bash
D=2026-11-12
sudo docker tag munin-retrieval:pre-catchup-$D munin-retrieval:latest
sudo docker tag munin-sandbox:pre-catchup-$D   munin-sandbox:latest
sudo tar -C /opt/munin -xzf /opt/munin/pre-catchup-$D.tgz   # old compose file and build contexts
cd /opt/munin/docker && sudo docker compose --profile rag up -d --no-build sandbox retrieval
sudo $R/deploy.sh verify
```

cluster.env: `sudo cp -a /opt/hugin/config/cluster.env.bak-$D /opt/hugin/config/cluster.env`,
then recreate retrieval as above.

chats.db: the code since `bc1ee62` makes no schema change, so restoring it
should not be needed. If it is: stop retrieval
(`cd /opt/munin/docker && sudo docker compose --profile rag stop retrieval`),
copy `chats.db.pre-catchup-$D` over `chats.db`, start retrieval. Chats written
after step 1 are lost.

vLLM scripts: comment-only changes, so no rollback needed.

## After the v1.1 branches merge

These four branches were rebased onto `main` on 2026-11-12
(`v1.1-<name>-rebased`) but not merged. Each adds deploy work:

| Branch | Cluster deploy | VPS |
|---|---|---|
| `v1.1-alerts-rebased` | `deploy.sh vllm` (new `check-vllm-health.sh`: one alert per incident, state in `/opt/munin/logs/vllm-health.incident`) | install the cluster probe (`frontend/monitoring/`, see MONITORING.md) |
| `v1.1-ci-suite-rebased` | `deploy.sh retrieval` (ships `tests/run_suite.py`), then the new `deploy.sh nightly-tests` mode (02:30 timer, not in `all`) | none |
| `v1.1-retire-tools-rebased` | `deploy.sh retrieval`, `personas`, `agents` (removes `invoke_agent`, `deep_research`, `remember`, `forget`; persona JSON changes, so research scorecards need a rerun before they are cited). `/opt/munin/config/agents.yml` stays on disk and is no longer read. | webui rebuild (TaskLog, artifact walk) |
| `v1.1-search-replace-rebased` | `deploy.sh retrieval` | none |

Merging them before step 6 means one retrieval rebuild instead of two.
