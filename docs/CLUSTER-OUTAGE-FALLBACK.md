# Cluster outage: running chat without the cluster

Status: findings only, nothing deployed. Written 2026-10-21, when the cluster
went down with a faulty PSU and the paper corpus (Qdrant + Neo4j) was assumed
lost. The question: what can come back on a separate VPS with a hosted
OpenAI-compatible LLM, leaving paper ingest out entirely.

## The one thing to know

The gateway does not talk to an LLM. It is a pure proxy to one upstream,
`CLUSTER_TUNNEL` (`frontend/gateway/main.py:31`), which the compose file fills
from `BACKEND_URL` (`frontend/docker-compose.yml:133`). Chat completions,
history, personas, artifacts, projects, memories and research all live in the
**retrieval** service (`backend/retrieval/main.py`). No retrieval container,
no chat. The fallback is therefore "run retrieval without a corpus", not
"run chat without retrieval".

## Retrieval starts without a corpus

Read from the code, not yet tried on a running instance:

- `get_qdrant()` (`database.py:466`) only builds a client; an empty or
  unreachable Qdrant fails at query time, not at startup.
- `get_neo4j()` (`database.py:479`) returns `None` when `NEO4J_PASSWORD` is
  unset or the connection fails.
- `document_store.ensure_collection()` and the payload-index creation are in
  try/except; `verify_paper_space()` (`database.py:431`) only raises on an
  encoder/collection dimension mismatch, a missing collection just warns.
- `verify_dispatch_registry()` (`main.py:~4313`) raises unguarded, but it
  checks code consistency, not data.
- torch is imported lazily inside the encoder getters (`database.py:289-375`),
  but startup preloads SPECTER, BGE-large and BGE-base (`main.py:4306-4330`),
  each in try/except. With no `/models` mount they download from the HF hub,
  about 2 GB of download and RAM for nothing. There is no flag to skip the
  preload; that needs a small code change. `ROUTER_ENABLED=false` keeps the
  router off its BGE model (`chat_service.py:83-95`).

## What comes back

| Feature | Without the corpus |
|---|---|
| Chat, streaming, routing, personas, artifacts, projects, memories | works |
| Web search (SearXNG / Brave), `web_fetch` | works (`mcp/tools/web.py`) |
| Semantic Scholar lookups and citations | works, external API |
| `run_python` | works with the `sandbox` container |
| Deep Research | should work on web + S2; local `paper_search` contributes nothing. Graceful degradation not confirmed |
| Login, API keys, usage, quotas, announcements | unaffected, on the frontend VPS (`gateway.db`, `sessions.db`) |
| Local paper search, citation graph, tags, knowledge map | gone |
| Per-user document upload | broken: needs GROBID (`document_store.py:36`) |

## State that is lost unless the cluster disk is readable

`CHATS_DB_PATH=/data/chats.db` (`chat_store.py:19`) is one SQLite file
holding every user's chats, artifacts, projects, memories, profile and
research jobs. If the cluster drive can be read in any other machine, copy
this file first; it is small. Without it users log in normally to empty
histories. Uploaded documents (`/data/user_docs` plus the Qdrant `user_docs`
collection) are lost with the cluster too.

## Deployment shape

On a separate VPS (x86 preferred; the current frontend VPS is ARM, 4 cores,
7.5 GiB, which is tight for the 10 GB torch image plus the encoders):

- Run `retrieval`, `sandbox`, `searxng` and an empty `qdrant`.
- Leave out `neo4j` (or run it empty), `grobid`, the `pipeline` profile and `vllm`.
- Hosted LLM: `scripts/configure.sh --llm-url ... --llm-model ... --llm-api-key ...`
  (`LLM_BASE_URL`, `LLM_MODEL_NAME`, `LLM_API_KEY`).
- Strict OpenAI-compatible APIs reject vLLM extras:
  - Set `LLM_THINKING_TOGGLE=false` so `chat_template_kwargs` is not sent.
  - Override `SAMPLING_DEFAULT` / `SAMPLING_CODE` if `top_k` / `min_p` are refused.
  - Set `VLLM_MAX_MODEL_LEN`, `VLLM_MAX_CONTEXT` and `VLLM_MAX_OUTPUT_TOKENS` to the hosted model's limits.
- Frontend: point `BACKEND_URL` at the new VPS. The path must stay private
  (tunnel or VPN, see [install/tunnel.md](install/tunnel.md)), with
  `MUNIN_GATEWAY_TOKEN` set on both sides, since retrieval trusts the
  gateway's identity headers.
- The published benchmark numbers are for the Qwen backbones; another hosted
  model will behave differently inside the harness.

## Open work

- Add an env flag to skip the encoder preload when there is no corpus.
- Confirm Deep Research and the paper tools degrade cleanly on an empty Qdrant.
- Try to recover `chats.db` from the cluster disk.
- When the cluster is back, switch `BACKEND_URL` back to the tunnel.
