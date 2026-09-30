"""
The retired MiroThinker /deepresearch/* routes are admin-only.

Regression for the 2026-09 review finding S5: the routes took no identity, and
the gateway retries a 404 on /api/X as /X, so any logged-in user could list
every user's past research questions (/deepresearch/jobs) and download any
report (/deepresearch/output/{id}). The jobs never recorded a submitter, so
admin-only is the only safe gate. The request id is also joined into a path,
so it must parse as a UUID.

Imports `main` (needs the container's deps); drives it through
httpx.ASGITransport, so no startup hook or model load runs.

    docker exec munin-retrieval python /app/tests/test_legacy_dr_routes.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import main  # noqa: E402

ADMIN = "admin@test.local"
USER = "user@test.local"
JOB = str(uuid.uuid4())


async def _role(_client, email: str) -> str:
    return "admin" if email == ADMIN else "user"


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _get(path: str, email: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://t") as c:
        return await c.get(path, headers={"X-Munin-Email": email})


async def _main() -> int:
    tmp = tempfile.mkdtemp(prefix="munin-test-legacy-dr-")
    jobs = os.path.join(tmp, "jobs")
    os.makedirs(os.path.join(jobs, JOB))
    Path(jobs, JOB, "output.md").write_text("# someone's report")
    Path(tmp, "secret.md").write_text("outside the jobs dir")
    main.DEEPRESEARCH_JOBS_DIR = jobs
    main.DEEPRESEARCH_QUEUE_DIR = os.path.join(tmp, "queue")
    main.metrics_proxy.is_configured = lambda: True
    main.metrics_proxy.lookup_role = _role

    results = []
    for path in ("/deepresearch/jobs", f"/deepresearch/status/{JOB}",
                 f"/deepresearch/output/{JOB}", "/deepresearch/queue"):
        r = await _get(path, USER)
        results.append(_check(f"non-admin {path} -> 403", r.status_code == 403,
                              f"{r.status_code} {r.text[:120]}"))
    r = await _get(f"/deepresearch/output/{JOB}", ADMIN)
    results.append(_check("admin can still read a report",
                          r.status_code == 200 and "report" in r.text, str(r.status_code)))
    r = await _get("/deepresearch/output/..%2Fsecret", ADMIN)
    results.append(_check("non-UUID request id -> 404", r.status_code == 404, str(r.status_code)))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
