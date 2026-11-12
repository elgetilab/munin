# Lite install: the backend on a small VM

For when there is no GPU host: the backend runs beside the frontend on one VM of
about 4 cores and 8 GB, and the language model is the Claude API, billed to your
key. It is `backend/docker/lite.yml` layered over the normal compose file.

## What you get

| Works | Off |
|---|---|
| Chat, routing, personas, artifacts, projects, memories | The shared paper corpus and `#tags` |
| Web search, Semantic Scholar, open-access papers | Citation graph tools |
| Deep Research, on the web and Semantic Scholar | Knowledge map |
| Document upload and search, with pypdf instead of GROBID | Paper ingest; uploads wait in `complete/` |
| `run_python`, in a small sandbox | LaTeX compilation |

Answers come from a model the published benchmarks did not measure, and chats and
uploads go to Anthropic under your account, so tell your users.

## Set up

From a copy of the repo beside the running frontend:

    cp backend/docker/lite.env.example .env

Fill in `ANTHROPIC_API_KEY`, `SEARXNG_SECRET` (`openssl rand -hex 32`), and
`MUNIN_DOMAIN`, `KB_GATE_TOKEN`, `CONTRIBUTORS_SYNC_TOKEN` and `MUNIN_GATEWAY_TOKEN`
from the frontend's `.env`. Then `chmod 600 .env` and:

    docker compose up -d --build

`RETRIEVAL_HOST_PORT=18080` is where the gateway already expects a tunnel, so the
frontend needs no change. **The gateway starts sending users to retrieval as soon
as that port answers**, so check the model first with only the adapter running:

    docker compose up -d --build llm-claude
    docker compose exec llm-claude python smoke.py

See [backend/llm-claude/README.md](../../backend/llm-claude/README.md) for its
settings. Add swap and keep a copy of `.runtime/data/chats.db`: it holds every
user's chats.

## The sandbox

It idles at about 40 MB, with a 1 GB, one-core limit and a read-only root. All its
writable space is RAM, so user code cannot fill the disk the login databases share.
Kernels run with no network, no capabilities and a separate uid per conversation.
The service that starts them still runs as root with `SYS_ADMIN` in its container
and shares the host's kernel, so a kernel exploit would reach the VM.

## Returning to the cluster

Point the frontend back by stopping retrieval here and bringing the tunnel up.
Chats made here are in this `chats.db`, not the cluster's, so decide what to keep
before switching. [lite-merge.md](lite-merge.md) merges them, with uploaded
documents and their search index, into the cluster first. Uploads still in
`complete/` then ingest on the next retry.
