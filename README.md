# Munin

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23191403.svg)](https://doi.org/10.5281/zenodo.23191403)

Munin is an open-source AI research platform built for scientific groups: a
private chat, retrieval and deep-research stack over your own paper collection,
driven by the language model of your choice. It has two halves, a **backend**
(retrieval, agents, the paper pipeline, the databases) and a **frontend**
(login, API gateway, uploads, web UI), which run together on one machine or
apart, with the backend on your GPU server or cluster and the frontend on a
small public VM. Everything is Docker Compose.

## What you get

- Chat UI with per-turn routing (chat, research or code), SSE streaming, a task execution log, PWA install.
- Retrieval over your own paper collection (Qdrant vector DB + Neo4j citation graph) and per-user uploaded documents.
- Any OpenAI-compatible model: a local vLLM, Ollama or llama.cpp, or a hosted API.
- Deep Research: a long-running, plan-driven research agent that runs inside the retrieval service against the live vLLM, streams its progress, and delivers a cited report as an artifact.
- MCP tool server: web search, paper search, `run_python` sandbox, agentic orchestration.
- Email-OTP authentication, API keys with rate limits and usage logging, resumable uploads.

## Layout

```
backend/    Retrieval API, MCP tools, agents, paper pipeline, deep research,
            Qdrant/Neo4j/GROBID. Compose file in backend/docker/.
frontend/   Caddy, email-OTP auth, API gateway, tusd, upload hook, static
            UIs, React chat (webui/). Compose file frontend/docker-compose.yml.
scripts/    configure.sh, which writes the .env for any install mode.
shared/     Cross-cut artifacts both sides consume: persona JSONs
            (shared/personas/), contributor allowlist
            (shared/config/contributors.yml), contracts (shared/docs/).
docs/       install/ (tunnel, reference deployment), paper-kit/ (the paper's
            supplementary material), paper-track/ and agent-track/ (eval and
            agent work, mostly historical), archive/.
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

![Munin architecture: the rented gateway and the on-premise cluster as trust boundaries with the reverse SSH tunnel between them; a request goes through the router into the model harness, to the bare LLM, or with the deep research toggle to a research job; the harness calls the agents, and source and search read the corpus and, past a per-request egress gate, scholarly APIs and the web](docs/paper-kit/figures/fig_architecture.png)

The gateway (`frontend/`) terminates TLS, authenticates and meters; the
cluster (`backend/`) runs inference, retrieval and the harness, and dials an
autossh reverse tunnel out to the gateway (VPS `:18080` to cluster `:8080`), so
nothing connects in. A request goes through the router into the model harness,
straight to the bare LLM, or, with the deep research toggle on, to a detached
research job whose report is saved as an artifact. The harness calls the
`compute`, `source` and `search` agents and the plain MCP tools; `source` and
`search` read the corpus and, past a per-request egress gate that is not the
model's choice, scholarly APIs and the web. A citation audit runs after the
turn and flags ungrounded citations on the saved answer. The figure is
generated from the code by
[`docs/paper-kit/figures/fig_architecture.py`](docs/paper-kit/figures/), so its
labels track the source.

The two sides share three contracts: the HTTP API surface
([`shared/docs/BACKEND-API.md`](shared/docs/BACKEND-API.md), canonical),
persona definitions ([`shared/personas/`](shared/personas)), and the
contributor allowlist ([`shared/config/contributors.yml`](shared/config/contributors.yml)).

## Running it

The full guide is [INSTALL.md](INSTALL.md). Munin runs in three shapes, all
plain Docker Compose configured by `scripts/configure.sh`:

| Mode | Where |
|---|---|
| one machine, local | everything on `http://localhost` (trying it, developing) |
| one server | everything on one public server with your domain |
| split | backend on your GPU server or cluster, frontend on a small public VM, joined by a reverse SSH tunnel |

### Try it on one machine

```bash
git clone <this repo> && cd munin
scripts/configure.sh --mode all --domain localhost --admin-email you@example.org \
    --llm-url http://host.docker.internal:11434 --llm-model qwen3:32b
docker compose up -d --build
```

`configure.sh` writes `.env` (every secret generated, paths filled in), puts
your address in the login whitelist and copies the other seed files from their
`.example`. Run it without flags to be asked instead. Only whitelisted
addresses can log in; after the first boot the auth database is the source of
truth and users are managed in the admin panel. Every other knob is documented
in [`.env.example`](.env.example).

Then open <http://localhost>. Your login code is printed to the auth log
(`docker compose logs munin-auth | grep "login code"`), because
`configure.sh --domain localhost` sets `AUTH_DEV_ECHO_OTP=1`, so you do not
need a mail server to get in. It never does that for a real domain.

**You must supply a language model.** Munin speaks OpenAI-compatible HTTP and
does not host one. `--llm-url` is the base URL without `/v1` (Munin appends it):
a vLLM you run, llama.cpp, Ollama, or a hosted API (add `--llm-api-key`). The
model must support tool calling; the published results used Qwen3.8-27B on
vLLM, and a small model does noticeably worse at multi-step tool use. For
Ollama, see [INSTALL.md](INSTALL.md#3-one-machine-local): by default it
listens only on the host's loopback, which a container cannot reach, and its
context window is far smaller than Munin's budget.

**Be ready for the download.** Roughly **18 GB of images** with every profile
enabled, dominated by the retrieval service (10.1 GB, mostly PyTorch) and the
`run_python` sandbox (2.8 GB, mostly TeX Live). Trim it by removing profiles
from `COMPOSE_PROFILES` in `.env`. On top of that, about 2.2 GB of embedding
models (BGE-large, BGE-base, SPECTER) download from Hugging Face on first start
into `.runtime/data/hf-cache/`, once.

The corpus starts empty. The `seed` profile downloads ~20 open-access arXiv
papers so search returns something; point `SEED_QUERY` at your own field, or
drop your own PDFs into `.runtime/data/papers/pdf/` and the pipeline watcher
will ingest them.

Every setting is documented in [`.env.example`](.env.example), and the things
that trip people up are in [INSTALL.md, section 8](INSTALL.md#8-when-something-is-wrong).
The same command with a real `--domain` (and `--smtp-host`) sets up both
halves on one public server instead.

### Split: backend and frontend on different machines

The same script, once per machine. The backend writes `munin-peer.env` with
the tokens both halves must share; copy it to the frontend machine.

```bash
# GPU / cluster side. --vps-host also sets up the reverse SSH tunnel.
scripts/configure.sh --mode backend --domain lab.example.edu --admin-email you@lab.example.edu \
    --llm-url http://gpu01:8000 --llm-model <served-name> --vps-host vps.lab.example.edu
# public side
scripts/configure.sh --mode frontend --domain lab.example.edu --admin-email you@lab.example.edu \
    --smtp-host smtp.lab.example.edu --peer-env munin-peer.env
```

The frontend reaches the backend at `BACKEND_URL` (default `127.0.0.1:18080`,
the tunnel's end). Keep that path private: a tunnel, a VPN, or a network nobody
else is on. `MUNIN_GATEWAY_TOKEN` makes retrieval refuse forwarded identity
that did not come through the gateway, but it is a second line, not the first.

### The reference deployment

The instance the paper measures runs the backend on a SLURM cluster, with vLLM
as a scheduled SLURM job and the paper pipeline under systemd, managed by
`backend/deploy.sh`. None of that is needed to run Munin; it is documented as a
worked example in
[docs/install/reference-deployment.md](docs/install/reference-deployment.md).
For per-side internals see [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md).

### What no install gives you

A fresh install will not reproduce the paper's numbers. Those need the
68k-paper corpus and a specific GPU. [`docs/paper-kit/10-REPRODUCE.md`](docs/paper-kit/10-REPRODUCE.md)
states plainly which results are externally reproducible (the metric code, BEIR
and SciFact, LitSearch, the abstention items) and which are not.

## Status

Munin is in production at [muninai.org](https://muninai.org), used by one
scientific group. This is its first public release (1.0.0, see
[CHANGELOG.md](CHANGELOG.md)); expect rough edges in setup ergonomics. Issues
and PRs are welcome, especially for missing prerequisites, undocumented
assumptions, or steps that break on hardware other than the reference one.

## Citing

The software is the Elgeti Lab's and is archived on Zenodo:
[10.5281/zenodo.23191403](https://doi.org/10.5281/zenodo.23191403) always
resolves to the newest version; v1.0.0 is
[10.5281/zenodo.23191404](https://doi.org/10.5281/zenodo.23191404). If you use
Munin, please cite the accompanying paper, *MuninAI: A Self-Hosted Agentic
Framework for independent Research Groups* (M. Fischer, J. Meiler, M. Elgeti;
in preparation). GitHub's "Cite this repository" gives both entries from
[CITATION.cff](CITATION.cff).

## Contributing

- Keep `backend/` and `frontend/` independent. Anything both use belongs in
  `shared/`, not in two copies.
- Configuration is written by `scripts/configure.sh` and run by Compose. There
  is deliberately no deploy script that drives both halves: they may live on
  different machines with different owners. `backend/deploy.sh` is the
  reference cluster's own tooling, not a general installer.
- `shared/docs/BACKEND-API.md` is the API contract; change it with the code.