# munin / backend

The backend half of the [Munin](https://muninai.org) monorepo:
retrieval API, MCP tooling, knowledge bases (Qdrant, Neo4j), paper
pipeline, in-process Deep Research, agentic orchestration, plus the
SLURM scripts the reference cluster serves vLLM with. It runs on any
Docker host (a GPU server, a workstation, a cluster head node).

To install Munin, start at the top-level [INSTALL.md](../INSTALL.md)
(`scripts/configure.sh --mode backend` plus `docker compose`). The
reference deployment (muninai.org on a SLURM cluster named `hugin`)
is walked through in
[`docs/install/reference-deployment.md`](../docs/install/reference-deployment.md).
This README is the internals and operations reference for that
deployment and for developers.

VPS-side code is in `../frontend/`; cross-cut artifacts in
`../shared/`. See the top-level `README.md` for monorepo rules.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| **Retrieval API** | 8080 | FastAPI: RAG, chat, agents, MCP, deep research |
| **Qdrant** | 6333 | Vector DB (papers + user_docs collections) |
| **Neo4j** | 7474/7687 | Citation graph |
| **GROBID** | 8070 | PDF parsing (Crossref polite-pool configured) |
| **SearXNG** | 8888 | Web search |
| **vLLM** | 8000 | LLM inference (SLURM job on the reference cluster, scheduled 6am to 2am) |
| **Sandbox** | internal | Jupyter-kernel sidecar for `run_python` tool |
| **Prometheus** | 9090 | Metrics, read by the admin Metrics tab (`monitoring` profile) |

The retrieval API is the only piece the frontend talks to. On the
reference deployment it is exposed to the VPS over an autossh reverse
tunnel (`config/munin-tunnel.service`) on port 18080; hosts that
cannot install a systemd unit can use the compose `tunnel` profile
(an autossh container doing the same thing). The tunnel recipe is in
[`docs/install/tunnel.md`](../docs/install/tunnel.md). Every port is
bound to `127.0.0.1` (retrieval's is configurable with
`RETRIEVAL_BIND_ADDR`). If retrieval is reachable by anything other
than the gateway, set the same `MUNIN_GATEWAY_TOKEN` on both sides:
requests carrying identity headers without it are refused
(`retrieval/gateway_token_guard.py`).

On a single host without SLURM, vLLM is either an external
OpenAI-compatible endpoint (`LLM_BASE_URL`) or the compose `vllm`
service (`gpu` profile).

## Cluster GPU layout

This section describes the reference cluster only. The hugin node
has 2x RTX 5090 (32 GB each). Both GPUs are exposed two ways via SLURM
(configured in the cluster's own SLURM setup, `gres.conf`; separate and not
public):

| Resource         | What it grants                                     | Who uses it (munin) |
|------------------|----------------------------------------------------|---------------------|
| `gpu:vllm:1`     | Whole GPU 1                                        | vLLM, `single` profile (`start-vllm-service.sh`) |
| `gpu:vllm:1,gpu:batch:1` | Both GPUs                                  | vLLM, `tp2` profile (`start-vllm-service-tp2.sh`, production) |
| `gpu:batch:1`    | Whole GPU 0                                        | free for batch jobs while vLLM runs `single` |
| `shard:N`        | N/8 of any free GPU (~4 GB VRAM per shard)         | (unused, available for future ephemeral jobs) |
| `shard:batch:N`  | N/8 of GPU 0 specifically                          | (unused) |
| `shard:vllm:N`   | N/8 of GPU 1 specifically (only when vLLM is down) | (unused) |

Production runs `qwen3.8-27b` on the `tp2` profile (tensor parallel
across both GPUs), so between 6am and 2am no GPU is free. SLURM
refuses to mix whole-GPU and shard allocations on the same physical
GPU, so shard jobs only fit when vLLM is down or on `single` (then on
GPU 0). The profile is chosen with `vllm-service start single|tp2`
and persists, so the 6am cron restores it. Partitions: `vllm-serving`
(vLLM), `standard` / `quickdirty` / `gputraining` (users).

## Architecture

```
VPS (frontend/)
    │ SSH tunnel (port 18080, autossh)
    ▼
Retrieval Service (FastAPI, :8080) ← CENTRAL API
    ├── Qdrant (:6333)       papers + user_docs collections
    ├── Neo4j (:7474/7687)   citation graph
    ├── SearXNG (:8888)      web search
    ├── GROBID (:8070)       PDF parsing
    ├── SQLite               chat persistence
    ├── Sandbox              run_python kernels (internal network only)
    └── vLLM (:8000)         LLM inference (SLURM job)

Deep Research runs inside the retrieval service (/api/research/*).
Paper pipeline, detect sweep, reattribution and the embedding map
run as systemd units on the host (compose `pipeline` profile on a
single host).
```

## Repository Structure

This is the layout of the `backend/` directory. Persona definitions
and the contributor allowlist are cross-cut and live in
`../shared/` (the deploy script propagates them). The allowlist
itself, `shared/config/contributors.yml`, is gitignored: copy it from
`contributors.yml.example` (`scripts/configure.sh` does this).
Every Python image and venv installs against the `constraints.txt`
next to its `requirements.txt`, frozen from the reference deployment.

```
backend/
├── README.md                 # This file
├── DESIGN.md                 # Original design intent (mostly historical)
├── deploy.sh
├── config/
│   ├── munin.env.template    # cluster.env keys, with what each one means
│   ├── agents.yml            # Agent registry
│   ├── faq.yml               # Curated answers for the faq tool
│   ├── models/               # One <slug>.env per backbone (see models/README.md)
│   ├── sudoers.d/            # Operator sudoers template (deploy.sh sudoers)
│   ├── munin-tunnel.service
│   ├── munin-paper-pipeline.service        # upload watcher
│   ├── munin-paper-detect.service          # continuous detect sweep
│   ├── munin-paper-reattribute.{service,timer}  # 04:30 attribution backfill
│   ├── munin-embedding-map.{service,timer} # nightly knowledge map
│   ├── munin-vllm-health.{service,timer}   # hourly vLLM health check
│   └── deepresearch-daemon.service         # LEGACY (deploy.sh deepresearch only)
├── docker/
│   ├── docker-compose.yml    # Qdrant, Neo4j, GROBID, SearXNG, retrieval, sandbox,
│   │                         #   plus profiles gpu, pipeline, seed, monitoring, tunnel
│   ├── grobid/grobid.yaml    # Crossref polite-pool mailto override
│   ├── prometheus/prometheus.yml
│   └── searxng/settings.yml
├── retrieval/                # THE MAIN API SERVICE
│   ├── main.py               # FastAPI, all routes
│   ├── database.py           # DB connections (Qdrant, Neo4j, SQLite)
│   ├── models.py             # Pydantic models
│   ├── chat_service.py       # /api/chat/completions orchestration
│   │                         #   (streaming loop, tool dispatch, save-always)
│   ├── chat_store.py         # Chat persistence CRUD
│   ├── chat_context.py       # Context assembly + compaction
│   ├── document_store.py     # User doc embedding + retrieval
│   ├── vllm_client.py        # Transport-retry wrapper for vLLM calls
│   │                         #   (vllm_post_json / vllm_post_stream)
│   ├── usage_tracker.py      # Per-purpose token aggregation (ContextVar)
│   ├── deep_research_manager.py  # In-process Deep Research (/api/research/*)
│   ├── site_config.py        # Public URLs + instance name, derived from MUNIN_DOMAIN
│   ├── gateway_token_guard.py    # MUNIN_GATEWAY_TOKEN check on identity headers
│   ├── Dockerfile, requirements.txt, constraints.txt
│   ├── mcp/                  # MCP server (schemas, executor with input
│   │                         #   validation, dispatchers, tools/*.py)
│   ├── agents/               # Agentic orchestration (registry, executor, parallel)
│   └── tests/
├── sandbox/                  # Jupyter-kernel sandbox sidecar (run_python tool)
├── scripts/
│   ├── vllm/                 # start-vllm-service{,-tp2}.sh, schedule-vllm.sh,
│   │                         #   model-env.sh, check-vllm-health.sh
│   ├── maintenance/          # maintenance.sh (munin-maintenance)
│   ├── deepresearch/         # LEGACY MiroThinker daemon + SLURM job
│   ├── knowledge/            # build_embedding_map.py
│   ├── seed/                 # seed_corpus.py (compose `seed` profile)
│   └── pipeline/             # paper_pipeline.py, paper_cleanup.py,
│                             #   paper_crawler.py. INGEST.md is the
│                             #   operator entry point for this dir.
└── docs/                     # Internal design + future-features notes
                              #   (docs/archive/ for frozen historical specs)
```

The canonical API contract lives at
[`../shared/docs/BACKEND-API.md`](../shared/docs/BACKEND-API.md).

## Key Paths on Cluster

Before `deploy.sh all` you create `/opt/hugin/config/cluster.env`
(secrets and settings, from `config/munin.env.template`). Afterwards,
expect:

- `/opt/munin/docker/.env`: a symlink to that file.
- `/opt/munin/config/`: `agents.yml`, `faq.yml`, `models/` (profile
  copies) and `active-model.env` (the active backbone).
- `/opt/munin/docker/`: installed compose file + grobid and
  Prometheus config.
- `/opt/munin/services/retrieval/`: deployed retrieval source.
- `/opt/munin/knowledge/qdrant_storage/`: Qdrant data.
- `/opt/munin/knowledge/neo4j_data/`: Neo4j data.
- `/opt/munin/data/chats.db`: chat SQLite database.
- `/opt/munin/data/models/`: LLM + embedding models on disk.
- `/opt/munin/data/papers/pdf/`: paper PDFs.
- `/opt/munin/data/user_docs/`: per-user uploaded documents.
- `/opt/munin/data/papers_cached/`: `read_paper` full-text cache (grows
  without bound; see `docs/RUNTIME-CONFIG.md`).
- `/opt/munin/data/deep_research/`: in-process Deep Research job
  checkpoints (the feature users see in chat).
- `/opt/munin/data/agent_traces/`, `.../agent_extracts/`: agent trace and
  source-extraction records.
- `/opt/munin/deepresearch/`: LEGACY MiroThinker job queue + results,
  kept so old reports stay downloadable.
- `/opt/munin/logs/`: service status files and vLLM job logs
  (`vllm-service-<jobid>.{out,err}`).

The env file path comes from the reference cluster (named `hugin`).
For another layout, override it per run:
`sudo HUGIN_ENV=/etc/munin/cluster.env ./deploy.sh all`.

## Deployment

`deploy.sh` is the reference deployment's installer (systemd units,
SLURM scripts, `/opt` layout). A plain Docker install does not use
it; see [INSTALL.md](../INSTALL.md).

Runs directly on the cluster head. Targets:
`/opt/munin/`, `/opt/cluster/scripts/`,
`/etc/systemd/system/`. Prepend any mode with `--dry-run` to see
what would change (`sudo ./deploy.sh --dry-run all`).

```bash
sudo ./deploy.sh all            # Full deploy: dirs → compose → personas →
                                #   agents → models → vllm → maintenance →
                                #   tunnel → knowledge → pipeline → searxng →
                                #   sandbox → retrieval (ends in verify)
sudo ./deploy.sh dirs           # Create filesystem layout (idempotent)
sudo ./deploy.sh compose        # docker-compose.yml + grobid.yaml only (no restart)
sudo ./deploy.sh personas       # Persona JSON + logos (loaded at start: follow with `retrieval`)
sudo ./deploy.sh agents         # agents.yml, faq.yml, munin.env.template; seeds contributors.yml
sudo ./deploy.sh models         # Stage embedding models (downloads bge-large)
sudo ./deploy.sh vllm           # vLLM SLURM scripts, model profiles, health timer
sudo ./deploy.sh model ...      # Backbone status / validate / activate (below)
sudo ./deploy.sh maintenance    # Install the munin-maintenance toggle
sudo ./deploy.sh tunnel         # Render + install munin-tunnel.service, restart it
sudo ./deploy.sh knowledge      # Embedding-map script, venv, nightly timer
sudo ./deploy.sh pipeline       # Pipeline scripts, venv, paper-* units
sudo ./deploy.sh searxng        # settings.yml, restart searxng
sudo ./deploy.sh sandbox        # Sync sandbox/, rebuild + restart sandbox container
sudo ./deploy.sh retrieval      # Sync retrieval/, rebuild + restart container, then verify
sudo ./deploy.sh verify         # Smoke-test retrieval endpoints + paper encoder pair
sudo ./deploy.sh monitoring     # Prometheus config + container (not in `all`)
sudo ./deploy.sh instance ...   # Side-by-side backbone instances (up/down/gates/ls)
sudo MUNIN_OPERATOR=<login> ./deploy.sh sudoers  # Operator sudoers drop-in (not in `all`)
sudo ./deploy.sh deepresearch   # LEGACY MiroThinker path (disabled; not in `all`)
```

Notes:
- **Required env**: `all`, `compose`, `retrieval` and the other
  container modes refuse to run unless `cluster.env` sets
  `MUNIN_DOMAIN`, `MUNIN_CLUSTER_NAME`, `NEO4J_PASSWORD` and
  `SEARXNG_SECRET`. `tunnel` (and therefore `all`) also needs
  `MUNIN_VPS_HOST`.
- **Not started by `all`**: GROBID is not a dependency of retrieval,
  so a fresh cluster needs `docker compose up -d grobid` once in
  `/opt/munin/docker`; Prometheus comes up with `deploy.sh monitoring`.
- **Model**: the SLURM scripts refuse to start until a backbone is
  active. On a fresh cluster run
  `sudo ./deploy.sh model activate qwen3.8-27b` (see below).
- **vLLM**: `deploy vllm` only stages the scripts. Cut over with
  `sudo vllm-service stop && sudo vllm-service start`. For round the
  clock serving use `sudo vllm-service enable-24x7` (undo with
  `disable-24x7`).
- **Env vars**: `/opt/munin/docker/.env` is a symlink to
  `/opt/hugin/config/cluster.env`, which Docker Compose auto-loads.
  Which variables actually reach the container, and which only have
  code defaults, is catalogued in [`docs/RUNTIME-CONFIG.md`](docs/RUNTIME-CONFIG.md).
- **Paper encoder**: `deploy retrieval` ends in `verify`, which fails
  the deploy if the live encoder/collection pair is not
  bge-large/papers_bge. Deliberate rollback:
  `EXPECTED_PAPER_ENCODER=specter EXPECTED_PAPERS_COLLECTION=papers sudo ./deploy.sh verify`.
- **Deep Research**: the in-chat feature (`/api/research/*`) runs inside
  retrieval and needs no deploy step of its own. `deploy deepresearch`
  provisions the retired MiroThinker daemon only, is excluded from
  `all`, and downloads the 17 GB weights solely with `--with-model`.
- **Paper detect**: `munin-paper-detect.service` runs
  `paper_cleanup.py sweep --no-quarantine` (detection only) until the
  DOI filename repair (`scripts/pipeline/repair_doi_filenames.py`) has
  been applied; drop the flag after that.

## Common Tasks

### Switch LLM model

The backbone is named in one file. Everything else derives from it.

```
sudo ./deploy.sh model status                    # what is active vs what runs
sudo ./deploy.sh model validate <slug>           # check a profile, change nothing
sudo ./deploy.sh model activate <slug> [--download] [--restart-vllm]
```

`activate` copies `config/models/<slug>.env` to
`/opt/munin/config/active-model.env`, stages that checkpoint's tokenizer for
the retrieval container (the silent trap of the old twelve-place checklist),
recreates the retrieval container with the profile's model name, thinking
mode, effort and sampling, and asserts `/api/models` reports the new name.
vLLM restarts on the persisted profile (`single` or `tp2`) with
`--restart-vllm`, or at the next `vllm-service stop && vllm-service start`;
both SLURM scripts source the active profile at job start, so the 6 AM cron
comes up on it too. After the restart read `GPU KV cache size` in the job
`.out`: the pool must cover `VLLM_MAX_NUM_SEQS_* x VLLM_MAX_MODEL_LEN`.

Shipped profiles: `qwen3.8-27b` (production), `gpt-oss-20b`,
`qwen3.6-35b-a3b` (record). To add a model, copy the closest profile and
change every line; `config/models/README.md` documents each key. The
values that differ per model, and used to be scattered, are the checkpoint
and served name, `--quantization`, both parsers, `LLM_THINKING_MODE` (how
sub-tasks turn reasoning off: Qwen `enable_thinking`, gpt-oss `effort_low`,
or `none`), `LLM_REASONING_EFFORT`, and the sampling profile
(`SAMPLING_DEFAULT` / `SAMPLING_CODE`, JSON), which since 2026-09-15 lives in
the profile rather than in `shared/personas/*.json` because it is a vendor
recommendation, not a persona trait, and one persona directory has to serve
whatever backbone is behind it.

Still by hand, because they are not the production backbone: the VPS gateway
proxies `/v1/models` to the backend's `/api/models`, so it needs no edit; the
benchmarks read `VLLM_MODEL_NAME` / `LLM_REASONING_EFFORT` /
`LLM_THINKING_MODE` from the environment, so export the profile before a
run (`source scripts/vllm/model-env.sh && munin_load_model_env`). Smoke-test
tool calling before trusting anything: a wrong `--tool-call-parser` breaks
every tool-using turn while plain chat keeps working.

A model swap invalidates the committed benchmark numbers. See
`../docs/paper-track/done/MODEL-SWAP-QWEN38-PLAN.md` for what has to be
re-measured and what does not, and
`../docs/paper-track/done/BACKBONE-SWITCH-AND-EVAL-PLAN.md` for running a second
backbone beside production instead of swapping.

### Add MCP tool
1. Implement in `retrieval/mcp/tools/*.py`.
2. Declare its schema in `retrieval/mcp/schemas.py`.
3. Add a dispatcher with `@register_tool("<name>")` in
   `retrieval/mcp/dispatchers.py`. Startup fails if a schema and a
   dispatcher do not pair up.
4. Redeploy retrieval.

### Personas (router profiles)
`/api/personas` returns a single user-facing identity, "Munin". The
three files in `../shared/personas/` (`chat`, `code`, `research`) are
internal profiles the router picks per turn; users can force one for
a turn with `/chat`, `/code` or `/research`. A new JSON file there
does not appear in the UI. To change a profile's prompt or tools,
edit its JSON and run `sudo ./deploy.sh personas && sudo ./deploy.sh
retrieval`. Personas are loaded once, when retrieval starts, so the
restart is what makes the change live (and the persona files and the code
must change together: the files carry placeholders the code fills in).

### Maintenance mode

Use this when Munin needs to go down deliberately (e.g. freeing the
GPU for other experiments), as opposed to the nightly 2-6 AM sleep.
Installed by `sudo ./deploy.sh maintenance` (or `deploy.sh all`),
which puts the toggle on `PATH` as `munin-maintenance`.

```
sudo munin-maintenance on "Running NTL9 experiments, back Wednesday"
sudo munin-maintenance status
sudo munin-maintenance off
```

`on` (the message argument is optional):
- writes the flag file `/opt/munin/data/maintenance.json`, which
  `/api/status` reports: the chat UI then shows a maintenance screen
  with your message instead of the "resting" sleeping page, and the
  static page at `chat.<domain>/maintenance` shows the same;
- disables the vLLM start/stop cron so vLLM does not auto-boot, and
  stops a running vLLM job;
- places the vLLM health check's hold file, so
  `munin-vllm-health.timer` does not restart vLLM;
- makes `/api/research/start` refuse new Deep Research jobs. Deep
  Research runs in-process against vLLM, so a job already running
  fails at its next model call.

`off` removes the flag and its own hold file, and restores the normal
6am/2am vLLM schedule (run `sudo vllm-service start` if you want it
up immediately, or `sudo vllm-service enable-24x7` if you were
running 24/7).

The flag file is the single source of truth; both the React chat app
and the static page read it via `/api/status`. No deploy or container
restart is needed to toggle: the flag takes effect on the next
`/api/status` poll (~60s in the UI).

## Related

- [`../frontend/`](../frontend): VPS-side of the monorepo
  (gateway, auth, chat UI; calls this service via the SSH tunnel).
- [`../shared/`](../shared): cross-cut artifacts (personas,
  `contributors.yml`, contract docs in `shared/docs/`).
- HuginSLURM (separate repo): base cluster setup (SLURM, CUDA,
  users). Not a code dependency.
