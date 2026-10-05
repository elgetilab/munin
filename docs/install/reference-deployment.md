# The reference deployment

This is how the instance the paper measures (muninai.org) is run: the backend
on a SLURM cluster (head node `hugin`, two GPUs) managed by
`backend/deploy.sh` and systemd, the frontend on a small VPS. You do not need
any of it to run Munin; [INSTALL.md](../../INSTALL.md) is the supported path.
Read this if your GPUs sit behind SLURM, or to see what the extra machinery
buys.

What it adds over `docker compose up`:

- vLLM runs as a SLURM job on a schedule (06:00 to 02:00 by default, freeing
  the GPUs overnight), not as a container.
- The paper pipeline (watcher, detection sweep, nightly re-attribution, nightly
  embedding map) runs as systemd units with their own venvs instead of the
  `pipeline` compose profile.
- `deploy.sh` copies the tree into `/opt/munin`, checks the cluster env before
  touching anything, and verifies the result (`deploy.sh verify`).

## Cluster prerequisites

- SLURM with a `vllm-serving` partition and GRES types `vllm` and `batch`
  (`#SBATCH --gres=gpu:vllm:1` in `backend/scripts/vllm/start-vllm-service.sh`;
  the two-GPU profile asks for one of each).
- Environment Modules with `cuda/13.0.2` (`module load cuda/13.0.2`).
- A vLLM venv at `/opt/munin/services/vllm/venv`, and the cron file that
  starts and stops vLLM on its schedule (`/etc/cron.d/hugin-cluster`, read by
  `schedule-vllm.sh` and `maintenance.sh`). `deploy.sh` creates neither; the
  reference cluster's own SLURM setup (separate, not public) does. On another
  cluster, create the venv with vLLM installed and either add the two cron
  entries or run `vllm-service enable-24x7`.
- Docker with Compose v2, Python 3.11 or newer, git, rsync, autossh, jq, curl.
- Root on the head node (`deploy.sh` installs into `/opt` and `/etc/systemd`).

## The cluster env file

`deploy.sh` reads `/opt/hugin/config/cluster.env` (override with
`HUGIN_ENV=...`) and links it to `/opt/munin/docker/.env`, so compose reads the
same values. Start from `backend/config/munin.env.template`. It refuses to
deploy without `MUNIN_DOMAIN`, `MUNIN_CLUSTER_NAME`, `NEO4J_PASSWORD` and
`SEARXNG_SECRET`, and the tunnel step needs `MUNIN_VPS_HOST`. Quote values
with spaces: bash also sources this file.

The tokens shared with the VPS (`ADMIN_INGEST_TOKEN`, `KB_GATE_TOKEN`,
`CONTRIBUTORS_SYNC_TOKEN`, and `MUNIN_GATEWAY_TOKEN` if you use it) must have
the same value in the VPS `.env`. `deploy.sh verify` fingerprint-checks
`KB_GATE_TOKEN` against the VPS when `METRICS_VPS_SSH` is set.

## Deploying

```bash
cd backend
sudo ./deploy.sh --dry-run all      # shows every step, changes nothing
sudo ./deploy.sh all
sudo ./deploy.sh model activate qwen3.8-27b --download   # the backbone (config/models/); --download fetches the weights on a fresh cluster
sudo vllm-service start
```

On a fresh cluster bring the whole stack up once; later deploys restart only
what changed:

```bash
cd /opt/munin/docker && sudo docker compose --profile rag --profile monitoring up -d
```

Partial deploys (`retrieval`, `personas`, `pipeline`, `tunnel`, ...) are
listed at the top of `deploy.sh` and in [backend/README.md](../../backend/README.md).
Persona files load only when retrieval starts, so deploy them together:
`sudo ./deploy.sh personas && sudo ./deploy.sh retrieval`.

## Serving the model through SLURM

The backbone is one file, `backend/config/models/<slug>.env` (checkpoint,
parsers, sampling, context window). `deploy.sh model activate <slug>` makes it
the production profile and stages its tokenizer; `deploy.sh model status`
shows what is active and what vLLM and retrieval actually serve. Changing the
model is that command plus a vLLM restart:

```bash
sudo ./deploy.sh model activate <slug> --restart-vllm
```

`vllm-service` (`start [single|tp2]`, `stop`, `status`, `enable-24x7`,
`disable-24x7`) controls the job; `tp2` spreads the model over both GPUs.
vLLM logs are in `/opt/munin/logs/vllm-service-<jobid>.{out,err}`. If a job
exits immediately, read the `.err` first: it is almost always the module load
or the GRES request.

Retrieval reaches vLLM at `host.docker.internal:8000`, so to the containers it
is just an OpenAI-compatible endpoint, the same as in any other install.

## The VPS

The reference VPS is a Hetzner CAX21 (ARM, Ubuntu 24.04) prepared with
`frontend/bootstrap.sh`, with uploads on a mounted volume at `/mnt/uploads`.
Its `.env` sets `MUNIN_DOMAIN` and the secrets; `BACKEND_URL` keeps its
default, the tunnel's end. It is deployed by rsync from a workstation rather
than by `git pull`; the recipe (build the chat UI, rsync the tree including
`shared/`, prune old bundles, `compose up`) is in
[frontend/README.md](../../frontend/README.md#deploy-to-vps). Sync the whole
tree, not only `frontend/`: the auth service mounts
`../shared/config/contributors.yml`.

A change to the static pages or the chat bundle is live as soon as it is
synced; only service code, the Caddyfile or the environment needs the
`compose up`.

## Checking it

```bash
sudo ./deploy.sh verify                     # cluster: health, model, paper space, personas, deep research
systemctl is-active munin-tunnel munin-paper-pipeline munin-paper-detect
ssh <admin>@<vps> 'curl -s http://127.0.0.1:18080/health'   # the tunnel, from the VPS end
```
