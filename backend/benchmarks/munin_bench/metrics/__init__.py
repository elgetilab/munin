"""Metric primitives for the Munin eval suite (Track A Phase 1).

Pure functions, no I/O. Everything downstream (BEIR, local pool, LitQA2,
and the later answer/abstention/harness tracks) scores through these and
reports significance through the bootstrap + Wilcoxon helpers here.
"""

from .ir_metrics import hits_at_k, mrr, ndcg_at_k, recall_at_k
from .bootstrap import paired_bootstrap, single_bootstrap
from .significance import paired_wilcoxon

__all__ = [
    "ndcg_at_k",
    "recall_at_k",
    "mrr",
    "hits_at_k",
    "paired_bootstrap",
    "single_bootstrap",
    "paired_wilcoxon",
]
