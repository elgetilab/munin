#!/bin/bash
#SBATCH --job-name=vllm-service
#SBATCH --partition=vllm-serving
#SBATCH --gres=gpu:vllm:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=20:00:00
#SBATCH --output=/opt/munin/logs/vllm-service-%j.out
#SBATCH --error=/opt/munin/logs/vllm-service-%j.err

# ==============================================================================
# MUNIN VLLM SERVICE - SLURM JOB SCRIPT
# ==============================================================================
# Runs vLLM serving on GPU 1 for the Munin backend.
# Model: Qwen3.5-35B-A3B-AWQ-4bit (Gated DeltaNet + MoE Hybrid)
# Personas: Chat (general), Code (programming), Research (academic)
# Scheduled daily 6am - 2am via cron (or 24/7 via: vllm-service enable-24x7)
#
# GPU allocation (hugin gres.conf):
#   - We claim the WHOLE GPU 1 via gpu:vllm:1. The 35B-A3B AWQ-4bit model
#     plus 64k KV cache needs ~30 GB VRAM, which fills the RTX 5090.
#   - While this job holds gpu:vllm:1, the 8 cooperative shards on GPU 1
#     (shard:vllm:N) are unavailable. Whole GPU 0 (gpu:batch:1) and its
#     8 shards (shard:batch:N) remain free for user / deepresearch jobs.
#   - If we ever shrink vLLM's footprint, the equivalent partial claim
#     would be `#SBATCH --gres=shard:vllm:N` (N out of 8, ~4 GB each).
#   - vllm-serving partition is capped at MaxCPUsPerNode=4 by SLURM.
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
MODEL_ID="cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit"
MODEL_PATH="/opt/munin/data/models/qwen3.5-35b-a3b-awq-4bit"
MODEL_NAME="qwen3.5-35b-a3b"
VLLM_PORT=8000

COMPOSE_DIR="/opt/munin"

# Load environment
if [ -f /opt/hugin/config/cluster.env ]; then
    source /opt/hugin/config/cluster.env
fi

echo "=============================================="
echo "MUNIN vLLM Service Starting"
echo "=============================================="
echo "Job ID:     $SLURM_JOB_ID"
echo "Node:       $SLURMD_NODENAME"
echo "Start Time: $(date)"
echo "GPU:        $CUDA_VISIBLE_DEVICES"
echo "=============================================="
echo ""
echo "Model: Qwen3.5-35B-A3B-AWQ-4bit"
echo "  - Gated DeltaNet + MoE Hybrid"
echo "  - 35B total parameters, 3B active"
echo "  - AWQ 4-bit quantization"
echo "  - 64k context window (native 262k)"
echo "  - Reasoning enabled (generates <think> traces)"
echo "  - 2 concurrent requests"
echo ""
echo "Personas (synced automatically from persona definitions):"
echo "  - Meitner  : Chat — general assistant (day-to-day, writing, web + paper search)"
echo "  - Turing   : Code — programming assistant (scientific computing, debugging)"
echo "  - Curie    : Research — research assistant (deep literature search, citations)"
echo "=============================================="

# Load CUDA 13.0.2 module (required for Blackwell SM 120a)
source /etc/profile.d/modules.sh
module load 13.0.2

# Explicitly set CUDA paths to ensure JIT compilation uses CUDA 13, not /usr/bin/nvcc
export CUDA_HOME=/opt/cuda/13.0.2
export CUDA_ROOT=/opt/cuda/13.0.2
export CUDA_PATH=/opt/cuda/13.0.2
export CUDACXX=/opt/cuda/13.0.2/bin/nvcc
export PATH=/opt/cuda/13.0.2/bin:$PATH
export LD_LIBRARY_PATH=/opt/cuda/13.0.2/lib64:$LD_LIBRARY_PATH

# Verify correct nvcc is being used
echo "CUDA compiler: $(which nvcc)"
nvcc --version

# Activate vLLM environment
source /opt/munin/services/vllm/venv/bin/activate

# Set environment variables
# GPU 1 is assigned via SLURM gres type "vllm" (see gres.conf)
echo "Using SLURM-assigned GPU: ${CUDA_VISIBLE_DEVICES:-not set}"

# Fix NVML/CUDA device mapping conflict
# CUDA_DEVICE_ORDER ensures consistent ordering between NVML and CUDA
# NVIDIA_VISIBLE_DEVICES syncs container/driver visibility with CUDA
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export NVIDIA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES}"

# vLLM specific settings
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export VLLM_SKIP_P2P_CHECK=1

# Memory optimization: reduce fragmentation
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# ------------------------------------------------------------------------------
# Download model if needed
# ------------------------------------------------------------------------------
# Check if model needs to be downloaded
if [ ! -d "$MODEL_PATH" ]; then
    echo ""
    echo "Model not found locally. Downloading from HuggingFace..."
    echo "This may take a while (model is ~19GB)..."
    huggingface-cli download "$MODEL_ID" --local-dir "$MODEL_PATH"
    echo "[OK] Model downloaded to $MODEL_PATH"
fi

# ------------------------------------------------------------------------------
# Start Retrieval Service (for knowledge base tools)
# ------------------------------------------------------------------------------
echo ""
echo "Starting Retrieval Service..."
cd /opt/munin/docker
docker compose --profile rag up -d retrieval

# Wait for retrieval service to be ready
for i in {1..30}; do
    if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
        echo "[OK] Retrieval service is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[WARNING] Retrieval service not responding, continuing anyway"
    fi
    sleep 1
done

# ------------------------------------------------------------------------------
# Start vLLM server - Qwen3.5-35B-A3B-AWQ-4bit
# ------------------------------------------------------------------------------
echo ""
echo "Starting vLLM server (Qwen3.5-35B-A3B-AWQ-4bit)..."

vllm serve "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port $VLLM_PORT \
    --gpu-memory-utilization 0.90 \
    --max-model-len 65536 \
    --max-num-seqs 2 \
    --dtype float16 \
    --quantization compressed-tensors \
    --kv-cache-dtype fp8 \
    --served-model-name "$MODEL_NAME" \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3 &

VLLM_PID=$!
echo "vLLM PID: $VLLM_PID"

# Wait for model to be ready (may take 5-10 minutes on first run due to JIT compilation)
echo "Waiting for model to load..."
TIMEOUT_SECONDS=600  # 10 minutes for first-run JIT compilation
INTERVAL=5
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT_SECONDS ]; do
    # Check if vLLM process is still running
    if ! kill -0 $VLLM_PID 2>/dev/null; then
        echo "[ERROR] vLLM process exited unexpectedly"
        echo "Check logs above for error details"
        exit 1
    fi

    # Check health endpoint
    if curl -sf http://127.0.0.1:$VLLM_PORT/health > /dev/null 2>&1; then
        echo "[OK] Qwen3.5-35B-A3B-AWQ-4bit is ready!"
        break
    fi

    # Progress indicator every 30 seconds
    if [ $((ELAPSED % 30)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
        echo "  Still loading... (${ELAPSED}s elapsed, vLLM PID $VLLM_PID running)"
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $TIMEOUT_SECONDS ]; then
    echo "[ERROR] Model failed to start within ${TIMEOUT_SECONDS}s timeout"
    echo "vLLM process is still running (PID $VLLM_PID) - may need more time"
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

# ------------------------------------------------------------------------------
# Service is live
# ------------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "MUNIN vLLM Service is LIVE"
echo "=============================================="
echo ""
echo "Endpoints:"
echo "  vLLM API:    http://127.0.0.1:$VLLM_PORT/v1"
echo ""
echo "Model:"
echo "  - $MODEL_NAME : Qwen3.5-35B-A3B (MoE 35B/3B active, AWQ-4bit, 64k ctx)"
echo ""
echo "Personas (synced from persona definitions):"
echo "  - Meitner  : Chat — general assistant (day-to-day, writing, web + paper search)"
echo "  - Turing   : Code — programming assistant (scientific computing, debugging)"
echo "  - Curie    : Research — research assistant (deep literature search, citations)"
echo ""
echo "Test API:"
echo "  curl http://127.0.0.1:$VLLM_PORT/v1/models"
echo ""

# Write status files
mkdir -p /opt/munin/logs
echo "$(hostname)" > /opt/munin/logs/current_node.txt
echo "$SLURM_JOB_ID" > /opt/munin/logs/current_job.txt
echo "running" > /opt/munin/logs/service_status.txt
date > /opt/munin/logs/service_started.txt

# ------------------------------------------------------------------------------
# Cleanup function
# ------------------------------------------------------------------------------
CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1

    echo ""
    echo "=============================================="
    echo "Shutting down Munin vLLM service..."
    echo "=============================================="

    # Stop vLLM process
    echo "Stopping vLLM server..."
    kill $VLLM_PID 2>/dev/null || true

    # Update status
    echo "offline" > /opt/munin/logs/service_status.txt
    rm -f /opt/munin/logs/current_node.txt
    rm -f /opt/munin/logs/current_job.txt

    echo ""
    echo "[OK] Shutdown complete."
    echo "=============================================="
}

trap cleanup EXIT SIGTERM SIGINT

# Keep job alive - monitor vLLM
echo ""
echo "Service running. Monitoring vLLM..."

while kill -0 $VLLM_PID 2>/dev/null; do
    sleep 30
done

echo "$(date): vLLM process exited"
