"""Paired significance test (Wilcoxon signed-rank) over per-query metrics.

A non-parametric companion to the bootstrap CI: reported alongside it so a
claimed improvement carries both an effect size (mean diff + CI) and a
distribution-free p-value.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import wilcoxon


def paired_wilcoxon(a: list[float], b: list[float]) -> dict:
    """Wilcoxon signed-rank test on the paired differences ``a - b``.

    Returns ``{statistic, p_value, n_nonzero}``. When every pair is tied
    (all differences zero) the test is undefined; we report ``p_value=1.0``,
    ``statistic=0.0``, ``n_nonzero=0`` rather than raising, so a no-change
    comparison degrades gracefully in a scorecard.
    """
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    if arr_a.shape != arr_b.shape:
        raise ValueError("paired_wilcoxon: a and b must be the same length")
    if arr_a.size == 0:
        raise ValueError("paired_wilcoxon: empty input")

    diffs = arr_a - arr_b
    n_nonzero = int(np.count_nonzero(diffs))
    if n_nonzero == 0:
        return {"statistic": 0.0, "p_value": 1.0, "n_nonzero": 0}

    # zero_method="wilcox" drops zero-differences, matching n_nonzero.
    result = wilcoxon(arr_a, arr_b, zero_method="wilcox")
    return {
        "statistic": float(result.statistic),
        "p_value": float(result.pvalue),
        "n_nonzero": n_nonzero,
    }
