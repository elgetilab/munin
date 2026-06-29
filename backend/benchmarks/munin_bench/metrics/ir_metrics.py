"""IR ranking metrics.

Conventions shared by every function here:

- ``ranked`` is a list of document ids in rank order (best first). Ids may
  repeat in pathological inputs; only the first occurrence counts toward
  rank-sensitive metrics (MRR), matching TREC behaviour.
- Graded relevance uses ``qrels``: ``{doc_id: grade}`` with integer grades
  (0/1/2/3). A doc absent from ``qrels`` is grade 0 (non-relevant).
- Binary metrics take ``relevant``: the set of doc ids with grade >= 1.

nDCG uses linear gain (the raw grade, not ``2**grade - 1``) and a
``log2(rank + 1)`` discount with 1-indexed ranks, so the top position has
discount ``log2(2) = 1``. The ideal DCG is computed from the qrels grades
themselves, never from the ranked list. Worked example (also a test):
``ndcg_at_k(['a','b','c'], {'a': 2, 'c': 1}, k=3)`` -> DCG = 2 + 0 +
1/log2(4) = 2.5, IDCG = 2 + 1/log2(3) ~= 2.6309, nDCG ~= 0.9501.
"""

from __future__ import annotations

import math


def _dcg(grades: list[int]) -> float:
    """Discounted cumulative gain over an already-ordered list of grades."""
    return sum(g / math.log2(rank + 1) for rank, g in enumerate(grades, start=1))


def ndcg_at_k(ranked: list[str], qrels: dict[str, int], k: int) -> float:
    """Normalised DCG at cutoff ``k`` (graded, linear gain, log2 discount).

    Returns 0.0 when the ideal DCG is 0 (no relevant docs in ``qrels``).
    """
    if k <= 0:
        return 0.0
    gains = [int(qrels.get(doc, 0)) for doc in ranked[:k]]
    dcg = _dcg(gains)
    ideal_grades = sorted((int(g) for g in qrels.values()), reverse=True)[:k]
    idcg = _dcg(ideal_grades)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the relevant set retrieved in the top ``k``.

    Returns 0.0 when there are no relevant docs (degenerate; reported as 0
    so it never silently inflates an average).
    """
    if not relevant or k <= 0:
        return 0.0
    top = set(ranked[:k])
    return len(top & relevant) / len(relevant)


def mrr(ranked: list[str], relevant: set[str]) -> float:
    """Reciprocal rank of the first relevant doc (0.0 if none present)."""
    if not relevant:
        return 0.0
    for rank, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def hits_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """1.0 if at least one relevant doc appears in the top ``k``, else 0.0."""
    if not relevant or k <= 0:
        return 0.0
    return 1.0 if set(ranked[:k]) & relevant else 0.0
