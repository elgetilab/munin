"""Statistics the way Prism reports them.

    from labkit import stats
    t = stats.anova2(df, "response", "genotype", "treatment")   # Type III table
    c = stats.posthoc(df, "response", "genotype", "treatment", compare="b_within_a")
    stats.asterisks(0.003)  -> "**"
"""
from __future__ import annotations

import itertools
import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sps


def asterisks(p: float) -> str:
    """Prism's default (GP) style: ns > 0.05, * <= 0.05, ** <= 0.01,
    *** <= 0.001, **** <= 0.0001."""
    if p is None or not np.isfinite(p):
        return "n/a"
    if p <= 0.0001:
        return "****"
    if p <= 0.001:
        return "***"
    if p <= 0.01:
        return "**"
    if p <= 0.05:
        return "*"
    return "ns"


def anova2(df: pd.DataFrame, dv: str, a: str, b: str) -> pd.DataFrame:
    """Two-way ANOVA with interaction, Type III sums of squares (sum-to-zero
    contrasts, as Prism and SPSS), valid for unbalanced designs.
    Returns rows a, b, a:b, Residual with SS, df, MS, F, p, stars."""
    import statsmodels.formula.api as smf
    from statsmodels.stats.anova import anova_lm
    d = df[[dv, a, b]].dropna().copy()
    d.columns = ["y", "A", "B"]
    m = smf.ols("y ~ C(A, Sum) * C(B, Sum)", data=d).fit()
    t = anova_lm(m, typ=3)
    t = t.drop(index="Intercept")
    t.index = [a, b, f"{a}:{b}", "Residual"]
    t = t.rename(columns={"sum_sq": "SS", "PR(>F)": "p"})
    t["MS"] = t["SS"] / t["df"]
    t["stars"] = [asterisks(p) if np.isfinite(p) else "" for p in t["p"]]
    t.attrs["mse"] = float(t.loc["Residual", "MS"])
    t.attrs["df_error"] = int(t.loc["Residual", "df"])
    return t[["SS", "df", "MS", "F", "p", "stars"]]


def posthoc(df: pd.DataFrame, dv: str, a: str, b: Optional[str] = None, *, compare: str = "all",
            method: str = "tukey", level: float = 0.95) -> pd.DataFrame:
    """Multiple comparisons on the POOLED error term of the ANOVA (as Prism
    does), not separate t tests.

    One-way (b=None): all pairs of a's levels.
    Two-way: compare='b_within_a' (B levels compared inside each A level),
    'a_within_b', or 'all' (every cell against every cell).
    method: 'tukey' (all pairs in a family), 'sidak' or 'bonferroni'.
    """
    d = df[[dv, a] + ([b] if b else [])].dropna()
    if b is None:
        groups = {(k,): g[dv].to_numpy(float) for k, g in d.groupby(a, sort=False)}
        resid = np.concatenate([g - g.mean() for g in groups.values()])
        df_err = len(d) - len(groups)
        families = [list(groups)]
    else:
        groups = {k: g[dv].to_numpy(float) for k, g in d.groupby([a, b], sort=False)}
        resid = np.concatenate([g - g.mean() for g in groups.values()])
        df_err = len(d) - len(groups)
        if compare == "all":
            families = [list(groups)]
        elif compare == "b_within_a":
            families = [[k for k in groups if k[0] == lev] for lev in d[a].unique()]
        elif compare == "a_within_b":
            families = [[k for k in groups if k[1] == lev] for lev in d[b].unique()]
        else:
            raise ValueError("compare: 'all', 'b_within_a' or 'a_within_b'")
    mse = float(resid @ resid) / df_err
    n_compare = sum(len(f) * (len(f) - 1) // 2 for f in families)
    rows = []
    for fam in families:
        k = len(fam)
        for g1, g2 in itertools.combinations(fam, 2):
            x1, x2 = groups[g1], groups[g2]
            diff = float(x1.mean() - x2.mean())
            se = math.sqrt(mse * (1 / len(x1) + 1 / len(x2)))
            if method == "tukey":
                q = abs(diff) / (se / math.sqrt(2))
                p = float(sps.studentized_range.sf(q, k, df_err))
                crit = float(sps.studentized_range.ppf(level, k, df_err)) * se / math.sqrt(2)
            elif method in ("sidak", "bonferroni"):
                t = abs(diff) / se
                p_raw = 2 * float(sps.t.sf(t, df_err))
                m = n_compare if b is not None and compare != "all" else len(fam) * (len(fam) - 1) // 2
                p = 1 - (1 - p_raw) ** m if method == "sidak" else min(1.0, p_raw * m)
                alpha_each = 1 - level ** (1 / m) if method == "sidak" else (1 - level) / m
                crit = float(sps.t.ppf(1 - alpha_each / 2, df_err)) * se
            else:
                raise ValueError("method: 'tukey', 'sidak' or 'bonferroni'")
            rows.append({"group1": " / ".join(map(str, g1)), "group2": " / ".join(map(str, g2)),
                         "diff": diff, "ci_low": diff - crit, "ci_high": diff + crit, "p_adj": p,
                         "stars": asterisks(p)})
    out = pd.DataFrame(rows)
    out.attrs.update({"mse": mse, "df_error": df_err, "method": method, "compare": compare})
    return out
