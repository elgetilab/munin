#!/usr/bin/env bash
# ==============================================================================
# Nightly retrieval test run on hugin: the live tier, plus the hermetic tier
# against the deployed image
# ==============================================================================
# GitHub CI runs the hermetic tier on every push (no network, no index). What it
# cannot see is the real Qdrant index and the staged encoders, so the files
# classified `live` in backend/retrieval/tests/suite.txt run here, nightly,
# against them. The hermetic tier runs too, inside the DEPLOYED image, which
# catches drift between the CI dependency set (no torch) and production's.
#
# Started by munin-nightly-tests.timer inside the 02:00-06:00 vLLM maintenance
# window. The live tier deliberately does NOT use vLLM: LLM_BASE_URL points at a
# closed port in the test container, so a run never competes with users for the
# model even if the window moves. Everything finishes by END_BY (05:45): the
# runner stops starting files once the remaining time is less than one file's
# timeout, and a hard `timeout` removes the container if it overruns anyway.
#
# Read-only against production: throwaway containers (`docker run --rm`), the
# index reached over munin-network for reads, personas/config/models mounted
# :ro, no /data mount (every test gets its own CHATS_DB_PATH under /tmp from
# run_suite.py). It never starts, stops or execs into a munin-* service.
#
# USAGE
#   nightly_live_tests.sh [--tier live|hermetic|all] [--checkout DIR]
#                         [--force] [--dry-run]
#
#   --checkout DIR  test that checkout (mounted read-only, same layout as
#                   CI) instead of the code baked into the deployed image
#   --force         run outside the 02:00-END_BY window (by hand)
#   --dry-run       print the docker commands, run nothing
#
# ENV (defaults are production)
#   IMAGE=munin-retrieval:latest  NETWORK=munin-network  MUNIN_ROOT=/opt/munin
#   PERSONAS_DIR=$MUNIN_ROOT/personas  CONFIG_DIR=$MUNIN_ROOT/config
#   MODELS_DIR=$MUNIN_ROOT/data/models  QDRANT_CONTAINER=munin-qdrant
#   LOG_ROOT=$MUNIN_ROOT/data/nightly-tests  KEEP_DAYS=14
#   WINDOW_START=02:00  END_BY=05:45  FILE_TIMEOUT=900
#
# Exit: 0 all selected tiers passed, 1 a tier failed, 2 not run (outside the
# window, or a precondition such as the image, network or Qdrant is missing).
# Results: $LOG_ROOT/<date>/<tier>.log plus one <file>.log per test file.
# ==============================================================================

set -euo pipefail

IMAGE="${IMAGE:-munin-retrieval:latest}"
NETWORK="${NETWORK:-munin-network}"
MUNIN_ROOT="${MUNIN_ROOT:-/opt/munin}"
PERSONAS_DIR="${PERSONAS_DIR:-$MUNIN_ROOT/personas}"
CONFIG_DIR="${CONFIG_DIR:-$MUNIN_ROOT/config}"
MODELS_DIR="${MODELS_DIR:-$MUNIN_ROOT/data/models}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-munin-qdrant}"
LOG_ROOT="${LOG_ROOT:-$MUNIN_ROOT/data/nightly-tests}"
KEEP_DAYS="${KEEP_DAYS:-14}"
WINDOW_START="${WINDOW_START:-02:00}"
END_BY="${END_BY:-05:45}"
FILE_TIMEOUT="${FILE_TIMEOUT:-900}"

TIER=all
CHECKOUT=""
FORCE=0
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --tier)     TIER="$2"; shift 2 ;;
        --checkout) CHECKOUT="$(cd "$2" && pwd)"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        -h|--help)  sed -n '2,45p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$TIER" in live|hermetic|all) ;; *) echo "--tier must be live, hermetic or all" >&2; exit 2 ;; esac

log() { echo "[nightly-tests] $*"; }

# --- the window ---------------------------------------------------------------
# Seconds since local midnight, so the arithmetic follows the host clock (the
# window is cluster-local time, like the vLLM schedule).
secs_of() { local h m; IFS=: read -r h m <<<"$1"; echo $((10#$h * 3600 + 10#$m * 60)); }
now=$(( $(date +%s) - $(date -d 'today 00:00' +%s) ))
start_s=$(secs_of "$WINDOW_START")
end_s=$(secs_of "$END_BY")
remaining=$(( end_s - now ))
if [ "$FORCE" = 1 ]; then
    remaining=$(( 4 * 3600 ))
elif [ "$now" -lt "$start_s" ] || [ "$remaining" -le 0 ]; then
    log "outside the ${WINDOW_START}-${END_BY} window (now $(date +%H:%M)); not running. --force to override."
    exit 2
fi
# Leave room for the last file to finish and the containers to be removed.
budget=$(( remaining - FILE_TIMEOUT - 60 ))
if [ "$budget" -le 0 ]; then
    log "only ${remaining}s left before ${END_BY}, less than one file's timeout; not running."
    exit 2
fi
log "window ok: ${remaining}s until ${END_BY}, runner budget ${budget}s, per-file ${FILE_TIMEOUT}s"

# --- preconditions (checked, never fixed: this script starts nothing) ----------
run() {
    if [ "$DRY_RUN" = 1 ]; then printf '[dry-run]'; printf ' %q' "$@"; echo; return 0; fi
    "$@"
}
precondition_failed=0
need() { if ! "$@" >/dev/null 2>&1; then log "precondition failed: $*"; precondition_failed=1; fi; }
if [ "$DRY_RUN" = 0 ]; then
    need docker image inspect "$IMAGE"
    need test -d "$PERSONAS_DIR"
    need test -d "$CONFIG_DIR"
    # The runner ships in the image from the first retrieval deploy after it
    # was added; before that only --checkout works.
    [ -n "$CHECKOUT" ] || need docker run --rm --network none "$IMAGE" test -f /app/tests/run_suite.py
    if [ "$TIER" != hermetic ]; then
        need docker network inspect "$NETWORK"
        need test "$(docker inspect -f '{{.State.Running}}' "$QDRANT_CONTAINER" 2>/dev/null)" = true
        need test -d "$MODELS_DIR/bge-large"
    fi
    if [ "$precondition_failed" = 1 ]; then exit 2; fi
fi

day="$LOG_ROOT/$(date +%F)"
run mkdir -p "$day"

# The command run inside the container. With --checkout, /app is assembled
# from symlinks into the read-only checkout, exactly as CI does
# (run_retrieval_tests.sh); otherwise the image's own /app is what is tested.
inner() {
    local tier="$1" setup=""
    if [ -n "$CHECKOUT" ]; then
        setup='rm -rf /app && mkdir /app && ln -s /repo/backend/retrieval/* /app/ && '
        setup+='ln -sfn /repo/shared/personas /app/personas && ln -sfn /repo/backend/config /app/config && '
    fi
    echo "${setup}cd /app && exec python /app/tests/run_suite.py --tier $tier \
--timeout $FILE_TIMEOUT --budget $budget --log-dir /results"
}

run_tier() {
    local tier="$1" name="munin-nightly-tests-$1-$$"
    local args=(--rm --name "$name" --cpus 4 --memory 8g
                -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e PYTHONDONTWRITEBYTECODE=1
                -v "$day/$tier:/results")
    if [ -n "$CHECKOUT" ]; then
        args+=(-v "$CHECKOUT:/repo:ro")
    else
        args+=(-v "$PERSONAS_DIR:/app/personas:ro" -v "$CONFIG_DIR:/app/config:ro")
    fi
    if [ "$tier" = live ]; then
        # Port 9 (discard) has no listener in the container: any LLM call fails
        # fast instead of reaching vLLM.
        args+=(--network "$NETWORK"
               -e QDRANT_HOST=qdrant -e QDRANT_PORT=6333
               -e LLM_BASE_URL=http://127.0.0.1:9 -e VLLM_URL=http://127.0.0.1:9
               -v "$MODELS_DIR/bge-large:/models/bge-large:ro"
               -v "$MODELS_DIR/bge-base:/models/bge-base:ro"
               -v "$MODELS_DIR/specter:/models/specter:ro")
    else
        args+=(--network none)
    fi
    run mkdir -p "$day/$tier"
    log "tier=$tier image=$IMAGE${CHECKOUT:+ checkout=$CHECKOUT}"
    local rc=0
    if [ "$DRY_RUN" = 1 ]; then
        run docker run "${args[@]}" "$IMAGE" bash -c "$(inner "$tier")"
        return 0
    fi
    timeout --signal=TERM "$((remaining - 30))" \
        docker run "${args[@]}" "$IMAGE" bash -c "$(inner "$tier")" \
        > "$day/$tier.log" 2>&1 || rc=$?
    if [ "$rc" = 124 ]; then
        log "tier=$tier overran ${END_BY}; removing its container"
        docker rm -f "$name" >/dev/null 2>&1 || true
    fi
    tail -n 3 "$day/$tier.log" | sed "s/^/[nightly-tests] $tier: /"
    return "$rc"
}

overall=0
for tier in hermetic live; do
    if [ "$TIER" = all ] || [ "$TIER" = "$tier" ]; then
        if run_tier "$tier"; then
            log "tier=$tier $([ "$DRY_RUN" = 1 ] && echo '(dry-run, not run)' || echo PASS)"
        else
            log "tier=$tier FAIL (see $day/$tier.log)"
            overall=1
        fi
        now=$(( $(date +%s) - $(date -d 'today 00:00' +%s) ))
        [ "$FORCE" = 1 ] || remaining=$(( end_s - now ))
        budget=$(( remaining - FILE_TIMEOUT - 60 ))
        if [ "$budget" -le 0 ] && [ "$tier" = hermetic ] && [ "$TIER" = all ]; then
            log "no time left for the live tier before ${END_BY}"
            overall=1
            break
        fi
    fi
done

[ "$DRY_RUN" = 1 ] || echo "$([ "$overall" = 0 ] && echo PASS || echo FAIL) $(date -Is)" > "$day/status"

# Keep two weeks of results.
if [ "$DRY_RUN" = 0 ] && [ -d "$LOG_ROOT" ]; then
    find "$LOG_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} + || true
fi
exit "$overall"
