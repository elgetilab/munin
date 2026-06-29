"""Bootstrap confidence intervals for per-query metric arrays.

Used to put CIs on a single system's mean metric and on the paired
difference between two systems over the same query set. Resampling is over
query indices (the unit of variance in IR), seeded for reproducibility so
committed scorecards are bit-stable on a fixed numpy.
"""

from __future__ import annotations

import numpy as np


def _percentile_ci(samples: np.ndarray, ci: float) -> tuple[float, float]:
    alpha = (1.0 - ci) / 2.0
    low = float(np.percentile(samples, 100.0 * alpha))
    high = float(np.percentile(samples, 100.0 * (1.0 - alpha)))
    return low, high


def paired_bootstrap(
    per_query_a: list[float],
    per_query_b: list[float],
    n_resamples: int = 1000,
    seed: int = 42,
    ci: float = 0.95,
) -> dict:
    """Paired bootstrap over the difference ``a - b`` per query.

    ``per_query_a[i]`` and ``per_query_b[i]`` are the two systems' scores on
    query ``i`` (same query order). Returns means, the observed mean
    difference, a percentile CI on the resampled mean difference, and a
    two-sided bootstrap p-value ``2 * min(P(diff <= 0), P(diff >= 0))``
    (clamped to 1.0).
    """
    a = np.asarray(per_query_a, dtype=float)
    b = np.asarray(per_query_b, dtype=float)
    if a.shape != b.shape:
        raise ValueError("paired_bootstrap: a and b must be the same length")
    if a.size == 0:
        raise ValueError("paired_bootstrap: empty input")

    diffs = a - b
    n = a.size
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    resampled_mean_diff = diffs[idx].mean(axis=1)

    ci_low, ci_high = _percentile_ci(resampled_mean_diff, ci)
    p_le = float(np.mean(resampled_mean_diff <= 0.0))
    p_ge = float(np.mean(resampled_mean_diff >= 0.0))
    p_value = min(1.0, 2.0 * min(p_le, p_ge))

    return {
        "mean_a": float(a.mean()),
        "mean_b": float(b.mean()),
        "mean_diff": float(diffs.mean()),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value_two_sided": p_value,
        "n": int(n),
        "n_resamples": int(n_resamples),
    }


def single_bootstrap(
    values: list[float],
    n_resamples: int = 1000,
    seed: int = 42,
    ci: float = 0.95,
) -> dict:
    """Unpaired percentile-bootstrap CI on the mean of one system's scores."""
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        raise ValueError("single_bootstrap: empty input")

    n = x.size
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    resampled_mean = x[idx].mean(axis=1)
    ci_low, ci_high = _percentile_ci(resampled_mean, ci)

    return {
        "mean": float(x.mean()),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n": int(n),
        "n_resamples": int(n_resamples),
    }
