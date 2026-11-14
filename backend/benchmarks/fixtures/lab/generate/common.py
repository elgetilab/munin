"""Shared helpers for the fixture generators.

Rules every generator follows:
- all randomness comes from numpy.random.default_rng(seed);
- data is written with fixed precision, then READ BACK, and reference values
  are computed from what was written, never from the in-memory floats;
- reference values are rounded to 6 significant digits so a scipy point
  release does not change truth.json.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from scipy import optimize, stats


def sig(x: Any, digits: int = 6) -> Any:
    """Round to `digits` significant digits, recursively; None and NaN stay None."""
    if isinstance(x, dict):
        return {k: sig(v, digits) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [sig(v, digits) for v in x]
    if x is None:
        return None
    if isinstance(x, (bool, str)):
        return x
    f = float(x)
    if not math.isfinite(f):
        return None
    if f == 0:
        return 0.0
    return float(f"{f:.{digits}g}")


def write_case(root: Path, case_id: str, suite: str, turns: list, *,
               inputs: Optional[list[str]] = None, results_keys: Optional[dict] = None,
               truth: Optional[dict] = None, files: Optional[dict[str, bytes]] = None,
               tags: Optional[list[str]] = None, provenance: str = "") -> Path:
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    for name, data in (files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    spec = {"id": case_id, "suite": suite, "inputs": inputs if inputs is not None else sorted(files or {}),
            "turns": turns, "results_keys": results_keys or {}, "tags": tags or [],
            "provenance": provenance}
    (d / "case.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")
    if truth is not None:
        (d / "truth.json").write_text(json.dumps(sig(truth), indent=1, sort_keys=True) + "\n")
    return d


def fit(f: Callable, x, y, p0, *, sigma=None, bounds=(-np.inf, np.inf), starts=None,
        maxfev: int = 20000) -> dict:
    """Least-squares fit with 95% Wald intervals (t distribution, df = n - p),
    the convention Prism reports as "asymmetrical: no" / standard error.
    `starts` is a list of extra start vectors; the lowest SSR wins."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    best = None
    for p in [p0] + list(starts or []):
        try:
            popt, pcov = optimize.curve_fit(f, x, y, p0=p, sigma=sigma, absolute_sigma=False,
                                            bounds=bounds, maxfev=maxfev)
        except (RuntimeError, ValueError):
            continue
        r = (y - f(x, *popt)) / (sigma if sigma is not None else 1.0)
        ssr = float(np.sum(r ** 2))
        if best is None or ssr < best[2]:
            best = (popt, pcov, ssr)
    if best is None:
        raise RuntimeError("reference fit failed")
    popt, pcov, ssr = best
    dof = len(y) - len(popt)
    se = np.sqrt(np.diag(pcov))
    t = stats.t.ppf(0.975, dof)
    return {"p": popt, "se": se, "ci": [(float(a - t * s), float(a + t * s)) for a, s in zip(popt, se)],
            "ssr": ssr, "dof": dof}


def csv_bytes(header: list[str], rows: list[list], fmt: Optional[list[str]] = None,
              sep: str = ",", decimal: str = ".", newline: str = "\n") -> bytes:
    out = [sep.join(header)]
    for row in rows:
        cells = []
        for i, v in enumerate(row):
            if isinstance(v, str):
                cells.append(v)
            elif v is None or (isinstance(v, float) and not math.isfinite(v)):
                cells.append("")
            else:
                s = (fmt[i] if fmt else "{}").format(v)
                cells.append(s.replace(".", decimal) if decimal != "." else s)
        out.append(sep.join(cells))
    return (newline.join(out) + newline).encode()


def read_back(data: bytes, sep: str = ",", decimal: str = ".") -> list[list[str]]:
    rows = []
    for line in data.decode().splitlines()[1:]:
        if line.strip():
            rows.append([c.replace(decimal, ".") if decimal != "." else c for c in line.split(sep)])
    return rows
