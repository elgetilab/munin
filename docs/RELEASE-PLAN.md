# Public release: assessment and plan

Status: **PLAN, nothing implemented.** Written 2026-10-02 for the publication
release. Decisions taken are in §2; open questions, each with a default, in §5.

Goal: another research group can install Munin as **frontend only, backend
only, or both**, on their own hardware, with their own domain and model, by
editing one `.env` file and running `docker compose`.

This builds on [SINGLE-HOST-PLAN.md](SINGLE-HOST-PLAN.md), which already made
the compose files path-parameterised, added `LLM_BASE_URL`, containerised the
pipeline, added dev-OTP login and the local Caddyfile, and put a single-host
smoke test in CI. That plan delivered "try it on one machine". This one
delivers "run it for your group".

---

## 1. Assessment

### 1.1 What already works

- One `.env` at the repo root plus `docker compose up` brings up all 19
  services on one host (`COMPOSE_FILE` + `COMPOSE_PROFILES`).
- Any OpenAI-compatible endpoint serves the model; `gpu` profile for a local
  vLLM container.
- The `single-host-smoke` CI job builds the retrieval image from a clean
  checkout and completes an LLM round-trip.

### 1.2 What stands in the way

**Personal data is tracked.** 239 distinct email addresses appear in the tree
(not counting `example.*`, `noreply` and `muninai.org`). The obvious ones:

- `frontend/auth/whitelist.csv`: ~146 real users with names and roles, in 5
  commits of history.
- `shared/config/contributors.yml`: real contributor emails.
- `backend/docs/corpus-quality/*.json|csv`: audit outputs carrying uploader
  emails.
- Test fixtures and several docs (`backend/docs/archive/*`,
  `frontend/scripts/BACKFILL-README.md`, `shared/docs/DECISIONS.md`,
  `shared/docs/CONTRIBUTOR-INGEST.md`) quoting real addresses and names.

The rest still needs classifying (some are paper authors inside audit outputs,
some are ours). The repo is private with no forks (checked 2026-10-02), so a
history rewrite before it goes public is a complete scrub, unlike the 2026-08-04
rewrite of an already-pushed history.

**No license, no citable release.** No `LICENSE`, `CITATION.cff`, `CHANGELOG`
or tag.

**The production domain is hardcoded.** `muninai.org` in the Caddyfile (11
occurrences), the auth CORS list and `COOKIE_DOMAIN` default
(`frontend/auth/main.py:80`, `:98-103`), compose defaults
(`MUNIN_PUBLIC_URL`, `CONTRIBUTORS_SYNC_URL`, `AUTH_CHECK_ROLE_URL`,
`UNPAYWALL_EMAIL`), sender/support addresses, the gateway's `ADMIN_EMAILS`
default, the web-tool User-Agent, and ~100 links in `frontend/static/*.html`.

**Defaults are the production values.** Deliberate until now (DECISIONS.md
2026-08, "defaults must be production"), and `check_compose.py` enforces it.
For a public release it means a stranger who forgets a variable silently talks
to `auth.muninai.org`.

**The group-deployment path is our cluster.** SLURM, Environment Modules,
systemd, root, and `backend/deploy.sh` (1,983 lines, `/opt/hugin/config/cluster.env`
hardcoded). Fine as our tooling, not as someone else's install path.

**Frontend-only and backend-only are not general.** They exist only as the
VPS + cluster split joined by the autossh tunnel.

**The backend trusts its caller.** The retrieval API takes identity from the
`X-Munin-Email` / `X-Munin-Role` headers the gateway sets. That is safe today
only because retrieval binds `127.0.0.1` and the gateway reaches it through
the tunnel. Exposing retrieval on a network interface would let anyone
impersonate any user. Any split install must keep that link private.

**Stale setup docs.** SETUP-PREREQUISITES still lists SPECTER2 and
MiroThinker; SETUP-CLUSTER says the model lives in three files (it is now a
profile under `backend/config/models/`); SETUP-VERIFY checks a
`deepresearch-daemon` SLURM unit (Deep Research is in-process now);
`bootstrap.sh` documents `--admin-user varghele`.

**Branding in prompts.** All three personas say "an AI assistant on the Hugin
research cluster".

**Unpinned images.** `qdrant:latest`, `searxng:latest`, `tusd:latest`,
`vllm-openai:latest`.

**Unverified paths** carried over from SINGLE-HOST-PLAN: containerised ingest
end to end, the frontend compose on a fresh host, the `seed` and `webui`
profiles booting.

---

## 2. Decisions taken (2026-10-02)

| Question | Decision |
|---|---|
| Distribution format | **Docker Compose**, the existing two files. No Helm, Ansible or pip packaging. |
| License | **Apache-2.0.** |
| Personal data in history | **Rewrite history** with `git-filter-repo` before the repo goes public. |
| Compose defaults | **Neutral defaults.** The reference deployment's values move into its own `cluster.env` and VPS `.env`. Reverses the DECISIONS.md 2026-08 rule; record the reversal there. |
| `backend/deploy.sh` | **Stays in the repo** as the reference deployment's tooling, referenced from the docs as an example, not documented as the install path. |
| Images | **Users build their own** (`docker compose build`). No registry publishing. |

Consequence of building locally: the web UI's `VITE_*` URLs can stay
build-time. Compose passes them as build args derived from `MUNIN_DOMAIN`, so
no runtime-config rewrite of the shipped UI is needed.

---

## 3. Target shape

### 3.1 Three install modes, one set of compose files

| Mode | Runs | Typical host |
|---|---|---|
| `all` | both halves | one GPU workstation or server (exists today) |
| `backend` | retrieval, Qdrant, Neo4j, GROBID, SearXNG, sandbox, pipeline, optional vLLM | GPU server or a cluster login node |
| `frontend` | Caddy, auth, gateway, tusd, hook-service, web UI | small public VPS |

Each mode is a `COMPOSE_FILE` + `COMPOSE_PROFILES` preset. In a split install
the frontend reaches the backend at `BACKEND_URL` (today `CLUSTER_TUNNEL`,
kept as an alias). Because of §1.2's header trust, that link is always private:
retrieval keeps binding loopback, and the transport is one of

- the autossh reverse tunnel (what we run, now a documented recipe),
- WireGuard or another VPN, with retrieval bound to the VPN address only,
- the same private LAN, with a firewall rule.

Plus defence in depth (§5 Q2): a shared secret the gateway sends and retrieval
requires, so a misconfigured bind is not an impersonation hole.

SLURM becomes an optional extra: its only job is serving vLLM, and
`LLM_BASE_URL` already decouples that. The vLLM SLURM scripts ship under an
"if your GPUs are behind SLURM" page.

### 3.2 Configuration: two layers, nothing else to edit

- **`.env`**: secrets and host facts. New keys:
  - `MUNIN_DOMAIN` drives the Caddyfile (`{$MUNIN_DOMAIN}`), `COOKIE_DOMAIN`,
    the auth CORS origins, `MUNIN_PUBLIC_URL`, the cluster's callback URLs, the
    webui build args, sender and support addresses.
  - `MODEL_PROFILE` names `backend/config/models/<slug>.env`, the single source
    for the model (replaces the "three files" step).
  - `BACKEND_URL` in frontend mode.
- **Mounted files** for group content, each shipped as `*.example` with the
  real file gitignored: personas, `contributors.yml`, `agents.yml`,
  `whitelist.csv` (first-boot seed).
- **`scripts/configure.sh`** asks mode, domain, LLM endpoint, admin email;
  generates every `openssl rand` token; writes `.env`. In a split install it
  prints the tokens that must be copied to the other side, which removes the
  "three tokens must match" failure class.

---

## 4. Phases

Each phase is independently verifiable. The reference deployment must be
unchanged after every phase; §4.7 says how that is checked.

### Phase 1: personal data and legal hygiene

1. **Inventory.** List every email address and personal name in the tree and
   in history (`git log -p --all`), classified as user, contributor, paper
   author in an audit output, or ours. Output: a private replacement map,
   never committed.
2. **Tip of tree.**
   - `whitelist.csv` and `contributors.yml` become `*.example` with synthetic
     entries; the real files are gitignored and stay on the hosts (both are
     already seed-only or deploy-synced, so production does not read them from
     git at runtime; confirm per file before moving).
   - `backend/docs/corpus-quality/` outputs move out of the repo to
     `/opt/munin/data/audits/`; the scripts that produce them stay.
   - Test fixtures and docs switch to `@example.org` addresses.
3. **History.** Back up the full repo as a tarball (2026-08-04 precedent),
   then `git-filter-repo` with `--invert-paths` for the removed files and
   `--replace-text` from the map. Verify zero matches across `git log -p
   --all`. You force-push; every clone must be re-cloned afterwards
   (includes `/opt/src` checkouts on hugin and the VPS rsync source).
4. **Legal.** `LICENSE` (Apache-2.0), `NOTICE`, `CITATION.cff`, `CHANGELOG.md`.
   Check third-party terms we pull at build time (GROBID Apache-2.0, Neo4j
   Community GPLv3 used as an unmodified image, TeX Live, BGE MIT, model
   weights under their own licenses) and list them in NOTICE.
5. **Neutral branding.** Personas say "your group's research cluster" (or an
   `INSTANCE_NAME` placeholder); `bootstrap.sh` requires `--admin-user` with no
   default; personal and internal-host names leave the docs.
6. **Doc pruning.** Decide what ships (§5 Q4).

**Verify:** the inventory script reports zero personal addresses in tree and
history; the auth and retrieval suites pass with synthetic fixtures.

### Phase 2: neutral defaults and `MUNIN_DOMAIN`

1. **Pin the reference values first.** Before changing any default, write
   every production value the reference deployment currently gets from a
   default into `/opt/hugin/config/cluster.env` and the VPS `.env`. Capture
   `docker compose config` for both sides as the baseline.
2. Flip compose and code defaults to neutral (`example.org`, empty, or fail
   fast at boot for anything security-relevant like `COOKIE_DOMAIN`).
3. `MUNIN_DOMAIN` through Caddyfile, auth CORS and cookie, compose URLs,
   gateway, User-Agent, webui build args.
4. Static pages: replace absolute `muninai.org` links with relative links
   where the target is same-host, and Caddy's `templates` directive for the
   rest (§5 Q3).
5. `MODEL_PROFILE` as the single model source on the single-host path (the
   cluster already uses `deploy.sh model activate`).
6. `HUGIN_ENV` in `deploy.sh` becomes overridable, default unchanged.
7. `check_compose.py`: the "cluster defaults stay production" invariant
   becomes "defaults contain no reference-deployment value", plus "the
   reference env reproduces the baseline".
8. DECISIONS.md entry recording the reversal and why.

**Verify:** `docker compose config` with the reference env is identical to the
step-1 baseline on both sides; all `deploy.sh --dry-run` modes unchanged;
`git grep muninai.org` outside docs and the reference env templates returns
nothing; a frontend deploy, then login and the admin panel, work in production.

### Phase 3: install modes

1. Mode presets and `scripts/configure.sh`.
2. `BACKEND_URL` (alias `CLUSTER_TUNNEL`) so the frontend file runs alone.
3. Backend-alone: the frontend URLs it calls back to (`AUTH_CHECK_ROLE_URL`,
   `CONTRIBUTORS_SYNC_URL`) derived from `MUNIN_DOMAIN`; retrieval bind address
   configurable but loopback by default.
4. Gateway-to-retrieval shared secret (§5 Q2).
5. Tunnel as a recipe: the autossh unit plus a containerised autossh option
   for hosts without systemd access.

**Verify:** `check_compose.py` resolves all three modes; the strict-stub CI
job runs per mode; a split install on two VMs completes a login and a chat
turn over the tunnel.

### Phase 4: build reproducibility

1. Pin every third-party image to a version (and record the digest in a
   comment).
2. Pin Python dependencies where they float (`frontend/upload` has none at
   all, per CI's "best-effort - no pinned deps").
3. One documented build command per mode; measured build time and disk
   footprint in the docs.

**Verify:** two builds a week apart from the same tag produce the same
dependency versions.

### Phase 5: docs

1. Replace SETUP-PREREQUISITES/CLUSTER/VPS/VERIFY with one `INSTALL.md`
   (three modes, one checklist each, verification at the end) plus
   `docs/install/` pages: split-install transport, SLURM-served vLLM, SMTP,
   upgrading. Old files: archive + stub per the repo's convention.
2. README "Running it" points at INSTALL.md; reference deployment and
   `deploy.sh` described as one worked example.
3. Remove stale content (SPECTER2, MiroThinker, `deepresearch-daemon`, "three
   files").

### Phase 6: verification and release

1. Clean VM per mode, following only INSTALL.md, ideally by someone outside
   the project. Covers the SINGLE-HOST-PLAN gaps: containerised ingest end to
   end, frontend compose on a fresh host, `seed` and `webui` profiles.
2. Tag `v1.0.0`, GitHub release notes from CHANGELOG, Zenodo archive for a DOI
   to cite in the paper. Flip the repo public.

### 4.7 Protecting the reference deployment

Phases 1 to 3 touch code production runs. The rule throughout: production
values are written into the reference env files **before** any default changes,
and each phase is verified by diffing resolved compose config and deploy
dry-runs against a baseline, then deploying. No phase changes production
behaviour.

---

## 5. Open questions (default in brackets)

1. **Real `whitelist.csv` / `contributors.yml` after untracking:** where do the
   canonical copies live? [On the hosts only. The auth DB is already the
   source of truth for users, and contributors already sync from auth, so the
   files are seeds.]
2. **Shared secret between gateway and retrieval?** [Yes. Small change, and it
   turns a misconfigured bind from an impersonation hole into a 401.]
3. **Static-page links:** Caddy `templates` substitution, or relative links
   plus a build-time `sed`? [Caddy `templates`, no build step.]
4. **Which docs ship?** [Keep `docs/paper-track`, `docs/agent-track`,
   `docs/paper-kit`, `docs/architecture` (the paper's evidence). Drop
   `docs/handoffs/`, corpus-quality outputs, and the dated audit and repair
   JSONs. Keep `docs/archive/` but scrub it.]
5. **Benchmarks data:** `backend/benchmarks/` holds 255 files. Do any carry
   corpus text or third-party dataset content we may not redistribute (LitQA2,
   SciFact, LitSearch items)? [Audit in Phase 1; keep scorecards and code,
   fetch datasets at run time.]
6. **Version number:** [`v1.0.0`, matching the paper.]
