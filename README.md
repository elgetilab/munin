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

Headline: on the production backbone (Qwen3.8-27B) the agentic harness
scores **0.874** on LitQA2 against **0.387** bare and **0.211** naive RAG
(n=199 paired, harness value +0.487 [0.407, 0.568], p<0.001). The same
three arms on Qwen3.6-35B-A3B under the same protocol give 0.869 / 0.337 /
0.126 and +0.533 [0.452, 0.613]: the two backbones reach the same accuracy
inside the harness (question-paired −0.005, p=0.93) and differ only outside
it. On gpt-oss-20b, from a different lab, 0.563 / 0.407 / 0.101 and +0.156.

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

## Running it

There are two paths, and they are genuinely different. Pick one.

### A. Try it on one machine

Everything in Docker on a single host. For evaluating Munin, developing on it,
or demonstrating it. **Not** how the production deployment runs.

```bash
git clone <this repo> && cd munin
cp .env.example .env
$EDITOR .env          # at minimum: LLM_BASE_URL, AUTH_SECRET_KEY, ADMIN_EMAILS
docker compose up -d
```

Then open <http://localhost>. Your login code is printed to the auth log
(`docker compose logs munin-auth | grep "login code"`), because
`AUTH_DEV_ECHO_OTP=1` is set in the example config so you do not need a mail
server to get in.

**You must supply a language model.** Munin speaks OpenAI-compatible HTTP and
does not host one. Point `LLM_BASE_URL` at Ollama, llama.cpp, a vLLM you run,
or a hosted API. Nothing answers without it.

**Be ready for the download.** Roughly **17 GB of images** with every profile
enabled, dominated by the retrieval service (8.7 GB, mostly PyTorch) and the
`run_python` sandbox (2.8 GB, mostly TeX Live). Trim it by removing profiles
from `COMPOSE_PROFILES` in `.env`. The paper encoder (~1.3 GB) is fetched from
HuggingFace on first start, on top of that.

The corpus starts empty. The `seed` profile downloads ~20 open-access arXiv
papers so search returns something; point `SEED_QUERY` at your own field, or
drop your own PDFs into `.runtime/data/papers/pdf/` and the pipeline watcher
will ingest them.

Details, every knob, and the things that will trip you up are in
[`.env.example`](.env.example) and [`docs/SINGLE-HOST-PLAN.md`](docs/SINGLE-HOST-PLAN.md).

### B. Deploy it for a group

The production topology: a SLURM cluster for inference, retrieval and the paper
pipeline, plus a small VPS for auth, the gateway and the web UI. This is what
the reference deployment runs and what the paper measures.

Four documents, in order; each works as a checklist.

1. [SETUP-PREREQUISITES.md](SETUP-PREREQUISITES.md): hardware, accounts, software, models, and secrets to gather before you start.
2. [SETUP-CLUSTER.md](SETUP-CLUSTER.md): provisioning the SLURM cluster side (vLLM, retrieval API, knowledge bases, paper pipeline, tunnel).
3. [SETUP-VPS.md](SETUP-VPS.md): provisioning the VPS side (Caddy, auth, gateway, uploads, web UI, DNS).
4. [SETUP-VERIFY.md](SETUP-VERIFY.md): end-to-end smoke tests.

For per-side internals after you are running, see [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md).

### What path A does not give you

It will not reproduce the paper's numbers. Those need the 68k-paper corpus and
a specific GPU. [`docs/paper-kit/10-REPRODUCE.md`](docs/paper-kit/10-REPRODUCE.md)
states plainly which results are externally reproducible (the metric code, BEIR
and SciFact, LitSearch, the abstention items) and which are not.

## Status

Munin is in production at [muninai.org](https://muninai.org), used by a single scientific group. The codebase is being prepared for public release; expect rough edges in setup ergonomics. Issues and PRs are welcome, especially for missing prerequisites, undocumented assumptions, or steps that break on a cluster other than the reference one.

## Don't

- Don't duplicate files across `backend/` and `frontend/`. If you find yourself wanting to, the file probably belongs in `shared/`.
- Don't add a top-level deploy script. The two runtimes have different lifecycles (cluster needs sudo + systemd; VPS is docker compose). Keeping them separate is intentional.