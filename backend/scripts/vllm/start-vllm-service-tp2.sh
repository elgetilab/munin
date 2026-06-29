#!/bin/bash
#SBATCH --job-name=vllm-service-tp2
#SBATCH --partition=vllm-serving
#SBATCH --gres=gpu:vllm:1,gpu:batch:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=20:00:00
#SBATCH --output=/opt/munin/logs/vllm-service-%j.out
#SBATCH --error=/opt/munin/logs/vllm-service-%j.err

# ==============================================================================
# MUNIN VLLM SERVICE - MULTI-GPU (TENSOR-PARALLEL) SLURM JOB SCRIPT
# ==============================================================================
# Same model as the single-GPU script, but SHARDED across BOTH RTX 5090s with
# tensor parallelism (TP=2) to unlock a much larger context window.
#
# Model: Qwen3.6-35B-A3B-AWQ-4bit (Gated DeltaNet + MoE Hybrid, 262k native)
#
# Why TP=2 helps here:
#   - The 23 GB AWQ-4bit WEIGHTS are the limiter on a single 32 GB 5090
#     (they leave only ~6 GB for everything else, hence the 64k cap).
#   - TP=2 splits the weights across both cards (~11.5 GB/card), freeing
#     ~17 GB/card. The KV cache is tiny on this model (hybrid attention: only
#     10 of 40 layers are full-attention, plus 2 KV heads -> ~10 KB/token fp8),
#     so the freed VRAM goes a long way: 128k context fits with wide headroom.
#
# TRADE-OFF (read before enabling):
#   - This claims the WHOLE of BOTH GPUs (gpu:vllm:1 + gpu:batch:1). While it
#     runs, GPU 0 is NO LONGER FREE for user / deepresearch / batch-eval jobs
#     (the eval suite's overnight `run_all` on GPU 0 cannot run alongside it).
#   - Use this for big-context sessions; use start-vllm-service.sh (single GPU,
#     64k) as the default when GPU 0 needs to stay free for batch work.
#   - The 5090s have NO NVLink, so TP communication crosses PCIe. Fine for a
#     3B-active MoE, but prefill of very long prompts is slower than single-GPU.
#
# GPU allocation (hugin gres.conf): gpu:batch=/dev/nvidia0, gpu:vllm=/dev/nvidia1.
# Requesting one of each gives this job both physical cards (TP ranks 0 and 1).
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
MODEL_ID="cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"
MODEL_PATH="/opt/munin/data/models/qwen3.6-35b-a3b-awq-4bit"
MODEL_NAME="qwen3.6-35b-a3b"
VLLM_PORT=8000

TENSOR_PARALLEL_SIZE=2
MAX_MODEL_LEN=131072          # 128k (single-GPU script uses 65536). Native cap 262144.
BACKEND_MAX_CONTEXT=125000    # retrieval history-trim ceiling (window minus a margin)

ENV_FILE=/opt/munin/config/munin.env

# Load environment
if [ -f /opt/hugin/config/cluster.env ]; then
    source /opt/hugin/config/cluster.env
fi

echo "=============================================="
echo "MUNIN vLLM Service Starting (MULTI-GPU / TP=2)"
echo "=============================================="
echo "Job ID:     $SLURM_JOB_ID"
echo "Node:       $SLURMD_NODENAME"
echo "Start Time: $(date)"
echo "GPUs:       $CUDA_VISIBLE_DEVICES   (tensor-parallel-size=$TENSOR_PARALLEL_SIZE)"
echo "Context:    $MAX_MODEL_LEN tokens"
echo "=============================================="
echo ""
echo "Model: Qwen3.6-35B-A3B-AWQ-4bit"
echo "  - Gated DeltaNet + MoE Hybrid, 35B total / 3B active, AWQ 4-bit"
echo "  - Tensor-parallel across BOTH RTX 5090s (weights sharded ~11.5 GB/card)"
echo "  - ${MAX_MODEL_LEN} context window (native 262144)"
echo "  - Reasoning enabled (generates <think> traces)"
echo ""
echo "NOTE: this claims BOTH GPUs. GPU 0 is unavailable for batch jobs while"
echo "      this is running. Use start-vllm-service.sh for single-GPU (64k)."
echo "=============================================="

# Load CUDA 13.0.2 module (required for Blackwell SM 120a)
source /etc/profile.d/modules.sh
module load cuda/13.0.2

# Explicitly set CUDA paths to ensure JIT compilation uses CUDA 13, not /usr/bin/nvcc
export CUDA_HOME=/opt/cuda/13.0.2
export CUDA_ROOT=/opt/cuda/13.0.2
export CUDA_PATH=/opt/cuda/13.0.2
export CUDACXX=/opt/cuda/13.0.2/bin/nvcc
export PATH=/opt/cuda/13.0.2/bin:$PATH
export LD_LIBRARY_PATH=/opt/cuda/13.0.2/lib64:$LD_LIBRARY_PATH

echo "CUDA compiler: $(which nvcc)"
nvcc --version

# Activate vLLM environment
source /opt/munin/services/vllm/venv/bin/activate

echo "Using SLURM-assigned GPUs: ${CUDA_VISIBLE_DEVICES:-not set}"

# Fix NVML/CUDA device mapping conflict; keep ordering consistent across both cards
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export NVIDIA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES}"

# vLLM / tensor-parallel settings
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export VLLM_SKIP_P2P_CHECK=1
# No NVLink on consumer 5090s: let NCCL fall back to PCIe/shared-memory transport
# instead of failing a P2P probe. (SKIP_P2P_CHECK above already relaxes vLLM's own
# check; NCCL_P2P_LEVEL=PXB keeps GPU-GPU traffic on PCIe.)
export NCCL_P2P_LEVEL=PXB

# Memory optimization: reduce fragmentation
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# ------------------------------------------------------------------------------
# Download model if needed
# ------------------------------------------------------------------------------
if [ ! -d "$MODEL_PATH" ]; then
    echo ""
    echo "Model not found locally. Downloading from HuggingFace (~23 GB)..."
    hf download "$MODEL_ID" --local-dir "$MODEL_PATH"
    echo "[OK] Model downloaded to $MODEL_PATH"
fi

# ------------------------------------------------------------------------------
# Pin the backend context window to match this mode, so the retrieval service
# actually uses the larger window (otherwise it keeps trimming prompts to 64k).
# The single-GPU script pins these back to 65536 / 60000. (Best-effort; warns
# rather than failing the job if munin.env is not writable.)
# ------------------------------------------------------------------------------
pin_env() {  # pin_env KEY VALUE
    local key="$1" val="$2"
    if [ ! -w "$ENV_FILE" ]; then
        echo "[WARN] $ENV_FILE not writable; set ${key}=${val} manually so the"
        echo "       backend uses the ${MAX_MODEL_LEN}-token window."
        return 0
    fi
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}
echo ""
echo "Pinning backend context window (VLLM_MAX_MODEL_LEN=$MAX_MODEL_LEN)..."
pin_env VLLM_MAX_MODEL_LEN "$MAX_MODEL_LEN"
pin_env VLLM_MAX_CONTEXT "$BACKEND_MAX_CONTEXT"

# ------------------------------------------------------------------------------
# Start Retrieval Service (for knowledge base tools)
# ------------------------------------------------------------------------------
echo ""
echo "Starting Retrieval Service..."
cd /opt/munin/docker
docker compose --profile rag up -d retrieval

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
# Start vLLM server - tensor-parallel over both GPUs
# ------------------------------------------------------------------------------
echo ""
echo "Starting vLLM server (TP=$TENSOR_PARALLEL_SIZE, ${MAX_MODEL_LEN} ctx)..."

vllm serve "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port $VLLM_PORT \
    --tensor-parallel-size $TENSOR_PARALLEL_SIZE \
    --gpu-memory-utilization 0.90 \
    --max-model-len $MAX_MODEL_LEN \
    --max-num-seqs 4 \
    --dtype float16 \
    --quantization compressed-tensors \
    --kv-cache-dtype fp8 \
    --served-model-name "$MODEL_NAME" \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3 &

VLLM_PID=$!
echo "vLLM PID: $VLLM_PID"

# Wait for model to be ready (TP adds NCCL init + per-rank JIT; allow extra time)
echo "Waiting for model to load..."
TIMEOUT_SECONDS=900   # 15 minutes: TP init + first-run JIT across 2 ranks
INTERVAL=5
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT_SECONDS ]; do
    if ! kill -0 $VLLM_PID 2>/dev/null; then
        echo "[ERROR] vLLM process exited unexpectedly"
        echo "Check logs above for error details (common TP issues: NCCL/PCIe init)"
        exit 1
    fi

    if curl -sf http://127.0.0.1:$VLLM_PORT/health > /dev/null 2>&1; then
        echo "[OK] Qwen3.6-35B-A3B (TP=$TENSOR_PARALLEL_SIZE, ${MAX_MODEL_LEN} ctx) is ready!"
        break
    fi

    if [ $((ELAPSED % 30)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
        echo "  Still loading... (${ELAPSED}s elapsed, vLLM PID $VLLM_PID running)"
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $TIMEOUT_SECONDS ]; then
    echo "[ERROR] Model failed to start within ${TIMEOUT_SECONDS}s timeout"
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

# ------------------------------------------------------------------------------
# Service is live
# ------------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "MUNIN vLLM Service is LIVE (TP=$TENSOR_PARALLEL_SIZE)"
echo "=============================================="
echo "  vLLM API:   http://127.0.0.1:$VLLM_PORT/v1"
echo "  Model:      $MODEL_NAME (MoE 35B/3B, AWQ-4bit, ${MAX_MODEL_LEN} ctx, 2x RTX 5090)"
echo "=============================================="

mkdir -p /opt/munin/logs
echo "$(hostname)" > /opt/munin/logs/current_node.txt
echo "$SLURM_JOB_ID" > /opt/munin/logs/current_job.txt
echo "running" > /opt/munin/logs/service_status.txt
date > /opt/munin/logs/service_started.txt

# ------------------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------------------
CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1
    echo ""
    echo "Shutting down Munin vLLM service (TP=$TENSOR_PARALLEL_SIZE)..."
    kill $VLLM_PID 2>/dev/null || true
    echo "offline" > /opt/munin/logs/service_status.txt
    rm -f /opt/munin/logs/current_node.txt /opt/munin/logs/current_job.txt
    echo "[OK] Shutdown complete."
}
trap cleanup EXIT SIGTERM SIGINT

echo ""
echo "Service running. Monitoring vLLM..."
while kill -0 $VLLM_PID 2>/dev/null; do
    sleep 30
done
echo "$(date): vLLM process exited"