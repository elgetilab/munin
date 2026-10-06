# Changelog

All notable changes to this project are recorded here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]: 0.9.0

First public release, accompanying the paper. The work behind it is recorded
in [`docs/RELEASE-PLAN.md`](docs/RELEASE-PLAN.md).

### Added

- **Install modes and `scripts/configure.sh`.** One script writes the `.env`
  for one machine (`--mode all`, on `localhost` or a real domain), a backend
  only, or a frontend only: secrets generated, paths filled in, seed files
  copied, the admin address put into the login whitelist, upload directories
  created. A split install shares its tokens through `munin-peer.env`.
- **[INSTALL.md](INSTALL.md)**, with `docs/install/tunnel.md` and
  `docs/install/reference-deployment.md`.
- **Any OpenAI-compatible model endpoint**, including hosted ones:
  `LLM_BASE_URL`, `LLM_MODEL_NAME` and `LLM_API_KEY` (sent as a Bearer token).
- **`BACKEND_URL`**, the frontend's one address for the backend, and
  **`MUNIN_GATEWAY_TOKEN`**, an optional shared secret without which
  retrieval refuses forwarded identity headers. **`RETRIEVAL_BIND_ADDR`** for
  a VPN or private network.
- **A `tunnel` compose profile**: an autossh container that dials the frontend
  machine, for hosts without systemd access.
- Apache-2.0 `LICENSE`, `NOTICE`, `CITATION.cff`.

### Changed

- **Every version is pinned**: third-party images to exact tags, Python
  dependencies through a `constraints.txt` per service, frozen from what the
  reference deployment runs. Rebuilding reproduces the same software.
- **The per-turn router is on by default** (`ROUTER_ENABLED=true`), as in
  every published number.
- **Sci-Hub is off by default** (`SCIHUB_ENABLED`). With it on, the paper tools
  and the crawler behave as before.
- The chat UI derives its sibling URLs at runtime from the host it is served
  on, so one build works on any domain.
- The LitQA2 dataset revision is pinned; LitQA2 question and answer text is no
  longer redistributed (answer scorecards keep verdicts and per-query arrays;
  the C2 question set keeps qids and source DOIs).
- CI runs on Ubuntu 24.04, with 26.04 alongside as an early warning, and boots
  the whole one-machine install in a smoke job.

### Breaking, for anyone running an earlier tree

- **`MUNIN_DOMAIN` is required.** Both compose files refuse to start without
  it, and `backend/deploy.sh` refuses to deploy. Every instance-specific URL,
  the cookie scope, the CORS origins and the contact addresses derive from it;
  none defaults to the reference deployment's values any more.
- **`NEO4J_PASSWORD` and `SEARXNG_SECRET` have no fallback values.**
- `backend/deploy.sh` also requires `MUNIN_CLUSTER_NAME` (the persona prompts
  carry placeholders filled in at load).
- `frontend/auth/whitelist.csv`, `shared/config/contributors.yml` and
  `frontend/config/quotas.yml` are no longer tracked; copy the `.example`
  files (configure.sh does).
- The post-login redirect only accepts the instance's own hosts.

### Fixed

- One-machine installs: GROBID and the API gateway both used host port 8070;
  the embedding models never loaded when no weights were staged (an empty
  mount directory counted as a model); `LLM_BASE_URL` never reached the
  containers; `Caddyfile.local` was never committed; the local layout did not
  route `/verify`, so no local login could complete; upload directories were
  created root-owned, so uploads failed.
- GROBID's JVM crashed on hosts whose cgroup v2 hierarchy exposes no
  controllers (`-XX:-UseContainerSupport`).
- The chat UI showed "Request failed" instead of the backend's error message.
- The flakiness suite's citation checks matched only the reference domain,
  and the post-login redirect accepted any URL (an open redirect).
- `bootstrap.sh` could lock the operator out of a VM that is not the reference
  image; it now settles the admin key before changing anything.
- The paper-detect sweep had failed on a read-only working directory since
  2026-05; maintenance mode revived the retired Deep Research daemon;
  `deploy.sh` exported an unset model name and could leave the tunnel unit
  disabled; the VPS backfill retried permanently rejected uploads forever.
- The embedding map defaulted to the retired `papers` collection.
- Reproduce instructions: the C2b shadow instance, `risk_coverage` (which a
  bare run let overwrite a committed scorecard), and the dataset loaders.
