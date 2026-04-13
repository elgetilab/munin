#!/usr/bin/env python3
"""
Deep Research Daemon

Watches the queue directory for new job requests, submits them to SLURM,
and updates job status. Also periodically updates SLURM queue status for
the web UI.

Run as: systemctl start deepresearch-daemon
Or manually: python3 deepresearch-daemon.py
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Configuration
QUEUE_DIR = Path(os.getenv("DEEPRESEARCH_QUEUE_DIR", "/opt/munin/deepresearch/queue"))
JOBS_DIR = Path(os.getenv("DEEPRESEARCH_JOBS_DIR", "/opt/munin/deepresearch/jobs"))
SLURM_QUEUE_FILE = Path(os.getenv("SLURM_QUEUE_FILE", "/opt/munin/deepresearch/slurm_queue.json"))
JOB_SCRIPT = Path(os.getenv("DEEPRESEARCH_JOB_SCRIPT", "/opt/cluster/scripts/llm/deepresearch-job.sh"))

# Timing
POLL_INTERVAL = 5  # Seconds between queue checks
QUEUE_UPDATE_INTERVAL = 30  # Seconds between SLURM queue updates


def log(message: str, level: str = "INFO"):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def ensure_directories():
    """Ensure required directories exist."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure SLURM queue file parent exists
    SLURM_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)


def get_pending_requests() -> list[Path]:
    """Get list of pending job request files, sorted by modification time."""
    if not QUEUE_DIR.exists():
        return []
    return sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)


def process_request(request_file: Path):
    """Process a single job request."""
    try:
        with open(request_file) as f:
            request = json.load(f)

        request_id = request["request_id"]
        question = request["question"]
        context = request.get("context", "")
        submitted_at = request.get("submitted_at", datetime.utcnow().isoformat() + "Z")

        log(f"Processing request: {request_id}")
        log(f"  Question: {question[:80]}...")

        # Create job directory
        job_dir = JOBS_DIR / request_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Copy request to job directory
        shutil.copy(request_file, job_dir / "request.json")

        # Write input file
        input_content = question
        if context:
            input_content += f"\n\nAdditional context: {context}"

        with open(job_dir / "input.txt", "w") as f:
            f.write(input_content)

        # Check if job script exists
        if not JOB_SCRIPT.exists():
            log(f"Job script not found: {JOB_SCRIPT}", "ERROR")
            write_failed_status(job_dir, request_id, question, submitted_at,
                              f"Job script not found: {JOB_SCRIPT}")
            request_file.unlink()
            return

        # Submit SLURM job
        env = os.environ.copy()
        env["JOB_DIR"] = str(job_dir)
        env["REQUEST_ID"] = request_id

        result = subprocess.run(
            ["sbatch", "--parsable", "--export=ALL", str(JOB_SCRIPT)],
            capture_output=True,
            text=True,
            env=env
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            log(f"SLURM submission failed: {error_msg}", "ERROR")
            write_failed_status(job_dir, request_id, question, submitted_at,
                              f"SLURM submission failed: {error_msg}")
            request_file.unlink()
            return

        slurm_job_id = result.stdout.strip()
        log(f"Submitted SLURM job {slurm_job_id} for request {request_id}")

        # Write initial status
        status = {
            "request_id": request_id,
            "slurm_job_id": slurm_job_id,
            "question": question,
            "status": "pending",
            "submitted_at": submitted_at,
        }
        with open(job_dir / "status.json", "w") as f:
            json.dump(status, f, indent=2)

        # Remove from queue
        request_file.unlink()
        log(f"Job {request_id} queued as SLURM job {slurm_job_id}")

    except json.JSONDecodeError as e:
        log(f"Invalid JSON in request {request_file}: {e}", "ERROR")
        # Move invalid file aside
        request_file.rename(request_file.with_suffix(".invalid"))
    except Exception as e:
        log(f"Error processing request {request_file}: {e}", "ERROR")


def write_failed_status(job_dir: Path, request_id: str, question: str,
                       submitted_at: str, error: str):
    """Write a failed status file."""
    status = {
        "request_id": request_id,
        "question": question,
        "status": "failed",
        "error": error,
        "submitted_at": submitted_at,
    }
    with open(job_dir / "status.json", "w") as f:
        json.dump(status, f, indent=2)


def update_job_statuses():
    """Update status of all pending/running jobs from SLURM."""
    if not JOBS_DIR.exists():
        return

    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue

        status_file = job_dir / "status.json"
        if not status_file.exists():
            continue

        try:
            with open(status_file) as f:
                status = json.load(f)
        except Exception:
            continue

        # Skip completed or failed jobs
        if status.get("status") in ("completed", "failed"):
            continue

        slurm_job_id = status.get("slurm_job_id")
        if not slurm_job_id:
            continue

        # Query SLURM for job state
        result = subprocess.run(
            ["squeue", "-j", slurm_job_id, "-h", "-o", "%T %r"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(None, 1)
            slurm_state = parts[0] if parts else "UNKNOWN"

            # Map SLURM state to our status
            if slurm_state == "RUNNING":
                status["status"] = "running"
                status["slurm_state"] = slurm_state
            elif slurm_state in ("PENDING", "CONFIGURING"):
                status["status"] = "pending"
                status["slurm_state"] = slurm_state

            # Get queue position for pending jobs
            if slurm_state == "PENDING":
                queue_result = subprocess.run(
                    ["squeue", "-p", "llm-batch", "-h", "-o", "%i %T"],
                    capture_output=True,
                    text=True
                )
                if queue_result.returncode == 0:
                    position = 1
                    for line in queue_result.stdout.strip().split('\n'):
                        if not line:
                            continue
                        parts = line.split()
                        if len(parts) >= 2:
                            jid, state = parts[0], parts[1]
                            if jid == slurm_job_id:
                                status["queue_position"] = position
                                break
                            if state == "PENDING":
                                position += 1
        else:
            # Job not in queue - check if completed via sacct
            result = subprocess.run(
                ["sacct", "-j", slurm_job_id, "-n", "-o", "State", "-P"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                sacct_state = result.stdout.strip().split('\n')[0]
                if "COMPLETED" in sacct_state:
                    # Check if status.json was updated by the job itself
                    # If not, mark as completed (job may have completed but not updated status)
                    with open(status_file) as f:
                        current_status = json.load(f)
                    if current_status.get("status") not in ("completed", "failed"):
                        status["status"] = "completed"
                        status["completed_at"] = datetime.utcnow().isoformat() + "Z"
                elif "FAILED" in sacct_state or "CANCELLED" in sacct_state or "TIMEOUT" in sacct_state:
                    status["status"] = "failed"
                    status["error"] = f"SLURM job {sacct_state}"

        # Write updated status
        with open(status_file, "w") as f:
            json.dump(status, f, indent=2)


def get_gpu_info() -> list[dict]:
    """Get GPU usage information from nvidia-smi."""
    try:
        # Get GPU memory and utilization
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )

        gpus = []
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "memory_used_mb": int(parts[2]),
                        "memory_total_mb": int(parts[3]),
                        "utilization_percent": int(parts[4]) if parts[4] != "[N/A]" else 0
                    })

        # Get running processes per GPU
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )

        # Get GPU UUID to index mapping
        uuid_result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5
        )

        uuid_to_index = {}
        if uuid_result.returncode == 0:
            for line in uuid_result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 2:
                    uuid_to_index[parts[1]] = int(parts[0])

        processes_by_gpu = {i: [] for i in range(len(gpus))}
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 4:
                    gpu_uuid = parts[0]
                    gpu_idx = uuid_to_index.get(gpu_uuid, 0)
                    process_name = parts[2].split('/')[-1]  # Get just the binary name
                    processes_by_gpu.setdefault(gpu_idx, []).append({
                        "pid": parts[1],
                        "name": process_name,
                        "memory_mb": int(parts[3]) if parts[3] != "[N/A]" else 0
                    })

        # Add processes to GPU info
        for gpu in gpus:
            gpu["processes"] = processes_by_gpu.get(gpu["index"], [])

        return gpus

    except Exception as e:
        return [{"error": str(e)}]


def update_slurm_queue():
    """Update the SLURM queue status file for the web UI."""
    try:
        # Include %b for GRES (GPU allocation) info
        result = subprocess.run(
            ["squeue", "-a", "-h", "-o", "%i|%j|%u|%P|%T|%M|%N|%b"],
            capture_output=True,
            text=True,
            timeout=10
        )

        queue = []
        deepresearch_count = 0

        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|')
                if len(parts) >= 7:
                    entry = {
                        "job_id": parts[0],
                        "job_name": parts[1],
                        "user": parts[2],
                        "partition": parts[3],
                        "state": parts[4],
                        "time": parts[5],
                        "nodes": parts[6] or "-",
                        "gres": parts[7] if len(parts) > 7 else None
                    }
                    queue.append(entry)
                    if parts[1].startswith("deepresearch"):
                        deepresearch_count += 1

        # Get GPU info
        gpu_info = get_gpu_info()

        data = {
            "total_jobs": len(queue),
            "deepresearch_jobs": deepresearch_count,
            "queue": queue,
            "gpus": gpu_info,
            "updated_at": datetime.utcnow().isoformat() + "Z"
        }

        # Write atomically
        temp_file = SLURM_QUEUE_FILE.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(data, f, indent=2)
        temp_file.rename(SLURM_QUEUE_FILE)

    except subprocess.TimeoutExpired:
        log("squeue command timed out", "WARNING")
    except Exception as e:
        log(f"Error updating SLURM queue: {e}", "WARNING")


def main():
    """Main daemon loop."""
    log("=" * 60)
    log("DEEP RESEARCH DAEMON")
    log("=" * 60)
    log(f"Queue directory:  {QUEUE_DIR}")
    log(f"Jobs directory:   {JOBS_DIR}")
    log(f"Queue file:       {SLURM_QUEUE_FILE}")
    log(f"Job script:       {JOB_SCRIPT}")
    log(f"Poll interval:    {POLL_INTERVAL}s")
    log(f"Queue update:     {QUEUE_UPDATE_INTERVAL}s")
    log("=" * 60)

    ensure_directories()

    if not JOB_SCRIPT.exists():
        log(f"WARNING: Job script not found: {JOB_SCRIPT}", "WARNING")
        log("Jobs will fail until script is installed")

    last_queue_update = 0

    log("Starting main loop...")

    while True:
        try:
            # Process pending requests
            for request_file in get_pending_requests():
                process_request(request_file)

            # Update job statuses
            update_job_statuses()

            # Periodically update SLURM queue
            now = time.time()
            if now - last_queue_update >= QUEUE_UPDATE_INTERVAL:
                update_slurm_queue()
                last_queue_update = now

        except KeyboardInterrupt:
            log("Received interrupt, shutting down...")
            break
        except Exception as e:
            log(f"Error in main loop: {e}", "ERROR")

        time.sleep(POLL_INTERVAL)

    log("Daemon stopped")


if __name__ == "__main__":
    main()
