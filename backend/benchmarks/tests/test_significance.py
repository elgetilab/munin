"""Wilcoxon signed-rank wrapper tests."""

import pytest
from scipy.stats import wilcoxon

from munin_bench.metrics import paired_wilcoxon


def test_wilcoxon_matches_scipy():
    a = [0.8, 0.7, 0.9, 0.6, 0.85, 0.75, 0.95, 0.65]
    b = [0.5, 0.6, 0.4, 0.55, 0.5, 0.6, 0.45, 0.5]
    out = paired_wilcoxon(a, b)
    ref = wilcoxon(a, b, zero_method="wilcox")
    assert out["statistic"] == pytest.approx(float(ref.statistic))
    assert out["p_value"] == pytest.approx(float(ref.pvalue))
    assert out["n_nonzero"] == 8


def test_wilcoxon_all_ties_degrades_gracefully():
    a = [0.5, 0.6, 0.7]
    out = paired_wilcoxon(a, list(a))
    assert out == {"statistic": 0.0, "p_value": 1.0, "n_nonzero": 0}


def test_wilcoxon_counts_only_nonzero_pairs():
    a = [0.5, 0.6, 0.7, 0.8]
    b = [0.5, 0.4, 0.7, 0.6]  # pairs 0 and 2 are ties
    out = paired_wilcoxon(a, b)
    assert out["n_nonzero"] == 2


def test_wilcoxon_shape_and_empty_validation():
    with pytest.raises(ValueError):
        paired_wilcoxon([0.1, 0.2], [0.1])
    with pytest.raises(ValueError):
        paired_wilcoxon([], [])
