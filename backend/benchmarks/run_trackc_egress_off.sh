#!/bin/bash
# Track C phase 2 — C2 paired arms at egress=OFF (2026-07-27).
#
# WHY: phase 1 ran the whole of Track C at egress=full to match the harness
# ablation. That is correct for C1 (a harder test: the model may search the
# entire web and must still conclude a fabricated paper does not exist) and for
# C2-present. It is WRONG for C2-absent: with the web open the model re-fetches
# the removed source papers from Semantic Scholar / Unpaywall (measured: 17 of
# 49 removed sources pulled back in), so "absent" stops meaning absent and the
# arm no longer measures corpus-grounded abstention.
#
# Egress must be held CONSTANT across the paired arms, so both are re-run here
# at egress=off. The egress=full pair is preserved as a secondary
# "with web access" condition.
set -u

cd "$(dirname "$0")"
PY=/opt/munin/services/pipeline/venv/bin/python
export PYTHONPATH="$HOME/.cache/munin_bench_deps:."
export MUNIN_EVAL_EGRESS=off
DATE=2026-07-27
EMAIL=litqa2-eval@localhost
LOG=trackc_egressoff_${DATE}.log

echo "=== Track C phase 2 (egress=off) start $(date -Is) ===" | tee "$LOG"
echo "egress=$MUNIN_EVAL_EGRESS concurrency=1 date=$DATE" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- [1/2] C2 present, egress=off (50 q, live corpus :8080) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.run_c2 --arm present \
    --base-url http://127.0.0.1:8080 --email "$EMAIL" \
    --concurrency 1 --date "$DATE" 2>&1 | tee -a "$LOG"
echo "C2-present-off exit=${PIPESTATUS[0]}" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- [2/2] C2 absent, egress=off (50 q, shadow corpus :8081) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.run_c2 --arm absent \
    --base-url http://127.0.0.1:8081 --email "$EMAIL" \
    --concurrency 1 --date "$DATE" 2>&1 | tee -a "$LOG"
echo "C2-absent-off exit=${PIPESTATUS[0]}" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "--- regenerating risk-coverage (egress=off C2 now canonical) $(date -Is) ---" | tee -a "$LOG"
$PY -m munin_bench.abstention.risk_coverage --date "$DATE" 2>&1 | tee -a "$LOG"

echo "=== Track C phase 2 done $(date -Is) ===" | tee -a "$LOG"
