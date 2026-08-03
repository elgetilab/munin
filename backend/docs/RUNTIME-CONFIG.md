# Retrieval runtime configuration

Every environment variable the retrieval service reads, and every
directory it writes to at runtime. Written 2026-08-03 while auditing
`deploy.sh`; the point is to make visible which knobs are actually
reachable from `/opt/hugin/config/cluster.env` and which are only
reachable by editing the repo.

Three sources of truth interact:

1. `backend/retrieval/*.py` reads the variable and supplies a default.
2. `backend/docker/docker-compose.yml` decides whether the variable is
   passed into the container at all, and with what fallback.
3. `/opt/hugin/config/cluster.env` (root-only, NOT in git) supplies the
   real values, via the `.env` symlink in `/opt/munin/docker/`.

A variable that step 2 does not pass **cannot be configured on the
cluster**, whatever cluster.env says. Changing it means editing the
compose file and redeploying.

## Wired through compose (tunable from cluster.env)

| Variable | Compose default | Consumer |
|---|---|---|
| `QDRANT_HOST` / `QDRANT_PORT` | `qdrant` / `6333` | `database.py` |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | service name / `neo4j` / from env | `database.py` |
| `SEARXNG_URL` | `http://searxng:8080` | `mcp/tools/web.py` |
| `SPECTER_MODEL_PATH` / `BGE_MODEL_PATH` / `BGE_LARGE_MODEL_PATH` | `/models/*` | `database.py` |
| `PAPER_ENCODER` | `bge-large` | `database.py` (paired, see below) |
| `PAPERS_COLLECTION` | `papers_bge` | `database.py` (paired, see below) |
| `QWEN_TOKENIZER_PATH` | `/models/qwen/tokenizer.json` | `chat_context.py` |
| `PAPERS_PDF_DIR` | `/papers` | `mcp/tools/papers.py` |
| `CHATS_DB_PATH` / `USER_DOCS_DIR` | `/data/chats.db` / `/data/user_docs` | `chat_store.py`, `document_store.py` |
| `VLLM_URL` / `VLLM_MODEL_NAME` | `host.docker.internal:8000` / `qwen3.6-35b-a3b` | `vllm_client.py` |
| `VLLM_MAX_MODEL_LEN` / `VLLM_MAX_CONTEXT` | `65536` / `60000` | `chat_context.py`; the vLLM start scripts export these to match the running mode |
| `ROUTER_ENABLED` | `false` (prod sets `true`) | `router.py` |
| `SEMANTIC_SCHOLAR_API_KEY` | empty | `mcp/tools/source.py` |
| `BRAVE_API_KEY` / `BRAVE_SEARCH_QPS` / `BRAVE_MAX_QUERIES` | empty / `1` / `3` | `mcp/tools/web.py` |
| `SANDBOX_URL` | `http://sandbox:8090` | `mcp/tools/run_python.py` |
| `ADMIN_INGEST_TOKEN` | empty | `main.py` ingest route |
| `CONTRIBUTORS_CONFIG` / `_SYNC_URL` / `_SYNC_TOKEN` / `_SYNC_INTERVAL_SECS` | `/data/contributors.yml`, auth URL, empty, `300` | `contributors_sync.py` |
| `AUTH_CHECK_ROLE_URL` / `KB_GATE_TOKEN` / `PROMETHEUS_URL` | auth URL / empty / `http://prometheus:9090` | `metrics_proxy.py` |
| `PAPER_PIPELINE_SCRIPT` / `GROBID_URL` / `PAPERS_PROCESSED_DIR` | `/app/pipeline/...` / `http://grobid:8070` / `/papers-processed` | ingest route |
| `MUNIN_REPORTED_DIR` | `/data/reported` | report route |
| `INGEST_CONCURRENCY` | `4` | ingest route |
| `DEEPRESEARCH_ENABLED` | `0` | legacy Miro submit gate (added 2026-08) |
| `DEEPRESEARCH_QUEUE_DIR` / `_JOBS_DIR` / `SLURM_QUEUE_FILE` | `/deepresearch/*` | legacy Miro read endpoints |
| `DEEP_RESEARCH_DIR` / `DEEP_RESEARCH_MAX_CONCURRENT` | `/data/deep_research` / `1` | in-process DR (added 2026-08) |
| `MUNIN_PUBLIC_URL` | `https://search.muninai.org` | report + paper links (added 2026-08) |

### The one paired setting

`PAPER_ENCODER` and `PAPERS_COLLECTION` are not independent. bge-large
emits 1024d and belongs with `papers_bge`; specter emits 768d and
belongs with `papers`. `database.verify_paper_space()` compares the
loaded encoder's real width against the collection's actual vector size
at startup and raises on mismatch, so a half-flip fails the boot rather
than returning nothing. `deploy.sh verify` re-checks the same thing over
`/api/status` and fails the deploy if the live pairing is not
bge-large/papers_bge (override with `EXPECTED_PAPER_ENCODER` +
`EXPECTED_PAPERS_COLLECTION` for a deliberate rollback).

## Read by the code, NOT passed by compose

These fall back to their code defaults on the cluster. Nothing is broken
(the defaults are what production wants today), but changing any of them
currently requires a repo edit plus redeploy, not a cluster.env line.
Listed so we can decide, per knob, whether it deserves compose wiring.

| Variable | Code default | Consumer | Note |
|---|---|---|---|
| `SOURCE_TRIAGE_ENABLED` | `1` | `mcp/tools/source.py:489` | kill switch for source-agent triage, no way to flip live |
| `UNPAYWALL_EMAIL` | `munin@muninai.org` | `mcp/tools/source.py:55` | polite-pool identity for Unpaywall |
| `PAPERS_CACHE_DIR` | `/data/papers_cached` | `mcp/tools/read_paper.py:61` | see growth note below |
| `PAPERS_CACHE_WARN_GB` | `5` | `mcp/tools/read_paper.py:63` | warn threshold only, no eviction |
| `AGENT_TRACE_DIR` | `/data/agent_traces` | `agent_trace.py:34` | |
| `AGENT_EXTRACT_DIR` | `/data/agent_extracts` | `mcp/tools/source.py:144` | |
| `CHAT_MAX_TOOL_CALLS` | `30` | `chat_service.py:133` | per-message tool-call ceiling |
| `DEFAULT_PERSONA` / `AUTO_PERSONA` | `chat` / `munin` | `personas.py` | |
| `PERSONAS_DIR` / `AGENTS_CONFIG` / `FAQ_PATH` | `/app/personas`, `/app/config/agents.yml`, `/app/config/faq.yml` | `personas.py`, faq tool | match the read-only mounts |
| `LOG_LEVEL` | `INFO` | `logging_config.py:117` | raising it live needs a compose edit |
| `PIPELINE_TIMEOUT_SECS` | `600` | ingest route | |
| `INGEST_ACQUIRE_TIMEOUT_SECS` | `5` | ingest route | |
| `SANDBOX_HTTP_TIMEOUT_S` | `180` | sandbox client | |
| `CONTRIBUTORS_SYNC_BACKOFF_SECS` | `60` | `contributors_sync.py` | |
| `VLLM_MAX_OUTPUT_TOKENS` | `16384` | `chat_context.py:37` | |
| `VLLM_GENERATION_RESERVE` | = max output tokens | `chat_context.py:43` | |
| `VLLM_CTX_MARGIN` | `512` | `chat_context.py:55` | |
| `VLLM_MIN_OUTPUT_TOKENS` | `256` | `chat_context.py:57` | |
| `VLLM_MAX_REFIT_RETRIES` | `3` | `chat_context.py:60` | |

## Runtime directories

All under `/opt/munin/data` on the host, mounted at `/data` in the
container. `deploy.sh dirs` creates them; the code also creates them on
first use, so an existing cluster is unaffected either way.

| Path (host) | Written by | Growth | Cleanup |
|---|---|---|---|
| `data/chats.db` | `chat_store.py` | steady, small | none (SQLite, WAL) |
| `data/user_docs/` | `document_store.py` | per user upload | manual |
| `data/reported/rpt_*.json` | report route | rare | manual triage |
| `data/papers_cached/` | `read_paper` | **2.0 GB as of 2026-08-03**, unbounded | none; only a warning above `PAPERS_CACHE_WARN_GB` |
| `data/agent_traces/` | `agent_trace.py` | ~4 MB, one file per traced turn | none |
| `data/agent_extracts/ex_*.json` | source agent | ~120 KB | none |
| `data/deep_research/dr_*.json` | in-process DR | ~480 KB, one per job | none |
| `data/papers/pdf/` | pipeline + ingest | large, corpus-sized | quarantine layout, see INGEST.md |
| `deepresearch/queue`, `deepresearch/jobs` | LEGACY Miro daemon | frozen (feature disabled) | keep for old reports |

The unbounded one that matters is `papers_cached`: it is a full-text
cache keyed by DOI, it only warns past 5 GB, and nothing evicts. Worth a
retention policy before it becomes a disk incident.

## Deep Research: two different things

- **Legacy (`/deepresearch/*`)**: MiroThinker-30B via `deepresearch-daemon`
  and a SLURM job. Disabled 2026-07 (see `shared/docs/DECISIONS.md`).
  Submission 503s unless `DEEPRESEARCH_ENABLED=1` AND the daemon is
  enabled. `deploy.sh deepresearch` provisions it and is deliberately
  NOT part of `deploy.sh all`.
- **Current (`/api/research/*`)**: runs in-process inside retrieval
  (`research_routes.py`, `deep_research_manager.py`,
  `deep_research_agent.py`), this is what users get in chat. It needs no
  daemon, no SLURM job, and no deploy step beyond the dirs above.
