#!/bin/bash
# ==============================================================================
# MODEL PROFILE LOADER - sourced, never executed
# ==============================================================================
# Loads a backbone profile (config/models/<slug>.env, or the active copy at
# /opt/munin/config/active-model.env) into the current shell, validates that
# every required key is present, derives the paths the callers need, and
# EXPORTS the variables docker compose substitutes into the retrieval
# container. Sourced by:
#   - start-vllm-service.sh / start-vllm-service-tp2.sh (production vLLM job)
#   - start-vllm-instance.sh (a secondary instance on another GPU/port)
#   - deploy.sh (model activate, instance up, and deploy_retrieval before
#     `docker compose up`, so a routine redeploy cannot regress the model)
#
# Before 2026-09-15 the model was named in eighteen places (README "Switch LLM
# model"); a swap that missed deploy.sh's VLLM_MODEL_DIR left the retrieval
# container budgeting context with the OLD tokenizer, silently. Everything now
# derives from one file.
#
# Usage:
#   source /opt/cluster/scripts/llm/model-env.sh
#   munin_load_model_env                      # the active profile
#   munin_load_model_env /path/to/slug.env    # a specific profile
# Then: $MODEL_PATH, $MODEL_NAME, $TOKENIZER_HOST_DIR, $VLLM_QUANT_ARGS,
#       $VLLM_EXTRA_ARGS ... are set, and the compose-visible set is exported.
# ==============================================================================

MUNIN_ROOT=${MUNIN_ROOT:-/opt/munin}
MUNIN_ACTIVE_MODEL_ENV=${MUNIN_ACTIVE_MODEL_ENV:-$MUNIN_ROOT/config/active-model.env}
MUNIN_MODEL_PROFILES_DIR=${MUNIN_MODEL_PROFILES_DIR:-$MUNIN_ROOT/config/models}

# Every key a profile must define (blank values are allowed where noted in
# config/models/README.md; ABSENT keys are not).
MUNIN_MODEL_REQUIRED_KEYS="MODEL_SLUG MODEL_ID MODEL_DIR MODEL_NAME MODEL_DESC HF_EXCLUDE \
VLLM_QUANTIZATION VLLM_KV_CACHE_DTYPE VLLM_TOOL_PARSER VLLM_REASONING_PARSER VLLM_EXTRA_ARGS \
VLLM_GPU_MEM_UTIL VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS_SINGLE VLLM_MAX_NUM_SEQS_TP2 \
LLM_THINKING_MODE LLM_REASONING_EFFORT KV_KIB_PER_TOKEN SAMPLING_DEFAULT SAMPLING_CODE"

# Variables the retrieval container reads via ${VAR:-default} substitution in
# docker-compose.yml. Exported so `docker compose up` in the same shell sees them.
MUNIN_MODEL_COMPOSE_EXPORTS="VLLM_MODEL_NAME LLM_THINKING_MODE LLM_REASONING_EFFORT \
SAMPLING_DEFAULT SAMPLING_CODE VLLM_TOOL_PARSER VLLM_REASONING_PARSER VLLM_EXTRA_ARGS \
VLLM_MAX_MODEL_LEN TOKENIZER_HOST_DIR"

munin_model_env_file() {
    # Resolve an argument to a profile path: a path, or a slug under the
    # installed profiles dir, or (no argument) the active profile.
    local arg=${1:-}
    if [ -z "$arg" ]; then
        echo "$MUNIN_ACTIVE_MODEL_ENV"
    elif [ -f "$arg" ]; then
        echo "$arg"
    elif [ -f "$MUNIN_MODEL_PROFILES_DIR/$arg.env" ]; then
        echo "$MUNIN_MODEL_PROFILES_DIR/$arg.env"
    else
        echo "$arg"   # let the caller report the missing file
    fi
}

munin_validate_model_env() {
    # $1 = profile file. Sources it in a subshell and reports missing keys.
    # Returns non-zero on any problem; prints one line per problem.
    local file=$1 rc=0 key
    if [ ! -f "$file" ]; then
        echo "[model-env] profile not found: $file"; return 1
    fi
    if ! bash -n "$file" 2>/dev/null; then
        echo "[model-env] profile is not valid shell: $file"; return 1
    fi
    for key in $MUNIN_MODEL_REQUIRED_KEYS; do
        if ! grep -qE "^${key}=" "$file"; then
            echo "[model-env] $file: missing required key $key"; rc=1
        fi
    done
    # Values that must not be blank.
    local v
    for key in MODEL_SLUG MODEL_ID MODEL_DIR MODEL_NAME VLLM_TOOL_PARSER VLLM_REASONING_PARSER \
               VLLM_GPU_MEM_UTIL VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS_SINGLE VLLM_MAX_NUM_SEQS_TP2 \
               LLM_THINKING_MODE KV_KIB_PER_TOKEN; do
        v=$(bash -c "set -a; . '$file'; printf '%s' \"\$$key\"" 2>/dev/null)
        if [ -z "$v" ]; then
            echo "[model-env] $file: $key must not be blank"; rc=1
        fi
    done
    v=$(bash -c ". '$file'; printf '%s' \"\$LLM_THINKING_MODE\"" 2>/dev/null)
    case "$v" in
        enable_thinking|effort_low|none) ;;
        *) echo "[model-env] $file: LLM_THINKING_MODE=$v (expected enable_thinking|effort_low|none)"; rc=1 ;;
    esac
    # The two sampling profiles must be JSON objects (or blank = Qwen fallback).
    for key in SAMPLING_DEFAULT SAMPLING_CODE; do
        v=$(bash -c ". '$file'; printf '%s' \"\$$key\"" 2>/dev/null)
        if [ -n "$v" ] && ! printf '%s' "$v" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d, dict)' 2>/dev/null; then
            echo "[model-env] $file: $key is not a JSON object"; rc=1
        fi
    done
    return $rc
}

munin_load_model_env() {
    # Source the profile into THIS shell, derive paths, export compose vars.
    local file
    file=$(munin_model_env_file "${1:-}")
    munin_validate_model_env "$file" || return 1
    # shellcheck disable=SC1090
    set -a; . "$file"; set +a

    MODEL_PROFILE_FILE=$file
    MODEL_PATH=$MUNIN_ROOT/data/models/$MODEL_DIR
    # Production's tokenizer dir keeps its historical name (compose mounts it
    # by default); a secondary instance stages its own under tokenizer-<slug>.
    if [ -z "${TOKENIZER_HOST_DIR:-}" ]; then
        TOKENIZER_HOST_DIR=$MUNIN_ROOT/data/models/qwen-tokenizer
    fi
    # `--quantization X` only when the profile names one; blank lets vLLM read
    # the checkpoint's own quant config (gpt-oss mxfp4).
    if [ -n "$VLLM_QUANTIZATION" ]; then
        VLLM_QUANT_ARGS="--quantization $VLLM_QUANTIZATION"
    else
        VLLM_QUANT_ARGS=""
    fi
    # The name retrieval asks vLLM for. Profiles define MODEL_NAME only; this
    # was exported but never set, so compose fell back to its literal default
    # and `model activate` of any other backbone left retrieval requesting a
    # model vLLM no longer served (every chat turn 404s).
    VLLM_MODEL_NAME=$MODEL_NAME
    export MODEL_PROFILE_FILE MODEL_PATH TOKENIZER_HOST_DIR VLLM_QUANT_ARGS
    local key
    for key in $MUNIN_MODEL_COMPOSE_EXPORTS; do
        export "$key"
    done
    return 0
}

munin_model_env_summary() {
    echo "  profile     : ${MODEL_PROFILE_FILE:-?}"
    echo "  model       : ${MODEL_NAME:-?}  (${MODEL_DESC:-?})"
    echo "  checkpoint  : ${MODEL_PATH:-?}  [${MODEL_ID:-?}]"
    echo "  parsers     : tool=${VLLM_TOOL_PARSER:-?} reasoning=${VLLM_REASONING_PARSER:-?}"
    echo "  quant/kv    : ${VLLM_QUANTIZATION:-auto} / ${VLLM_KV_CACHE_DTYPE:-?}  extra: ${VLLM_EXTRA_ARGS:-none}"
    echo "  thinking    : mode=${LLM_THINKING_MODE:-?} effort=${LLM_REASONING_EFFORT:-none}"
    echo "  sampling    : default=${SAMPLING_DEFAULT:-qwen3-fallback}"
    echo "                code=${SAMPLING_CODE:-qwen3-fallback}"
    echo "  tokenizer   : ${TOKENIZER_HOST_DIR:-?}"
}
