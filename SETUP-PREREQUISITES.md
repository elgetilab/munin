# Munin: Prerequisites

Before you start setup, gather everything below. Each section is a checklist; tick items off as you go. Once this is complete, proceed to [SETUP-CLUSTER.md](SETUP-CLUSTER.md).

## Hardware

### Cluster (backend)

You need a Linux cluster with SLURM that you administer.

- [ ] Sudo on the SLURM head node.
- [ ] Ability to submit GPU SLURM jobs.
- [ ] Permission to install systemd units on the head node.
- [ ] Outbound internet access (PyPI, container registries, model downloads).

Recommended baseline:

- 2x NVIDIA GPUs, 24+ GB VRAM each. One handles vLLM inference, the other handles deep-research jobs. One GPU is enough if you skip deep research; vLLM minimum VRAM depends on the model you choose.
- 100+ GB of scratch on the head node for `/opt/munin/` (config, embeddings, models, chat SQLite).
- 200+ GB of additional storage for paper PDFs if you intend to ingest a sizeable collection.

### VPS (frontend)

You need a small public VPS. Reference deploy: Hetzner CAX21 (4 vCPU ARM, 8 GB RAM, Ubuntu 24.04) plus a Hetzner Volume for uploads. Any cloud provider works.

- [ ] Linux VM with Docker support and a public IPv4 address.
- [ ] At least 2 vCPU, 4 GB RAM, 40 GB disk for the OS and containers.
- [ ] A separately mounted volume (or extra disk) for uploads, sized to your team's expected upload volume. Reference: 50 GB.

## Network

- [ ] A registered domain name. Reference: `muninai.org`.
- [ ] A DNS provider with API or panel access for A records. Reference: HostEurope.
- [ ] Outbound SSH from the cluster head to the VPS (port 22 by default). The autossh tunnel runs in this direction; the cluster initiates the connection so the cluster does not need to accept inbound traffic.

## Accounts

- [ ] SMTP credentials for transactional email (login OTPs). Any standard SMTP service works: SendGrid, Postmark, AWS SES, your university mail relay, your domain registrar's mail product. Reference deploy uses HostEurope SMTP.
- [ ] (Optional) A Crossref "polite pool" mailto address (an email you control). GROBID uses this when resolving paper metadata, so Crossref does not rate-limit you. See [Crossref etiquette](https://api.crossref.org/swagger-ui/index.html).

## Software

These are installed per-machine. The deploy scripts assume they are already present.

### On the cluster head

- [ ] SLURM with working `sbatch` / `squeue`.
- [ ] CUDA + nvidia-driver compatible with your chosen vLLM model.
- [ ] Docker and Docker Compose v2.
- [ ] Python 3.11.
- [ ] git, rsync, autossh, jq, curl.
- [ ] Environment Modules (the SLURM scripts use `module load` to select CUDA versions).

### On the VPS

- [ ] Ubuntu 24.04. Other Debian-likes likely work but are untested.
- [ ] Root SSH access for the first-time bootstrap.
- [ ] Docker and Docker Compose are installed by `frontend/bootstrap.sh`. You do not need them pre-installed.

### On your workstation (used for deploys)

- [ ] git, rsync, ssh.
- [ ] Node.js 20+ and npm (for building the chat web UI before rsync).

## Models

The cluster will download these on first deploy. Plan for 50 to 200 GB depending on your vLLM model choice.

- [ ] vLLM serving model of your choice. Configure in `backend/scripts/vllm/start-vllm-service.sh` (`MODEL_ID`, `MODEL_PATH`, `MODEL_NAME`).
- [ ] SPECTER2 paper embeddings, ~440 MB, downloaded automatically.
- [ ] BGE-base user-document embeddings, ~440 MB, downloaded automatically.
- [ ] MiroThinker deep-research model, ~30 GB, downloaded automatically by `deploy.sh deepresearch`. Skip if you do not enable deep research.

## Secrets you will need to generate or obtain

Keep these in a password manager; you will paste them into config files later.

- [ ] `AUTH_SECRET_KEY`: generate with `openssl rand -hex 32`.
- [ ] SMTP host, port, username, password.
- [ ] List of admin emails (`ADMIN_EMAILS`).
- [ ] User whitelist: emails of the people allowed to log in, with display names.
- [ ] vLLM API key: any random string, shared between the retrieval API and vLLM.
- [ ] Neo4j password: any password, used for the citation-graph database.
- [ ] Cluster-side secrets your environment needs in `cluster.env`. The full var list is in `backend/config/munin.env.template`; SETUP-CLUSTER.md walks through populating it.

## Skills assumed

To set this up successfully, you should be comfortable with:

- Linux sysadmin (systemd, SSH, file permissions, journalctl).
- Docker Compose.
- DNS and TLS.
- SLURM job submission.
- Reading and editing YAML and shell scripts.

When this list is fully ticked, continue with [SETUP-CLUSTER.md](SETUP-CLUSTER.md).
