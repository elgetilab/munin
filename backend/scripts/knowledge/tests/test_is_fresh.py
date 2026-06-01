"""
Tests for `build_embedding_map.is_fresh`.

Regression guard for the 2026-05-12 incident: the nightly labelling run
hit vLLM 404s (stale model name in the systemd unit env after a model
upgrade) and emitted /opt/munin/knowledge/embedding_map.json with all
411 cluster labels falling back to 'cluster-N'. From May 14 onwards
every nightly run hit `is_fresh` (cluster_id present on every point →
treated as fresh → skipped), so the busted labels persisted for almost
three weeks. The fix adds a label-quality gate: if more than
FALLBACK_FAIL_THRESHOLD of real clusters in the existing JSON are
'cluster-N' fallbacks, the file is NOT fresh and a rebuild is forced.

Pure Python (no Qdrant, no vLLM). Run:
    python scripts/knowledge/tests/test_is_fresh.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_KNOWLEDGE_DIR = os.path.dirname(_HERE)
_BUILD_PATH = os.path.join(_KNOWLEDGE_DIR, "build_embedding_map.py")


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _load_build_module():
    """Import build_embedding_map.py for symbol access without running
    its main(). Registers the module in `sys.modules` BEFORE executing
    so the `@dataclass` decorator on PaperPoint can resolve its own
    type annotations (without the registration, dataclasses raises an
    AttributeError on the module-not-found lookup)."""
    name = "build_embedding_map_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _BUILD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


@dataclass
class FakeRecord:
    """Stand-in for PaperPoint: is_fresh only reads existing_cluster_id."""
    existing_cluster_id: Optional[int]


def _write_map(
    path: Path,
    paper_count: int,
    clusters: list[dict],
) -> None:
    payload = {
        "generated_at": "2026-05-13T00:00:00Z",
        "paper_count": paper_count,
        "cluster_count": len(clusters),
        "points": [],
        "clusters": clusters,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_missing_file_is_not_fresh() -> bool:
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "embedding_map.json")
        records = [FakeRecord(existing_cluster_id=0)]
        ok = not mod.is_fresh(path, paper_count=1, records=records)
        return _check("missing file -> not fresh", ok)


def test_paper_count_mismatch_is_not_fresh() -> bool:
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        _write_map(
            path,
            paper_count=10,
            clusters=[{"id": 0, "label": "Real label", "size": 5}],
        )
        records = [FakeRecord(existing_cluster_id=0)] * 11
        ok = not mod.is_fresh(str(path), paper_count=11, records=records)
        return _check("paper_count mismatch -> not fresh", ok)


def test_records_missing_cluster_id_is_not_fresh() -> bool:
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        _write_map(
            path,
            paper_count=2,
            clusters=[{"id": 0, "label": "Real label", "size": 2}],
        )
        records = [
            FakeRecord(existing_cluster_id=0),
            FakeRecord(existing_cluster_id=None),
        ]
        ok = not mod.is_fresh(str(path), paper_count=2, records=records)
        return _check("missing cluster_id on a record -> not fresh", ok)


def test_all_fallback_labels_is_not_fresh() -> bool:
    """The May 2026 incident shape: every cluster fell back to
    'cluster-N'. Must force rebuild."""
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        clusters = [
            {"id": cid, "label": f"cluster-{cid}", "size": 1}
            for cid in range(10)
        ]
        _write_map(path, paper_count=10, clusters=clusters)
        records = [FakeRecord(existing_cluster_id=0)] * 10
        ok = not mod.is_fresh(str(path), paper_count=10, records=records)
        return _check(
            "all cluster-N fallback labels -> not fresh (force rebuild)",
            ok,
        )


def test_majority_fallback_labels_is_not_fresh() -> bool:
    """Half-and-half is on the boundary of FALLBACK_FAIL_THRESHOLD;
    use a clear majority (60%) to trigger rebuild."""
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        clusters = [
            {"id": 0, "label": "Real topic A", "size": 5},
            {"id": 1, "label": "Real topic B", "size": 5},
            {"id": 2, "label": "cluster-2", "size": 5},
            {"id": 3, "label": "cluster-3", "size": 5},
            {"id": 4, "label": "cluster-4", "size": 5},
        ]
        _write_map(path, paper_count=25, clusters=clusters)
        records = [FakeRecord(existing_cluster_id=0)] * 25
        ok = not mod.is_fresh(str(path), paper_count=25, records=records)
        return _check(
            "60% cluster-N fallback labels -> not fresh", ok
        )


def test_real_labels_are_fresh() -> bool:
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        clusters = [
            {"id": 0, "label": "Solid-state NMR", "size": 5},
            {"id": 1, "label": "Lipid raft biology", "size": 5},
            {"id": 2, "label": "Transformer attention mechanisms", "size": 5},
        ]
        _write_map(path, paper_count=15, clusters=clusters)
        records = [FakeRecord(existing_cluster_id=0)] * 15
        ok = mod.is_fresh(str(path), paper_count=15, records=records)
        return _check("all real labels -> fresh", ok)


def test_noise_cluster_does_not_count_as_fallback() -> bool:
    """The noise cluster (id == NOISE_CLUSTER_ID, -1) is statically
    labelled 'Unclustered' and must NOT be treated as a fallback even
    if it were ever labelled 'cluster--1'."""
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        clusters = [
            {"id": -1, "label": "Unclustered", "size": 50},
            {"id": 0, "label": "Solid-state NMR", "size": 5},
            {"id": 1, "label": "Lipid raft biology", "size": 5},
        ]
        _write_map(path, paper_count=60, clusters=clusters)
        records = [FakeRecord(existing_cluster_id=0)] * 60
        ok = mod.is_fresh(str(path), paper_count=60, records=records)
        return _check(
            "noise cluster ignored in label-quality gate", ok
        )


def test_minority_fallback_is_still_fresh() -> bool:
    """vLLM hiccups during one cluster's labelling shouldn't force a
    full rebuild — only systemic failure should. 1/5 = 20% < threshold."""
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        clusters = [
            {"id": 0, "label": "Real A", "size": 5},
            {"id": 1, "label": "Real B", "size": 5},
            {"id": 2, "label": "Real C", "size": 5},
            {"id": 3, "label": "Real D", "size": 5},
            {"id": 4, "label": "cluster-4", "size": 5},
        ]
        _write_map(path, paper_count=25, clusters=clusters)
        records = [FakeRecord(existing_cluster_id=0)] * 25
        ok = mod.is_fresh(str(path), paper_count=25, records=records)
        return _check("20% fallback labels -> still fresh", ok)


def test_corrupt_json_is_not_fresh() -> bool:
    mod = _load_build_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "embedding_map.json"
        path.write_text("{ not valid json", encoding="utf-8")
        records = [FakeRecord(existing_cluster_id=0)] * 5
        ok = not mod.is_fresh(str(path), paper_count=5, records=records)
        return _check("corrupt JSON -> not fresh", ok)


def main() -> int:
    tests = [
        test_missing_file_is_not_fresh,
        test_paper_count_mismatch_is_not_fresh,
        test_records_missing_cluster_id_is_not_fresh,
        test_all_fallback_labels_is_not_fresh,
        test_majority_fallback_labels_is_not_fresh,
        test_real_labels_are_fresh,
        test_noise_cluster_does_not_count_as_fallback,
        test_minority_fallback_is_still_fresh,
        test_corrupt_json_is_not_fresh,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(
        f"\n{len(results) - failed}/{len(results)} passed; {failed} failed."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
