# Munin: Cluster (backend) setup

Complete after [SETUP-PREREQUISITES.md](SETUP-PREREQUISITES.md). All commands run on your SLURM head node as root unless noted.

When this is done, proceed to [SETUP-VPS.md](SETUP-VPS.md). The cluster and VPS sides are independent until step 8 of this document, where they meet for the SSH tunnel handshake.

## 1. Clone the monorepo

- [ ] Clone somewhere on the head node:
  ```bash
  git clone https://github.com/<your-fork>/munin.git /opt/src/munin
  cd /opt/src/munin
  ```

## 2. Cluster environment file

The deploy script expects a populated env file at
`/opt/hugin/config/cluster.env`. The path comes from the reference
cluster (named `hugin`) and is hardcoded in `backend/deploy.sh`
(`HUGIN_ENV`). To use a different path, edit `deploy.sh` to match.

`deploy.sh compose` symlinks `$HUGIN_ENV` into `/opt/munin/docker/.env`
so `docker compose` picks it up automatically; nothing to wire by hand
once the file exists.

- [ ] Create the directory:
  ```bash
  mkdir -p /opt/hugin/config
  chmod 700 /opt/hugin/config
  ```
- [ ] Copy the template:
  ```bash
  cp backend/config/munin.env.template /opt/hugin/config/cluster.env
  chmod 600 /opt/hugin/config/cluster.env
  ```
- [ ] Fill in the values. The template has comments explaining each var. Required at minimum:
  - `NEO4J_PASSWORD`: pick any password (must match what Neo4j is
    initialised with on first boot — once persistent data exists,
    changing this without rotating in `cypher-shell` will lock you
    out of the graph).
  - `ADMIN_INGEST_TOKEN`: `openssl rand -hex 32`. Must match the
    same key on the VPS so the upload hook can authenticate against
    `/api/admin/ingest`.
  - `CONTRIBUTORS_SYNC_TOKEN`: `openssl rand -hex 32`. Must match
    the same key on the VPS so the cluster can pull
    `auth.<your-domain>/admin/contributors.yaml` every 5 minutes.
  - `KB_GATE_TOKEN`: `openssl rand -hex 32`. Must match the same
    key on the VPS. Originally the bearer for the tusd KB-upload
    pre-create hook (VPS-only); since 2026-06-02 also used by the
    cluster's metrics proxy to call `/admin/check-role` for the
    in-house Metrics dashboard. If you already set it on the VPS
    side, copy that exact value here -- they MUST match. See
    `shared/docs/MONITORING.md` for the monitoring setup. There is
    no auto-sync between the two copies, so they can drift on a
    later token rotation; optionally set `METRICS_VPS_SSH` (and
    `METRICS_VPS_SSH_KEY`) in this same file so `deploy.sh verify`
    fingerprint-checks the cluster value against the VPS on every
    deploy and warns on mismatch.
  - `SEMANTIC_SCHOLAR_API_KEY`: optional, only set if you have one
    (the citation graph builder uses it when present).

## 3. Personas and contributors

Both files live in `shared/` because the VPS reads them too. Edit once, deploy from this side, and the VPS picks them up at runtime via the API.

- [ ] Edit `shared/personas/` to define which AI personas appear in the chat UI. Each persona is one JSON file plus an optional inverted-style SVG logo. Use the existing files as templates.
- [ ] Edit `shared/config/contributors.yml` to list who is allowed to contribute papers to the shared collection. One entry per person; see the existing schema.

## 4. vLLM model selection

For a first install, these three are enough to bring the stack up:

- [ ] `backend/scripts/vllm/start-vllm-service.sh`: set `MODEL_ID`, `MODEL_PATH`, `MODEL_NAME`.
- [ ] `backend/docker/docker-compose.yml`: set `VLLM_MODEL_NAME` to the same model name the retrieval service should request.
- [ ] `backend/retrieval/database.py`: update the fallback model name to match.

**Changing the model on a cluster that is already running is a longer
list** (eleven places, including a tokenizer-staging step that fails
silently). Do not use the three above for that. The canonical checklist is
`backend/README.md` -> Common Tasks -> "Switch LLM model".

## 5. Run the deploy

- [ ] Dry-run first:
  ```bash
  cd backend && sudo ./deploy.sh --dry-run all
  ```
  Read the output. Confirm paths look right for your cluster.
- [ ] Real deploy:
  ```bash
  sudo ./deploy.sh all
  ```

This populates `/opt/munin/` with config, the compose stack, retrieval source, deep-research daemon, persona files, vLLM scripts, and the systemd-managed SSH tunnel service. It also installs scripts under `/opt/cluster/scripts/llm/` and systemd units under `/etc/systemd/system/`.

For partial redeploys (single component), see `backend/README.md` § Deployment.

## 6. Start vLLM

The deploy stages the vLLM scripts but does not start vLLM itself.

- [ ] First-time start:
  ```bash
  sudo vllm-service start
  ```
- [ ] Verify:
  ```bash
  curl -s http://127.0.0.1:8000/v1/models | jq
  ```
- [ ] (Optional) The default schedule runs vLLM 06:00 to 02:00 to free GPUs overnight. To run 24/7, edit `backend/scripts/vllm/schedule-vllm.sh` and redeploy.

## 7. Knowledge bases

- [ ] Verify Qdrant is up: `curl -s http://127.0.0.1:6333/dashboard/ | head`.
- [ ] Verify Neo4j is up: `curl -s http://127.0.0.1:7474/`.
- [ ] (Optional) Trigger an initial paper-pipeline run to populate the papers collection. See `backend/scripts/pipeline/paper_pipeline.py`. You can also wait until your group starts uploading papers via the VPS upload UI.

## 8. SSH tunnel to the VPS

The cluster initiates a reverse tunnel to the VPS so the VPS can reach the retrieval API at `127.0.0.1:18080`. The VPS-side `tunnel` user must already exist before this step works, so finish [SETUP-VPS.md](SETUP-VPS.md) sections 1 to 3 first, then return here.

- [ ] On the head node, generate a tunnel SSH key for root if one does not exist:
  ```bash
  ssh-keygen -t ed25519 -f /root/.ssh/munin_tunnel -N ''
  ```
- [ ] Add `/root/.ssh/munin_tunnel.pub` to the VPS `tunnel` user's `~/.ssh/authorized_keys`, prefixed with the restrictive options shown in [`frontend/docs/hugin-tunnel-setup.md`](frontend/docs/hugin-tunnel-setup.md). The options force port-forwarding only, no shell.
- [ ] Test the connection:
  ```bash
  ssh -i /root/.ssh/munin_tunnel -N tunnel@<vps-ip>
  ```
  It should connect silently. Ctrl-C to exit.
- [ ] Enable the systemd unit:
  ```bash
  sudo systemctl enable --now munin-tunnel
  ```
- [ ] Verify on the VPS: `ss -tlnp | grep 18080` should show a listener.

## 9. Verify cluster-side

- [ ] `curl -s http://127.0.0.1:8080/api/status | jq` returns `ok: true` and shows the loaded models.
- [ ] `systemctl is-active munin-tunnel deepresearch-daemon` returns `active` for both.
- [ ] `docker ps` lists qdrant, neo4j, grobid, searxng, retrieval, sandbox.

If any of these fail, see [SETUP-VERIFY.md](SETUP-VERIFY.md) § "If something fails" for diagnostics.

When this is green, continue with [SETUP-VPS.md](SETUP-VPS.md) (or finish it, if you started in parallel).
