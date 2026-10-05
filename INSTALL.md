# Installing Munin

Munin has two halves:

- the **backend**: retrieval API, agents, Qdrant and Neo4j, GROBID, the paper
  pipeline, the `run_python` sandbox. It needs disk and, for a local model, a
  GPU.
- the **frontend**: Caddy (TLS), email-code login, the API gateway, resumable
  uploads, the chat UI. It is small and is the only part the internet sees.

They run together on one machine or apart on two. Everything is Docker
Compose, configured by one `.env` that `scripts/configure.sh` writes for you.

## 1. Pick a mode

| Mode | What runs where | Good for |
|---|---|---|
| **One machine, local** (`--mode all --domain localhost`) | everything on `http://localhost`, no TLS, no mail server | trying it, developing, demos |
| **One server** (`--mode all --domain lab.example.edu`) | everything on one public server with real certificates | a small group with one GPU server |
| **Split** (`--mode backend` + `--mode frontend`) | backend on your GPU server or cluster, frontend on a small public VM, joined by a reverse SSH tunnel | a group whose GPUs must not face the internet (what the reference deployment does) |

You always bring the language model: any OpenAI-compatible endpoint (vLLM,
llama.cpp, Ollama, or a hosted API with `--llm-api-key`). Give its base URL
without `/v1`; Munin appends it. Tool calling must work. The published results
used Qwen3.8-27B served by vLLM; a small model works but does noticeably worse
on multi-step tool use.

## 2. What you need

Every machine: Linux, Docker Engine with Compose v2.20 or newer, git, and
outbound internet access during the build (PyPI, npm, Docker Hub, Hugging
Face).

| | Backend | Frontend |
|---|---|---|
| Disk | ~18 GB of images (retrieval 10.1 GB, sandbox 2.8 GB, GROBID 1.7 GB, the rest under 1 GB each) plus ~2.2 GB of embedding models downloaded once on first start (into `.runtime/data/hf-cache/`), plus your papers | ~1 GB plus uploads |
| Memory | 16 GB works for a small corpus (GROBID alone is capped at 6 GB); more for a large one | 2 GB |
| Ports | none open: everything binds `127.0.0.1` | 80 and 443 open to the internet |
| Other | a model endpoint the containers can reach | a domain with DNS you control, and SMTP for login codes |

For a public install you need these DNS A records, all pointing at the
frontend machine: `<domain>`, `auth.`, `chat.`, `search.`, `docs.`,
`research.`, `upload.` and `api.<domain>`. Caddy gets the certificates itself
once they resolve.

## 3. One machine, local

```bash
git clone https://github.com/elgetilab/munin.git && cd munin
scripts/configure.sh --mode all --domain localhost --admin-email you@example.org \
    --llm-url http://host.docker.internal:11434 --llm-model qwen3:32b
docker compose up -d --build
```

The first build takes a while (the images above). Then open
<http://localhost>, enter your address, and read the login code from the log:

```bash
docker compose logs munin-auth | grep "login code"
```

With `--domain localhost` the code is logged instead of emailed. That never
happens for a real domain. `host.docker.internal` is the machine itself as
seen from inside a container; use it for a model server running on the host.

The other pages are on their own ports: search on `:8081`, docs on `:8082`,
the landing page on `:8083`, uploads on `:8084`, and the API for API-key
clients on `:8085` (all `http://localhost`).

**Ollama** needs two settings before Munin can use it. It listens only on the
host's loopback, which a container cannot reach: start it with
`OLLAMA_HOST=0.0.0.0:11434` (and keep port 11434 firewalled from the outside).
Its default context window is a few thousand tokens, far below the 65536 Munin
budgets for: raise it with `OLLAMA_CONTEXT_LENGTH=32768` (or what your
hardware allows) and set `VLLM_MAX_MODEL_LEN=32768` and
`VLLM_MAX_CONTEXT=28000` in `.env` to match, or long turns are silently cut.

The corpus starts empty. The `seed` profile fetches about 20 open-access arXiv
papers on first start (set `SEED_QUERY` in `.env` to choose the field), or drop
your own PDFs into `.runtime/data/papers/pdf/` and the pipeline ingests them.

## 4. One server

The same command with your domain and a mail server:

```bash
scripts/configure.sh --mode all --domain lab.example.edu --admin-email you@lab.example.edu \
    --llm-url http://gpu01:8000 --llm-model <served-model-name> \
    --smtp-host smtp.lab.example.edu --smtp-user munin --smtp-sender noreply@lab.example.edu
docker compose up -d --build
```

It asks for the SMTP password. Set up the DNS records first (section 2), then
open `https://chat.lab.example.edu`.

## 5. Split install

Order matters: the backend generates the tunnel key the frontend machine has
to accept, and the tokens both halves share.

**1. Backend** (GPU server or cluster head):

```bash
scripts/configure.sh --mode backend --domain lab.example.edu --admin-email you@lab.example.edu \
    --llm-url http://gpu01:8000 --llm-model <served-model-name> \
    --vps-host vps.lab.example.edu
```

This writes `.env`, a tunnel key under `.runtime/config/tunnel/`, and
`munin-peer.env` (the shared tokens; it holds secrets, so copy it over a
private channel and delete it afterwards). It ends by printing the exact
`bootstrap.sh` command for the VPS, with the tunnel key filled in.

**2. VPS, as root, once:** copy `frontend/bootstrap.sh` to a fresh Ubuntu
24.04 VM (there is no clone there yet: `scp frontend/bootstrap.sh root@vps:`)
and run the command configure.sh printed:

```bash
bash bootstrap.sh --admin-user <you> --tunnel-pubkey 'ssh-ed25519 AAAA... munin-tunnel@lab.example.edu'
```

It installs a firewall (22, 80, 443), fail2ban and Docker, creates your admin
user with sudo, creates the `tunnel` user that may only forward one port, and
restricts SSH logins to those users plus the account you ran it from. Your
admin user gets the SSH keys of root and of that account (or the one you pass
with `--admin-pubkey`); if there is none, it stops before changing anything.
**Before closing your session, check in a second terminal that
`ssh <you>@vps` works.** It is tested on Hetzner's Ubuntu 24.04 image; its
`--volume-id` mounts a Hetzner volume at `/mnt/uploads` (elsewhere, mount your
upload disk there yourself or leave uploads on the root disk). If you skip
bootstrap, see [docs/install/tunnel.md](docs/install/tunnel.md) for the tunnel
user it would have created.

**3. Frontend** (the VPS, as the admin user):

```bash
git clone https://github.com/elgetilab/munin.git && cd munin
scripts/configure.sh --mode frontend --domain lab.example.edu --admin-email you@lab.example.edu \
    --smtp-host smtp.lab.example.edu --peer-env ~/munin-peer.env --uploads-dir /mnt/uploads
docker compose up -d --build
```

**4. Start the backend:** `docker compose up -d --build` on the backend. The
`tunnel` container dials the VPS and exposes retrieval there as
`127.0.0.1:18080`, the frontend's default `BACKEND_URL`. Nothing connects in to
the backend.

The tunnel is one way to give the frontend a private path to the backend; a VPN
or a private network works too (set `BACKEND_URL` on the frontend). Never
expose the retrieval port to a network other people can reach: it takes the
user's identity from headers the gateway sets. `MUNIN_GATEWAY_TOKEN`, which
configure.sh sets on both halves, makes it refuse identity headers that did not
come through the gateway, but that is a second line of defence, not the first.
Details and a systemd alternative: [docs/install/tunnel.md](docs/install/tunnel.md).

## 6. Check that it works

On the backend:

```bash
docker compose ps                                  # all services Up
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool
```

`/api/status` should show `"vllm": {"status": "running", ...}` (your model
endpoint answered) and `"retrieval": "ok"` under `services`.

On the frontend of a split install, the tunnel's end must answer:

```bash
curl -s http://127.0.0.1:18080/health
```

Then in a browser: log in, send a message (the answer streams in), ask
"run python: print(2+2)" (a `run_python` call returning 4), and open the
upload page (`https://upload.<domain>`, or `http://localhost:8084` locally).

## 7. Afterwards

- **Users.** Only addresses in `frontend/auth/whitelist.csv` can log in at
  first; configure.sh puts yours there as admin. After the first start the
  auth database is the source of truth: add people in the chat UI's admin
  panel. The CSV is re-read on restart but only adds, never changes.
- **Papers.** Upload through the upload page (open to admins and group
  leaders; set a user's role and group in the admin panel), or drop PDFs into
  `.runtime/data/papers/pdf/` on the backend.
- **The model.** Change `LLM_BASE_URL`, `LLM_MODEL_NAME` (and `LLM_API_KEY`
  for a hosted one) in `.env`, then `docker compose up -d retrieval`. If the
  endpoint rejects the extra field vLLM accepts, set `LLM_THINKING_TOGGLE=0`
  (see `.env.example`).
- **Sci-Hub** is off. `SCIHUB_ENABLED=1` makes the paper tools suggest it when
  a PDF is not in the corpus, and the crawler fall back to it. Whether that is
  legal and acceptable where you are is yours to decide.
- **Every other setting** is documented in [`.env.example`](.env.example).
- **Upgrading.** `git pull && docker compose up -d --build`. Versions are
  pinned (image tags, and `constraints.txt` next to each `requirements.txt`),
  so a rebuild reproduces the same software rather than pulling whatever is
  newest. Do not re-run configure.sh on a live install: it generates new
  secrets.
- **Backups.** Everything stateful on the backend is under `.runtime/`
  (`knowledge/` holds Qdrant and Neo4j, `data/` the chats, papers and uploaded
  documents), plus Docker volumes. On the frontend: the `auth_data` (users,
  sessions) and `gateway_data` (API keys, usage) volumes, and the uploads
  directory. Volume names carry the Compose project: `munin_auth_data` on a
  one-machine install, `frontend_auth_data` on a frontend-only one
  (`docker volume ls` shows them).
- **Cleaning up a local install.** The databases write `.runtime/` as their
  own users, so removing it needs `sudo rm -rf .runtime`.

## 8. When something is wrong

| Symptom | Likely cause |
|---|---|
| `docker compose` says `MUNIN_DOMAIN must be set` | no `.env` in the directory you ran it from: run it from the repo root, or run configure.sh |
| Login page says the address is not authorized | it is not in the auth database; add it in the admin panel, or (before the first start) to `whitelist.csv` |
| No login email | SMTP settings, or the provider rejects `SMTP_SENDER` for a domain you do not own; `docker compose logs munin-auth` |
| Chat hangs or shows "Backend unavailable" (split) | tunnel down: `docker compose logs tunnel` on the backend, `curl 127.0.0.1:18080/health` on the VPS |
| `/api/status` shows the model as unreachable | `LLM_BASE_URL` is not reachable from inside the container: `localhost` there is the container itself, and a server listening only on the host's loopback (Ollama's default) is invisible to it |
| Requests to a hosted model fail with 401 | `LLM_API_KEY` missing or wrong |
| Answers get cut off or forget the start of a long turn | the model's context window is smaller than `VLLM_MAX_MODEL_LEN` (Ollama's default is); lower it in `.env` to what the endpoint serves |
| Summaries fail while plain chat works | the endpoint rejects `chat_template_kwargs`: `LLM_THINKING_TOGGLE=0` |
| Uploads fail | the uploads directory is not owned by uid 1000 (tusd): `sudo chown -R 1000:1000 <uploads dir>` |
| Search returns nothing | the corpus is empty; see "Papers" above |
| Every request with a login returns 401 "gateway token required" | `MUNIN_GATEWAY_TOKEN` differs between the halves |

## 9. The reference deployment

The instance the paper measures (muninai.org) runs the backend on a SLURM
cluster with systemd units and `backend/deploy.sh` instead of plain
`docker compose up`. You do not need any of that; it is documented as a worked
example in [docs/install/reference-deployment.md](docs/install/reference-deployment.md),
including serving vLLM through SLURM.
