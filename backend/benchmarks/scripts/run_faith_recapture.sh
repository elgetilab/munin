#!/bin/bash
# ==============================================================================
# run_faith_recapture.sh - re-capture the agentic arm with the complete
# retrieval-tool set and re-score per-arm faithfulness, on Qwen3.8 (production)
# and then on gpt-oss-20b (eval instance).
# ==============================================================================
# Why: the faithfulness capture's RETRIEVAL_TOOLS lacked `search` and `source`
# until 2026-09-16, so every agentic arm was judged without the full-text
# passages the harness read. Contexts are extracted at capture time, so the
# only fix is a fresh agentic capture. The RAG arm's contexts (top-5 abstracts)
# were complete, so each new tag dir gets the ORIGINAL run's rag.json copied
# in and the paired test compares the new agentic capture against the same RAG
# arm the original comparison used. Bare is unscoreable and is not re-run.
#
#   scripts/run_faith_recapture.sh [--date D] [--skip-qwen] [--skip-gptoss]
#
# Phases (runs/faith-recapture/<phase>.done markers, re-entrant):
#   q1  Qwen3.8 agentic arm on production :8080, tag qwen38-27b-recapture
#   q2  judge (CPU) + T11 on that arm
#   g0  production TP=2 -> single (GPU 0 free), gpt-oss instance up
#   g1  gpt-oss agentic arm on :8082, tag gpt-oss-20b-recapture
#   g2  judge (CPU) + T11
#   g3  instance down, production back to TP=2
# ==============================================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"; BACKEND="$(cd "$BENCH/.." && pwd)"
PY=${PY:-/opt/munin/services/pipeline/venv/bin/python}
export PYTHONPATH="$HOME/.cache/munin_bench_deps:$BENCH"
EMAIL=${MUNIN_BENCH_EMAIL:-litqa2-eval@localhost}
DEPLOY="sudo -n $BACKEND/deploy.sh"; VLLM_SERVICE="sudo -n /usr/local/bin/vllm-service"
DATE=$(date -u +%Y-%m-%d); SKIP_QWEN=0; SKIP_GPTOSS=0
while [ $# -gt 0 ]; do case "$1" in
    --date) DATE=$2; shift 2 ;; --skip-qwen) SKIP_QWEN=1; shift ;; --skip-gptoss) SKIP_GPTOSS=1; shift ;;
    *) echo "unknown flag $1"; exit 1 ;; esac; done
RUNS="$BENCH/runs/faith-recapture"; mkdir -p "$RUNS"; LOG="$RUNS/driver.log"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }
die() { log "FAIL: $*"; log "stopped; re-run the same command, finished phases are skipped."; exit 1; }
phase_done() { [ -f "$RUNS/$1.done" ]; }
mark_done() { ts > "$RUNS/$1.done"; log "phase $1 done"; }
run_logged() { log "+ $*"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }
export MUNIN_EVAL_EGRESS=full MUNIN_BENCH_ENTAILMENT_DEVICE=${MUNIN_BENCH_ENTAILMENT_DEVICE:-cpu}

# Faithfulness on a tag dir: copy the original RAG arm in, score, T11, rename scorecards.
score_arm() {   # $1 tag  $2 original-tag (for rag.json)  $3 suffix
    local tag=$1 orig=$2 sfx=$3
    cp -n "$BENCH/ablation_runs/$orig/rag.json" "$BENCH/ablation_runs/$tag/rag.json"
    cp -n "$BENCH/ablation_runs/$orig/rag.meta.json" "$BENCH/ablation_runs/$tag/rag.meta.json" 2>/dev/null || true
    echo "rag.json copied from ablation_runs/$orig (complete abstract contexts); agentic re-captured $(ts) with search+source in RETRIEVAL_TOOLS" \
        > "$BENCH/ablation_runs/$tag/README"
    run_logged $PY -m munin_bench.ablation.faithfulness --date "$DATE" --tag "$tag" --device "$MUNIN_BENCH_ENTAILMENT_DEVICE" || die "faithfulness $tag failed"
    mv "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness.json" "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness-$sfx.json"
    run_logged $PY -m munin_bench.toolreliability.score "$BENCH/ablation_runs/$tag/agentic.json" --tag "${DATE}_toolreliability-$sfx" || true
    run_logged $PY -m munin_bench.ablation.compare --tag "$tag" || true
}

log "=== faith recapture  date=$DATE  judge=$MUNIN_BENCH_ENTAILMENT_DEVICE ==="

# ---------------- Qwen3.8 on production ----------------
if [ "$SKIP_QWEN" != "1" ]; then
    if ! phase_done q1; then
        log "--- q1: Qwen3.8 agentic arm on production :8080 (n=199, egress=full, concurrency 1) ---"
        curl -sf --max-time 5 http://127.0.0.1:8080/api/models | grep -q '"qwen3.8-27b"' || die "production /api/models does not report qwen3.8-27b"
        VLLM_URL=http://127.0.0.1:8000 VLLM_MODEL_NAME=qwen3.8-27b LLM_THINKING_MODE=enable_thinking \
            run_logged $PY -m munin_bench.ablation.run_arm --arm agentic --n 199 --tag qwen38-27b-recapture \
            --base-url http://127.0.0.1:8080 --email "$EMAIL" || die "Qwen3.8 agentic arm failed"
        mark_done q1
    fi
    if ! phase_done q2; then
        log "--- q2: judge + T11 on the Qwen3.8 recapture ---"
        score_arm qwen38-27b-recapture qwen38-27b qwen38-recapture
        mark_done q2
    fi
fi

# ---------------- gpt-oss-20b on an instance ----------------
if [ "$SKIP_GPTOSS" != "1" ]; then
    if ! phase_done g0; then
        log "--- g0: production to single-GPU profile, gpt-oss instance up ---"
        if squeue -h -n vllm-service-tp2 -o %i | grep -q .; then
            echo tp2 > "$RUNS/prod-profile-before"
            run_logged $VLLM_SERVICE stop || true
            for i in $(seq 1 60); do squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . || break; sleep 5; done
            squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . && die "old production job still in the queue"
            run_logged $VLLM_SERVICE start single || die "start single failed; production is DOWN"
            for i in $(seq 1 240); do curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 5; done
            curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 || die "production not healthy on single after 20 min"
            log "production on single-GPU profile"
        else
            echo single > "$RUNS/prod-profile-before"
        fi
        if ! curl -sf --max-time 3 http://127.0.0.1:8082/health >/dev/null 2>&1; then
            run_logged $DEPLOY instance up gpt-oss-20b --name eval --vllm-port 8001 --api-port 8082 --gpu-util 0.80 || die "instance up failed"
        fi
        mark_done g0
    fi
    if ! phase_done g1; then
        log "--- g1: gpt-oss-20b agentic arm on :8082 (n=199, egress=full, concurrency 1) ---"
        source "$BACKEND/scripts/vllm/model-env.sh"; MUNIN_MODEL_PROFILES_DIR="$BACKEND/config/models"
        munin_load_model_env gpt-oss-20b || die "profile"
        VLLM_URL=http://127.0.0.1:8001 VLLM_MODEL_NAME=gpt-oss-20b \
            run_logged $PY -m munin_bench.ablation.run_arm --arm agentic --n 199 --tag gpt-oss-20b-recapture \
            --base-url http://127.0.0.1:8082 --email "$EMAIL" || die "gpt-oss agentic arm failed"
        mark_done g1
    fi
    if ! phase_done g2; then
        log "--- g2: judge + T11 on the gpt-oss recapture ---"
        score_arm gpt-oss-20b-recapture gpt-oss-20b gpt-oss-20b-recapture
        mark_done g2
    fi
    if ! phase_done g3; then
        log "--- g3: instance down, production back to TP=2 ---"
        run_logged $DEPLOY instance down eval || log "WARN: instance down failed"
        if [ "$(cat "$RUNS/prod-profile-before" 2>/dev/null)" = "tp2" ]; then
            run_logged $VLLM_SERVICE stop || true
            for i in $(seq 1 60); do squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . || break; sleep 5; done
            squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . && die "old production job still in the queue; start TP=2 by hand"
            run_logged $VLLM_SERVICE start tp2 || die "start tp2 failed; production is DOWN"
            newjob=$(squeue -h -n vllm-service-tp2 -o %i | head -1); [ -n "$newjob" ] || die "no tp2 job after start"
            for i in $(seq 1 240); do
                curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && break
                squeue -h -j "$newjob" -o %T | grep -q . || die "job $newjob left the queue before serving"
                sleep 5
            done
            curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && log "production healthy on TP=2 (job $newjob)" || die "production not healthy after 20 min"
        fi
        mark_done g3
    fi
fi
log "=== done. scorecards:"; ls -1 "$BENCH"/scorecards/"${DATE}"_*recapture* 2>/dev/null | tee -a "$LOG"
