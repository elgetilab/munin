# munin / backend (cluster)

Cluster-side of the [Munin](https://muninai.org) AI research
monorepo. Everything that runs on the SLURM cluster: vLLM,
retrieval API, MCP tooling, knowledge bases (Qdrant, Neo4j),
paper pipeline, deep research daemon, agentic orchestration.

For first-time setup on a new cluster, follow the top-level
[SETUP-CLUSTER.md](../SETUP-CLUSTER.md). This README is the
operating reference once the deploy is in place.

VPS-side code is in `../frontend/`; cross-cut artifacts in
`../shared/`. See the top-level `CLAUDE.md` for monorepo rules.

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
├── CLAUDE.md                 # Agent context (conventions, gotchas)
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
│   ├── chat_store.py         # Chat persistence CRUD
│   ├── chat_context.py       # Context assembly + compaction
│   ├── document_store.py     # User doc embedding + retrieval
│   ├── Dockerfile, requirements.txt
│   ├── mcp/                  # MCP server (schemas, executor,
│   │                         #   endpoints, tools/{web,papers,llm,agents}.py)
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
- `/opt/munin/deepresearch/`: deep-research job queue + results.
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
                                #   agents → vllm → deepresearch → tunnel →
                                #   sandbox → retrieval (rebuilds both
                                #   containers)
sudo ./deploy.sh sandbox        # Sync sandbox/, rebuild + restart sandbox container
sudo ./deploy.sh retrieval      # Sync retrieval/, rebuild + restart container
sudo ./deploy.sh compose        # docker-compose.yml + grobid.yaml only (no restart)
sudo ./deploy.sh personas       # Persona JSON + logos (mounted; no restart)
sudo ./deploy.sh agents         # config/agents.yml + munin.env.template
sudo ./deploy.sh vllm           # vLLM SLURM + cron scripts → /opt/cluster/scripts/llm/
sudo ./deploy.sh deepresearch   # Deep research daemon + MiroThinker model (guarded)
sudo ./deploy.sh tunnel         # munin-tunnel.service install + restart
sudo ./deploy.sh dirs           # Create filesystem layout (idempotent)
```

Notes:
- **vLLM**: `deploy vllm` only stages the scripts. Cut over with
  `sudo vllm-service stop && sudo vllm-service start`.
- **Env vars**: `/opt/munin/docker/.env` is a symlink to
  `/opt/hugin/config/cluster.env`, which Docker Compose auto-loads.
- **MiroThinker download** is guarded by the target directory, so
  re-running `deploy deepresearch` is a no-op once the model is
  present.

## Common Tasks

### Switch LLM model
1. Edit `MODEL_ID` / `MODEL_PATH` / `MODEL_NAME` in
   `scripts/vllm/start-vllm-service.sh`.
2. Update `VLLM_MODEL_NAME` in `docker/docker-compose.yml`.
3. Update the fallback in `retrieval/database.py`.
4. Update `temp` / `top_p` in `../shared/personas/*.json` if the
   new model warrants different sampling.
5. Deploy + restart vLLM (`sudo vllm-service stop && sudo
   vllm-service start`) and retrieval (`sudo ./deploy.sh
   retrieval`).

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

## Related

- [`../frontend/`](../frontend): VPS-side of the monorepo
  (gateway, auth, chat UI; calls this service via the SSH tunnel).
- [`../shared/`](../shared): cross-cut artifacts (personas,
  `contributors.yml`, contract docs in `shared/docs/`).
- HuginSLURM (separate repo): base cluster setup (SLURM, CUDA,
  users). Not a code dependency.
