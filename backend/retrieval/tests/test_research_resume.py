"""
Deep Research start/resume must not trust client-supplied ids.

Regression for the 2026-09 review findings S2 and S7: POST /api/research/start
used `resume_job_id` as the job id unchecked, so "../../app/x" became a
checkpoint path (arbitrary .json write and read as root) and another user's
`dr_` id was INSERT OR REPLACEd to the caller; `conversation_id` was never
ownership-checked, so the question landed in someone else's chat.

Drives the real router through httpx.ASGITransport on a bare FastAPI app, so
main.py and its model loading are never imported; the detached research task
is replaced by a no-op.

Run:
    python backend/retrieval/tests/test_research_resume.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMPDIR = tempfile.mkdtemp(prefix="munin-test-research-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")
os.environ["DEEP_RESEARCH_DIR"] = os.path.join(_TMPDIR, "checkpoints")

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import chat_store  # noqa: E402
import deep_research_agent as dr  # noqa: E402
import deep_research_manager as manager  # noqa: E402
import research_store  # noqa: E402
from research_routes import router  # noqa: E402

ALICE = "alice@test.local"
BOB = "bob@test.local"
BOB_JOB = "dr_" + "b" * 16


async def _noop_run(*_a, **_k) -> None:
    return None


manager._run = _noop_run  # never start a real research task

app = FastAPI()
app.include_router(router)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _start(email: str, **body) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        return await c.post("/api/research/start", json={"question": "q?", **body},
                            headers={"X-Munin-Email": email})


async def _setup() -> str:
    await chat_store.get_db()
    conv = await chat_store.create_conversation(user_email=BOB, persona="munin", title="bob")
    await research_store.create_job(BOB_JOB, conv["id"], BOB, "bob's question")
    return conv["id"]


async def test_traversal_id_rejected(_bob_conv) -> bool:
    target = os.path.join(_TMPDIR, "escaped.json")
    r = await _start(ALICE, resume_job_id="../escaped")
    return (_check("traversal resume id -> 400", r.status_code == 400, r.text)
            and _check("no file written outside the checkpoint dir",
                       not os.path.exists(target)))


async def test_other_users_job_not_taken(_bob_conv) -> bool:
    r = await _start(ALICE, resume_job_id=BOB_JOB)
    job = await research_store.get_job(BOB_JOB)
    return (_check("resume of another user's job -> 400", r.status_code == 400, r.text)
            and _check("job still owned by its owner", job["user_email"] == BOB, str(job)))


async def test_running_job_not_restarted(_bob_conv) -> bool:
    manager._tasks[BOB_JOB] = object()  # stands in for a live task
    try:
        r = await _start(BOB, resume_job_id=BOB_JOB)
    finally:
        manager._tasks.pop(BOB_JOB, None)
    return _check("resume of a running job -> 400", r.status_code == 400, r.text)


async def test_owner_can_resume(_bob_conv) -> bool:
    r = await _start(BOB, resume_job_id=BOB_JOB, conversation_id=_bob_conv)
    return _check("owner resumes own idle job -> 200 with same id",
                  r.status_code == 200 and r.json()["job_id"] == BOB_JOB, r.text)


async def test_foreign_conversation_rejected(bob_conv) -> bool:
    before = len((await chat_store.get_conversation(bob_conv, BOB))["messages"])
    r = await _start(ALICE, conversation_id=bob_conv)
    after = len((await chat_store.get_conversation(bob_conv, BOB))["messages"])
    return (_check("start into another user's conversation -> 404", r.status_code == 404, r.text)
            and _check("no message written into it", before == after, f"{before}->{after}"))


async def test_fresh_start_still_works(_bob_conv) -> bool:
    r = await _start(ALICE)
    return _check("fresh start without ids -> 200",
                  r.status_code == 200 and r.json()["job_id"].startswith("dr_"), r.text)


def test_checkpoint_path_guard() -> bool:
    return (_check("checkpoint path refuses traversal", dr._checkpoint_path("../x") is None)
            and _check("checkpoint path refuses non-minted ids", dr._checkpoint_path("dr_x") is None)
            and _check("checkpoint path accepts a minted id",
                       dr._checkpoint_path("dr_" + "0" * 16) is not None))


async def _main() -> int:
    bob_conv = await _setup()
    results = []
    try:
        for t in (test_checkpoint_path_guard, test_traversal_id_rejected,
                  test_other_users_job_not_taken, test_running_job_not_restarted,
                  test_owner_can_resume, test_foreign_conversation_rejected,
                  test_fresh_start_still_works):
            try:
                r = t() if t is test_checkpoint_path_guard else await t(bob_conv)
                results.append(r)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                results.append(_check(t.__name__, False, "raised"))
    finally:
        await chat_store.close_db()  # aiosqlite's thread keeps the process alive
    passed = sum(results)
    print(f"\n{passed} passed, {len(results) - passed} failed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
