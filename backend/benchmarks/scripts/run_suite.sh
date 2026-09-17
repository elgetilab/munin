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
#                                    [--gpu-util U] [--tracks trackd,egressoff,c1,answer,c2b,faith,derived]
#                                    [--copy-arms-from TAG] [--ablation-tag TAG]
#   --tag names the run (runs/<tag>/ markers and log); --ablation-tag names the
#   ablation_runs/<tag>/ arm dir (default: the run tag), so one track can be
#   re-run later under a fresh run tag against the original arms.
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
#   3 suite        Track D (3 arms, n=199) + agentic arm at egress=off (the
#                  corpus-only decomposition, standard since 2026-09-17) + C1
#                  (100) + standalone answer (199) + C2b pair (2x50) +
#                  faithfulness per arm (CPU judge; the Track D capture carries
#                  complete contexts since 2026-09-16) + T11 + risk-coverage +
#                  routing anchor tier + paired compares.
#                  --tracks a,b,... restricts phase 3 to those sub-phases
#                  (3a trackd, 3a2 egressoff, 3b c1, 3c answer, 3d c2b,
#                  3e faith, 3f derived); --copy-arms-from <tag> supplies the
#                  bare/rag arms for egressoff when 3a is not in the run.
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
TRACKS="trackd,egressoff,c1,answer,c2b,faith,derived"
COPY_ARMS_FROM=""
ABL_TAG=""
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
        --tracks) TRACKS=$2; shift 2 ;;
        --copy-arms-from) COPY_ARMS_FROM=$2; shift 2 ;;
        --ablation-tag) ABL_TAG=$2; shift 2 ;;
        *) echo "unknown flag $1"; exit 1 ;;
    esac
done
TAG=${TAG:-$SLUG}
ABL_TAG=${ABL_TAG:-$TAG}
SHADOW_NAME="$NAME-shadow"
RUNS="$BENCH/runs/$TAG"
mkdir -p "$RUNS"
LOG="$RUNS/driver.log"
API="http://127.0.0.1:$API_PORT"
SHADOW_API="http://127.0.0.1:$SHADOW_API_PORT"
VLLM="http://127.0.0.1:$VLLM_PORT"

# --- helpers --------------------------------------------------------------------
want() { case ",$TRACKS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
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
export MUNIN_ABLATION_TAG="$ABL_TAG"

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

export RUNS
brave_errors_since() {   # count Brave HTTP 402/429 RESPONSES in the eval container log since $1
    # status code only: a DOI or query string containing "402" is not an error
    # (the first version matched 10.1016/j.jmb.2023.103402 in a query URL)
    $DEPLOY instance logs "$NAME" --since "$1" 2>&1 | grep -iE "api\.search\.brave\.com" | grep -cE '"HTTP/1\.[01] (402|429)' || true
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
bare_trunc = sum(1 for r in bare if r.get("empty_kind") == "truncated" or r.get("finish_reason") == "length")
bare_kinds = Counter(r.get("empty_kind") for r in bare if r.get("empty_kind"))
leak_names = sum(1 for e in ev if "<|" in (e.get("name") or ""))
mean_s = sum(r["elapsed_s"] for r in ag) / n
web = sum(1 for e in ev if e.get("name") == "web_search")
fails = []
def gate(name, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] smoke/{name}: {detail}")
    if not ok: fails.append(name)
gate("tool_calls_per_query", 0.4 <= ratio <= 2.5, f"{calls:.2f} vs Qwen3.8 {base_calls:.2f} (ratio {ratio:.2f}, allowed 0.4..2.5)")
gate("agentic_unparseable", ver["unparseable"] <= 0.25 * n, f"{ver['unparseable']}/{n} unparseable, reasons {dict(reasons)} (<= 25%)")
gate("agentic_empty_content", reasons.get("empty_content", 0) <= 0.10 * n, f"{reasons.get('empty_content', 0)}/{n} empty (<= 10%)")
gate("tool_markup_leaks", leaks <= max(1, 0.02 * n), f"{leaks} answers with raw tool markup (<= 2%; the model may quote a token it saw)")
gate("tool_error_rate", err <= max(2 * base_err, 0.05), f"{err:.3f} vs Qwen3.8 {base_err:.3f} (<= 2x)")
gate("bare_truncated", bare_trunc == 0, f"{bare_trunc} bare answers hit the output budget (== 0); {bare_unp} unparseable in total, empty kinds {dict(bare_kinds)} (a model that ends its turn without answering is a finding, not a gate)")
gate("tool_name_tokens", leak_names == 0, f"{leak_names} tool calls with a channel token in the name (== 0; the executor repair should have caught them)")
gate("brave_402_429", brave == 0, f"{brave} Brave 402/429 lines in the instance log")
print(f"[INFO] smoke/agentic: acc={ver['correct']/n:.2f} abstain={ver['abstain']/n:.2f} mean {mean_s:.0f}s/q; "
      f"web_search calls {web} (~${web*3.6*0.005:.2f}); forecast Track D agentic ~{mean_s*199/3600:.1f} h")
json.dump({"calls_per_query": calls, "baseline_calls": base_calls, "verdicts": dict(ver), "reasons": dict(reasons),
           "markup_leaks": leaks, "tool_error_rate": err, "baseline_error_rate": base_err, "bare_unparseable": bare_unp,
           "bare_truncated": bare_trunc, "bare_empty_kinds": dict(bare_kinds), "tool_name_tokens": leak_names,
           "brave_errors": brave, "mean_agentic_s": mean_s, "web_search_calls": web, "fails": fails},
          open(os.path.join(os.environ["RUNS"], "smoke.json"), "w"), indent=2)
sys.exit(1 if fails else 0)
PYEOF
    [ "${PIPESTATUS[0]}" -eq 0 ] || die "smoke gates failed (see $RUNS/smoke.json)"
    mark_done 2
fi
stop_if_until 2

# ==============================================================================
# Phase 3: the suite, concurrency 1 throughout
# ==============================================================================
if ! phase_done 3; then
    log "--- phase 3: suite (tracks: $TRACKS) ---"
    suite_start=$(cat "$RUNS/suite-start" 2>/dev/null || { ts | tee "$RUNS/suite-start"; })

    if want trackd && ! phase_done 3a; then
        log "3a Track D, 3 arms, n=199 (resumable), egress=full"
        run_logged $PY -m munin_bench.pipelines.run_all --tag "$TAG" --ablation-tag "$ABL_TAG" --tracks ablation --limit 199 \
            --with-reliability --certify --base-url "$API" --email "$EMAIL" --date "$DATE" "${PROV[@]}"
        rc=$?; [ $rc -le 1 ] || die "run_all ablation failed (rc=$rc)"   # rc 1 = certification FAIL, a finding
        [ $rc -eq 1 ] && log "NOTE: certification gate FAIL on this backbone (thresholds are Qwen3.6-derived); recorded, not acted on"
        run_logged $PY -m munin_bench.ablation.compare --date "$DATE" --tag "$ABL_TAG" || die "ablation compare failed"
        mv "$BENCH/scorecards/${DATE}_harness-ablation.json" "$BENCH/scorecards/${DATE}_harness-ablation-$SLUG.json" 2>/dev/null || true
        mark_done 3a
    fi
    if want egressoff && ! phase_done 3a2; then
        log "3a2 agentic arm at egress=off (corpus-only decomposition), n=199, resumable"
        src=${COPY_ARMS_FROM:-$ABL_TAG}
        [ -f "$BENCH/ablation_runs/$src/bare.json" ] || die "egressoff needs bare/rag arms to pair against; none under ablation_runs/$src (use --copy-arms-from)"
        mkdir -p "$BENCH/ablation_runs/$ABL_TAG-egressoff"
        for f in bare.json bare.meta.json rag.json rag.meta.json; do
            [ -f "$BENCH/ablation_runs/$src/$f" ] && cp -n "$BENCH/ablation_runs/$src/$f" "$BENCH/ablation_runs/$ABL_TAG-egressoff/$f"
        done
        echo "bare/rag copied from ablation_runs/$src (they make no tool calls, so egress does not touch them); agentic captured at MUNIN_EVAL_EGRESS=off $(ts)" \
            > "$BENCH/ablation_runs/$ABL_TAG-egressoff/README"
        MUNIN_EVAL_EGRESS=off run_logged $PY -m munin_bench.ablation.run_arm --arm agentic --n 199 --tag "$ABL_TAG-egressoff" \
            --base-url "$API" --email "$EMAIL" || die "egress=off agentic arm failed"
        run_logged $PY -m munin_bench.ablation.compare --date "$DATE" --tag "$ABL_TAG-egressoff" || die "egressoff compare failed"
        mv "$BENCH/scorecards/${DATE}_harness-ablation.json" "$BENCH/scorecards/${DATE}_harness-ablation-agentic-egressoff-$SLUG.json" 2>/dev/null || true
        run_logged $PY -m munin_bench.toolreliability.score "$BENCH/ablation_runs/$ABL_TAG-egressoff/agentic.json" \
            --tag "${DATE}_toolreliability-$SLUG-egressoff" || true
        # full vs off, question-paired, from the two agentic arrays
        $PY - "$BENCH/ablation_runs/$src/agentic.json" "$BENCH/ablation_runs/$ABL_TAG-egressoff/agentic.json" "$RUNS/egress-full-vs-off.json" <<'PYEOF' 2>&1 | tee -a "$LOG"
import json, sys
from collections import Counter
from munin_bench.metrics.bootstrap import paired_bootstrap
full = {r["qid"]: r for r in json.load(open(sys.argv[1]))["per_q"]}
off = {r["qid"]: r for r in json.load(open(sys.argv[2]))["per_q"]}
ids = sorted(set(full) & set(off))
d = paired_bootstrap([1.0 if full[i]["verdict"] == "correct" else 0.0 for i in ids],
                     [1.0 if off[i]["verdict"] == "correct" else 0.0 for i in ids])
trans = Counter(f"{full[i]['verdict']}->{off[i]['verdict']}" for i in ids)
out = {"n": len(ids), "full_minus_off": {k: d[k] for k in ("mean_diff", "ci_low", "ci_high", "p_value_two_sided")},
       "transitions_full_to_off": dict(trans),
       "acc_full": sum(1 for i in ids if full[i]["verdict"] == "correct") / len(ids),
       "acc_off": sum(1 for i in ids if off[i]["verdict"] == "correct") / len(ids)}
json.dump(out, open(sys.argv[3], "w"), indent=2)
print(f"[egress] full {out['acc_full']:.3f} vs off {out['acc_off']:.3f}: full-off {d['mean_diff']:+.3f} [{d['ci_low']:+.3f},{d['ci_high']:+.3f}] p={d['p_value_two_sided']:.3f}; transitions {dict(trans)}")
PYEOF
        mark_done 3a2
    fi
    if want c1 && ! phase_done 3b; then
        log "3b C1 fabricated, n=100, egress=full (resumable)"
        run_logged $PY -m munin_bench.abstention.run_c1 --base-url "$API" --email "$EMAIL" --concurrency 1 \
            --deadline 900 --date "$DATE" --out-dir "$BENCH/c1_runs/$TAG" "--out-suffix=-$SLUG" || die "C1 failed"
        mark_done 3b
    fi
    if want answer && ! phase_done 3c; then
        log "3c LitQA2 standalone answer track, n=199, egress=full (resumable)"
        run_logged $PY -m munin_bench.pipelines.run_litqa2 --track answer --base-url "$API" --email "$EMAIL" \
            --concurrency 1 --tag "$TAG" || die "answer track failed"
        cp "$BENCH/results/litqa2/answer.$TAG.json" "$BENCH/scorecards/${DATE}_answer-$SLUG-900s.json"
        mark_done 3c
    fi
    if want c2b && ! phase_done 3d; then
        log "3d C2b pair, n=50 x 2, egress=off (present :$API_PORT, absent :$SHADOW_API_PORT)"
        MUNIN_EVAL_EGRESS=off run_logged $PY -m munin_bench.abstention.run_c2 --arm present --base-url "$API" \
            --email "$EMAIL" --concurrency 1 --work-dir "$BENCH/c2_runs/$TAG" || die "C2 present failed"
        MUNIN_EVAL_EGRESS=off run_logged $PY -m munin_bench.abstention.run_c2 --arm absent --base-url "$SHADOW_API" \
            --email "$EMAIL" --concurrency 1 --work-dir "$BENCH/c2_runs/$TAG" --date "$DATE" \
            --out-tag "abstention-c2-shadow-$SLUG" --vs-dir "$BENCH/c2_runs" --vs-suffix "" || die "C2 absent failed"
        mark_done 3d
    fi
    if want faith && ! phase_done 3e; then
        log "3e faithfulness per arm (MiniCheck on $MUNIN_BENCH_ENTAILMENT_DEVICE)"
        run_logged $PY -m munin_bench.ablation.faithfulness --date "$DATE" --tag "$ABL_TAG" \
            --device "$MUNIN_BENCH_ENTAILMENT_DEVICE" || die "faithfulness failed"
        mv "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness.json" \
           "$BENCH/scorecards/${DATE}_harness-ablation-faithfulness-$SLUG.json" 2>/dev/null || true
        mark_done 3e
    fi
    if want derived && ! phase_done 3f; then
        log "3f derived: T11, risk-coverage, routing anchor tier, paired compares"
        run_logged $PY -m munin_bench.toolreliability.score "$BENCH/ablation_runs/$ABL_TAG/agentic.json" \
            --tag "${DATE}_toolreliability-$SLUG" || die "T11 failed"
        run_logged $PY -m munin_bench.abstention.risk_coverage --date "$DATE" --tag "$ABL_TAG" \
            --c2-dir "$BENCH/c2_runs/$TAG" --c1-scorecard "$BENCH/scorecards/${DATE}_abstention-c1-fabricated-$SLUG.json" \
            "--out-suffix=-$SLUG" || die "risk-coverage failed"
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
    # Wait for the old job to LEAVE the queue. `vllm-service start` refuses while
    # a production job is still COMPLETING ("already running"), and the dying
    # job keeps answering /health for a while, so the first version of this
    # step declared victory over a job that was on its way out and production
    # stayed down from 06:03 until a human noticed at 08:03 on 2026-09-16.
    for i in $(seq 1 60); do
        squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . || break; sleep 5
    done
    squeue -h -n vllm-service,vllm-service-tp2 -o %i | grep -q . && die "old production job still in the queue after 5 min; start TP=2 by hand: sudo vllm-service start tp2"
    run_logged $VLLM_SERVICE start tp2 || die "vllm-service start tp2 failed; production is DOWN"
    newjob=$(squeue -h -n vllm-service-tp2 -o %i | head -1)
    [ -n "$newjob" ] || die "no vllm-service-tp2 job after start; production is DOWN: sudo vllm-service start tp2"
    log "production job $newjob submitted; waiting for vLLM (up to 20 min) ..."
    for i in $(seq 1 240); do
        curl -sf --max-time 3 http://127.0.0.1:8000/health > /dev/null 2>&1 && break
        squeue -h -j "$newjob" -o %T | grep -q . || die "production job $newjob left the queue before serving; see /opt/munin/logs/vllm-service-$newjob.out"
        sleep 5
    done
    curl -sf --max-time 3 http://127.0.0.1:8000/health > /dev/null 2>&1 && log "production vLLM healthy on TP=2 (job $newjob)" \
        || die "production vLLM not healthy after 20 min; check squeue / vllm-service status"
    mark_done 4
fi

log "=== done. Files to commit (both scorecard folders, README rows, RESULTS.md section):"
ls -1 "$BENCH"/scorecards/"${DATE}"_* 2>/dev/null | tee -a "$LOG"
[ -f "$RUNS/DEGRADED-brave" ] && log "!! DEGRADED: Brave errors during the run; do not certify the agentic numbers"
log "driver log: $LOG"
