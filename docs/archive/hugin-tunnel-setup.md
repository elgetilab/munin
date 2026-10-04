# Setting Up a Reverse SSH Tunnel from the Cluster to Hugin VPS

This guide describes how to create a persistent reverse SSH tunnel from the
SLURM cluster to a second VPS ("hugin"), giving hugin access to services
running on the cluster. This mirrors the existing tunnel to the Munin VPS
(`munin-tunnel.service`).

---

## Overview

The cluster has **no inbound connectivity**. To expose cluster services to
hugin, the cluster initiates an outbound SSH connection to hugin and sets up
reverse port forwards. `autossh` keeps the tunnel alive automatically.

```
Hugin VPS
    ├── 127.0.0.1:13000  ──► cluster:3000  (Open WebUI)
    ├── 127.0.0.1:18080  ──► cluster:8080  (Retrieval API)
    └── 127.0.0.1:18000  ──► cluster:8000  (vLLM — optional)
                ▲
                │  Reverse SSH tunnel (outbound from cluster)
                │
        SLURM Cluster
```

Adjust the port mappings below based on which services hugin actually needs.

---

## Step 1: Prepare Hugin VPS (run on hugin)

### 1a. Create a dedicated tunnel user

```bash
# Create a restricted user with no shell access
sudo useradd -r -m -s /usr/sbin/nologin tunnel

# Create .ssh directory
sudo mkdir -p /home/tunnel/.ssh
sudo chmod 700 /home/tunnel/.ssh
sudo chown tunnel:tunnel /home/tunnel/.ssh
```

### 1b. Lock down sshd for the tunnel user

Add the following block to `/etc/ssh/sshd_config`:

```
Match User tunnel
    AllowTcpForwarding remote
    X11Forwarding no
    AllowAgentForwarding no
    PermitTunnel no
    ForceCommand /bin/true
    GatewayPorts no
```

- `AllowTcpForwarding remote` — only reverse forwards (no outbound tunnels)
- `GatewayPorts no` — tunnel ports bind to 127.0.0.1 only (safe default)

Then reload sshd:

```bash
sudo systemctl reload sshd
```

### 1c. Configure the firewall

Ensure hugin's firewall allows inbound SSH (port 22) from the cluster's
public IP. The tunnel ports (13000, 18080, 18000) do **not** need firewall
rules since they bind to localhost only.

---

## Step 2: Create an SSH key pair (run on the cluster)

```bash
# Generate a dedicated key for this tunnel
sudo ssh-keygen -t ed25519 -f /root/.ssh/hugin_tunnel -N "" -C "hugin-tunnel"
```

Then copy the **public** key to hugin:

```bash
# Display the public key
sudo cat /root/.ssh/hugin_tunnel.pub
```

On hugin, paste it into the tunnel user's authorized_keys with restrictions:

```bash
# On hugin — paste the public key with restrictions
echo 'restrict,port-forwarding ssh-ed25519 AAAA... hugin-tunnel' \
  | sudo tee /home/tunnel/.ssh/authorized_keys
sudo chmod 600 /home/tunnel/.ssh/authorized_keys
sudo chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
```

The `restrict` prefix disables shell, agent forwarding, X11, and pty.
`port-forwarding` re-enables only port forwarding.

---

## Step 3: Test the connection (run on the cluster)

```bash
sudo ssh -i /root/.ssh/hugin_tunnel \
    -o StrictHostKeyChecking=accept-new \
    tunnel@<HUGIN_IP> \
    echo "connection works"
```

This should connect and immediately close (ForceCommand /bin/true). If it
works, proceed to the systemd service.

---

## Step 4: Create the systemd service (run on the cluster)

Create `/etc/systemd/system/hugin-tunnel.service`:

```ini
[Unit]
Description=Reverse SSH tunnel to Hugin VPS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=AUTOSSH_GATETIME=0
Environment=AUTOSSH_POLL=30

ExecStart=/usr/bin/autossh -M 0 -N \
    -o "ServerAliveInterval=15" \
    -o "ServerAliveCountMax=3" \
    -o "ExitOnForwardFailure=yes" \
    -o "StrictHostKeyChecking=accept-new" \
    -i /root/.ssh/hugin_tunnel \
    -R 127.0.0.1:13000:localhost:3000 \
    -R 127.0.0.1:18080:localhost:8080 \
    -R 127.0.0.1:18000:localhost:8000 \
    tunnel@<HUGIN_IP>

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Replace `<HUGIN_IP>` with hugin's actual public IP.**

**Port mapping reference** (remove lines you don't need):

| `-R` flag | Hugin localhost port | Cluster service |
|---|---|---|
| `-R 127.0.0.1:13000:localhost:3000` | 13000 | Open WebUI |
| `-R 127.0.0.1:18080:localhost:8080` | 18080 | Retrieval API |
| `-R 127.0.0.1:18000:localhost:8000` | 18000 | vLLM (direct) |

> **Port conflict note:** If hugin already uses ports 13000/18080/18000 for
> other purposes, change the left-hand port numbers (the hugin side) to
> something else, e.g. `-R 127.0.0.1:14000:localhost:3000`.

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hugin-tunnel.service
```

---

## Step 5: Verify (run on hugin)

```bash
# Check that the tunnel ports are listening
ss -tlnp | grep -E '13000|18080|18000'

# Test Open WebUI
curl -s http://127.0.0.1:13000 | head -20

# Test Retrieval API
curl -s http://127.0.0.1:18080/health

# Test vLLM (if forwarded)
curl -s http://127.0.0.1:18000/v1/models
```

---

## Step 6: Monitor (run on the cluster)

```bash
# Check tunnel status
systemctl status hugin-tunnel

# Follow logs
journalctl -fu hugin-tunnel
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Tunnel fails to start | `journalctl -u hugin-tunnel` for SSH errors |
| Port already in use on hugin | `ss -tlnp \| grep <port>` — change the hugin-side port |
| Tunnel connects but ports don't appear | Verify `GatewayPorts no` is set (not `clientspecified`) and the `-R` uses `127.0.0.1:` prefix |
| Tunnel drops frequently | Check network between cluster and hugin; `ServerAliveInterval` is set to 15s |
| `autossh` not installed | `sudo apt install autossh` on the cluster |

---

## Summary of files involved

| Location | File | Purpose |
|---|---|---|
| Cluster | `/root/.ssh/hugin_tunnel` | SSH private key |
| Cluster | `/etc/systemd/system/hugin-tunnel.service` | systemd unit |
| Hugin | `/home/tunnel/.ssh/authorized_keys` | Public key |
| Hugin | `/etc/ssh/sshd_config` | tunnel user restrictions |