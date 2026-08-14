# Single-host Docker deployment: investigation and plan

Status: **scope decided 2026-08-14, implementation not started.** Written for
the public-release push. Decisions taken are in §5.

Goal: `git clone && cp .env.example .env && docker compose up` brings up the
whole Munin stack on one machine.

---

## 0. Scope, stated up front

What a reader of the paper can realistically do matters more than a vague
"it runs anywhere", because it changes several design decisions.

**In scope.** A reviewer, a collaborator, or another group brings up the full
stack on one machine, logs in, uploads a handful of PDFs, and uses chat,
search, retrieval, agents, uploads, the sandbox, LaTeX and Deep Research
against **their own** corpus and **their own** LLM endpoint. Decided
2026-08-14: the default `up` brings up every feature profile, not a minimal
core (§5).

**Explicitly not in scope.** Reproducing the paper's numbers. Those need the
68k-paper private corpus and a specific GPU, and `docs/paper-kit/10-REPRODUCE.md`
already says plainly which results are externally reproducible (metric code,
BEIR/SciFact, LitSearch, the abstention items) and which are not. This plan
does not change that, and it should not pretend to.

**Consequence of the scoping:** we optimise for "up and working in 15 minutes
with an empty corpus", not for "bit-identical to production". A cluster
operator still uses `backend/deploy.sh`.

---

## 1. What is actually in the way

Findings from reading the tree. None of these are hypothetical.

### 1.1 The backend compose file is a deployed artifact, not source

`backend/docker/docker-compose.yml` cannot run from the repo. Every path is
absolute:

- Volumes are all `/opt/munin/...` and `/opt/cluster/scripts/pipeline`.
- Build contexts are `/opt/munin/services/retrieval` with a dockerfile path of
  `../../../../../../opt/munin/services/retrieval/Dockerfile`, which only
  resolves because the file is installed into `/opt/munin/docker/` first.
- `.env` is a symlink to `/opt/hugin/config/cluster.env`, created by `deploy.sh`.

So the file only works after `deploy.sh dirs && deploy.sh compose && deploy.sh
retrieval` has copied sources into `/opt`. It is the *output* of a deploy, and
it is checked in as if it were an input.

### 1.2 A large amount of the system is not containerised at all

| Component | Today | Needed on one host |
|---|---|---|
| vLLM | SLURM job, `gpu:vllm:1`, cron 6am-2am | An OpenAI-compatible endpoint from somewhere |
| Paper pipeline watcher | systemd, host venv | Container |
| Paper detect sweep (15 min) | systemd, host venv | Container |
| Paper reattribute (04:30) | systemd timer | Container |
| Embedding map (nightly) | systemd timer, separate venv | Container |
| Reverse tunnel | autossh systemd unit | Not needed, same host |
| Maintenance toggle | root shell script on PATH | Optional |

Three separate Python environments exist on the host today
(`services/pipeline/venv`, `services/knowledge/venv`, `services/vllm/venv`),
each with its own requirements file.

### 1.3 The frontend assumes public subdomains and real TLS

- The Caddyfile routes seven `*.muninai.org` subdomains and requests real
  certificates via ACME. On a laptop there are no public DNS names.
- **The chat UI hardcodes `https://auth.muninai.org`** in `webui/src/lib/api.ts`
  for `/auth/me` and the whole admin surface, plus about 20 more hardcoded
  absolute links in `App.tsx`, `Sidebar.tsx`, `Settings.tsx`, `SleepingPage.tsx`,
  `MaintenancePage.tsx`. A locally-built UI would call the production
  deployment. This is a genuine blocker, not cosmetic.
- `network_mode: host` on caddy, tusd, hook-service and api-gateway, so
  service-name DNS is unavailable and everything talks over `127.0.0.1`.
- `/mnt/uploads` is a hardcoded host path from the reference VPS.

### 1.4 You cannot log in without an SMTP server

`munin-auth` delivers the OTP purely by `aiosmtplib`. There is no dev mode, no
console fallback, no bypass. A first-time user with no mail credentials is
locked out of their own instance at step one.

### 1.5 Assorted smaller blockers

- `frontend/static/chat/` is **gitignored but 63 files are still tracked**, so a
  fresh clone gets a stale, partial build of the chat UI. Either commit the
  build properly or untrack it and build in Docker.
- The api-gateway points at `CLUSTER_TUNNEL: http://127.0.0.1:18080`, which on
  one host should just be the retrieval service.
- `INGEST_CONCURRENCY`, GROBID heap (12 GB) and its 16 GB memory cap are sized
  for a cluster node and will fail or thrash on a 16 GB laptop.
- The GROBID image in use (`lfoppiano/grobid:0.8.2`, the deep-learning variant)
  is very large. The CRF-only image is a fraction of the size and is almost
  certainly the right default for a demo.

### 1.6 What is already fine, and worth knowing

Three things do **not** need work, which meaningfully reduces the job:

- **A fresh, empty corpus is handled.** `database.verify_paper_space()` treats a
  missing collection as non-fatal by design, with an explicit comment saying a
  fresh cluster has no papers until the pipeline creates one.
- **Models self-download.** `get_paper_encoder()` falls back to
  `BAAI/bge-large-en-v1.5` from HuggingFace when the mounted path is absent, so
  no weights need shipping.
- **There is no hard SLURM coupling in the service.** The only SLURM contact is
  reading a `slurm_queue.json` file, and a missing file returns an empty queue.

---

## 2. Design decisions I propose

### D1. One parameterised compose file, not a third one

The tempting move is to add `compose.yaml` at the root for local use and leave
the cluster one alone. **I recommend against it.** That is exactly the failure
the monorepo merge was created to fix: `DECISIONS.md` 2026-04 records that
personas, the contributor script, and the sync doc each existed in two copies
and drifted. A second compose file describing the same eleven services would
drift the same way, and the drift would be invisible until a deploy broke.

Instead: make `backend/docker/docker-compose.yml` **path-parameterised** so the
same file serves both.

```yaml
volumes:
  - ${MUNIN_ROOT:-../.runtime}/knowledge/qdrant_storage:/qdrant/storage
build:
  context: ${MUNIN_RETRIEVAL_SRC:-../retrieval}
```

Compose interpolates variables in build contexts and volume paths, so this
works. `deploy.sh` sets `MUNIN_ROOT=/opt/munin` and the existing behaviour is
unchanged; a local run takes the defaults.

**This does not violate the "no top-level deploy script" rule.** That rule is
about the two runtimes having different lifecycles (cluster needs root and
systemd, VPS is compose). A single-host compose file is a third, explicitly
labelled *development and demonstration* target, and it does not replace either
production path. I would still like this confirmed rather than assumed, since
it is a stated repo rule.

### D2. Bring your own LLM endpoint, with an optional GPU profile

vLLM on SLURM cannot be containerised meaningfully for a general audience. Most
people trying the repo will not have two RTX 5090s.

- **Default:** `LLM_BASE_URL` env var pointing at any OpenAI-compatible
  endpoint. The user supplies vLLM, Ollama, llama.cpp, LM Studio, or a hosted
  API. The code already routes every call through `vllm_client.py`, so this is a
  configuration change rather than a refactor.
- **Optional `gpu` profile:** a vLLM container for people who do have a GPU,
  reproducing the production serve flags.
- **Document the honest caveat:** the harness was measured with a
  reasoning-capable model that supports tool calls, and behaviour with a small
  local model will be worse. That is a documentation problem, not a code one.

Needs verification before committing: how much of the tool-call and reasoning
handling is Qwen-specific (`--tool-call-parser qwen3_xml`,
`--reasoning-parser qwen3` are server-side, but the client may make
assumptions).

### D3. One pipeline image replacing four systemd units

Build a single `munin-pipeline` image carrying the pipeline plus knowledge
requirements, and run it as compose services with the schedule expressed as
compose-level concerns rather than systemd timers:

- `pipeline-watcher` (the `--watch` loop, already a poll loop, maps directly)
- `pipeline-detect` (sweep loop)
- `pipeline-maintenance` (reattribute + embedding map, a simple sleep-and-run
  loop or an in-container cron)

All three go behind a `pipeline` profile so a demo user who only wants chat does
not pay for them.

### D4. A `demo` auth mode

Add `AUTH_DEV_ECHO_OTP=1` to `munin-auth`, which logs the OTP instead of
sending mail. It must be loud (a warning banner in the log on every boot) and
must default off. Without this there is no way in.

### D5. A local Caddy profile plus a build-time API base

**Decided: do this now, and verify it with a real frontend deploy before
submission.** The env vars default to the production values, so the built
output is unchanged; the deploy is what proves that claim rather than assuming
it.

Two halves:

- A second Caddyfile (`caddy/Caddyfile.local`) serving everything on
  `http://localhost` with path prefixes instead of subdomains, no ACME.
- **Replace the hardcoded `https://auth.muninai.org` in the webui with a Vite
  env var** (`VITE_AUTH_BASE`, `VITE_API_BASE`), defaulting to the production
  values so nothing changes for the real deploy. This is the single most
  invasive code change in the plan and touches roughly six files.

### D6. Seed data, so a fresh instance does not look broken

A `seed` profile that ingests ~20 open-access papers so search, retrieval, and
the citation graph return something on first boot. An empty instance is
indistinguishable from a broken one, and that is the first impression a
reviewer gets.

**Fetched, not committed.** The repo carries a list of DOIs and arXiv ids plus
a fetch script; the PDFs are pulled at setup time from arXiv and PMC. That
sidesteps redistribution entirely and keeps the repo small. Cost: the seed step
needs network access and will partly fail behind a strict firewall, so it must
degrade to "corpus is empty, here is how to add your own" rather than erroring
out.

---

## 3. Phased plan

Each phase is independently useful and independently verifiable.

### Phase 1: make the backend compose file portable

- Parameterise every absolute path behind `MUNIN_ROOT` and friends.
- Fix the build contexts to be repo-relative by default.
- Add `.env.example` at the root, consolidating the 33 cluster keys and 11 VPS
  keys into one documented file with working defaults.
- Update `deploy.sh` to export the production values, and confirm a dry-run
  deploy is byte-identical to today.
- **Verify:** `deploy.sh --dry-run all` shows no change on the cluster, and
  `docker compose up qdrant neo4j` works from a fresh clone.

### Phase 2: containerise the host-side daemons

- One `munin-pipeline` image, three compose services, `pipeline` profile.
- Drop the host venvs from the setup path.
- **Verify:** dropping a PDF into the watched directory ingests it end to end,
  with no systemd anywhere.

### Phase 3: LLM endpoint indirection

- `LLM_BASE_URL` / `LLM_MODEL_NAME` throughout, replacing the
  `host.docker.internal:8000` assumption.
- Optional `gpu` vLLM profile.
- **Verify:** the stack answers a chat turn against a small Ollama model.

### Phase 4: make the frontend portable

- `VITE_AUTH_BASE` / `VITE_API_BASE` in the webui, production values as
  defaults.
- Local Caddyfile, no TLS, path-based routing.
- Build the webui in a Docker stage so no host `npm` is needed.
- Untrack the stale `frontend/static/chat/` build.
- `AUTH_DEV_ECHO_OTP` demo mode.
- **Verify:** log in at `http://localhost` on a machine with no mail server and
  no DNS.

### Phase 5: one-command up, seed data, and docs

- Root `compose.yaml` (or a documented `--profile` invocation) that starts
  everything.
- `seed` profile with ~20 open-access papers.
- Rewrite the four SETUP-*.md documents around two paths: "try it on one
  machine" and "deploy it for a group". The current documents assume the
  cluster-plus-VPS split from the first line.
- **Verify:** a clean machine, following only the README, reaches a working
  chat turn. Ideally tested by someone who has not seen the repo.

### Phase 6 (optional): CI smoke test

A GitHub Actions job that brings the stack up against a stub LLM and asserts a
chat turn completes. This is what stops the quick-start rotting three months
after the paper is out, which is the normal fate of these things.

---

## 4. Effort and risk

| Phase | Size | Main risk |
|---|---|---|
| 1 Compose portability | Medium | Breaking the live cluster deploy. Mitigated by dry-run diffing. |
| 2 Pipeline containers | Medium | Torch image size; GROBID contention tuning differs on a laptop. |
| 3 LLM indirection | Small to medium | Unknown depth of Qwen-specific assumptions in the client. Needs a spike first. |
| 4 Frontend portability | **Largest** | Touches the shipped UI. Needs care that the production build is unchanged. |
| 5 Compose entry + docs | Medium | Mostly writing. |
| 6 CI | Small | Runner resource limits. |

Biggest single risk: **Phase 4 changes code that is live in production for real
users.** Everything else is additive. I would want the webui changes behind
defaults that reproduce today's behaviour exactly, and a deploy of the frontend
verified before the paper goes out.

Second risk: **total image footprint.** Retrieval carries torch, sandbox carries
about 1 GB of TeX Live, GROBID's DL image is very large. A first `docker compose
up` could pull tens of gigabytes, which undercuts "quick to set up". Switching
GROBID to the CRF image and making the sandbox and pipeline profiles opt-in
addresses most of it, and the number should be measured and stated in the
README rather than discovered by the user.

---

## 5. Decisions taken (2026-08-14)

| Question | Decision |
|---|---|
| Scope of the one-command setup | **Everything, all feature profiles on**: uploads, sandbox, LaTeX, Deep Research, pipeline. See the footprint note below. |
| The webui's hardcoded auth domain | **Fix now**, behind env vars defaulting to the production values, and **verify with a real frontend deploy** before submission. |
| Seed papers | **Yes, ~20 open-access**, fetched by DOI or arXiv id at setup time rather than committed, so nothing is redistributed. |
| Compose layout | **Parameterise the existing file.** No second compose file describing the same services. |

### Consequences of "everything on"

Footprint stops being a footnote and becomes a work item. Retrieval carries
torch, the sandbox carries roughly a gigabyte of TeX Live, and GROBID's
deep-learning image is very large. Three mitigations move into the plan proper
rather than being optional:

- Switch GROBID to the **CRF-only image** as the default, with the DL image as
  an opt-in for anyone who wants the better parser.
- **Measure and publish the actual pull size and cold-start time** in the
  README. A user who knows it is 18 GB up front is fine; one who discovers it
  at minute forty is not.
- Keep the profiles as **profiles** even though they all default on, so a user
  on a small machine can turn things off, and document which ones cost the
  most.

### How one-command entry works without a root compose file

Set `COMPOSE_FILE` in the repo-root `.env`:

```
COMPOSE_FILE=backend/docker/docker-compose.yml:frontend/docker-compose.yml
COMPOSE_PROFILES=rag,pipeline,monitoring
```

Docker Compose reads `.env` from the working directory and honours both keys,
so a plain `docker compose up` at the repo root brings up both halves. No new
file describes any service, nothing duplicates the cluster path, and the
"no top-level deploy script" rule is untouched: there is no script.

### Still assumed, flag if wrong

**No GPU by default.** "All profiles on" is read as all *feature* profiles; the
`gpu` vLLM profile stays opt-in, because it cannot work on a machine without
the hardware and would fail the default `up`. The default remains a
bring-your-own `LLM_BASE_URL`.
