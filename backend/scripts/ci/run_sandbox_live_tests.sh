#!/usr/bin/env bash
# Live sandbox suites against a standalone container of the sandbox image,
# never against production. Builds the image without TeX (fast), starts it with
# no network and the one capability the service needs (SYS_ADMIN for
# `unshare --net`), runs the suites inside it and removes it again.
#
#   backend/scripts/ci/run_sandbox_live_tests.sh            # artifacts suite
#   WITH_TEX=1 backend/scripts/ci/run_sandbox_live_tests.sh  # + isolation suite
#
# The isolation suite compiles LaTeX, so it needs the image built with TeX.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TAG="munin-sandbox:ci-live"
NAME="munin-sbx-ci-$$"
TEX="${WITH_TEX:-0}"

docker build -q --build-arg INSTALL_TEX="$TEX" -t "$TAG" "$REPO/backend/sandbox" >/dev/null
docker run -d --name "$NAME" --network none --cap-add SYS_ADMIN --pids-limit 2048 "$TAG" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 60); do
    if docker exec "$NAME" python -c "import httpx; httpx.get('http://localhost:8090/healthz').raise_for_status()" 2>/dev/null; then
        break
    fi
    sleep 1
done

suites=(test_artifacts_live.py)
[ "$TEX" = "1" ] && suites+=(test_isolation_live.py)
status=0
for suite in "${suites[@]}"; do
    docker cp "$REPO/backend/sandbox/tests/$suite" "$NAME:/tmp/$suite"
    echo "== $suite"
    docker exec "$NAME" python "/tmp/$suite" || status=1
done
exit $status
