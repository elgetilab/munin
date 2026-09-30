"""
Document ids must never escape the caller's own document directory.

Regression for the 2026-09 review finding S3: document_id was joined into a
path unchecked, so DELETE /api/documents/%2e%2e resolved to the parent of the
caller's directory and rmtree'd every user's uploads, and a chat content
block with "../<hash>/<doc_id>" read another user's file.

Run:
    python backend/retrieval/tests/test_document_id_guard.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import document_store as DS  # noqa: E402

ALICE = "alice@example.org"
BOB = "bob@example.org"
BOB_DOC = "doc_0000000000b0"


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="munin-test-docid-")
    DS.USER_DOCS_DIR = tmp
    DS.get_qdrant = lambda: None  # no vector store in this test

    alice_doc = os.path.join(tmp, DS._email_hash(ALICE), "doc_0000000000a0")
    bob_dir = os.path.join(tmp, DS._email_hash(BOB), BOB_DOC)
    for d, name in ((alice_doc, "a.txt"), (bob_dir, "secret.txt")):
        os.makedirs(d)
        Path(d, name).write_text("x")

    results = []
    for bad in ("..", ".", "../..", f"../{DS._email_hash(BOB)}/{BOB_DOC}", "", None):
        deleted = asyncio.run(DS.delete_document(bad, ALICE))
        results.append(_check(f"delete_document({bad!r}) refused", deleted is False))
    results.append(_check("other users' files survive", os.path.isfile(Path(bob_dir, "secret.txt"))))
    results.append(_check("caller's own files survive", os.path.isfile(Path(alice_doc, "a.txt"))))

    cross = DS.get_document_file_path(ALICE, f"../{DS._email_hash(BOB)}/{BOB_DOC}")
    results.append(_check("cross-user path lookup refused", cross is None, str(cross)))
    results.append(_check("own document still resolves",
                          DS.get_document_file_path(ALICE, "doc_0000000000a0") is not None))
    results.append(_check("own document still deletes",
                          asyncio.run(DS.delete_document("doc_0000000000a0", ALICE)) is True
                          and not os.path.exists(alice_doc)))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
