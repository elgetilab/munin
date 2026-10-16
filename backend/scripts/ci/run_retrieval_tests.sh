#!/usr/bin/env bash
# ==============================================================================
# Run the hermetic tier of backend/retrieval/tests in a container, no network
# ==============================================================================
# This is the exact command GitHub CI runs (job retrieval-tests), so a local
# run reproduces CI:
#
#   docker build -t munin-retrieval-tests:ci \
#     -f backend/scripts/ci/retrieval-tests.Dockerfile backend/retrieval
#   backend/scripts/ci/run_retrieval_tests.sh [run_suite.py args...]
#
# The repo is mounted READ-ONLY at /repo, and /app is assembled inside the
# container from symlinks: retrieval's files, plus /app/personas ->
# shared/personas and /app/config -> backend/config, which is where
# docker-compose mounts them in production. That gives the tests BOTH
# layouts at once (tests/README.md, "The three environments"): /app imports
# and the deployed fixture paths work, and since /app/tests resolves into the
# repo, the repo-relative checks (BACKEND-API.md, the SLURM scripts) run too
# instead of SKIPping. Nothing is written into the checkout, unlike a nested
# `-v ...:/app/personas` mount, which makes docker create root-owned mount
# points inside it.
#
# Env: IMAGE (default munin-retrieval-tests:ci). IMAGE=munin-retrieval:latest
# runs the same checkout against the production dependency set instead. Extra arguments go to
# run_suite.py, e.g. --only test_router. The live tier is not run from here:
# it needs munin-network, see backend/scripts/ci/nightly_live_tests.sh.
# ==============================================================================

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE="${IMAGE:-munin-retrieval-tests:ci}"

exec docker run --rm --network none \
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
    -v "$REPO:/repo:ro" \
    "$IMAGE" \
    bash -c '
        set -e
        # The production image bakes the deployed code into /app; replace it
        # (this container is throwaway) so the checkout is what gets tested.
        rm -rf /app
        mkdir /app
        ln -s /repo/backend/retrieval/* /app/
        # -fn: replace, not nest into, a stray personas/ or config/ left in
        # backend/retrieval by an earlier ad-hoc nested mount.
        ln -sfn /repo/shared/personas /app/personas
        ln -sfn /repo/backend/config /app/config
        cd /app
        exec python /app/tests/run_suite.py --tier hermetic "$@"
    ' run_suite "$@"
