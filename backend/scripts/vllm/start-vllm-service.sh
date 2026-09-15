#!/bin/bash
#SBATCH --job-name=vllm-service
#SBATCH --partition=vllm-serving
#SBATCH --gres=gpu:vllm:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=16G
#SBATCH --time=20:00:00
#SBATCH --output=/opt/munin/logs/vllm-service-%j.out
#SBATCH --error=/opt/munin/logs/vllm-service-%j.err

# ==============================================================================
# MUNIN VLLM SERVICE - SLURM JOB SCRIPT
# ==============================================================================
# Runs vLLM serving on GPU 1 for the Munin backend.
# Model: whatever /opt/munin/config/active-model.env names (deploy.sh model
#        activate <slug>; profiles in config/models/). Qwen3.8-27B since 2026-08-26.
# Personas: Chat (general), Code (programming), Research (academic)
# Scheduled daily 6am - 2am via cron (or 24/7 via: vllm-service enable-24x7)
#
# GPU allocation (hugin gres.conf):
#   - We claim the WHOLE GPU 1 via gpu:vllm:1. On Qwen3.8 the 27B AWQ-INT4
#     model (21 GB) plus 64k KV cache needs ~28 GB VRAM, which fills the
#     RTX 5090. Qwen3.8 has 3.2x the per-token KV of the old MoE (16
#     full-attention layers x 4 KV heads x head_dim 256 = 32 KB/token at
#     fp8), so the 2 GB saved on weights did NOT translate into headroom.
#     Check `GPU KV cache size` in this job's .out after any profile change;
#     the profile's VLLM_MAX_NUM_SEQS_SINGLE must be covered by the pool.
#   - While this job holds gpu:vllm:1, the 8 cooperative shards on GPU 1
#     (shard:vllm:N) are unavailable. Whole GPU 0 (gpu:batch:1) and its
#     8 shards (shard:batch:N) remain free for user / deepresearch jobs.
#   - If we ever shrink vLLM's footprint, the equivalent partial claim
#     would be `#SBATCH --gres=shard:vllm:N` (N out of 8, ~4 GB each).
#   - CPU: 12 cores. MEASURED 2026-08-27 at the old allocation of 4:
#     vLLM ran at 374-387% of its 400% cap under 8 concurrent requests,
#     i.e. 94-97% saturated, so CPU was a real ceiling and not just
#     bookkeeping. Raising this REQUIRES the partition cap to move too
#     (`MaxCPUsPerNode` on vllm-serving was 4); the job pends forever
#     otherwise. Note 12 of the partition's 20 CPUs are held whenever
#     vLLM runs, so batch partitions are squeezed accordingly.
#     Re-measure after changing: the baseline to beat is 604 tok/s
#     aggregate at 8 concurrent, 108 tok/s single-stream.
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# Model profile. Everything model-specific (checkpoint, served name, parsers,
# quantization, thinking mode, sampling) comes from the ACTIVE profile written
# by `deploy.sh model activate <slug>` from config/models/<slug>.env. SLURM
# copies this script into its spool dir, so look for the loader in the install
# location first, then beside the script for a direct repo-side run.
# ------------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _cand in /opt/cluster/scripts/llm/model-env.sh "$_SCRIPT_DIR/model-env.sh"; do
    if [ -f "$_cand" ]; then source "$_cand"; break; fi
done
if ! declare -F munin_load_model_env > /dev/null; then
    echo "[ERROR] model-env.sh not found in /opt/cluster/scripts/llm nor $_SCRIPT_DIR"
    echo "        Run: sudo ./deploy.sh vllm"
    exit 1
fi
# MUNIN_MODEL_PROFILE lets an operator start ONE job on a non-active profile
# (a test start); the persisted default is the active profile.
munin_load_model_env "${MUNIN_MODEL_PROFILE:-}" || exit 1

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
echo "Model: $MODEL_NAME"
echo "  - $MODEL_DESC"
echo "  - ${VLLM_MAX_MODEL_LEN} context window, ${VLLM_MAX_NUM_SEQS_SINGLE} concurrent requests"
echo "  - parsers: tool=$VLLM_TOOL_PARSER reasoning=$VLLM_REASONING_PARSER"
echo "  - thinking mode: $LLM_THINKING_MODE  effort: ${LLM_REASONING_EFFORT:-none}"
echo "  - profile: $MODEL_PROFILE_FILE"
echo ""
echo "Personas (synced automatically from persona definitions):"
echo "  - Meitner  : Chat — general assistant (day-to-day, writing, web + paper search)"
echo "  - Turing   : Code — programming assistant (scientific computing, debugging)"
echo "  - Curie    : Research — research assistant (deep literature search, citations)"
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
    echo "Model not found locally. Downloading $MODEL_ID from HuggingFace..."
    _excl=""
    for _g in $HF_EXCLUDE; do _excl="$_excl --exclude $_g"; done
    # shellcheck disable=SC2086
    hf download "$MODEL_ID" --local-dir "$MODEL_PATH" $_excl
    echo "[OK] Model downloaded to $MODEL_PATH"
fi

# ------------------------------------------------------------------------------
# Pin the backend context window to the window this job serves (the profile's
# VLLM_MAX_MODEL_LEN, 65536 for every committed number). The retrieval container
# reads these via compose ${VLLM_MAX_MODEL_LEN}/${VLLM_MAX_CONTEXT} substitution
# at create time, so exporting them here (before `docker compose up`) makes the
# backend trim to the window this mode actually serves. The model variables
# (VLLM_MODEL_NAME, LLM_THINKING_MODE, SAMPLING_*, ...) were exported by
# munin_load_model_env above and travel the same way.
# 65536 -> 60000 keeps the historical trim ceiling exactly.
# ------------------------------------------------------------------------------
export VLLM_MAX_MODEL_LEN
if [ "$VLLM_MAX_MODEL_LEN" = "65536" ]; then
    export VLLM_MAX_CONTEXT=60000
else
    export VLLM_MAX_CONTEXT=$((VLLM_MAX_MODEL_LEN - 5536))
fi

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
# Start vLLM server on the active profile
# ------------------------------------------------------------------------------
echo ""
echo "Starting vLLM server ($MODEL_NAME from $MODEL_PATH)..."

# shellcheck disable=SC2086  # VLLM_QUANT_ARGS / VLLM_EXTRA_ARGS are word lists
vllm serve "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port $VLLM_PORT \
    --gpu-memory-utilization $VLLM_GPU_MEM_UTIL \
    --max-model-len $VLLM_MAX_MODEL_LEN \
    --max-num-seqs $VLLM_MAX_NUM_SEQS_SINGLE \
    $VLLM_QUANT_ARGS \
    --kv-cache-dtype $VLLM_KV_CACHE_DTYPE \
    --served-model-name "$MODEL_NAME" \
    --enable-auto-tool-choice \
    --tool-call-parser $VLLM_TOOL_PARSER \
    --reasoning-parser $VLLM_REASONING_PARSER \
    $VLLM_EXTRA_ARGS &

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
        echo "[OK] $MODEL_NAME is ready!"
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
echo "  - $MODEL_NAME : $MODEL_DESC"
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
# Post-launch assertion: confirm the backend actually budgets against this
# window (64k). Catches the case where vLLM is correct but the retrieval
# container is on a stale VLLM_MAX_MODEL_LEN, silently clamping long answers.
# Non-fatal (warns loudly; never kills the live service).
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
    "$CHECK_WINDOW" "$VLLM_MAX_MODEL_LEN" || true
else
    echo "[WARN] check-context-window.sh not found in /opt/cluster/scripts/llm"
    echo "       nor $SCRIPT_DIR; skipping window assertion"
fi

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
