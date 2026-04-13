# Hugin/Munin Cluster Services

This document describes all services running on the cluster and how to manage them.

## Service Overview

| Service | Type | Port | Description |
|---------|------|------|-------------|
| slurmctld | Systemd | - | SLURM controller daemon |
| slurmd | Systemd | - | SLURM compute node daemon |
| deepresearch-daemon | Systemd | - | Deep Research job queue processor |
| vllm-service | SLURM Job | 8000 | vLLM inference server (GPU 1) |
| Open WebUI | Docker | 3000 | Chat interface |
| Qdrant | Docker | 6333 | Vector database |
| Neo4j | Docker | 7474, 7687 | Graph database |
| Grobid | Docker | 8070 | PDF parsing |
| SearXNG | Docker | 8888 | Web search |
| Retrieval | Docker | 8080 | Paper search API + Deep Research API |
| Cloudflared | Docker | - | Cloudflare Tunnel |

---

## Starting the Cluster

### 1. Start SLURM Services

SLURM manages compute resources and job scheduling.

```bash
# Start SLURM controller (run once on head node)
sudo systemctl start slurmctld

# Start SLURM compute daemon
sudo systemctl start slurmd

# Check status
sudo systemctl status slurmctld slurmd

# Resume node if in drain state
sudo scontrol update nodename=hugin state=resume
```

**Enable on boot:**
```bash
sudo systemctl enable slurmctld slurmd
```

### 2. Start Docker Services

Docker services include databases and the retrieval API.

```bash
cd /opt/munin/docker

# Start core services (Qdrant, Neo4j, Grobid, SearXNG, Cloudflared)
docker compose up -d

# Start RAG services (includes Retrieval API)
docker compose --profile rag up -d

# Check status
docker compose ps
```

### 3. Start Deep Research Daemon

The Deep Research daemon watches for job submissions and processes them via SLURM.

```bash
# Start the daemon
sudo systemctl start deepresearch-daemon

# Enable on boot
sudo systemctl enable deepresearch-daemon

# Check status
sudo systemctl status deepresearch-daemon

# View logs
sudo journalctl -u deepresearch-daemon -f
```

### 4. Start vLLM Service (Chat)

The vLLM service runs as a SLURM job on GPU 1.

```bash
# Start vLLM service
vllm-service start
# Or: /opt/cluster/scripts/llm/schedule-vllm.sh start

# Check status
vllm-service status

# Stop vLLM service
vllm-service stop
```

The vLLM service is scheduled automatically via cron:
- Starts at 6:00 AM
- Stops at midnight
- For 24/7 operation: `vllm-service enable-24x7` (revert with `disable-24x7`)

---

## Service Details

### SLURM Controller (slurmctld)

**Config:** `/etc/slurm/slurm.conf`
**Logs:** `/var/log/slurm/slurmctld.log`

```bash
# Restart after config changes
sudo systemctl restart slurmctld

# Check configuration
scontrol show config
```

### SLURM Compute Daemon (slurmd)

**Config:** `/etc/slurm/slurm.conf`
**Logs:** `/var/log/slurm/slurmd.log`

```bash
# Restart
sudo systemctl restart slurmd

# Check node state
sinfo -N
scontrol show node hugin
```

### Deep Research Daemon

**Service:** `deepresearch-daemon.service`
**Script:** `/opt/cluster/scripts/llm/deepresearch-daemon.py`
**Model:** MiroThinker-v1.5-30B (`/opt/munin/data/models/mirothinker-v1.5-30b`)
**Directories:**
- Queue: `/opt/munin/deepresearch/queue/`
- Jobs: `/opt/munin/deepresearch/jobs/`
- SLURM status: `/opt/munin/deepresearch/slurm_queue.json`

The daemon:
1. Watches `/opt/munin/deepresearch/queue/` for new job requests (JSON files)
2. Submits jobs to SLURM using `deepresearch-job.sh`
3. Updates job status by querying SLURM
4. Updates `/opt/munin/deepresearch/slurm_queue.json` every 30 seconds

```bash
# Manual run (for debugging)
sudo python3 /opt/cluster/scripts/llm/deepresearch-daemon.py

# Check SLURM queue file
cat /opt/munin/deepresearch/slurm_queue.json | jq
```

### vLLM Service

**SLURM Job:** `vllm-service`
**Partition:** `vllm-serving` (GPU 1)
**Script:** `/opt/cluster/scripts/llm/start-vllm-service.sh`

The vLLM service:
- Runs on GPU 1 (vllm type in gres.conf)
- Starts Open WebUI container
- Writes status to `/opt/munin/logs/`

```bash
# Check if running
squeue -n vllm-service

# View logs
cat /opt/munin/logs/vllm-service-*.out

# Force stop
scancel $(squeue -n vllm-service -h -o %i)
```

### Retrieval Service (Docker)

**Container:** `munin-retrieval`
**Port:** 8080
**Endpoints:**
- `/` - Paper Search UI
- `/deepresearch` - Deep Research UI
- `/retrieve` - RAG API
- `/deepresearch/submit` - Submit research job
- `/docs` - API documentation

```bash
# Restart
cd /opt/munin/docker
docker compose --profile rag restart retrieval

# View logs
docker logs munin-retrieval -f

# Rebuild after code changes
docker compose --profile rag build retrieval
docker compose --profile rag up -d retrieval
```

---

## Scheduling

### Automatic vLLM Scheduling

The vLLM service is scheduled via cron at `/etc/cron.d/hugin-cluster`:

```cron
# Start vLLM at 6 AM
0 6 * * * root /opt/cluster/scripts/llm/schedule-vllm.sh start >> /var/log/cluster-admin/vllm-schedule.log 2>&1

# Stop vLLM at 2 AM (frees GPU for batch jobs)
0 2 * * * root /opt/cluster/scripts/llm/schedule-vllm.sh stop >> /var/log/cluster-admin/vllm-schedule.log 2>&1
```

To temporarily run 24/7 (disables cron start/stop):
```bash
vllm-service enable-24x7
```

To restore the normal schedule:
```bash
vllm-service disable-24x7
```

### Auto-Resume After Reboot

The SLURM node is automatically resumed after reboot via systemd:

**Service:** `slurm-auto-resume.service`

```bash
# Check status
sudo systemctl status slurm-auto-resume

# Enable
sudo systemctl enable slurm-auto-resume
```

---

## Startup Order

For a full cluster startup, services should be started in this order:

1. **SLURM** (required for job scheduling)
   ```bash
   sudo systemctl start slurmctld slurmd
   sudo scontrol update nodename=hugin state=resume
   ```

2. **Docker services** (databases and APIs)
   ```bash
   cd /opt/munin/docker
   docker compose up -d
   docker compose --profile rag up -d
   ```

3. **Deep Research daemon** (job processor)
   ```bash
   sudo systemctl start deepresearch-daemon
   ```

4. **vLLM service** (optional - for chat)
   ```bash
   vllm-service start
   ```

### Quick Start Script

Create `/opt/cluster/scripts/admin/start-all.sh`:

```bash
#!/bin/bash
echo "Starting Hugin/Munin cluster..."

# SLURM
sudo systemctl start slurmctld slurmd
sleep 2
sudo scontrol update nodename=hugin state=resume

# Docker
cd /opt/munin/docker
docker compose up -d
docker compose --profile rag up -d

# Deep Research daemon
sudo systemctl start deepresearch-daemon

echo "Cluster started. vLLM service not started (use: vllm-service start)"
```

---

## Troubleshooting

### SLURM Node in DRAIN State

```bash
# Check reason
scontrol show node hugin | grep -i reason

# Resume
sudo scontrol update nodename=hugin state=resume
```

### Deep Research Jobs Not Processing

1. Check daemon is running:
   ```bash
   sudo systemctl status deepresearch-daemon
   ```

2. Check for queued requests:
   ```bash
   ls /opt/munin/deepresearch/queue/
   ```

3. Check SLURM queue:
   ```bash
   squeue -p llm-batch
   ```

4. Check job logs:
   ```bash
   ls /opt/munin/deepresearch/jobs/
   cat /opt/munin/deepresearch/jobs/*/status.json
   ```

### Docker Container Not Starting

```bash
# Check logs
docker logs munin-retrieval

# Check if port is in use
sudo netstat -tlnp | grep 8080

# Restart
cd /opt/munin/docker
docker compose --profile rag restart retrieval
```

### vLLM Service Fails to Start

1. Check if GPU is available:
   ```bash
   nvidia-smi
   ```

2. Check SLURM partition:
   ```bash
   sinfo -p vllm-serving
   ```

3. Check job output:
   ```bash
   cat /opt/munin/logs/vllm-service-*.out
   ```

---

## Cluster User for Deep Research

**No separate cluster user is required.** The Deep Research daemon runs as root and submits SLURM jobs under the root user. The jobs use the `llm-batch` partition which is available to all users.

If you want to isolate Deep Research jobs:

1. Create a dedicated user:
   ```bash
   sudo useradd -r -s /bin/false munin-research
   ```

2. Update the systemd service:
   ```ini
   # In /etc/systemd/system/deepresearch-daemon.service
   User=munin-research
   ```

3. Update directory permissions:
   ```bash
   sudo chown -R munin-research:docker /opt/munin/deepresearch
   ```

4. Grant SLURM access:
   ```bash
   # No additional config needed - SLURM allows all local users by default
   ```

---

## Ports Summary

| Port | Service | Access |
|------|---------|--------|
| 3000 | Open WebUI | Cloudflare Tunnel |
| 6333 | Qdrant | localhost only |
| 6334 | Qdrant gRPC | localhost only |
| 7474 | Neo4j Browser | localhost only |
| 7687 | Neo4j Bolt | localhost only |
| 8000 | vLLM API | localhost only |
| 8070 | Grobid | localhost only |
| 8080 | Retrieval API | Cloudflare Tunnel |
| 8888 | SearXNG | localhost only |

Public access is managed through Cloudflare Tunnel:
- `chat.muninai.org` → localhost:3000
- `search.muninai.org` → localhost:8080
