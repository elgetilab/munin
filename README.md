# Munin

Munin is an open-source AI research platform built for scientific groups. It pairs a SLURM cluster (LLM inference, retrieval, agentic orchestration, paper pipeline) with a small VPS (auth, API gateway, web UI). Two deploy targets, one source tree.

This repository is meant to be self-hostable. If you run a scientific group with a SLURM cluster and want a private chat, RAG, and deep-research stack on top of your own data, this is for you.

## What you get

- Chat UI with persona switching, SSE streaming, task execution log, PWA install.
- Retrieval over your own paper collection (Qdrant vector DB + Neo4j citation graph) and per-user uploaded documents.
- vLLM-served LLMs of your choice, scheduled on cluster GPUs.
- Deep-research daemon (long-running multi-step research as SLURM jobs).
- MCP tool server: web search, paper search, `run_python` sandbox, agentic orchestration.
- Email-OTP authentication, API keys with rate limits and usage logging, resumable uploads.

## Layout

```
backend/    Cluster-side. vLLM, retrieval API, MCP, paper pipeline, deep research.
            Deploys on the SLURM head via backend/deploy.sh.
frontend/   VPS-side. Caddy, email-OTP auth, API gateway, tusd, hook service,
            static UIs, React chat (webui/). Deploys via frontend/docker-compose.yml.
shared/     Cross-cut artifacts both sides consume: persona JSONs
            (shared/personas/), contributor allowlist
            (shared/config/contributors.yml), contracts (shared/docs/).
docs/       paper-track/ (eval suite, benchmarks), agent-track/ (agent
            architecture, Deep Research), architecture/, handoffs/, archive/.
```

## Paper artifacts

[`PAPER.md`](PAPER.md) is the index for the publication: every headline
claim, the measurement behind it, the scorecard filename, what is explicitly
**not** claimed, and the reproduce command per track. The numbers themselves
live in [`backend/benchmarks/RESULTS.md`](backend/benchmarks/RESULTS.md),
with committed per-run scorecards under `backend/benchmarks/scorecards/`.

Headline: the agentic harness scores **0.839** on LitQA2 against **0.302**
bare and **0.171** naive RAG (n=199 paired, harness value +0.538 [0.457,
0.618], p<0.001).

## Architecture

```
Internet
   ▼
VPS (frontend/)
   Caddy → munin-auth, api-gateway, tusd, hook-service, static UIs
                                     │
                                     │ autossh tunnel (VPS :18080 → cluster :8080)
                                     ▼
Cluster (backend/)
   retrieval API (:8080), vLLM, Qdrant, Neo4j, GROBID, SearXNG,
   deep research daemon, paper pipeline
```

The two sides share three contracts: the HTTP API surface
([`shared/docs/BACKEND-API.md`](shared/docs/BACKEND-API.md), canonical),
persona definitions ([`shared/personas/`](shared/personas)), and the
contributor allowlist ([`shared/config/contributors.yml`](shared/config/contributors.yml)).

## Reproducing this on your own infrastructure

Setup is split across four documents. Read them in order; each one works as a checklist you can tick through.

1. [SETUP-PREREQUISITES.md](SETUP-PREREQUISITES.md): hardware, accounts, software, models, and secrets to gather before you start.
2. [SETUP-CLUSTER.md](SETUP-CLUSTER.md): provisioning the SLURM cluster side (vLLM, retrieval API, knowledge bases, paper pipeline, tunnel).
3. [SETUP-VPS.md](SETUP-VPS.md): provisioning the VPS side (Caddy, auth, gateway, uploads, web UI, DNS).
4. [SETUP-VERIFY.md](SETUP-VERIFY.md): end-to-end smoke tests.

For per-side internals after you are running, see [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md).

## Status

Munin is in production at [muninai.org](https://muninai.org), used by a single scientific group. The codebase is being prepared for public release; expect rough edges in setup ergonomics. Issues and PRs are welcome, especially for missing prerequisites, undocumented assumptions, or steps that break on a cluster other than the reference one.

## Don't

- Don't duplicate files across `backend/` and `frontend/`. If you find yourself wanting to, the file probably belongs in `shared/`.
- Don't add a top-level deploy script. The two runtimes have different lifecycles (cluster needs sudo + systemd; VPS is docker compose). Keeping them separate is intentional.