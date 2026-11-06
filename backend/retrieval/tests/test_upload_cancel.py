"""
Tests for two faults found on the live site on 2026-11-06:

- An upload stopped retrieval answering anything else. A 60 KB text file held it
  up for about 30 seconds and larger PDFs for over 2 minutes, because embedding
  ran on the event loop.
- Cancel only stopped the browser. The gateway does not pass the disconnect on, so
  the document was stored anyway.

    docker exec munin-retrieval python /app/tests/test_upload_cancel.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upload_tracker import UploadTracker  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def test_tracker() -> None:
    now = [0.0]
    t = UploadTracker(ttl_sec=100, clock=lambda: now[0])

    check("cancel before finish: nothing to remove yet", t.cancel("a", "u1") is None)
    check("cancel before finish: the route will see it", t.is_cancelled("a", "u1"))
    check("another user's upload is not affected", not t.is_cancelled("b", "u1"))

    t.finish("a", "u2", "doc2")
    check("a finished upload is not cancelled", not t.is_cancelled("a", "u2"))
    check("cancel after finish names the document", t.cancel("a", "u2") == "doc2")
    check("that document is only named once", t.cancel("a", "u2") is None)

    t.finish("a", "u3", "doc3")
    check("another user cannot cancel it", t.cancel("b", "u3") is None)
    check("the owner still can", t.cancel("a", "u3") == "doc3")

    t.finish("a", "u4", "doc4")
    now[0] = 200.0
    check("a finished upload is forgotten after the TTL", t.cancel("a", "u4") is None)
    now[0] = 400.0
    t.cancel("a", "other")
    check("a cancel is forgotten after the TTL", not t.is_cancelled("a", "u4"))


class _FakeQdrant:
    def __init__(self) -> None:
        self.points: list = []

    def upsert(self, collection_name, points) -> None:
        self.points.extend(points)


class _SlowBGE:
    """Stands in for the model: slow per text, and records the largest batch."""

    def __init__(self) -> None:
        self.largest = 0
        self.lock = threading.Lock()

    def encode(self, chunks, show_progress_bar=False):
        import numpy as np
        with self.lock:
            self.largest = max(self.largest, len(chunks))
        time.sleep(0.05 * len(chunks))
        return np.zeros((len(chunks), 768), dtype="float32")


def _with_store(run) -> None:
    DS = importlib.import_module("document_store")
    saved = {n: getattr(DS, n) for n in ("USER_DOCS_DIR", "get_qdrant", "get_bge", "ensure_collection")}
    qdrant, bge = _FakeQdrant(), _SlowBGE()
    with tempfile.TemporaryDirectory() as tmp:
        DS.USER_DOCS_DIR = tmp
        DS.get_qdrant = lambda: qdrant
        DS.get_bge = lambda: bge
        DS.ensure_collection = lambda: None
        try:
            run(DS, qdrant, bge)
        finally:
            for n, v in saved.items():
                setattr(DS, n, v)


def test_upload_leaves_the_event_loop_free() -> None:
    def run(DS, qdrant, bge) -> None:
        async def main():
            gaps: list[float] = []
            done = asyncio.Event()

            async def ticker():
                last = time.perf_counter()
                while not done.is_set():
                    await asyncio.sleep(0.01)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            tick = asyncio.create_task(ticker())
            text = "\n\n".join(f"{i} " + "word " * 500 for i in range(30))
            started = time.perf_counter()
            result = await DS.upload_document("big.txt", text.encode(), "u@test.local")
            took = time.perf_counter() - started
            done.set()
            await tick
            return result, took, max(gaps)

        result, took, worst = asyncio.run(main())
        check("the document is embedded", result["status"] == "embedded", result)
        check("every chunk reached the index", len(qdrant.points) == result["chunks"])
        check("embedding took long enough to mean something", took > 0.5, took)
        check("the event loop kept running during embedding", worst < 0.3, f"worst gap {worst:.2f}s of {took:.2f}s")
        check("embedding is split, so a chat query waits for one batch at most",
              bge.largest <= DS.EMBED_BATCH_SIZE, bge.largest)

    _with_store(run)


def test_encoder_is_used_by_one_thread_at_a_time() -> None:
    database = importlib.import_module("database")

    class Model:
        dim = 768
        inside = 0
        most = 0

        def encode(self, text):
            Model.inside += 1
            Model.most = max(Model.most, Model.inside)
            time.sleep(0.02)
            Model.inside -= 1
            return text

    encoder = database._OneAtATimeEncoder(Model())
    threads = [threading.Thread(target=encoder.encode, args=("q",)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("only one thread is ever inside the model", Model.most == 1, Model.most)
    check("the model's other attributes still work", encoder.dim == 768)


def test_cancel_routes() -> None:
    os.environ.setdefault("MUNIN_GATEWAY_TOKEN", "")
    main = importlib.import_module("main")
    import httpx

    USER = "user@test.local"
    deleted: list[str] = []
    gate = {"release": None}

    async def fake_upload(filename, file_bytes, user_email, conversation_id=None, project_id=None):
        if gate["release"] is not None:
            await gate["release"].wait()
        return {"document_id": f"doc_{uuid.uuid4().hex[:12]}", "filename": filename,
                "chunks": 1, "status": "embedded", "upload_time": "t"}

    async def fake_delete(document_id, user_email):
        deleted.append(document_id)
        return True

    saved = (main.document_store.upload_document, main.document_store.delete_document,
             main._require_user_email, main.upload_tracker)
    main.document_store.upload_document = fake_upload
    main.document_store.delete_document = fake_delete
    main._require_user_email = lambda request: USER
    main.upload_tracker = UploadTracker()

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            files = {"file": ("a.txt", b"hello", "text/plain")}

            r = await c.post("/api/documents/upload", files=files)
            check("an upload with no id still works", r.status_code == 200, r.text)

            r = await c.post("/api/documents/upload", files=files, data={"upload_id": "bad id!"})
            check("a malformed upload id is refused", r.status_code == 400, r.status_code)
            r = await c.post("/api/documents/upload/bad id!/cancel")
            check("a malformed cancel id is refused", r.status_code == 400, r.status_code)

            # Cancel lands while the upload is being processed.
            gate["release"] = asyncio.Event()
            pending = asyncio.create_task(
                c.post("/api/documents/upload", files=files, data={"upload_id": "during-1234"}))
            await asyncio.sleep(0.05)
            r = await c.post("/api/documents/upload/during-1234/cancel")
            check("cancel during: accepted", r.status_code == 200 and r.json()["deleted"] is False, r.text)
            gate["release"].set()
            r = await pending
            check("cancel during: the upload reports it", r.status_code == 409, r.status_code)
            check("cancel during: the document is removed", len(deleted) == 1, deleted)
            gate["release"] = None

            # Cancel lands after the upload finished.
            r = await c.post("/api/documents/upload", files=files, data={"upload_id": "after-12345"})
            doc = r.json()["document_id"]
            r = await c.post("/api/documents/upload/after-12345/cancel")
            check("cancel after: the document is removed",
                  r.json()["deleted"] is True and deleted[-1] == doc, r.text)

            # Cancel lands before the upload reaches the store.
            before = len(deleted)
            await c.post("/api/documents/upload/before-1234/cancel")
            r = await c.post("/api/documents/upload", files=files, data={"upload_id": "before-1234"})
            check("cancel before: the upload reports it", r.status_code == 409, r.status_code)
            check("cancel before: the document is removed", len(deleted) == before + 1)

    try:
        asyncio.run(run())
    finally:
        (main.document_store.upload_document, main.document_store.delete_document,
         main._require_user_email, main.upload_tracker) = saved


if __name__ == "__main__":
    only_tracker = "--tracker-only" in sys.argv
    tests = [test_tracker] if only_tracker else [
        test_tracker, test_upload_leaves_the_event_loop_free,
        test_encoder_is_used_by_one_thread_at_a_time, test_cancel_routes]
    for test in tests:
        print(f"-- {test.__name__}")
        try:
            test()
        except Exception:
            traceback.print_exc()
            failures.append(test.__name__)
    print(f"{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)
