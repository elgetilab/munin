#!/bin/bash
# ==============================================================================
# run_suite.sh <slug> - the full generation suite on a second backbone, unattended
# ==============================================================================
# Brings a backbone up as an eval-only instance beside production (GPU 0,
# vLLM :8001, retrieval :8082, plus a shadow-corpus instance :8083 for the C2b
# absent arm), gates it, smokes it, runs every generation track at
# concurrency 1, derives the offline metrics, tears the instance down and
# restores the production profile. docs/paper-track/BACKBONE-SWITCH-AND-EVAL-PLAN.md
# is the plan this implements.
#
#   scripts/run_suite.sh gpt-oss-20b [--date YYYY-MM-DD] [--tag T] [--until PHASE]
#                                    [--api-port 8082] [--shadow-api-port 8083]
#                                    [--vllm-port 8001] [--no-teardown] [--no-smoke]
#
# Re-entrant: every phase writes runs/<tag>/phase-N.done and a re-invocation
# skips completed phases; the tracks themselves resume from their captures.
# So the answer to "it died" is always "run the same command again".
# Every gate prints the number it measured next to its threshold; on a gate
# failure the driver stops and reports. It never tunes anything.
#
# Root steps (instance up/down, vllm-service) go through `sudo -n`, which the
# rule installed by `deploy.sh sudoers` allows without a password. The bench
# itself runs as the invoking user, exactly as every committed run did.
#
# Phases:
#   0 preflight    sudo works; production on the single-GPU profile; 24x7 on;
#                  checkpoint present; disk; shadow corpus built + leak-free
#   1 instances    instance up <slug> --name eval; instance up ... --corpus shadow
#   2 smoke        run_all --limit 20 on :8082, gated against the Qwen3.8 arm
#   3 suite        Track D (3 arms, n=199) + C1 (100) + standalone answer (199)
#                  + C2b pair (2x50) + faithfulness per arm (CPU judge) + T11 +
#                  risk-coverage + routing anchor tier + paired compares
#   4 teardown     instances down, shadow dropped, 24x7 restored, production
#                  back on TP=2
# ==============================================================================
set -u

SLUG=${1:-}
[ -n "$SLUG" ] || { sed -n '2,40p' "$0"; exit 1; }
shift

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$(cd "$HERE/.." && pwd)"                   # backend/benchmarks
BACKEND="$(cd "$BENCH/.." && pwd)"                # backend
REPO="$(cd "$BACKEND/.." && pwd)"

PY=${PY:-/opt/munin/services/pipeline/venv/bin/python}
export PYTHONPATH="$HOME/.cache/munin_bench_deps:$BENCH"
EMAIL=${MUNIN_BENCH_EMAIL:-litqa2-eval@localhost}
DEPLOY="sudo -n $BACKEND/deploy.sh"
VLLM_SERVICE="sudo -n /usr/local/bin/vllm-service"

DATE=$(date -u +%Y-%m-%d)
TAG=""
UNTIL=""
NAME=eval
VLLM_PORT=8001
API_PORT=8082
SHADOW_API_PORT=8083
TEARDOWN=1
SMOKE=1
# GPU 0 is not empty on hugin: the paper-pipeline watcher (~1.8 GiB) and a
# remote-desktop session sit on it. 0.90 OOM'd during CUDA-graph warmup on the
# second instance start (2026-09-15); 0.80 leaves ~3 GiB of slack and still
# ~800k KV tokens for gpt-oss (12x at 64k). Recorded in provenance.
GPU_UTIL=0.80
while [ $# -gt 0 ]; do
    case "$1" in
        --date) DATE=$2; shift 2 ;;
        --tag) TAG=$2; shift 2 ;;
        --until) UNTIL=$2; shift 2 ;;
        --name) NAME=$2; shift 2 ;;
        --vllm-port) VLLM_PORT=$2; shift 2 ;;
        --api-port) API_PORT=$2; shift 2 ;;
        --shadow-api-port) SHADOW_API_PORT=$2; shift 2 ;;
        --no-teardown) TEARDOWN=0; shift ;;
        --no-smoke) SMOKE=0; shift ;;
        --gpu-util) GPU_UTIL=$2; shift 2 ;;
        *) echo "unknown flag $1"; exit 1 ;;
    esac
done
TAG=${TAG:-$SLUG}
SHADOW_NAME="$NAME-shadow"
RUNS="$BENCH/runs/$TAG"
mkdir -p "$RUNS"
LOG="$RUNS/driver.log"
API="http://127.0.0.1:$API_PORT"
SHADOW_API="http://127.0.0.1:$SHADOW_API_PORT"
VLLM="http://127.0.0.1:$VLLM_PORT"

# --- helpers --------------------------------------------------------------------
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }
die() { log "FAIL: $*"; log "stopped. Fix and re-run the same command; completed phases are skipped."; exit 1; }
done_marker() { echo "$RUNS/phase-$1.done"; }
phase_done() { [ -f "$(done_marker "$1")" ]; }
mark_done() { ts > "$(done_marker "$1")"; log "phase $1 done"; }
stop_if_until() { if [ "$UNTIL" = "$1" ]; then log "--until $1 reached; stopping here."; exit 0; fi; }
run_logged() {
    # run a command, tee its output to the log, return its exit status
    log "+ $*"
    "$@" 2>&1 | tee -a "$LOG"
    return "${PIPESTATUS[0]}"
}

# The model profile: exported into this shell so the bench's config.py sees the
# same VLLM_MODEL_NAME / LLM_REASONING_EFFORT / LLM_THINKING_MODE the instance runs.
# shellcheck disable=SC1090
source "$BACKEND/scripts/vllm/model-env.sh"
MUNIN_MODEL_PROFILES_DIR="$BACKEND/config/models"
munin_load_model_env "$SLUG" || exit 1
export VLLM_URL="$VLLM" VLLM_MODEL_NAME="$MODEL_NAME" LLM_THINKING_MODE LLM_REASONING_EFFORT
export MUNIN_EVAL_EGRESS=full
export MUNIN_BENCH_ENTAILMENT_DEVICE=${MUNIN_BENCH_ENTAILMENT_DEVICE:-cpu}
export MUNIN_ABLATION_TAG="$TAG"

log "=== run_suite $SLUG  tag=$TAG date=$DATE  vllm=$VLLM api=$API shadow=$SHADOW_API ==="
log "model=$MODEL_NAME profile=$MODEL_PROFILE_FILE thinking=$LLM_THINKING_MODE effort=${LLM_REASONING_EFFORT:-none} egress=$MUNIN_EVAL_EGRESS judge=$MUNIN_BENCH_ENTAILMENT_DEVICE"

# Provenance flags shared by every scorecard-writing step.
PROV=(--provenance "backbone=$MODEL_SLUG" --provenance "checkpoint=$MODEL_ID"
      --provenance "checkpoint_dir=$MODEL_PATH" --provenance "instance=$NAME"
      --provenance "tool_parser=$VLLM_TOOL_PARSER" --provenance "reasoning_parser=$VLLM_REASONING_PARSER"
      --provenance "thinking_mode=$LLM_THINKING_MODE" --provenance "sampling_default=$SAMPLING_DEFAULT"
      --provenance "sampling_code=$SAMPLING_CODE" --provenance "serving_profile=single-gpu-instance"
      --provenance "max_num_seqs=$VLLM_MAX_NUM_SEQS_SINGLE" --provenance "gpu_mem_util=$GPU_UTIL" --provenance "concurrency=1"
      --provenance "deadline_s=900" --provenance "driver=run_suite.sh")

gates_field() {   # $1 = gates.json, $2 = gate, $3 = field
    python3 -c 'import json,sys; g=json.load(open(sys.argv[1]))["gates"].get(sys.argv[2],{}); v=g.get(sys.argv[3]); print("" if v is None else v)' "$1" "$2" "$3" 2>/dev/null
}

# ==============================================================================
# Phase 0: preflight
# ==============================================================================
if ! phase_done 0; then
    log "--- phase 0: preflight ---"
    # the sudoers rule is command-scoped, so probe with an allowed command, not `true`
    $VLLM_SERVICE status > /dev/null 2>&1 || die "passwordless sudo for vllm-service missing: sudo MUNIN_OPERATOR=$USER $BACKEND/deploy.sh sudoers"
    # production must be on the single-GPU profile: TP=2 holds GPU 0.
    prod_job=$(squeue -h -n vllm-service -o %T 2>/dev/null | head -1)
    tp2_job=$(squeue -h -n vllm-service-tp2 -o %T 2>/dev/null | head -1)
    profile_file=$(cat /opt/munin/logs/vllm_profile 2>/dev/null | tr -d '[:space:]')
    [ -z "$tp2_job" ] || die "production runs the TP=2 profile ($tp2_job); GPU 0 is not free. Switch by hand: sudo vllm-service stop && sudo vllm-service start single"
    [ "$prod_job" = "RUNNING" ] || die "production single-GPU vLLM is not RUNNING (state: ${prod_job:-none}); start it: sudo vllm-service start single"
    [ "$profile_file" = "single" ] || die "persisted profile is '$profile_file', not single: the 06:00 cron would bring TP=2 back and pend it behind the instance. Fix: sudo vllm-service start single"
    log "production: single-GPU profile RUNNING, persisted=$profile_file"
    echo "$profile_file" > "$RUNS/prod-profile-at-start"
    # 24x7 for the window: the 02:00 stop would take production down mid-run (users)
    if $VLLM_SERVICE status 2>/dev/null | grep -q "Mode: 24/7"; then
        log "vLLM schedule: already 24/7"; echo no > "$RUNS/restore-schedule"
    else
        run_logged $VLLM_SERVICE enable-24x7 || die "enable-24x7 failed"
        echo yes > "$RUNS/restore-schedule"; log "vLLM schedule: 24/7 enabled for the window (restored in phase 4)"
    fi
    [ -f "$MODEL_PATH/config.json" ] || die "checkpoint missing at $MODEL_PATH"
    free_gb=$(df -BG --output=avail /opt/munin/data | tail -1 | tr -dc '0-9')
    [ "$free_gb" -ge 40 ] || die "only ${free_gb}G free under /opt/munin/data"
    log "checkpoint present, ${free_gb}G free"
    # shadow corpus for the C2b absent arm
    if $PY -m munin_bench.abstention.shadow_corpus verify 2>&1 | tee -a "$LOG" | grep -q "MISSING"; then
        log "shadow corpus missing; building from the frozen removed_dois ..."
        run_logged $PY -m munin_bench.abstention.shadow_corpus build || die "shadow build failed"
    fi
    run_logged $PY -m munin_bench.abstention.shadow_corpus verify || die "shadow corpus leaks or is missing"
    mark_done 0
fi
stop_if_until 0

# ==============================================================================
# Phase 1: instances up
# ==============================================================================
if ! phase_done 1; then
    log "--- phase 1: instances up ---"
    if [ ! -f "/opt/munin/instances/$NAME/gates.json" ] || \
       ! curl -sf --max-time 3 "$API/health" > /dev/null 2>&1; then
        run_logged $DEPLOY instance up "$SLUG" --name "$NAME" --vllm-port "$VLLM_PORT" --api-port "$API_PORT" --gpu-util "$GPU_UTIL" \
            || die "instance up $NAME failed (see /opt/munin/instances/$NAME/gates.json)"
    else
        log "instance $NAME already up and gated"
    fi
    if ! curl -sf --max-time 3 "$SHADOW_API/health" > /dev/null 2>&1; then
        run_logged $DEPLOY instance up "$SLUG" --name "$SHADOW_NAME" --vllm "$NAME" --api-port "$SHADOW_API_PORT" --corpus shadow \
            || die "instance up $SHADOW_NAME failed"
    else
        log "instance $SHADOW_NAME already up"
    fi
    cp "/opt/munin/instances/$NAME/gates.json" "$RUNS/gates.json"
    run_logged $DEPLOY instance ls
    # one removed paper must be findable on the live instance and absent on the shadow
    doi=$(python3 -c 'import json; print(json.load(open("'"$BENCH"'/munin_bench/abstention/c2_questions.json"))["meta"]["removed_dois"][3])')
    hit_live=$(curl -sf -X POST http://127.0.0.1:6333/collections/papers_bge/points/count -H 'Content-Type: application/json' -d "{\"exact\":true,\"filter\":{\"must\":[{\"key\":\"doi\",\"match\":{\"value\":\"$doi\"}}]}}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["count"])')
    hit_shadow=$(curl -sf -X POST http://127.0.0.1:6333/collections/papers_shadow/points/count -H 'Content-Type: application/json' -d "{\"exact\":true,\"filter\":{\"must\":[{\"key\":\"doi\",\"match\":{\"value\":\"$doi\"}}]}}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["count"])')
    log "removed-paper probe $doi: papers_bge=$hit_live papers_shadow=$hit_shadow"
    mark_done 1
fi
stop_if_until 1
[ -f "$RUNS/gates.json" ] || cp "/opt/munin/instances/$NAME/gates.json" "$RUNS/gates.json" 2>/dev/null || true
MOE_BACKEND=$(gates_field "$RUNS/gates.json" kv_pool moe_backend)
KV_TOKENS=$(gates_field "$RUNS/gates.json" kv_pool kv_tokens)
DECODE_TPS=$(gates_field "$RUNS/gates.json" decode_tps tokens_per_s)
PREFILL_TPS=$(gates_field "$RUNS/gates.json" prefill_tps tokens_per_s)
THINK_RATIO=$(gates_field "$RUNS/gates.json" thinking_off ratio)
PROV+=(--provenance "moe_backend=${MOE_BACKEND:-n/a}" --provenance "kv_tokens=${KV_TOKENS:-?}"
       --provenance "decode_tok_s=${DECODE_TPS:-?}" --provenance "prefill_tok_s=${PREFILL_TPS:-?}"
       --provenance "thinking_off_token_ratio=${THINK_RATIO:-?}")
START_TS=$(cat "$RUNS/phase-1.done" 2>/dev/null || ts)

brave_errors_since() {   # count Brave 402/429 lines in the eval container log since $1
    $DEPLOY instance logs "$NAME" --since "$1" 2>&1 | grep -ciE "brave.*(402|429)|(402|429).*brave" || true
}

# ==============================================================================
# Phase 2: smoke (--limit 20), gated against the 08-26 Qwen3.8 arm
# ==============================================================================
if [ "$SMOKE" = "1" ] && ! phase_done 2; then
    log "--- phase 2: smoke (20 questions x 3 arms + 20 C1 items) ---"
    smoke_start=$(ts)
    run_logged $PY -m munin_bench.pipelines.run_all --tag "$TAG-smoke" --tracks ablation --limit 20 \
        --base-url "$API" --email "$EMAIL" --date "$DATE" "${PROV[@]}" || die "smoke run_all failed"
    run_logged $PY -m munin_bench.abstention.run_c1 --base-url "$API" --email "$EMAIL" --limit 20 \
        --concurrency 1 --deadline 900 --out-dir "$BENCH/c1_runs/$TAG-smoke" || die "smoke C1 failed"
    brave=$(brave_errors_since "$smoke_start")
    $PY - "$BENCH/ablation_runs/$TAG-smoke" "$BENCH/ablation_runs/qwen38-27b" "$brave" <<'PYEOF' 2>&1 | tee -a "$LOG"
import json, os, sys
from collections import Counter
new, base, brave = sys.argv[1], sys.argv[2], int(sys.argv[3])
def load(d, arm): return json.load(open(os.path.join(d, f"{arm}.json")))["per_q"]
ag, bare = load(new, "agentic"), load(new, "bare")
base_ag = load(base, "agentic")
n = len(ag)
calls = sum(r["tool_calls"] for r in ag) / n
base_calls = sum(r["tool_calls"] for r in base_ag) / len(base_ag)
ratio = calls / base_calls
ver = Counter(r["verdict"] for r in ag)
reasons = Counter(r.get("reason") for r in ag if r["verdict"] == "unparseable")
leaks = sum(1 for r in ag if r.get("tool_markup_in_content"))
ev = [e for r in ag for e in (r.get("tool_events") or [])]
err = sum(1 for e in ev if e.get("is_error")) / len(ev) if ev else 0.0
base_ev = [e for r in base_ag for e in (r.get("tool_events") or [])]
base_err = sum(1 for e in base_ev if e.get("is_error")) / len(base_ev) if base_ev else 0.0
bare_unp = sum(1 for r in bare if r["verdict"] == "unparseable")
mean_s = sum(r["elapsed_s"] for r in ag) / n
web = sum(1 for e in ev if e.get("name") == "web_search")
fails = []
def gate(name, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] smoke/{name}: {detail}")
    if not ok: fails.append(name)
gate("tool_calls_per_query", 0.4 <= ratio <= 2.5, f"{calls:.2f} vs Qwen3.8 {base_calls:.2f} (ratio {ratio:.2f}, allowed 0.4..2.5)")
gate("agentic_unparseable", ver["unparseable"] <= 0.25 * n, f"{ver['unparseable']}/{n} unparseable, reasons {dict(reasons)} (<= 25%)")
gate("agentic_empty_content", reasons.get("empty_content", 0) <= 0.10 * n, f"{reasons.get('empty_content', 0)}/{n} empty (<= 10%)")
gate("tool_markup_leaks", leaks == 0, f"{leaks} answers with raw tool markup (== 0)")
gate("tool_error_rate", err <= max(2 * base_err, 0.05), f"{err:.3f} vs Qwen3.8 {base_err:.3f} (<= 2x)")
gate("bare_unparseable", bare_unp == 0, f"{bare_unp} (== 0; non-zero means the 16,384-token budget is being exceeded)")
gate("brave_402_429", brave == 0, f"{brave} Brave 402/429 lines in the instance log")
print(f"[INFO] smoke/agentic: acc={ver['correct']/n:.2f} abstain={ver['abstain']/n:.2f} mean {mean_s:.0f}s/q; "
      f"web_search calls {web} (~${web*3.6*0.005:.2f}); forecast Track D agentic ~{mean_s*199/3600:.1f} h")
json.dump({"calls_per_query": calls, "baseline_calls": base_calls, "verdicts": dict(ver), "reasons": dict(reasons),
           "markup_leaks": leaks, "tool_error_rate": err, "baseline_error_rate": base_err, "bare_unparseable": bare_unp,
           "brave_errors": brave, "mean_agentic_s": mean_s, "web_search_calls": web, "fails": fails},
          open(os.path.join(os.environ["RUNS"], "smoke.json"), "w"), indent=2)
sys.exit(1 if fails else 0)
PYEOF
    [ "${PIPESTATUS[0]}" -eq 0 ] || die "smoke gates failed (see $RUNS/smoke.json)"
    mark_done 2
fi
stop_if_until 2
export RUNS

# ==============================================================================
# Phase 3: the suite, concurrency 1 throughout
# ==============================================================================
if ! phase_done 3; then
    log "--- phase 3: suite ---"
    suite_start=$(cat "$RUNS/suite-start" 2>/dev/null || { ts | tee "$RUNS/suite-start"; })

    if ! phase_done 3a; then
        log "3a Track D, 3 arms, n=199 (resumable), egress=full"
        run_logged $PY -m munin_bench.pipelines.run_all --tag "$TAG" --tracks ablation --limit 199 \
            --with-reliability --certify --base-url "$API" --email "$EMAIL" --date "$DATE" "${PROV[@]}"
        rc=$?; [ $rc -le 1 ] || die "run_all ablation failed (rc=$rc)"   # rc 1 = certification FAIL, a finding
        [ $rc -eq 1 ] && log "NOTE: certification gate FAIL on this backbone (thresholds are Qwen3.6-derived); recorded, not acted on"
        run_logged $PY -m munin_bench.ablation.compare --date "$DATE" --tag "$TAG" || die "ablation compare failed"
        mv "$BENCH/scorecards/${DATE}_harness-ablation.json" "$BENCH/scorecards/${DATE}_harness-ablation-$SLUG.json" 2>/dev/null || true
        mark_done 3a
    fi
    if ! phase_done 3b; then
        log "3b C1 fabricated, n=100, egress=full (resumable)"
        run_logged $PY -m munin_bench.abstention.run_c1 --base-url "$API" --email "$EMAIL" --concurrency 1 \
            --deadline 900 --date "$DATE" --out-dir "$BENCH/c1_runs/$TAG" --out-suffix "-$SLUG" || die "C1 failed"
        mark_done 3b
    fi
    if ! phase_done 3c; then
        log "3c LitQA2 standalone answer track, n=199, egress=full (resumable)"
        run_logged $PY -m munin_bench.pipelines.run_litqa2 --track answer --base-url "$API" --email "$EMAIL" \
            --concurrency 1 --tag "$TAG" || die "answer track failed"
        cp "$BENCH/results/litqa2/answer.$TAG.json" "$BENCH/scorecards/${DATE}_answer-$SLUG-900s.json"
        mark_done 3c
    fi
    if ! phase_done 3d; then
        log "3d C2b pair, n=50 x 2, egress=off (present :$API_PORT, absent :$SHADOW_API_PORT)"
        MUNIN_EVAL_EGRESS=off run_logged $PY -m munin_bench.abstention.run_c2 --arm present --base-url "$API" \
            --email "$EMAIL" --concurrency 1 --work-dir "$BENCH/c2_runs/$TAG" || die "C2 present failed"
        MUNIN_EVAL_EGRESS=off run_logged $PY -m munin_bench.abstention.run_c2 --arm absent --base-url "$SHADOW_API" \
            --email "$EMAIL" --concurrency 1 --work-dir "$BENCH/c2_runs/$TAG" --date "$DATE" \
            --out-tag "abstention-c2-shadow-$SLUG" --vs-dir "$BENCH/c2_runs" --vs-suffix "" || die "C2 absent failed"
        mark_done 3d
    fi
    if ! phase_done 3e; then
        log "3e faithfulness per arm (MiniCheck on $MUNIN_BENCH_ENTAILMENT_DEVICE)"
        run_logged $PY -m munin_bench.ablation.faithfulness --date "$DATE" --tag "$TAG" \
            --device "$MUNIN_BENCH_ENTAILMENT_DEVICE" || die "faithfulness failed"
        mv "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness.json" \
           "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness-$SLUG.json" 2>/dev/null || true
        mark_done 3e
    fi
    if ! phase_done 3f; then
        log "3f derived: T11, risk-coverage, routing anchor tier, paired compares"
        run_logged $PY -m munin_bench.toolreliability.score "$BENCH/ablation_runs/$TAG/agentic.json" \
            --tag "${DATE}_toolreliability-$SLUG" || die "T11 failed"
        run_logged $PY -m munin_bench.abstention.risk_coverage --date "$DATE" --tag "$TAG" \
            --c2-dir "$BENCH/c2_runs/$TAG" --c1-scorecard "$BENCH/scorecards/${DATE}_abstention-c1-fabricated-$SLUG.json" \
            --out-suffix "-$SLUG" || die "risk-coverage failed"
        run_logged $PY -m munin_bench.routing.run --base "$API" --email "routing-eval@munin.local" \
            --tag "routing-$SLUG-$DATE" --tier anchor || log "WARN: routing eval failed (not a paper number; continuing)"
        old_abl=$(ls "$BENCH"/scorecards/2026-08-26_harness-ablation.json 2>/dev/null | head -1)
        [ -n "$old_abl" ] && run_logged $PY -m munin_bench.pipelines.compare "$old_abl" \
            "$BENCH/scorecards/${DATE}_$TAG.json" > "$RUNS/compare-trackd-vs-qwen38.md" 2>&1 || true
        mark_done 3f
    fi
    brave=$(brave_errors_since "$suite_start")
    log "Brave 402/429 lines during the suite: $brave"
    if [ "$brave" != "0" ]; then
        log "WARNING: Brave degraded during the run; the agentic numbers are NOT clean (the 2026-07-26 lesson). Marking."
        echo "$brave" > "$RUNS/DEGRADED-brave"
    fi
    mark_done 3
fi
stop_if_until 3

# ==============================================================================
# Phase 4: teardown and restore
# ==============================================================================
if [ "$TEARDOWN" = "1" ] && ! phase_done 4; then
    log "--- phase 4: teardown ---"
    run_logged $DEPLOY instance down "$SHADOW_NAME" || log "WARN: down $SHADOW_NAME failed"
    run_logged $DEPLOY instance down "$NAME" || log "WARN: down $NAME failed"
    run_logged $PY -m munin_bench.abstention.shadow_corpus drop || log "WARN: shadow drop failed"
    if [ "$(cat "$RUNS/restore-schedule" 2>/dev/null)" = "yes" ]; then
        run_logged $VLLM_SERVICE disable-24x7 || log "WARN: disable-24x7 failed"
    fi
    log "restoring production to the TP=2 profile ..."
    run_logged $VLLM_SERVICE stop || true
    sleep 10
    run_logged $VLLM_SERVICE start tp2 || die "vllm-service start tp2 failed; production is DOWN"
    for i in $(seq 1 180); do
        curl -sf --max-time 3 http://127.0.0.1:8000/health > /dev/null 2>&1 && break; sleep 5
    done
    curl -sf --max-time 3 http://127.0.0.1:8000/health > /dev/null 2>&1 && log "production vLLM healthy on TP=2" \
        || log "WARNING: production vLLM not healthy after 15 min; check squeue / vllm-service status"
    mark_done 4
fi

log "=== done. Files to commit (both scorecard folders, README rows, RESULTS.md section):"
ls -1 "$BENCH"/scorecards/"${DATE}"_* 2>/dev/null | tee -a "$LOG"
[ -f "$RUNS/DEGRADED-brave" ] && log "!! DEGRADED: Brave errors during the run; do not certify the agentic numbers"
log "driver log: $LOG"
