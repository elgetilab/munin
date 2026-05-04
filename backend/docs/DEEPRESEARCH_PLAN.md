# Deep Research Feature - Implementation Plan

## Goal
Add a "Deep Research" page to Munin where users can submit research questions that run as SLURM jobs using the MiroThinker-v1.5-30B model, with results available as Markdown and PDF.

## Architecture

```
[Browser] → [Cloudflare Access] → [Cloudflare Tunnel]
                                        ↓
                    [Docker: Retrieval Service (FastAPI)]
                              ↓ writes JSON
               /opt/munin/deepresearch/queue/
                              ↓
              [Host: deepresearch-daemon.service]
                              ↓ sbatch
              [SLURM: llm-batch partition, GPU 0]
                              ↓ writes output
               /opt/munin/deepresearch/jobs/{id}/
```

## Key Design Decisions

1. **File-based job queue** - Container writes request JSON, host daemon submits to SLURM
2. **JSON storage per job** - Simple, debuggable, can add email later
3. **Auto-refresh UI** - Poll status every 10 seconds
4. **Show cluster queue** - Users see all SLURM jobs to understand wait times
5. **MD + PDF output** - pandoc for PDF generation

## Configuration
- **Model**: `cyankiwi/MiroThinker-v1.5-30B-AWQ-4bit`
- **Partition**: llm-batch (GPU 0 batch type)
- **Timeout**: 30 minutes
- **Rate limits**: None (Cloudflare Access handles auth)

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `phase2-munin/retrieval/main.py` | Add 5 deepresearch API endpoints |
| `phase2-munin/retrieval/static/deepresearch.html` | Create new page |
| `phase2-munin/docker-compose.yml` | Add volume mounts |
| `scripts/llm/deepresearch-job.sh` | SLURM job script |
| `scripts/llm/deepresearch-daemon.py` | Host daemon |
| `config/deepresearch-daemon.service` | Systemd service |

---

## Implementation Steps

### Phase 1: Create Directory Structure

```bash
sudo mkdir -p /opt/munin/deepresearch/{queue,jobs}
sudo chown -R root:docker /opt/munin/deepresearch
sudo chmod 775 /opt/munin/deepresearch/queue
```

### Phase 2: Add API Endpoints to Retrieval Service

**File: `phase2-munin/retrieval/main.py`**

Add these endpoints:
- `POST /deepresearch/submit` - Submit new job (writes to queue dir)
- `GET /deepresearch/status/{request_id}` - Get job status
- `GET /deepresearch/output/{request_id}` - Get MD or PDF output
- `GET /deepresearch/queue` - Get SLURM queue status (reads status file)
- `GET /deepresearch` - Serve deepresearch.html

Environment variables to add:
```python
DEEPRESEARCH_QUEUE_DIR = os.getenv("DEEPRESEARCH_QUEUE_DIR", "/deepresearch/queue")
DEEPRESEARCH_JOBS_DIR = os.getenv("DEEPRESEARCH_JOBS_DIR", "/deepresearch/jobs")
SLURM_QUEUE_FILE = os.getenv("SLURM_QUEUE_FILE", "/deepresearch/slurm_queue.json")
```

### Phase 3: Create Frontend Page

**File: `phase2-munin/retrieval/static/deepresearch.html`**

Key features:
- Same dark theme as search.html
- Textarea for research question
- Submit button → calls `/deepresearch/submit`
- Status card showing: queued → pending → running → completed
- SLURM queue panel (auto-refresh every 30s)
- Output tabs: Markdown preview + PDF download
- Auto-refresh status every 10s while job pending/running

### Phase 4: Create SLURM Job Script

**File: `scripts/llm/deepresearch-job.sh`**

```bash
#SBATCH --job-name=deepresearch
#SBATCH --partition=llm-batch
#SBATCH --gres=gpu:batch:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
```

Flow:
1. Read question from `$JOB_DIR/input.txt`
2. Run vLLM inference with MiroThinker model
3. Write output to `$JOB_DIR/output.md`
4. Generate PDF with pandoc → `$JOB_DIR/output.pdf`
5. Update `$JOB_DIR/status.json`

### Phase 5: Create Host Daemon

**File: `scripts/llm/deepresearch-daemon.py`**

Daemon loop (every 5 seconds):
1. Check `/opt/munin/deepresearch/queue/` for new `.json` files
2. For each request:
   - Create job directory in `/opt/munin/deepresearch/jobs/{request_id}/`
   - Write `input.txt` with question
   - Submit to SLURM: `sbatch --parsable deepresearch-job.sh`
   - Write initial `status.json`
   - Delete request from queue
3. Update status of pending/running jobs (query squeue)
4. Every 30s: Update `/opt/munin/deepresearch/slurm_queue.json` with full queue

### Phase 6: Create Systemd Service

**File: `config/deepresearch-daemon.service`**

```ini
[Unit]
Description=Deep Research Queue Daemon
After=slurmd.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/cluster/scripts/llm/deepresearch-daemon.py
Restart=always

[Install]
WantedBy=multi-user.target
```

### Phase 7: Update Docker Compose

**File: `phase2-munin/docker-compose.yml`**

Add to retrieval service:
```yaml
volumes:
  - /opt/munin/deepresearch/queue:/deepresearch/queue:rw
  - /opt/munin/deepresearch/jobs:/deepresearch/jobs:ro
  - /opt/munin/deepresearch/slurm_queue.json:/deepresearch/slurm_queue.json:ro
environment:
  - DEEPRESEARCH_QUEUE_DIR=/deepresearch/queue
  - DEEPRESEARCH_JOBS_DIR=/deepresearch/jobs
  - SLURM_QUEUE_FILE=/deepresearch/slurm_queue.json
```

### Phase 8: Install PDF Dependencies

```bash
sudo apt-get install pandoc texlive-xetex texlive-fonts-recommended
```

### Phase 9: Deploy

```bash
# Copy scripts
sudo cp scripts/llm/deepresearch-*.{sh,py} /opt/cluster/scripts/llm/
sudo chmod +x /opt/cluster/scripts/llm/deepresearch-*

# Install systemd service
sudo cp config/deepresearch-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepresearch-daemon

# Rebuild retrieval container
cd phase2-munin && ./deploy.sh
```

---

## Job Storage Structure

```
/opt/munin/deepresearch/
├── queue/                    # Pending requests (JSON)
│   └── {uuid}.json
├── jobs/                     # Job data
│   └── {request_id}/
│       ├── request.json      # Original request
│       ├── status.json       # Current status
│       ├── input.txt         # Question for SLURM
│       ├── output.md         # Generated report
│       └── output.pdf        # PDF version
└── slurm_queue.json          # Current SLURM queue state
```

**status.json schema:**
```json
{
  "request_id": "uuid",
  "slurm_job_id": "12345",
  "status": "pending|running|completed|failed",
  "question": "...",
  "submitted_at": "ISO timestamp",
  "started_at": "ISO timestamp",
  "completed_at": "ISO timestamp",
  "queue_position": 2,
  "error": null
}
```

---

## Verification

1. **Submit job**: Enter question, click submit, see "queued" status
2. **Queue pickup**: Daemon moves request to jobs dir, submits to SLURM
3. **Status updates**: Page shows pending → running → completed
4. **SLURM queue**: Panel shows all cluster jobs
5. **Output**: Markdown renders, PDF downloads
6. **Timeout**: Job fails gracefully after 30 minutes

---

## Security

- Container cannot run SLURM commands directly (file-based handoff)
- Queue dir writable by container, jobs dir read-only
- Input validation: max 10,000 characters
- Cloudflare Access handles authentication
