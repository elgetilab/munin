#!/bin/bash
#SBATCH --job-name=vllm-inst
#SBATCH --partition=standard
#SBATCH --gres=gpu:batch:1
#SBATCH --cpus-per-task=6
#SBATCH --mem=16G
#SBATCH --time=2-00:00:00
#SBATCH --output=/opt/munin/logs/vllm-inst-%x-%j.out
#SBATCH --error=/opt/munin/logs/vllm-inst-%x-%j.err

# ==============================================================================
# MUNIN vLLM SECONDARY INSTANCE - SLURM JOB SCRIPT
# ==============================================================================
# Serves ONE backbone profile on a port other than production's, for an
# evaluation (or a second model beside production). Submitted by
# `deploy.sh instance up <slug> ...`, never by the production scheduler:
#   - the job name is vllm-inst-<name>, which schedule-vllm.sh does not match,
#     so the 02:00 stop and 06:00 start leave it alone;
#   - it claims GPU 0 (gpu:batch:1), the card production leaves free on the
#     single-GPU profile. It cannot coexist with the TP=2 profile;
#   - it writes NONE of the production status files (current_job.txt,
#     service_status.txt, vllm_profile) and does not touch docker compose;
#     the retrieval container that talks to it is created by deploy.sh.
#
# Parameters (environment, exported by `deploy.sh instance up` via sbatch
# --export; every one has a default so a hand `sbatch` works too):
#   MUNIN_MODEL_PROFILE   profile file or slug (required in practice)
#   INSTANCE_NAME         short name, becomes part of log names (default: eval)
#   INSTANCE_PORT         vLLM port (default: 8001)
#   INSTANCE_GPU_UTIL     --gpu-memory-utilization (default: the profile's)
#   INSTANCE_MAX_NUM_SEQS --max-num-seqs (default: the profile's single-GPU cap)
#   INSTANCE_STATE_DIR    where health/state files go (default: /opt/munin/instances/<name>)
#
# Compared with start-vllm-service.sh this script is deliberately thin: no
# retrieval container management, no window assertion (deploy.sh runs the
# gates against the instance's own API port), no cron interplay.
# ==============================================================================

set -e

INSTANCE_NAME=${INSTANCE_NAME:-eval}
INSTANCE_PORT=${INSTANCE_PORT:-8001}
INSTANCE_STATE_DIR=${INSTANCE_STATE_DIR:-/opt/munin/instances/$INSTANCE_NAME}

# ------------------------------------------------------------------------------
# Model profile (see start-vllm-service.sh for why the loader is looked up in
# the install location first: SLURM runs a spool copy of this file).
# ------------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for _cand in /opt/cluster/scripts/llm/model-env.sh "$_SCRIPT_DIR/model-env.sh"; do
    if [ -f "$_cand" ]; then source "$_cand"; break; fi
done
if ! declare -F munin_load_model_env > /dev/null; then
    echo "[ERROR] model-env.sh not found; run: sudo ./deploy.sh vllm"; exit 1
fi
if [ -z "${MUNIN_MODEL_PROFILE:-}" ]; then
    echo "[ERROR] MUNIN_MODEL_PROFILE is required for an instance (a slug or profile path)"; exit 1
fi
# The instance never inherits production's tokenizer dir.
TOKENIZER_HOST_DIR=${TOKENIZER_HOST_DIR:-/opt/munin/data/models/tokenizer-$(basename "${MUNIN_MODEL_PROFILE%.env}")}
munin_load_model_env "$MUNIN_MODEL_PROFILE" || exit 1

INSTANCE_GPU_UTIL=${INSTANCE_GPU_UTIL:-$VLLM_GPU_MEM_UTIL}
INSTANCE_MAX_NUM_SEQS=${INSTANCE_MAX_NUM_SEQS:-$VLLM_MAX_NUM_SEQS_SINGLE}

if [ -f /opt/hugin/config/cluster.env ]; then
    source /opt/hugin/config/cluster.env
fi

echo "=============================================="
echo "MUNIN vLLM INSTANCE '$INSTANCE_NAME' starting"
echo "=============================================="
echo "Job ID:     $SLURM_JOB_ID"
echo "Node:       $SLURMD_NODENAME"
echo "Start Time: $(date)"
echo "GPU:        $CUDA_VISIBLE_DEVICES"
echo "Port:       $INSTANCE_PORT"
echo "=============================================="
munin_model_env_summary
echo "  gpu util    : $INSTANCE_GPU_UTIL"
echo "  max seqs    : $INSTANCE_MAX_NUM_SEQS"
echo "=============================================="

source /etc/profile.d/modules.sh
module load cuda/13.0.2
export CUDA_HOME=/opt/cuda/13.0.2
export CUDA_ROOT=/opt/cuda/13.0.2
export CUDA_PATH=/opt/cuda/13.0.2
export CUDACXX=/opt/cuda/13.0.2/bin/nvcc
export PATH=/opt/cuda/13.0.2/bin:$PATH
export LD_LIBRARY_PATH=/opt/cuda/13.0.2/lib64:$LD_LIBRARY_PATH

source /opt/munin/services/vllm/venv/bin/activate

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export NVIDIA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES}"
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export VLLM_SKIP_P2P_CHECK=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

if [ ! -f "$MODEL_PATH/config.json" ]; then
    echo "[ERROR] checkpoint missing at $MODEL_PATH (deploy.sh instance up --download fetches it)"
    exit 1
fi

mkdir -p "$INSTANCE_STATE_DIR"
echo "$SLURM_JOB_ID" > "$INSTANCE_STATE_DIR/job_id"
echo "$INSTANCE_PORT" > "$INSTANCE_STATE_DIR/vllm_port"
echo "$MODEL_NAME" > "$INSTANCE_STATE_DIR/model_name"
echo "$MODEL_PROFILE_FILE" > "$INSTANCE_STATE_DIR/profile"
echo "starting" > "$INSTANCE_STATE_DIR/status"

echo ""
echo "Starting vLLM ($MODEL_NAME from $MODEL_PATH) on :$INSTANCE_PORT ..."
# shellcheck disable=SC2086  # VLLM_QUANT_ARGS / VLLM_EXTRA_ARGS are word lists
vllm serve "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port "$INSTANCE_PORT" \
    --gpu-memory-utilization "$INSTANCE_GPU_UTIL" \
    --max-model-len "$VLLM_MAX_MODEL_LEN" \
    --max-num-seqs "$INSTANCE_MAX_NUM_SEQS" \
    $VLLM_QUANT_ARGS \
    --kv-cache-dtype "$VLLM_KV_CACHE_DTYPE" \
    --served-model-name "$MODEL_NAME" \
    --enable-auto-tool-choice \
    --tool-call-parser "$VLLM_TOOL_PARSER" \
    --reasoning-parser "$VLLM_REASONING_PARSER" \
    $VLLM_EXTRA_ARGS &
VLLM_PID=$!
echo "vLLM PID: $VLLM_PID"

TIMEOUT_SECONDS=900
INTERVAL=5
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT_SECONDS ]; do
    if ! kill -0 $VLLM_PID 2>/dev/null; then
        echo "[ERROR] vLLM process exited unexpectedly"
        echo "failed" > "$INSTANCE_STATE_DIR/status"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$INSTANCE_PORT/health" > /dev/null 2>&1; then
        echo "[OK] $MODEL_NAME is ready on :$INSTANCE_PORT"
        break
    fi
    if [ $((ELAPSED % 30)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
        echo "  Still loading... (${ELAPSED}s elapsed)"
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done
if [ $ELAPSED -ge $TIMEOUT_SECONDS ]; then
    echo "[ERROR] model failed to start within ${TIMEOUT_SECONDS}s"
    echo "failed" > "$INSTANCE_STATE_DIR/status"
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

echo "running" > "$INSTANCE_STATE_DIR/status"
date > "$INSTANCE_STATE_DIR/started_at"
echo ""
echo "Instance '$INSTANCE_NAME' LIVE: http://127.0.0.1:$INSTANCE_PORT/v1  ($MODEL_NAME)"

CLEANUP_DONE=0
cleanup() {
    [ "$CLEANUP_DONE" -eq 1 ] && return
    CLEANUP_DONE=1
    echo ""
    echo "Shutting down instance '$INSTANCE_NAME' ..."
    kill $VLLM_PID 2>/dev/null || true
    echo "offline" > "$INSTANCE_STATE_DIR/status"
    rm -f "$INSTANCE_STATE_DIR/job_id"
    echo "[OK] instance stopped."
}
trap cleanup EXIT SIGTERM SIGINT

while kill -0 $VLLM_PID 2>/dev/null; do
    sleep 30
done
echo "$(date): vLLM process exited"
