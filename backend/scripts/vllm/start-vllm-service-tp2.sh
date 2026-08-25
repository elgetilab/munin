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
# Model: Qwen3.8-27B-AWQ-INT4 (Gated DeltaNet + Gated Attention, dense, 262k native)
#
# Why TP=2 helps here:
#   - The 23 GB AWQ-4bit WEIGHTS are the limiter on a single 32 GB 5090
#     (they leave only ~6 GB for everything else, hence the 64k cap).
#   - TP=2 splits the weights across both cards (~10 GB/card), freeing room.
#
#   KV BUDGET (recomputed 2026-08-25 for Qwen3.8-27B; the old MoE numbers here
#   were NOT transferable). Qwen3.8 has 16 full-attention layers with 4 KV heads
#   at head_dim 256, which is ~34.6 KB/token fp8 MEASURED, against ~10 KB on the
#   old 35B-A3B. So KV is 3.2x dearer per token and the flags had to move:
#     single-GPU  measured : 206,802 tok ->  3.16x at 65,536  (--max-num-seqs 2)
#     TP=2        estimated : ~817,000 tok ->  6.2x at 131,072
#   `--max-num-seqs` is 4, not the 8 inherited from the MoE. 8 would be 1.29x
#   OVERSUBSCRIBED at 128k (8 x 131,072 = 1,048,576 tok against ~817,000), and 6
#   sits exactly on the arithmetic limit with no margin. 4 preserves roughly the
#   1.6x margin the MoE config had (13x available / 8 admitted).
#
#   AFTER THE FIRST START, read the real number and re-tune:
#     grep "GPU KV cache size\|Maximum concurrency" /opt/munin/logs/vllm-service-<job>.out
#   If it confirms >=6.2x at 131,072, raising to 6 is defensible. Do not raise it
#   on the estimate alone: admission is bounded by the WORST case (every sequence
#   at max-model-len), not by observed average usage, and preemption is costlier
#   here than on a pure-attention model because the Gated DeltaNet recurrent
#   state has to be recomputed rather than just re-read.
#
# ALLOCATION: claims the WHOLE of BOTH GPUs (gpu:vllm:1 + gpu:batch:1). While it
# runs, ALL shards on both cards are blocked, so NO batch/deepresearch/user job
# can schedule. Use start-vllm-service.sh (single GPU, 64k) as the default when
# GPU 0 needs to stay free.
#   - The 5090s have NO NVLink, so TP communication crosses PCIe. Fine for a
#     3B-active MoE, but prefill of very long prompts is slower than single-GPU.
#
# WHY NOT shards (to free batch capacity)? Tested 2026-06-29, CONFIRMED IMPOSSIBLE
# on this SLURM setup (ConstrainDevices=yes):
#   - A single-type shard job (e.g. --gres=shard:batch:2) runs, but its cgroup
#     confines it to ONE physical card: nvidia-smi inside the job sees only that
#     GPU. So forcing CUDA_VISIBLE_DEVICES=0,1 is blocked at the cgroup, not just
#     the env - the override cannot reach the second card.
#   - The dual-type claim that WOULD span both cards (shard:vllm:6,shard:batch:6)
#     is UNSCHEDULABLE: it pends on Reason=Resources even on a fully idle node.
#   => TP=2 needs BOTH GPUs visible, which only the whole-GPU claim
#      (gpu:vllm:1,gpu:batch:1) gives, and that blocks all shards. Freeing shards
#      under TP=2 would require a SLURM-level change (gres.conf / disabling
#      cgroup ConstrainDevices). Until then: TP=2 = both whole cards, no batch;
#      use start-vllm-service.sh (single GPU) when GPU 0 must stay free.
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
MODEL_ID="cyankiwi/Qwen3.8-27B-AWQ-INT4"
MODEL_PATH="/opt/munin/data/models/qwen3.8-27b-awq-int4"
MODEL_NAME="qwen3.8-27b"
VLLM_PORT=8000

TENSOR_PARALLEL_SIZE=2
MAX_MODEL_LEN=131072          # 128k (single-GPU script uses 65536). Native cap 262144.
BACKEND_MAX_CONTEXT=125000    # retrieval history-trim ceiling (window minus a margin)

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
echo "Model: Qwen3.8-27B-AWQ-INT4"
echo "  - Gated DeltaNet + MoE Hybrid, 35B total / 3B active, AWQ 4-bit"
echo "  - Tensor-parallel across BOTH RTX 5090s (weights sharded ~11.5 GB/card)"
echo "  - ${MAX_MODEL_LEN} context window (native 262144)"
echo "  - Reasoning enabled (generates <think> traces)"
echo ""
echo "NOTE: claims BOTH whole GPUs (all shards blocked). No batch jobs can run"
echo "      alongside. Default is start-vllm-service.sh (single-GPU, 64k)."
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
# The retrieval container reads these via compose
# ${VLLM_MAX_MODEL_LEN}/${VLLM_MAX_CONTEXT} substitution at create time, so we
# export them here (before `docker compose up`). The single-GPU script exports
# 65536 / 60000 to put the cap back.
# ------------------------------------------------------------------------------
echo ""
echo "Pinning backend context window (VLLM_MAX_MODEL_LEN=$MAX_MODEL_LEN)..."
export VLLM_MAX_MODEL_LEN="$MAX_MODEL_LEN"
export VLLM_MAX_CONTEXT="$BACKEND_MAX_CONTEXT"

# ------------------------------------------------------------------------------
# Start Retrieval Service (for knowledge base tools)
# ------------------------------------------------------------------------------
echo ""
echo "Starting Retrieval Service..."
cd /opt/munin/docker
# --force-recreate so the retrieval container is recreated with the exported
# VLLM_MAX_MODEL_LEN/VLLM_MAX_CONTEXT above; a plain `up -d` leaves an
# already-running container on its old window, silently keeping the backend
# trimming to the wrong size.
docker compose --profile rag up -d --force-recreate retrieval

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
    --gpu-memory-utilization 0.85 \
    --max-model-len $MAX_MODEL_LEN \
    --max-num-seqs 4 \
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
        echo "[OK] Qwen3.8-27B (TP=$TENSOR_PARALLEL_SIZE, ${MAX_MODEL_LEN} ctx) is ready!"
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
# Post-launch assertion: confirm the backend actually budgets against this
# window ($MAX_MODEL_LEN). Catches the case where vLLM is correct but the
# retrieval container is on a stale VLLM_MAX_MODEL_LEN, silently clamping long
# answers. Non-fatal (warns loudly; never kills the live service).
# ------------------------------------------------------------------------------
# SLURM copies the batch script into its spool directory before running it, so
# `dirname "${BASH_SOURCE[0]}"` resolves to the spool copy, NOT to the install
# directory. That silently skipped this assertion on EVERY SLURM start (checked
# jobs 919, 920, 923, 964 - all logged "not found beside this script"), which is
# exactly the class of quiet drift the assertion exists to catch. Look in the
# install location first, then beside the script for a direct repo-side run.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_WINDOW=""
for _cand in /opt/cluster/scripts/llm/check-context-window.sh \
             "$SCRIPT_DIR/check-context-window.sh"; do
    if [ -x "$_cand" ]; then CHECK_WINDOW="$_cand"; break; fi
done
if [ -n "$CHECK_WINDOW" ]; then
    "$CHECK_WINDOW" "$MAX_MODEL_LEN" || true
else
    echo "[WARN] check-context-window.sh not found in /opt/cluster/scripts/llm"
    echo "       nor $SCRIPT_DIR; skipping window assertion"
fi

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