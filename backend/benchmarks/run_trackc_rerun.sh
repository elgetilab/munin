#!/bin/bash
# Track C re-run on the current (post-agent-architecture) harness — 2026-07-27.
#
# WHY: the committed C1/C2 captures were from 2026-07-10 and predate the agent
# architecture + source(mode=qa) full-text reading, which took over-abstention
# from ~42% to 6%. The risk-coverage figure was therefore plotting two system
# generations on shared axes.
#
# Config is pinned to match the 2026-07-27 harness ablation so the points are
# comparable: egress=full, concurrency=1, deadline=900.
set -u

cd "$(dirname "$0")"
PY=/opt/munin/services/pipeline/venv/bin/python
export PYTHONPATH="$HOME/.cache/munin_bench_deps:."
export MUNIN_EVAL_EGRESS=full
DATE=2026-07-27
EMAIL=litqa2-eval@localhost
LOG=trackc_rerun_${DATE}.log

echo "=== Track C re-run start $(date -Is) ===" | tee "$LOG"
echo "egress=$MUNIN_EVAL_EGRESS concurrency=1 deadline=900 date=$DATE" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- [1/3] C1 fabricated (100 items, :8080) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.run_c1 \
    --base-url http://127.0.0.1:8080 --email "$EMAIL" \
    --concurrency 1 --deadline 900 --date "$DATE" 2>&1 | tee -a "$LOG"
echo "C1 exit=${PIPESTATUS[0]}" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- [2/3] C2 present (50 q, live corpus :8080) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.run_c2 --arm present \
    --base-url http://127.0.0.1:8080 --email "$EMAIL" \
    --concurrency 1 --date "$DATE" 2>&1 | tee -a "$LOG"
echo "C2-present exit=${PIPESTATUS[0]}" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- [3/3] C2 absent (50 q, shadow corpus :8081) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.run_c2 --arm absent \
    --base-url http://127.0.0.1:8081 --email "$EMAIL" \
    --concurrency 1 --date "$DATE" 2>&1 | tee -a "$LOG"
echo "C2-absent exit=${PIPESTATUS[0]}" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- regenerating risk-coverage $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.risk_coverage --date "$DATE" 2>&1 | tee -a "$LOG"

echo "=== Track C re-run done $(date -Is) ===" | tee -a "$LOG"
