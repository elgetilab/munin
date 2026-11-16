"""Tests for chat_store.write_txn and the chats.db write-lock fix.

Why (docs/agent-track/CHATDB-LOCK-PLAN.md): on 2026-11-16 a save for a
deleted conversation failed its foreign-key check after its INSERT had taken
the write lock, nothing rolled back, and production held the chats.db write
lock until a restart. Every write now runs in write_txn: serialised per
process, BEGIN IMMEDIATE, commit or rollback on any exception.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_chatdb_txn.py
"""
from __future__ import annotations

import ast
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

_DB_DIR = tempfile.mkdtemp(prefix="munin-test-chatdb-")
DB = os.path.join(_DB_DIR, "chats.db")
os.environ["CHATS_DB_PATH"] = DB

import chat_store  # noqa: E402

STORES = ["chat_store.py", "artifact_store.py", "user_profile_store.py",
          "memory_proposals_store.py", "research_store.py", "project_store.py",
          "plan_store.py", "memory_store.py"]
# Functions allowed to call commit() themselves.
COMMIT_OK = {("chat_store.py", "init_db"), ("chat_store.py", "write_txn")}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _other_can_write(timeout: float = 1.0) -> bool:
    c = sqlite3.connect(DB, timeout=timeout)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def _run(coro_fn):
    """Fresh connection per test: asyncio.run gives each its own loop."""
    async def wrapper():
        await chat_store.init_db()
        try:
            return await coro_fn()
        finally:
            await chat_store.close_db()
    return asyncio.run(wrapper())


async def _conv() -> str:
    c = await chat_store.create_conversation(user_email="t@example.org", persona="code")
    return c["id"]


# --- the incident ----------------------------------------------------------------

def test_fk_failure_rolls_back_and_releases_the_lock() -> bool:
    async def go():
        err = None
        try:
            await chat_store.add_message("no-such-conversation", "assistant", "x")
        except Exception as e:
            err = type(e).__name__
        db = await chat_store.get_db()
        return err, db.in_transaction, await asyncio.to_thread(_other_can_write)
    err, in_tx, free = _run(go)
    return _check("an FK-failing save rolls back and frees the write lock",
                  err == "IntegrityError" and not in_tx and free, f"{err} in_tx={in_tx} free={free}")


def test_stale_snapshot_does_not_wedge_writes() -> bool:
    async def go():
        cid = await _conv()
        for i in range(3):
            await chat_store.add_message(cid, "user", f"m{i}")
        db = await chat_store.get_db()

        async def reader():
            # A half-read multi-row cursor pins a read snapshot.
            cur = await db.execute("SELECT content FROM messages")
            await cur.fetchone()
            await asyncio.sleep(0.3)
            await cur.close()

        def other_process_commits():
            c = sqlite3.connect(DB, timeout=5)
            c.execute("UPDATE conversations SET title = 'x'")
            c.commit()
            c.close()

        rt = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        await asyncio.to_thread(other_process_commits)
        msg = await chat_store.add_message(cid, "user", "after")
        await rt
        return msg["index_in_conversation"], db.in_transaction
    idx, in_tx = _run(go)
    return _check("a stale read snapshot is waited out, not a wedge", idx == 3 and not in_tx,
                  f"idx={idx} in_tx={in_tx}")


def test_cancelled_unit_rolls_back() -> bool:
    async def go():
        cid = await _conv()

        async def unit():
            async with chat_store.write_txn() as db:
                await db.execute("UPDATE conversations SET title = 'half' WHERE id = ?", (cid,))
                await asyncio.sleep(5)
        t = asyncio.create_task(unit())
        await asyncio.sleep(0.1)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        conv = await chat_store.get_conversation(cid, "t@example.org")
        db = await chat_store.get_db()
        return conv["title"], db.in_transaction, await asyncio.to_thread(_other_can_write)
    title, in_tx, free = _run(go)
    return _check("a cancelled unit is rolled back", title != "half" and not in_tx and free,
                  f"title={title!r} in_tx={in_tx} free={free}")


def test_failed_unit_is_not_committed_by_another() -> bool:
    async def go():
        cid = await _conv()

        async def failing():
            async with chat_store.write_txn() as db:
                await db.execute("UPDATE conversations SET title = 'partial' WHERE id = ?", (cid,))
                await asyncio.sleep(0.2)
                raise RuntimeError("boom")

        async def other():
            await asyncio.sleep(0.05)
            await chat_store.add_message(cid, "user", "hello")
        r = await asyncio.gather(failing(), other(), return_exceptions=True)
        conv = await chat_store.get_conversation(cid, "t@example.org")
        return r, conv["title"], len(conv["messages"])
    r, title, n = _run(go)
    return _check("another coroutine's commit does not commit a failed unit",
                  isinstance(r[0], RuntimeError) and title != "partial" and n == 1,
                  f"{r} title={title!r} n={n}")


def test_nested_write_txn_joins() -> bool:
    async def go():
        cid = await _conv()
        async with chat_store.write_txn():
            await asyncio.wait_for(chat_store.add_message(cid, "user", "inner"), 3)
        conv = await chat_store.get_conversation(cid, "t@example.org")
        return len(conv["messages"])
    n = _run(go)
    return _check("a write unit inside another joins it (no deadlock)", n == 1, f"n={n}")


def test_research_tables_ddl_is_a_write_unit() -> bool:
    import research_store

    async def go():
        research_store._tables_ready = False
        await research_store.create_job("j1", None, "t@example.org", "q")
        await research_store.append_event("j1", {"t": 1.0, "kind": "x"})
        db = await chat_store.get_db()
        cur = await db.execute("SELECT COUNT(*) AS n FROM research_events")
        return (await cur.fetchone())["n"], db.in_transaction
    n, in_tx = _run(go)
    return _check("research_store tables and writes go through write_txn", n == 1 and not in_tx,
                  f"n={n} in_tx={in_tx}")


# --- guard: no store commits outside write_txn ---------------------------------------

def test_no_commit_outside_write_txn() -> bool:
    bad = []
    for f in STORES:
        src = (ROOT / f).read_text()
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (f, fn.name) in COMMIT_OK:
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("commit", "executescript")
                        and fn.name not in {g.name for g in ast.walk(fn) if g is not fn
                                            and isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef))}):
                    bad.append(f"{f}:{n.lineno} {fn.name} {n.func.attr}")
    return _check("no store calls commit()/executescript outside write_txn", not bad, str(bad))


# --- deleting a chat stops its running turn ---------------------------------------------

def test_delete_stops_the_live_turn() -> bool:
    import stream_registry
    import main

    class FakeStream:
        def __init__(self):
            self.done = False
            self.cancel_event = asyncio.Event()
            self.cancel_reason = None

    async def go():
        s = FakeStream()

        async def turn():
            await s.cancel_event.wait()
            await asyncio.sleep(0.2)   # the turn saves, then finishes
            s.done = True
        t = asyncio.create_task(turn())
        orig = stream_registry.registry.find_for_conversation
        stream_registry.registry.find_for_conversation = lambda cid, u: s
        try:
            await main._stop_live_turn("c1", "t@example.org")
        finally:
            stream_registry.registry.find_for_conversation = orig
        await t
        return s.cancel_reason, s.done
    reason, done = asyncio.run(go())
    return _check("deleting a chat cancels its live turn and waits for it",
                  reason == "conversation deleted" and done, f"{reason} {done}")


# --- two processes hammering one file ------------------------------------------------------

WORKER = textwrap.dedent('''
    import asyncio, os, sys, random, collections
    sys.path.insert(0, sys.argv[1])
    os.environ["CHATS_DB_PATH"] = sys.argv[2]
    import chat_store

    async def main():
        await chat_store.init_db()
        errs = collections.Counter()
        me = f"u{os.getpid()}@x"
        conv = await chat_store.create_conversation(user_email=me, persona="code")
        async def one(i):
            try:
                if i % 7 == 0:
                    c = await chat_store.create_conversation(user_email=me, persona="code")
                    await chat_store.add_message(c["id"], "user", "x" * 2000)
                    await chat_store.delete_conversation(c["id"], me)
                else:
                    await chat_store.add_message(conv["id"], "user", "y" * random.randint(10, 20000))
            except Exception as e:
                errs[type(e).__name__ + ": " + str(e)[:40]] += 1
        for _ in range(int(sys.argv[3])):
            await asyncio.gather(*(one(i) for i in range(8)))
        db = await chat_store.get_db()
        print("RESULT", sum(errs.values()), db.in_transaction, dict(errs), flush=True)
        await chat_store.close_db()
    asyncio.run(main())
''')


def test_two_processes_share_the_file_without_errors() -> bool:
    d = tempfile.mkdtemp(prefix="munin-test-chatdb-2p-")
    try:
        db = os.path.join(d, "chats.db")
        w = os.path.join(d, "worker.py")
        Path(w).write_text(WORKER)
        env = dict(os.environ, CHATS_DB_PATH=db)
        subprocess.run([sys.executable, "-c",
                        f"import sys,asyncio; sys.path.insert(0,{str(ROOT)!r}); import chat_store;"
                        "asyncio.run(chat_store.init_db()); asyncio.run(chat_store.close_db())"],
                       env=env, check=True, capture_output=True)
        results = []
        for _ in range(3):
            ps = [subprocess.Popen([sys.executable, "-W", "ignore", w, str(ROOT), db, "15"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                  for _ in range(2)]
            for p in ps:
                out, err = p.communicate(timeout=300)
                line = [x for x in out.splitlines() if x.startswith("RESULT")]
                results.append(line[0] if line else f"NO RESULT rc={p.returncode} {err[-300:]}")
        ok = all(r.startswith("RESULT 0 False") for r in results)
        return _check("two processes x3: no lock errors, no process left in a transaction", ok,
                      "; ".join(results))
    finally:
        shutil.rmtree(d, ignore_errors=True)


TESTS = [
    test_fk_failure_rolls_back_and_releases_the_lock,
    test_stale_snapshot_does_not_wedge_writes,
    test_cancelled_unit_rolls_back,
    test_failed_unit_is_not_committed_by_another,
    test_nested_write_txn_joins,
    test_research_tables_ddl_is_a_write_unit,
    test_no_commit_outside_write_txn,
    test_delete_stops_the_live_turn,
    test_two_processes_share_the_file_without_errors,
]


def main() -> int:
    passed = 0
    try:
        for t in TESTS:
            try:
                passed += bool(t())
            except Exception:
                traceback.print_exc()
                print(f"[FAIL] {t.__name__} raised")
    finally:
        shutil.rmtree(_DB_DIR, ignore_errors=True)
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
