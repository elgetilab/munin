# Munin: VPS (frontend) setup

Complete after [SETUP-PREREQUISITES.md](SETUP-PREREQUISITES.md). You can do this in parallel with [SETUP-CLUSTER.md](SETUP-CLUSTER.md); the two sides only meet at the SSH tunnel handshake (step 8 of SETUP-CLUSTER.md and step 8 of this document).

## 1. Provision the VM

- [ ] Create a Linux VM at your cloud provider with a public IPv4 and Ubuntu 24.04. Reference: Hetzner CAX21.
- [ ] Add an SSH key during creation.
- [ ] Attach a separate volume for uploads, mount path `/mnt/uploads`. Reference: 50 GB Hetzner Volume.
- [ ] Note the public IP for the next step.

## 2. DNS

In your DNS provider, add A records for each subdomain Munin uses. The reference deploy uses `muninai.org`; substitute your domain.

- [ ] `<your-domain>` to VPS IP (apex)
- [ ] `auth.<your-domain>` to VPS IP
- [ ] `chat.<your-domain>` to VPS IP
- [ ] `docs.<your-domain>` to VPS IP
- [ ] `search.<your-domain>` to VPS IP
- [ ] `research.<your-domain>` to VPS IP
- [ ] `upload.<your-domain>` to VPS IP
- [ ] `api.<your-domain>` to VPS IP

Wait for DNS to propagate before deploying. Caddy needs the A records to resolve to your VPS for automatic Let's Encrypt issuance.

## 3. Bootstrap the VPS

`frontend/bootstrap.sh` is idempotent: it installs Docker, creates an admin user, creates the `tunnel` user (port-forward only), mounts `/mnt/uploads`, and applies basic firewall rules. Re-running it on a configured VPS is a no-op.

- [ ] SSH in as root: `ssh root@<vps-ip>`.
- [ ] Copy `frontend/bootstrap.sh` to the VPS (e.g. via `scp`) and run it.
- [ ] (Optional) Edit the script first to change the default admin username from `varghele` to one of yours.
- [ ] After bootstrap, switch to the admin user from now on: `ssh <admin>@<vps-ip>`.

## 4. Project files

- [ ] On your workstation, build the chat UI:
  ```bash
  cd frontend/webui
  npm install
  npm run build
  cd ../..
  ```
  The build outputs to `frontend/static/chat/`.
- [ ] Rsync the monorepo to the VPS from the repo root. Two passes:
  the first syncs the whole tree without `--delete` (so VPS-only state
  is preserved); the second prunes stale hashed JS/CSS bundles in
  `frontend/static/chat/assets/`, which Vite re-hashes on every build
  and would otherwise accumulate. Run from the repo root so `shared/`
  ships alongside `frontend/`; `backend/docker-compose.yml` references
  `../shared/config/contributors.yml`.
  ```bash
  rsync -avz --exclude '.env' --exclude '.git' --exclude 'node_modules' \
    ./ <admin>@<vps-ip>:~/munin/
  rsync -avz --delete \
    frontend/static/chat/assets/ \
    <admin>@<vps-ip>:~/munin/frontend/static/chat/assets/
  ```
- [ ] On the VPS: `cd ~/munin/frontend` (post-monorepo,
  `docker-compose.yml` lives under `frontend/`, not the repo root).

## 5. Secrets and seed lists

- [ ] On the VPS, copy the env template:
  ```bash
  cd ~/munin/frontend
  cp .env.template .env
  chmod 600 .env
  ```
- [ ] Edit `frontend/.env`. Required:
  - `AUTH_SECRET_KEY`: paste the value you generated in
    SETUP-PREREQUISITES (or `openssl rand -hex 32` if you skipped it).
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
    `SMTP_SENDER`: from your SMTP provider.
  - `ADMIN_EMAILS`: comma-separated list of admin email addresses.
  - `ADMIN_INGEST_TOKEN`: `openssl rand -hex 32`. Must match the
    value in the cluster's `cluster.env`.
  - `KB_GATE_TOKEN`: `openssl rand -hex 32`. **Must match the same
    key in the cluster's `cluster.env`.** Originally VPS-only (gates
    KB uploads via the tusd pre-create hook); since 2026-06-02 the
    cluster also uses it to call `/admin/check-role` for the in-house
    Metrics dashboard. Generate once on either side, copy the exact
    value to the other.
  - `CONTRIBUTORS_SYNC_TOKEN`: `openssl rand -hex 32`. Must match
    the value in the cluster's `cluster.env`; lets the cluster pull
    the regenerated `contributors.yaml` from `/admin/contributors.yaml`.
- [ ] Copy `auth/whitelist.csv.example` to `auth/whitelist.csv` and edit it
  (the real file is gitignored), and do the same for
  `../shared/config/contributors.yml.example` and `config/quotas.yml.example`.
  One row per seed user, format
  `email,name,role`. This file is now a **first-boot seed only**: the
  auth DB inside the `auth_data` volume is the source of truth after
  first start. To add / edit / remove users after deploy, sign in as
  an admin and use the Admin panel "Users" tab in the chat UI. The
  CSV is re-read on container restart in an additive-only mode (new
  emails imported, existing rows never overwritten).

## 6. Caddy domain

- [ ] Edit `frontend/caddy/Caddyfile`: replace every occurrence of `muninai.org` with your domain. Be thorough; the file has many references.

## 7. Start the stack

- [ ] On the VPS:
  ```bash
  cd ~/munin/frontend
  docker compose up -d --build
  ```
- [ ] Watch logs while Caddy issues certificates:
  ```bash
  docker compose logs -f caddy
  ```
  Issuance can take 1 to 2 minutes per subdomain on first start.
- [ ] When all certificates are issued, `docker compose ps` should
  show `caddy`, `munin-auth`, `api-gateway`, `tusd`, and `hook-service`
  all running (compose abbreviates the per-service container names;
  the full names include the compose project prefix, e.g.
  `frontend-munin-auth-1`).

## 8. Tunnel handshake

The cluster will SSH to the VPS as the `tunnel` user. The bootstrap script created `tunnel` with no password and a forced command (no shell). To allow the cluster's autossh to connect:

- [ ] Get the cluster head's tunnel public key (created in SETUP-CLUSTER.md step 8): `/root/.ssh/munin_tunnel.pub`.
- [ ] On the VPS, append it to `~tunnel/.ssh/authorized_keys`, prefixed with the restrictive options. The exact line is documented in [`docs/install/tunnel.md`](../install/tunnel.md).
- [ ] From the cluster, test:
  ```bash
  ssh -i /root/.ssh/munin_tunnel -N tunnel@<vps-ip>
  ```
  It should connect silently. Ctrl-C to exit.
- [ ] Return to [SETUP-CLUSTER.md](SETUP-CLUSTER.md) §8 and finish enabling `munin-tunnel` if you have not already.
- [ ] Once enabled, on the VPS verify the listener exists:
  ```bash
  ss -tlnp | grep 18080
  ```

## 9. First login

- [ ] Visit `https://chat.<your-domain>` in a browser. You should be redirected to `https://auth.<your-domain>/login`.
- [ ] Enter a whitelisted email. You should receive a 6-digit OTP via SMTP.
- [ ] Enter the OTP. You should be redirected back to the chat UI and see the welcome screen.

If the login email never arrives, see [SETUP-VERIFY.md](SETUP-VERIFY.md) § "If something fails".

When you can log in, continue with [SETUP-VERIFY.md](SETUP-VERIFY.md) for end-to-end checks.
