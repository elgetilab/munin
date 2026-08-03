"""
Tests for the paper encoder / collection pairing guard (database.py, 2026-08).

PAPER_ENCODER and PAPERS_COLLECTION are a pair: bge-large is 1024d and belongs
with papers_bge, specter is 768d and belongs with papers. Flipping one without
the other used to produce silent empty results at query time, so
verify_paper_space() turns it into a boot failure.

Covers:
  - paper_space() reports the encoder's real width and the collection's
  - verify_paper_space() raises when the two disagree
  - a missing / unreadable collection is a warning, not a failure
    (fresh cluster, before the first ingest)

Qdrant and the encoder are stubbed, so there is no network or model load.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_paper_space.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


class _FakeEncoder:
    def __init__(self, dim: int):
        self._dim = dim

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


class _FakeQdrant:
    """Minimal stand-in exposing the two calls paper_space() makes."""

    def __init__(self, dim: int | None):
        self._dim = dim

    def collection_exists(self, _name: str) -> bool:
        return self._dim is not None

    def get_collection(self, _name: str):
        class _Vectors:
            size = self._dim

        class _Params:
            vectors = _Vectors()

        class _Config:
            params = _Params()

        class _Info:
            config = _Config()

        return _Info()


def _stub(monkey: dict, encoder_dim: int, collection_dim: int | None,
          encoder: str = "bge-large", collection: str = "papers_bge") -> None:
    """Point database at fake clients. Records originals in `monkey`."""
    monkey["get_paper_encoder"] = database.get_paper_encoder
    monkey["get_qdrant"] = database.get_qdrant
    monkey["PAPER_ENCODER"] = database.PAPER_ENCODER
    monkey["PAPERS_COLLECTION"] = database.PAPERS_COLLECTION
    monkey["_papers_collection_dim"] = database._papers_collection_dim

    database.get_paper_encoder = lambda: _FakeEncoder(encoder_dim)
    database.get_qdrant = lambda: _FakeQdrant(collection_dim)
    database.PAPER_ENCODER = encoder
    database.PAPERS_COLLECTION = collection
    database._papers_collection_dim = None   # drop the memo between cases


def _restore(monkey: dict) -> None:
    database.get_paper_encoder = monkey["get_paper_encoder"]
    database.get_qdrant = monkey["get_qdrant"]
    database.PAPER_ENCODER = monkey["PAPER_ENCODER"]
    database.PAPERS_COLLECTION = monkey["PAPERS_COLLECTION"]
    database._papers_collection_dim = monkey["_papers_collection_dim"]


def test_paper_space_reports_both_dims() -> bool:
    monkey: dict = {}
    _stub(monkey, encoder_dim=1024, collection_dim=1024)
    try:
        space = database.paper_space()
        return _check(
            "paper_space reports encoder + collection widths",
            space["encoder"] == "bge-large"
            and space["collection"] == "papers_bge"
            and space["encoder_dim"] == 1024
            and space["collection_dim"] == 1024,
            str(space))
    finally:
        _restore(monkey)


def test_matching_pair_does_not_raise() -> bool:
    monkey: dict = {}
    _stub(monkey, encoder_dim=1024, collection_dim=1024)
    try:
        database.verify_paper_space()
        return _check("matching pair boots", True)
    except Exception as e:  # noqa: BLE001
        return _check("matching pair boots", False, repr(e))
    finally:
        _restore(monkey)


def test_rollback_pair_does_not_raise() -> bool:
    """specter + papers is a legitimate pairing, just not the default one."""
    monkey: dict = {}
    _stub(monkey, encoder_dim=768, collection_dim=768,
          encoder="specter", collection="papers")
    try:
        database.verify_paper_space()
        return _check("rollback pair (specter/papers) boots", True)
    except Exception as e:  # noqa: BLE001
        return _check("rollback pair (specter/papers) boots", False, repr(e))
    finally:
        _restore(monkey)


def test_half_flip_raises() -> bool:
    """BGE encoder against the 768d legacy collection: the failure mode
    this guard exists for."""
    monkey: dict = {}
    _stub(monkey, encoder_dim=1024, collection_dim=768, collection="papers")
    try:
        database.verify_paper_space()
        return _check("half-flip raises", False, "no exception raised")
    except RuntimeError as e:
        msg = str(e)
        return _check("half-flip raises", "1024d" in msg and "768d" in msg, msg)
    except Exception as e:  # noqa: BLE001
        return _check("half-flip raises", False, f"wrong type: {e!r}")
    finally:
        _restore(monkey)


def test_missing_collection_is_not_fatal() -> bool:
    """A fresh cluster has no papers collection yet; that must not block
    startup."""
    monkey: dict = {}
    _stub(monkey, encoder_dim=1024, collection_dim=None)
    try:
        space = database.verify_paper_space()
        return _check("absent collection warns instead of raising",
                      space["collection_dim"] is None)
    except Exception as e:  # noqa: BLE001
        return _check("absent collection warns instead of raising",
                      False, repr(e))
    finally:
        _restore(monkey)


def main() -> int:
    tests = [
        test_paper_space_reports_both_dims,
        test_matching_pair_does_not_raise,
        test_rollback_pair_does_not_raise,
        test_half_flip_raises,
        test_missing_collection_is_not_fatal,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
