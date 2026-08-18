# Single-host Docker deployment: investigation and plan

Status: **Phases 1-5 implemented (2026-08-14 to 2026-08-18); Phase 6 optional
and not started.** Written for the public-release push. Decisions taken are in
§5; each phase carries its own outcome note below.

**One thing is owed across all of it:** nothing here has been brought up end to
end on a machine without a live deployment. Every phase was verified by
configuration resolution, unit tests and dry-run diffing, which catches a great
deal but is not the same as watching it boot. See "What is still unverified".

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
- ~~The GROBID image in use is the large deep-learning variant.~~ **This was
  wrong**, corrected 2026-08-18 by measuring: `lfoppiano/grobid:0.8.2` is
  1.73 GB on disk and the official `grobid/grobid:0.8.2` is 9.53 GB compressed.
  Production already runs the smaller one, and the "fix" would have made the
  download 5x worse.

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

> **Phase 1 DONE 2026-08-14.** All host paths parameterised behind `MUNIN_ROOT`
> and seven source-path variables, defaults unchanged from production; build
> contexts repo-relative-capable; container names, image tags, host ports and
> networks parameterised by `MUNIN_PREFIX`; GROBID image and heap tunable; root
> `.env.example` added. Verified: `docker compose config` with defaults differs
> from the pre-change baseline only in the four intended lines, and all ten
> `deploy.sh --dry-run` modes are byte-identical.
>
> **Two findings from Phase 1 that were not in the original analysis:**
>
> 1. **Compose derives the project name from the directory basename**, which
>    was `docker` for both `/opt/munin/docker` and a clone's `backend/docker`.
>    A `compose up` from a clone on a host already running Munin therefore
>    recreated the live containers against the clone's empty data directories,
>    and a `compose down` removed the production stack. This happened for real
>    on 2026-08-14 during testing. Fixed by pinning `name: ${MUNIN_PREFIX:-munin}`,
>    which forces a **one-time migration** on the cluster (documented in the
>    compose header; `deploy.sh` refuses to deploy until it is done). Prometheus
>    loses its TSDB history to the project rename; nothing else moves.
> 2. **Bind-mount directories are created root-owned**, so `.runtime/` cannot be
>    cleaned up by the user who ran `docker compose up`. Needs either a
>    documented `sudo rm -rf`, or user-namespace mapping. Folded into Phase 5.

### Phase 2: containerise the host-side daemons

- One `munin-pipeline` image, three compose services, `pipeline` profile.
- Drop the host venvs from the setup path.
- **Verify:** dropping a PDF into the watched directory ingests it end to end,
  with no systemd anywhere.

> **Phase 2 DONE 2026-08-14.** Four systemd units now have container
> equivalents behind the `pipeline` profile: `pipeline-watcher`,
> `pipeline-detect`, `pipeline-reattribute`, `knowledge-map`. Nightly timers
> are replaced by `scripts/run-daily.sh`, a ~40-line scheduler that recomputes
> its target from the wall clock each cycle (so it neither drifts nor breaks on
> DST), keeps looping when a job fails, and can run once at startup in place of
> the timers' `Persistent=true` catch-up.
>
> **The image split was decided by measurement, not guesswork.** The watcher,
> detect and reattribute jobs reuse the retrieval image because every module
> they import is already installed there (verified inside the running
> container), so they cost nothing extra. Only the embedding map needs a new
> image, for umap-learn and hdbscan. Building that FROM retrieval would have
> inherited torch for a job that never loads a model; from `python:3.11-slim`
> it is **941 MB against retrieval's 8.67 GB**.
>
> **Footprint data for Phase 5's README number:** retrieval 8.67 GB, sandbox
> 2.83 GB, knowledge 941 MB, before GROBID, Qdrant, Neo4j, Prometheus and
> SearXNG. "All profiles on" is expensive and the README must say so.
>
> **Two stale defaults found and fixed**, both the same class as the
> `database.py` bug in DECISIONS.md 2026-08 ("defaults must be production"):
> `paper_pipeline.py` defaulted to `specter` / `papers`, so any run without
> cluster.env ingested into the retired 768d corpus; and the repo's
> `munin-embedding-map.service` still said `QDRANT_COLLECTION=papers`, correct
> in production only because of an **untracked systemd drop-in** added at the
> 2026-07 cutover, so a deploy to a fresh cluster would have mapped the wrong
> corpus.
>
> **Not yet verified:** the end-to-end ingest test (drop a PDF, watch it land
> in Qdrant and Neo4j). It needs a host without a live deployment, for the same
> reason Phase 1's bring-up test does.

### Phase 3: LLM endpoint indirection

- `LLM_BASE_URL` / `LLM_MODEL_NAME` throughout, replacing the
  `host.docker.internal:8000` assumption.
- Optional `gpu` vLLM profile.
- **Verify:** the stack answers a chat turn against a small Ollama model.

> **Phase 3 DONE 2026-08-14.**
>
> **The spike's answer: the coupling is one field, not a rewrite.** Tool calling
> is already portable, because the vLLM-side `--tool-call-parser qwen3_xml`
> normalises to standard OpenAI `tool_calls` before the client ever sees it, and
> the reasoning fields are read defensively (`delta.reasoning or
> delta.reasoning_content`) so their absence is harmless. `<think>` stripping is
> a regex on output and a no-op when there is nothing to strip. The single real
> blocker was `chat_template_kwargs`, vLLM's non-OpenAI passthrough used at 9
> call sites to suppress reasoning on mechanical sub-tasks: a strict server 400s
> on unknown top-level fields, which would have made every summarise, expand and
> transcribe call fail while plain chat appeared to work.
>
> Gated behind `LLM_THINKING_TOGGLE` (default on, so the reference deployment is
> byte-identical) via `thinking_off_fields()` / `thinking_off()`, which also
> collapses 9 copies of the same literal into one helper. `LLM_BASE_URL` and
> `LLM_MODEL_NAME` added as the primary names, with `VLLM_URL` / `VLLM_MODEL_NAME`
> kept as aliases because cluster.env and every vLLM launch script export them.
> Internal symbol names unchanged, so the 43 call sites across 14 modules do not
> churn. Optional `gpu` profile added, deliberately excluded from the default
> profile set because it cannot start without an NVIDIA runtime.
>
> **A testing trap worth not re-laying.** The first version of the test drove
> `importlib.reload(database)` under `monkeypatch.setenv`. It passed in
> isolation and failed inside the full suite: once another module has imported
> `database`, a reload does not reliably re-read the environment. Rather than
> keep chasing the root cause, the config moved into pure functions
> (`resolve_llm_endpoint(env)`, `_env_flag(name, default, env)`) that take a
> mapping, which is both directly testable and the reason the empty-string case
> is now pinned rather than assumed. Reading config at import time is what made
> this untestable in the first place, and is the same shape as the stale-default
> bugs found in Phase 2.
>
> **Verified:** 25 new tests pass alone and in the suite; the full retrieval
> suite goes 552 passed / 23 failed before to 577 passed / 23 failed after, the
> same 23 being pre-existing environment failures (they read files outside the
> mounted directory), confirmed by running the identical suite against a
> pristine worktree of the previous commit. Compose defaults byte-identical; all
> deploy dry-runs unchanged.
>
> **Not yet verified:** an actual chat turn against a non-vLLM endpoint. That
> needs a host where the stack can be brought up.

### Phase 4: make the frontend portable

- `VITE_AUTH_BASE` / `VITE_API_BASE` in the webui, production values as
  defaults.
- Local Caddyfile, no TLS, path-based routing.
- Build the webui in a Docker stage so no host `npm` is needed.
- Untrack the stale `frontend/static/chat/` build.
- `AUTH_DEV_ECHO_OTP` demo mode.
- **Verify:** log in at `http://localhost` on a machine with no mail server and
  no DNS.

> **Phase 4 DONE 2026-08-18** (deploy verification still owed, see below).
>
> **Site URLs behind Vite env vars.** `webui/src/lib/urls.ts` is now the single
> source: `AUTH_BASE` (the only functional one, driving `/auth/me` and the
> admin API) plus six navigation URLs. 20 hardcoded literals across six
> components replaced. **Every default is the production value**, so a build
> with no `VITE_*` set targets the live deployment exactly as before.
>
> **The by-hand build is not byte-identical, and that is expected.** The bundle
> hash changes because the code is structurally different: the same 7 origins
> now appear once each in a shared module instead of repeated per call site,
> and `/auth/me` / `/admin` are built by template literal rather than sitting in
> the bundle as whole strings. The semantic proof is that the 219 existing msw
> tests still pass, since they mock the absolute production URLs and a
> mismatch would 404. A build with `VITE_AUTH_BASE` set was confirmed to put
> the override in the bundle, so both directions work.
>
> **Login without a mail server.** `AUTH_DEV_ECHO_OTP` logs the code instead of
> emailing it, returning before any SMTP work so a host with no relay does not
> hang on a connect timeout. Off by default, and it prints a four-line banner on
> every boot, because leaving it on means log access equals login access.
>
> **`caddy/Caddyfile.local`** serves the whole stack from one origin,
> `http://localhost`, splitting by path. That is not only convenience: same
> origin means the session cookie needs no cross-site handling, where the
> subdomain layout depends on a cookie scoped to `.muninai.org` that has no
> `localhost` equivalent. Selected by `MUNIN_CADDYFILE`, defaulting to the
> production file. The explicit `command:` this required was checked against the
> caddy image's default CMD and is byte-identical.
>
> **Docker webui build** (`webui/Dockerfile`, profile `webui`) removes the host
> Node requirement. Deliberately NOT wired into caddy's `depends_on`: a
> dependency on a profiled service implicitly enables that profile everywhere,
> including production. The cost is that the first page load can 404 until the
> build finishes.
>
> **Frontend compose paths parameterised**, the same treatment as Phase 1. This
> was mandatory, not tidiness: verified by probe that with two files in
> `COMPOSE_FILE`, a literal `./caddy` in the *second* file resolves against the
> *first* file's directory. Without it, every frontend mount would have pointed
> into `backend/docker/`.
>
> **The stale `frontend/static/chat` build is untracked** (63 files, gitignored
> since some earlier change but still committed).
>
> **Verified:** both Caddyfiles pass `caddy validate`; combined two-file compose
> resolves every frontend path into the working tree; frontend compose defaults
> unchanged bar the equivalent caddy command, an empty `AUTH_DEV_ECHO_OTP`, and
> `SMTP_PORT` now defaulting to 587 instead of an empty string that `int()`
> would have raised on; backend compose defaults and deploy dry-runs unchanged.
> Test suites: auth 97, webui 222, gateway 11, upload 10, all passing.
>
> **Still owed: the deploy verification you asked for.** Nothing here has been
> deployed to the VPS. The production build path (`npm run build` + rsync) is
> untouched and the defaults are unchanged, but that is an argument, not a
> demonstration. Deploy the frontend and confirm login and the admin panel
> before the paper goes out.

### Phase 5: one-command up, seed data, and docs

- Root `compose.yaml` (or a documented `--profile` invocation) that starts
  everything.
- `seed` profile with ~20 open-access papers.
- Rewrite the four SETUP-*.md documents around two paths: "try it on one
  machine" and "deploy it for a group". The current documents assume the
  cluster-plus-VPS split from the first line.
- **Verify:** a clean machine, following only the README, reaches a working
  chat turn. Ideally tested by someone who has not seen the repo.

> **Phase 5 DONE 2026-08-18.**
>
> **One-command entry** without a root compose file, per the decision in §5:
> `COMPOSE_FILE` and `COMPOSE_PROFILES` in the repo-root `.env` make a plain
> `docker compose up` at the root bring up both halves. 19 services resolve.
>
> **Seed corpus** (`backend/scripts/seed/seed_corpus.py`, profile `seed`). It
> QUERIES the arXiv API rather than shipping a list of ids, deliberately: a
> hardcoded list is a list that can be wrong, and a plausible-but-wrong
> identifier resolving to a real-but-different paper is precisely the failure
> mode this project's own evaluation is about. Querying makes every id real by
> construction and lets a user seed their own field via `SEED_QUERY`. Verified
> live against arXiv: 3 real on-topic PDFs downloaded, a re-run skipped all 3,
> an unreachable network exits 0 with an explanation rather than failing the
> stack, and bad arguments exit 2.
>
> **Footprint measured and published** in the README: ~17 GB with every profile
> on, dominated by retrieval at 8.67 GB (PyTorch) and the sandbox at 2.83 GB
> (TeX Live), plus ~1.3 GB of encoder weights fetched on first start.
>
> **A Phase 1 error, caught by measuring.** I had recommended switching GROBID
> to `grobid/grobid:0.8.2` as "the CRF-only image, far smaller". The opposite is
> true: the deployed `lfoppiano/grobid:0.8.2` is 1.73 GB on disk and
> `grobid/grobid:0.8.2` is 9.53 GB compressed. Following my own advice would
> have made the download roughly 5x worse. Corrected in `.env.example`, the
> compose header and §1.5 above.
>
> **Docs restructured** around the two paths the README now leads with, with the
> four SETUP documents explicitly marked as the group-deployment path.
>
> **Root-owned `.runtime` documented, not fixed.** Docker creates bind-mount
> sources as root and the databases write as their own container users. Running
> everything under the invoking uid would fix it, but Qdrant and Neo4j expect
> their own uids, so the cleanup command is documented rather than the problem
> papered over.

---

## Verification status

Updated 2026-08-18 after the first `single-host-smoke` run.

### Verified

| Claim | Evidence |
|---|---|
| The retrieval image builds from a clean checkout | `single-host-smoke`, run 32133192116. Validates the Phase 1 build-context change on a machine that has never seen `/opt/munin`. |
| `docker compose up` brings the stack up and it serves | Same run: healthy in ~31 s with Qdrant and Neo4j connected. |
| Munin works against a NON-vLLM endpoint | Same run: `/api/status` reports the model endpoint reachable, and `llm_summarize` completes a round-trip returning the stub's reply. This is the Phase 3 indirection working end to end. |
| The compose invariants hold, and would catch a regression | `compose` job, 17-18 s per run. Each check was verified to FAIL when its invariant is deliberately broken. |
| `LLM_THINKING_TOGGLE` is load-bearing | Strict stub 400s with `chat_template_kwargs` present, 200s without. |
| The frontend deploy | Deployed 2026-08-18: bundle byte-identical to the local build, `/auth/me` and `/auth/check` reachable, forward-auth redirecting correctly, dev-OTP off. |
| The compose-project migration on the cluster | All seven containers on project `munin`; `deploy.sh retrieval` deployed and `verify` passed; `llm_summarize` confirmed against production vLLM. |

### Still unverified

| Not done | Why, and what it would take |
|---|---|
| A PDF ingesting end to end through the containerised pipeline | The smoke runs `COMPOSE_PROFILES=rag` only. Adding `pipeline` pulls in the 941 MB knowledge image and the ingest daemons, costing several minutes per run. The systemd equivalent is exercised in production daily; the containerised one never has been. |
| The `seed` and `webui` profiles booting | Config-resolution only. `seed` was verified standalone against the live arXiv API; the compose service wrapping it has not run. `webui` adds a Node build to the job. |
| The frontend half booting under compose | Config resolution is checked both standalone and combined. The VPS runs these containers continuously, but never from this repo's parameterised paths on a fresh host. |
| A real small model, rather than a stub | The stub proves the transport and request shape. It says nothing about whether a 3B model can actually drive the agent loop, which is a quality question the paper already scopes out. |
| An actual OTP login on the deployed frontend | Needs a human to receive the email. |
| `.runtime` cleanup without root | Documented rather than fixed; would need user-namespace mapping that Qdrant and Neo4j do not expect. |

The honest summary: the quick-start is proven to boot and answer, on a clean
machine, in CI. What remains untested is the ingest path in containers and the
profiles the smoke job leaves out to stay under ten minutes.

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

- ~~Switch GROBID to the CRF-only image~~ **Not needed, and the premise was
  wrong.** Measured 2026-08-18: the deployed `lfoppiano/grobid:0.8.2` is 1.73 GB
  on disk; the official `grobid/grobid:0.8.2` is 9.53 GB compressed. Production
  already runs the small one.
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
