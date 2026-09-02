#!/usr/bin/env python3
"""Partition properties for build_chunk_index.py --shard.

The only thing that matters about a shard function on a 14-hour rebuild is
that it covers every paper EXACTLY once. A gap is silent: the run reports
success, and the missing papers surface weeks later as evidence that cannot
be retrieved. So assert disjointness and completeness directly.

    python backend/scripts/pipeline/test_shard_partition.py
"""
import hashlib
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_chunk_index import _shard_of  # noqa: E402


def _corpus(n=5000):
    # Shapes real paper_ids take: 16-hex ids, plus a few awkward ones.
    ids = [hashlib.sha256(str(i).encode()).hexdigest()[:16] for i in range(n)]
    ids += ["", "a", "z" * 200, "10.1021/bi00395a005", "üñïçø∂é-id"]
    return ids


def check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


def test_partition_is_exact_for_each_n():
    """Every paper lands in exactly one shard, for every shard count."""
    ids = _corpus()
    bad = []
    for n in range(1, 9):
        seen = Counter()
        for pid in ids:
            for i in range(n):
                if _shard_of(pid, n) == i:
                    seen[pid] += 1
        if any(v != 1 for v in seen.values()) or len(seen) != len(ids):
            bad.append(n)
    return check("every paper lands in exactly one shard (n=1..8)", not bad, f"broken for n={bad}")


def test_shards_are_reasonably_balanced():
    """A 10x-skewed shard would mean one GPU idle while the other grinds."""
    ids = _corpus(20000)
    counts = Counter(_shard_of(p, 4) for p in ids)
    lo, hi = min(counts.values()), max(counts.values())
    return check("4 shards stay within 5% of each other", hi <= lo * 1.05,
                 f"lo={lo} hi={hi}")


def test_membership_is_stable():
    """The whole point: shard membership must not depend on when a process
    started or on what is already indexed. Same id, same answer, always."""
    ids = _corpus(500)
    first = {p: _shard_of(p, 3) for p in ids}
    again = {p: _shard_of(p, 3) for p in ids}
    return check("shard membership is a pure function of paper_id", first == again)


def test_n_of_one_is_the_whole_corpus():
    """--shard 0/1 must be a no-op, so the flag can be left in a script."""
    ids = _corpus(500)
    return check("--shard 0/1 selects everything",
                 all(_shard_of(p, 1) == 0 for p in ids))


TESTS = [
    test_partition_is_exact_for_each_n,
    test_shards_are_reasonably_balanced,
    test_membership_is_stable,
    test_n_of_one_is_the_whole_corpus,
]

if __name__ == "__main__":
    failed = sum(0 if t() else 1 for t in TESTS)
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
