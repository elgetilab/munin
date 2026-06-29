"""Bootstrap CI tests: reproducibility (seeded) and coverage (the CI brackets
the known truth on synthetic data)."""

import numpy as np
import pytest

from munin_bench.metrics import paired_bootstrap, single_bootstrap


def test_single_bootstrap_reproducible():
    vals = [0.1, 0.5, 0.3, 0.9, 0.4, 0.2, 0.8, 0.6]
    a = single_bootstrap(vals, seed=7)
    b = single_bootstrap(vals, seed=7)
    assert a == b


def test_single_bootstrap_mean_and_bracket():
    vals = [0.2, 0.4, 0.6, 0.8]
    out = single_bootstrap(vals, n_resamples=2000, seed=1)
    assert out["mean"] == pytest.approx(0.5)
    assert out["ci_low"] <= out["mean"] <= out["ci_high"]
    assert out["n"] == 4


def test_single_bootstrap_ci_covers_true_mean():
    rng = np.random.default_rng(123)
    true_mean = 0.6
    sample = (rng.normal(true_mean, 0.1, size=200)).tolist()
    out = single_bootstrap(sample, n_resamples=2000, seed=99)
    assert out["ci_low"] <= true_mean <= out["ci_high"]


def test_paired_bootstrap_constant_shift():
    # b is a, shifted down by 0.1 everywhere -> mean_diff == 0.1, and the
    # difference is constant so the CI collapses around 0.1 and p is tiny.
    a = [0.3, 0.5, 0.7, 0.2, 0.9, 0.4]
    b = [x - 0.1 for x in a]
    out = paired_bootstrap(a, b, n_resamples=2000, seed=5)
    assert out["mean_diff"] == pytest.approx(0.1)
    assert out["ci_low"] == pytest.approx(0.1, abs=1e-9)
    assert out["ci_high"] == pytest.approx(0.1, abs=1e-9)
    assert out["p_value_two_sided"] <= 0.05


def test_paired_bootstrap_no_difference_high_p():
    a = [0.4, 0.6, 0.5, 0.7, 0.3]
    out = paired_bootstrap(a, list(a), n_resamples=1000, seed=3)
    assert out["mean_diff"] == pytest.approx(0.0)
    # identical arrays -> all resampled diffs are 0 -> p clamps to 1.0
    assert out["p_value_two_sided"] == pytest.approx(1.0)


def test_paired_bootstrap_reproducible_and_validates_shape():
    a = [0.1, 0.2, 0.3]
    b = [0.0, 0.2, 0.1]
    assert paired_bootstrap(a, b, seed=11) == paired_bootstrap(a, b, seed=11)
    with pytest.raises(ValueError):
        paired_bootstrap([0.1, 0.2], [0.1], seed=1)
    with pytest.raises(ValueError):
        paired_bootstrap([], [], seed=1)


def test_single_bootstrap_empty_raises():
    with pytest.raises(ValueError):
        single_bootstrap([], seed=1)
