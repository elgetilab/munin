# munin / backend (cluster)

Cluster-side of the [Munin](https://muninai.org) AI research
monorepo. Everything that runs on the SLURM cluster: vLLM,
retrieval API, MCP tooling, knowledge bases (Qdrant, Neo4j),
paper pipeline, deep research daemon, agentic orchestration.

For first-time setup on a new cluster, follow the top-level
[SETUP-CLUSTER.md](../SETUP-CLUSTER.md). This README is the
operating reference once the deploy is in place.

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
| **vLLM** | 8000 | LLM inference (GPU 1, SLURM job, scheduled 6am to 2am) |
| **Sandbox** | internal | Jupyter-kernel sidecar for `run_python` tool |

The retrieval API is the only externally reachable piece. It is
exposed to the VPS over an autossh reverse tunnel
(`config/munin-tunnel.service`) on port 18080. Everything else is
bound to `127.0.0.1`.

## Cluster GPU layout

The hugin node has 2x RTX 5090 (32 GB each). Both GPUs are exposed
two ways via SLURM (config lives in `HuginSLURM/config/gres.conf` (HuginSLURM, not public)):

| Resource         | What it grants                                     | Who uses it (munin) |
|------------------|----------------------------------------------------|---------------------|
| `gpu:batch:1`    | Whole GPU 0                                        | deepresearch SLURM job (30B MiroThinker) |
| `gpu:vllm:1`     | Whole GPU 1                                        | vLLM service (35B-A3B, 6am to 2am) |
| `shard:N`        | N/8 of any free GPU (~4 GB VRAM per shard)         | (unused, available for future ephemeral jobs) |
| `shard:batch:N`  | N/8 of GPU 0 specifically                          | (unused) |
| `shard:vllm:N`   | N/8 of GPU 1 specifically (only when vLLM is down) | (unused) |

SLURM refuses to mix whole-GPU and shard allocations on the same
physical GPU. While vLLM holds GPU 1 (typical 6am to 2am window), the
8 `shard:vllm` slots are blocked; shard jobs land on GPU 0 instead.
Partitions: `vllm-serving` (this service), `llm-batch` (deepresearch),
`standard` / `quickdirty` / `gputraining` (users).

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
    └── vLLM (:8000)         LLM inference (GPU 1, SLURM job)

Deep Research Daemon (systemd) → SLURM jobs on GPU 0
```

## Repository Structure

This is the layout of the `backend/` directory. Persona definitions
and the contributor allowlist are cross-cut and live in
`../shared/` (the deploy script propagates them).

```
backend/
├── README.md                 # This file
├── DESIGN.md                 # Original design intent (mostly historical)
├── deploy.sh
├── config/
│   ├── munin.env.template
│   ├── agents.yml            # Agent registry
│   ├── deepresearch-daemon.service
│   ├── munin-tunnel.service
│   ├── munin-paper-pipeline.service
│   └── munin-paper-cleanup.{service,timer}
├── docker/
│   ├── docker-compose.yml    # Qdrant, Neo4j, GROBID, SearXNG, retrieval, sandbox
│   ├── grobid/grobid.yaml    # Crossref polite-pool mailto override
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
│   ├── Dockerfile, requirements.txt
│   ├── mcp/                  # MCP server (schemas, executor with input
│   │                         #   validation, tools/{web,papers,llm,agents}.py)
│   ├── agents/               # Agentic orchestration (registry, executor, parallel)
│   └── tests/
├── sandbox/                  # Jupyter-kernel sandbox sidecar (run_python tool)
├── scripts/
│   ├── vllm/                 # start-vllm-service.sh, schedule-vllm.sh
│   ├── deepresearch/         # daemon + SLURM job
│   ├── knowledge/            # build_embedding_map.py
│   └── pipeline/             # paper_pipeline.py, paper_cleanup.py,
│                             #   paper_crawler.py. INGEST.md is the
│                             #   operator entry point for this dir.
└── docs/                     # Internal design + future-features notes
                              #   (docs/archive/ for frozen historical specs)
```

The canonical API contract lives at
[`../shared/docs/BACKEND-API.md`](../shared/docs/BACKEND-API.md).

## Key Paths on Cluster

After `deploy.sh all`, expect:

- `/opt/munin/config/munin.env`: secrets (symlinked from `/opt/hugin/config/cluster.env`).
- `/opt/munin/docker/`: installed compose file + grobid override.
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
- `/opt/munin/logs/`: service status files.

The `/opt/hugin/...` path is hardcoded in `deploy.sh` from the
reference cluster (named `hugin`). If your cluster uses a different
layout, edit `deploy.sh` to match.

## Deployment

Runs directly on the cluster head. Targets:
`/opt/munin/`, `/opt/cluster/scripts/llm/`,
`/etc/systemd/system/`. Prepend any mode with `--dry-run` to see
what would change.

```bash
sudo ./deploy.sh all            # Full deploy: dirs → compose → personas →
                                #   agents → models → vllm → tunnel →
                                #   sandbox → retrieval (rebuilds both
                                #   containers)
sudo ./deploy.sh sandbox        # Sync sandbox/, rebuild + restart sandbox container
sudo ./deploy.sh retrieval      # Sync retrieval/, rebuild + restart container
sudo ./deploy.sh compose        # docker-compose.yml + grobid.yaml only (no restart)
sudo ./deploy.sh personas       # Persona JSON + logos (mounted; no restart)
sudo ./deploy.sh agents         # config/agents.yml + munin.env.template
sudo ./deploy.sh models         # Stage embedding models (downloads bge-large)
sudo ./deploy.sh vllm           # vLLM SLURM + cron scripts → /opt/cluster/scripts/llm/
sudo ./deploy.sh deepresearch   # LEGACY MiroThinker path (disabled; not in `all`)
sudo ./deploy.sh tunnel         # munin-tunnel.service install + restart
sudo ./deploy.sh dirs           # Create filesystem layout (idempotent)
```

Notes:
- **vLLM**: `deploy vllm` only stages the scripts. Cut over with
  `sudo vllm-service stop && sudo vllm-service start`.
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

## Common Tasks

### Switch LLM model

Canonical checklist. Eleven places name the model; an earlier version of
this list named three, which is how a swap ends up half-applied. Grouped
by what breaks if you miss it.

**Serving**
1. `scripts/vllm/start-vllm-service.sh`: `MODEL_ID`, `MODEL_PATH`,
   `MODEL_NAME`. Check `--dtype` and `--quantization` still suit the new
   checkpoint, and re-check the VRAM headroom: after the first start, read
   `GPU KV cache size` out of `/opt/munin/logs/vllm-service-<job>.out`.
2. `scripts/vllm/start-vllm-service-tp2.sh`: the same three constants. Easy
   to forget because it is the non-default profile.
3. `deploy.sh`: `VLLM_MODEL_DIR` (near the top). **This is the trap.**
   `stage_qwen_tokenizer` copies `tokenizer.json` out of that directory so
   the retrieval container can budget context with the real tokenizer. Miss
   it and the container silently keeps budgeting with the OLD model's
   tokenizer: no error, just wrong trim decisions.

**Backend**
4. `docker/docker-compose.yml`: three occurrences, `VLLM_MODEL_NAME` (275,
   659) and `VLLM_SERVE_MODEL` (477).
5. `retrieval/database.py`: `DEFAULT_LLM_MODEL_NAME`.
6. `retrieval/main.py`: the `VLLM_MODEL_NAME` fallback literal (~line 1090).
   A second fallback, separate from the one above.
7. `config/munin.env.template` and `config/munin-embedding-map.service`.
8. `../.env.example`: the published defaults and the "results used X" note.
9. `../shared/personas/*.json`: `params.temperature` / `top_p` / `top_k` /
   `min_p` / `presence_penalty`, if the new model recommends different
   sampling. The current values are Qwen3's recommended set. These are part
   of the system under test, so changing them mid-benchmark invalidates the
   comparison.

**Benchmarks** (only if you intend to re-measure)
10. `benchmarks/munin_bench/config.py`: `VLLM_MODEL_NAME`.
11. `benchmarks/munin_bench/ablation/vllm_answer.py`: `MODEL`. The bare arm
    calls vLLM directly, bypassing the gateway, so missing this makes the
    headline ablation compare two different models to each other.
12. `docker/docker-compose.shadow.yml`: the Track C2 shadow instance, or its
    paired arms differ by model as well as by corpus.

**Then**
13. `sudo vllm-service stop && sudo vllm-service start`, and
    `sudo ./deploy.sh retrieval`. Confirm the tokenizer actually moved:
    the deploy prints `[OK] tokenizer staged to ...`.
14. Smoke-test tool calling before trusting anything. If the new model
    needs a different `--tool-call-parser`, every tool-using turn breaks
    while plain chat keeps working, which is a confusing way to find out.

A model swap also invalidates the committed benchmark numbers. See
`../docs/paper-track/MODEL-SWAP-QWEN38-PLAN.md` for what has to be
re-measured and what does not.

### Add MCP tool
1. Implement in `retrieval/mcp/tools/*.py`.
2. Register in `retrieval/mcp/schemas.py`.
3. Map in `retrieval/mcp/executor.py`.
4. Redeploy retrieval.

### Add persona
1. Create `../shared/personas/<id>.json`.
2. Optionally add `../shared/personas/logos/<name>-<id>-inverted.svg`.
3. `sudo ./deploy.sh personas` rsyncs into `/opt/munin/personas/`.
   No container restart needed; retrieval reads the directory at
   request time.

### Maintenance mode

Use this when Munin needs to go down deliberately (e.g. freeing the
GPU for other experiments) — as opposed to the nightly 2-6 AM sleep.
Installed by `sudo ./deploy.sh maintenance` (or `deploy.sh all`),
which puts the toggle on `PATH` as `munin-maintenance`.

```
sudo munin-maintenance on "Running NTL9 experiments, back Wednesday"
sudo munin-maintenance status
sudo munin-maintenance off
```

`on` (the message argument is optional):
- writes the flag file `/opt/munin/data/maintenance.json`, which
  `/api/status` reports — the chat UI then shows a maintenance screen
  with your message instead of the "resting" sleeping page, and the
  static page at `chat.muninai.org/maintenance` shows the same;
- disables the vLLM start/stop cron so vLLM does not auto-boot, and
  stops a running vLLM job;
- stops the Deep Research (MiroThinker) daemon so no new jobs launch
  — jobs already on SLURM are left to finish.

`off` removes the flag, restores the normal 6am/2am vLLM schedule
(run `sudo vllm-service start` if you want it up immediately, or
`sudo vllm-service enable-24x7` if you were running 24/7), and
restarts the Deep Research daemon.

The flag file is the single source of truth; both the React chat app
and the static page read it via `/api/status`. No deploy or container
restart is needed to toggle — the flag takes effect on the next
`/api/status` poll (~60s in the UI).

## Related

- [`../frontend/`](../frontend): VPS-side of the monorepo
  (gateway, auth, chat UI; calls this service via the SSH tunnel).
- [`../shared/`](../shared): cross-cut artifacts (personas,
  `contributors.yml`, contract docs in `shared/docs/`).
- HuginSLURM (separate repo): base cluster setup (SLURM, CUDA,
  users). Not a code dependency.
