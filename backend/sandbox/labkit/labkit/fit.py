"""Nonlinear regression the way GraphPad Prism does it, with the same names.

    from labkit import fit
    r = fit("one_phase_decay", t, y)          # y: 1D, or 2D (one column per replicate)
    r.params["Y0"], r.ci["K"], r.to_dict()
    print(r)                                   # table with definitions and warnings

Defaults follow Prism's: every replicate is fitted as its own point, the
intervals are asymptotic (Wald) on a t distribution with the residual degrees
of freedom, and parameters carry Prism's names. Anything the call does not
say and cannot be inferred safely raises instead of guessing (for dose
response: is X the concentration or its log?).
"""
from __future__ import annotations

import inspect
import math
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

import numpy as np
from scipy import optimize, stats

LN2 = math.log(2.0)


def _pow10(v: float) -> float:
    try:
        return 10.0 ** v
    except OverflowError:
        return float("inf")


# ==============================================================================
# Model library
# ==============================================================================

@dataclass
class Model:
    """A named model: f(x, *params) with Prism parameter names."""
    name: str
    func: Callable
    params: list[str]
    equation: str
    definitions: dict[str, str]
    guess: Optional[Callable] = None          # (x, y) -> initial values
    derived: Optional[Callable] = None        # (p: dict) -> list of _Derived
    dose_response: bool = False               # X is log10(concentration)
    constants: tuple[str, ...] = ()           # must be given in fit(constants=)
    bounds: dict[str, tuple] = field(default_factory=dict)


@dataclass
class _Derived:
    name: str
    definition: str
    value: float
    # Either a monotonic transform of one fitted parameter (CI endpoints map
    # through it), or a gradient over the fitted parameters (delta method).
    of: Optional[str] = None
    transform: Optional[Callable] = None
    grad: Optional[dict] = None


def _span(y):
    y = np.asarray(y, float)
    return float(np.nanmax(y) - np.nanmin(y)) or 1.0


def _g_linear(x, y):
    s, b = np.polyfit(np.asarray(x, float), np.asarray(y, float), 1)
    return [s, b]


def _g_sigmoid(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    order = np.argsort(x)
    lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
    mid = (lo + hi) / 2
    xm = float(x[order][np.nanargmin(np.abs(y[order] - mid))])
    rising = np.corrcoef(x, y)[0, 1] >= 0 if len(x) > 2 else True
    return lo, hi, xm, (1.0 if rising else -1.0)


def _g_3pl(x, y):
    lo, hi, xm, _ = _g_sigmoid(x, y)
    return [lo, hi, xm]


def _g_4pl(x, y):
    lo, hi, xm, s = _g_sigmoid(x, y)
    return [lo, hi, xm, s]


def _g_5pl(x, y):
    lo, hi, xm, s = _g_sigmoid(x, y)
    return [lo, hi, xm, s, 1.0]


def _g_mm(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    vmax = float(np.nanmax(y)) * 1.2
    half = vmax / 2
    km = float(x[np.nanargmin(np.abs(y - half))]) or float(np.nanmedian(x)) or 1.0
    return [vmax, abs(km)]


def _g_decay1(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    order = np.argsort(x)
    y0, plat = float(y[order][0]), float(y[order][-1])
    rng = float(np.ptp(x)) or 1.0
    return [y0, plat, 3.0 / rng]


def _g_assoc1(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    order = np.argsort(x)
    y0, plat = float(y[order][0]), float(y[order][-1])
    return [y0, plat, 3.0 / (float(np.ptp(x)) or 1.0)]


def _g_decay2(x, y):
    y0, plat, k = _g_decay1(x, y)
    return [y0, plat, k * 5, k / 3, 50.0]


def _f_linear(x, slope, yint):
    return slope * x + yint


def _f_3pl(x, bottom, top, logec50):
    return bottom + (top - bottom) / (1 + 10 ** (logec50 - x))


def _f_4pl(x, bottom, top, logec50, hill):
    return bottom + (top - bottom) / (1 + 10 ** ((logec50 - x) * hill))


def _f_5pl(x, bottom, top, logxb, hill, s):
    return bottom + (top - bottom) / (1 + 10 ** ((logxb - x) * hill)) ** s


def _f_mm(x, vmax, km):
    return vmax * x / (km + x)


def _f_decay1(x, y0, plateau, k):
    return (y0 - plateau) * np.exp(-k * x) + plateau


def _f_assoc1(x, y0, plateau, k):
    return y0 + (plateau - y0) * (1 - np.exp(-k * x))


def _f_decay2(x, y0, plateau, kfast, kslow, pfast):
    span = y0 - plateau
    return plateau + span * pfast * 0.01 * np.exp(-kfast * x) + span * (100 - pfast) * 0.01 * np.exp(-kslow * x)


def _d_ec50(p):
    return [_Derived("EC50", "10^LogEC50, in X's concentration units", _pow10(p["LogEC50"]),
                     of="LogEC50", transform=_pow10)]


def _d_5pl(p):
    s, h = p["S"], p["HillSlope"]
    logec50 = p["LogXb"] + (1.0 / h) * math.log10(2 ** (1.0 / s) - 1) if s > 0 and h != 0 else float("nan")
    out = [_Derived("LogEC50", "log10 of the X giving a response halfway between Bottom and Top", logec50,
                    grad=_num_grad(lambda q: q["LogXb"] + (1.0 / q["HillSlope"]) * math.log10(2 ** (1.0 / q["S"]) - 1), p))]
    out.append(_Derived("EC50", "10^LogEC50", _pow10(logec50) if np.isfinite(logec50) else float("nan"),
                        grad=_num_grad(lambda q: _pow10(q["LogXb"] + (1.0 / q["HillSlope"]) * math.log10(2 ** (1.0 / q["S"]) - 1)), p)))
    return out


def _d_decay1(p):
    return [
        _Derived("Span", "Y0 - Plateau: the total drop (NOT the value at time 0)", p["Y0"] - p["Plateau"],
                 grad={"Y0": 1.0, "Plateau": -1.0}),
        _Derived("HalfLife", "ln(2)/K, in X units", LN2 / p["K"] if p["K"] else float("inf"),
                 of="K", transform=lambda v: LN2 / v if v else float("inf")),
        _Derived("Tau", "1/K, the time constant, in X units", 1 / p["K"] if p["K"] else float("inf"),
                 of="K", transform=lambda v: 1 / v if v else float("inf")),
    ]


def _d_assoc1(p):
    out = _d_decay1(p)
    out[0] = _Derived("Span", "Plateau - Y0", p["Plateau"] - p["Y0"], grad={"Y0": -1.0, "Plateau": 1.0})
    return out


def _d_decay2(p):
    span = p["Y0"] - p["Plateau"]
    return [
        _Derived("SpanFast", "(Y0 - Plateau) * PercentFast/100", span * p["PercentFast"] / 100,
                 grad=_num_grad(lambda q: (q["Y0"] - q["Plateau"]) * q["PercentFast"] / 100, p)),
        _Derived("SpanSlow", "(Y0 - Plateau) * (100 - PercentFast)/100", span * (100 - p["PercentFast"]) / 100,
                 grad=_num_grad(lambda q: (q["Y0"] - q["Plateau"]) * (100 - q["PercentFast"]) / 100, p)),
        _Derived("HalfLifeFast", "ln(2)/KFast", LN2 / p["KFast"] if p["KFast"] else float("inf"),
                 of="KFast", transform=lambda v: LN2 / v if v else float("inf")),
        _Derived("HalfLifeSlow", "ln(2)/KSlow", LN2 / p["KSlow"] if p["KSlow"] else float("inf"),
                 of="KSlow", transform=lambda v: LN2 / v if v else float("inf")),
    ]


def _num_grad(fn, p, h=1e-6):
    g = {}
    for k, v in p.items():
        step = h * max(abs(v), 1.0)
        a, b = dict(p), dict(p)
        a[k] = v + step
        b[k] = v - step
        try:
            g[k] = (fn(a) - fn(b)) / (2 * step)
        except (ZeroDivisionError, ValueError, OverflowError):
            g[k] = float("nan")
    return g


def _d_linear(p):
    return [_Derived("XIntercept", "-YIntercept/Slope", -p["YIntercept"] / p["Slope"] if p["Slope"] else float("nan"),
                     grad=_num_grad(lambda q: -q["YIntercept"] / q["Slope"], p))]


DEFS_SIGMOID = {
    "Bottom": "the plateau at low X (response in Y units)",
    "Top": "the plateau at high X",
    "LogEC50": "log10 of the concentration giving a response halfway between Bottom and Top",
    "HillSlope": "steepness; negative for a falling curve",
}

MODELS: dict[str, Model] = {}


def _reg(m: Model) -> None:
    MODELS[m.name] = m


_reg(Model("linear", _f_linear, ["Slope", "YIntercept"], "Y = Slope*X + YIntercept",
           {"Slope": "change in Y per unit X", "YIntercept": "Y at X = 0"}, _g_linear, _d_linear))
_reg(Model("3pl", _f_3pl, ["Bottom", "Top", "LogEC50"],
           "Y = Bottom + (Top-Bottom)/(1+10^(LogEC50-X)); X = log10(concentration), Hill slope fixed at 1",
           {k: DEFS_SIGMOID[k] for k in ("Bottom", "Top", "LogEC50")}, _g_3pl, _d_ec50, dose_response=True))
_reg(Model("4pl", _f_4pl, ["Bottom", "Top", "LogEC50", "HillSlope"],
           "Y = Bottom + (Top-Bottom)/(1+10^((LogEC50-X)*HillSlope)); X = log10(concentration)",
           DEFS_SIGMOID, _g_4pl, _d_ec50, dose_response=True))
_reg(Model("5pl", _f_5pl, ["Bottom", "Top", "LogXb", "HillSlope", "S"],
           "Y = Bottom + (Top-Bottom)/(1+10^((LogXb-X)*HillSlope))^S; X = log10(concentration)",
           {**{k: DEFS_SIGMOID[k] for k in ("Bottom", "Top", "HillSlope")},
            "LogXb": "log10 of the inflection-related X (not the EC50 unless S = 1)",
            "S": "asymmetry factor (1 = symmetric 4PL)"},
           _g_5pl, _d_5pl, dose_response=True, bounds={"S": (1e-6, None)}))
_reg(Model("michaelis_menten", _f_mm, ["Vmax", "Km"], "Y = Vmax*X/(Km + X)",
           {"Vmax": "maximum velocity, in Y units", "Km": "substrate concentration at half Vmax, in X units"},
           _g_mm))
_reg(Model("one_phase_decay", _f_decay1, ["Y0", "Plateau", "K"], "Y = (Y0 - Plateau)*exp(-K*X) + Plateau",
           {"Y0": "Y at X = 0 (the starting VALUE, not the drop)", "Plateau": "Y at infinite X",
            "K": "rate constant, in inverse X units"}, _g_decay1, _d_decay1))
_reg(Model("one_phase_association", _f_assoc1, ["Y0", "Plateau", "K"], "Y = Y0 + (Plateau - Y0)*(1 - exp(-K*X))",
           {"Y0": "Y at X = 0", "Plateau": "Y at infinite X", "K": "rate constant, in inverse X units"},
           _g_assoc1, _d_assoc1))
_reg(Model("two_phase_decay", _f_decay2, ["Y0", "Plateau", "KFast", "KSlow", "PercentFast"],
           "Y = Plateau + SpanFast*exp(-KFast*X) + SpanSlow*exp(-KSlow*X), SpanFast = (Y0-Plateau)*PercentFast/100",
           {"Y0": "Y at X = 0", "Plateau": "Y at infinite X", "KFast": "faster rate constant",
            "KSlow": "slower rate constant", "PercentFast": "percent of the span in the fast phase"},
           _g_decay2, _d_decay2, bounds={"PercentFast": (0, 100)}))


def _f_competitive(x, vmax, km, ki):
    s, i = x
    return vmax * s / (km * (1 + i / ki) + s)


def _g_competitive(x, y):
    s, i = x
    vmax, km = _g_mm(s[i == np.min(i)], y[i == np.min(i)])
    return [vmax, km, float(np.median(i[i > 0])) if np.any(i > 0) else 1.0]


_reg(Model("competitive_inhibition", _f_competitive, ["Vmax", "Km", "Ki"],
           "Y = Vmax*S/(Km*(1 + I/Ki) + S); X = (S, I): substrate and inhibitor concentration",
           {"Vmax": "maximum velocity", "Km": "Michaelis constant without inhibitor",
            "Ki": "inhibition constant, in inhibitor concentration units"}, _g_competitive))


def _f_morrison(x, v0, kiapp, et):
    q = et + x + kiapp
    return v0 * (1 - (q - np.sqrt(np.maximum(q * q - 4 * et * x, 0.0))) / (2 * et))


def _g_morrison(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    v0 = float(np.nanmax(y))
    k = float(x[np.nanargmin(np.abs(y - v0 / 2))]) or 1.0
    return [v0, abs(k)]


_reg(Model("morrison", lambda x, v0, kiapp, Et: _f_morrison(x, v0, kiapp, Et), ["V0", "KiApp"],
           "Y = V0*(1 - ((Et+X+KiApp) - sqrt((Et+X+KiApp)^2 - 4*Et*X))/(2*Et)); tight binding, X = inhibitor",
           {"V0": "velocity without inhibitor", "KiApp": "apparent Ki (substrate competition not removed)"},
           _g_morrison, constants=("Et",)))


# ==============================================================================
# Data shaping
# ==============================================================================

def _as_xy(x, y, replicates: str):
    """Return flat (x, y) for fitting, and the per-x means for reporting."""
    if isinstance(x, tuple):
        xs = tuple(np.asarray(v, float) for v in x)
        ya = np.asarray(y, float)
        if ya.ndim != 1:
            raise ValueError("multi-variable X: give y as a 1D array matching each X array")
        ok = np.isfinite(ya) & np.all([np.isfinite(v) for v in xs], axis=0)
        return tuple(v[ok] for v in xs), ya[ok]
    try:
        import pandas as pd
        if isinstance(y, (pd.DataFrame, pd.Series)):
            y = y.to_numpy(dtype=float)
        if isinstance(x, (pd.Series, pd.Index)):
            x = x.to_numpy(dtype=float)
    except ImportError:  # pragma: no cover
        pass
    xa = np.asarray(x, float)
    if isinstance(y, (list, tuple)) and y and isinstance(y[0], (list, tuple, np.ndarray)):
        width = max(len(r) for r in y)
        ya = np.full((len(y), width), np.nan)
        for i, r in enumerate(y):
            ya[i, :len(r)] = r
    else:
        ya = np.asarray(y, float)
    if ya.ndim == 2:
        if ya.shape[0] != xa.shape[0]:
            raise ValueError(f"y has {ya.shape[0]} rows but x has {xa.shape[0]} values (one row per X)")
        if replicates == "mean":
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore", RuntimeWarning)
                ym = np.nanmean(ya, axis=1)
            ok = np.isfinite(ym) & np.isfinite(xa)
            return xa[ok], ym[ok]
        if replicates != "all":
            raise ValueError("replicates must be 'all' (fit every replicate, Prism's default) or 'mean'")
        xx = np.repeat(xa, ya.shape[1])
        yy = ya.ravel()
    else:
        if ya.shape != xa.shape:
            raise ValueError(f"x has {xa.shape} values but y has {ya.shape}")
        xx, yy = xa, ya
    ok = np.isfinite(xx) & np.isfinite(yy)
    return xx[ok], yy[ok]


# ==============================================================================
# Result
# ==============================================================================

@dataclass
class FitResult:
    model: str
    equation: str
    params: dict[str, float]
    se: dict[str, float]
    ci: dict[str, tuple]
    definitions: dict[str, str]
    n: int
    dof: int
    rss: float
    sy_x: float
    r2: float
    aicc: float
    dependency: dict[str, float]
    warnings: list[str]
    ci_method: str
    level: float
    fitted: list[str]
    _cov: Any = None
    _f: Any = None
    _theta: Any = None
    _sigma_fn: Any = None
    _x: Any = None
    _y: Any = None
    outliers: list[int] = field(default_factory=list)

    def predict(self, x):
        return self._f(np.asarray(x, float) if not isinstance(x, tuple) else x, self._theta)

    def bands(self, x, kind: str = "confidence"):
        """(lower, upper) confidence or prediction band at x."""
        xa = np.asarray(x, float) if not isinstance(x, tuple) else x
        yhat = self._f(xa, self._theta)
        k = len(self._theta)
        g = np.empty((np.size(yhat), k))
        for j in range(k):
            step = 1e-6 * max(abs(self._theta[j]), 1.0)
            tp = self._theta.copy(); tm = self._theta.copy()
            tp[j] += step; tm[j] -= step
            g[:, j] = (self._f(xa, tp) - self._f(xa, tm)) / (2 * step)
        var = np.einsum("ij,jk,ik->i", g, self._cov, g)
        if kind == "prediction":
            s2 = self.sy_x ** 2
            sig = self._sigma_fn(yhat) if self._sigma_fn else 1.0
            var = var + s2 * np.asarray(sig) ** 2
        elif kind != "confidence":
            raise ValueError("kind must be 'confidence' or 'prediction'")
        t = stats.t.ppf(0.5 + self.level / 2, self.dof)
        half = t * np.sqrt(np.maximum(var, 0))
        return yhat - half, yhat + half

    def to_dict(self) -> dict:
        """Flat JSON-ready dict: each parameter, `<name>_se`, `<name>_ci` [lo, hi]."""
        out: dict[str, Any] = {}
        for k, v in self.params.items():
            out[k] = _clean(v)
            if k in self.se:
                out[f"{k}_se"] = _clean(self.se[k])
            if k in self.ci:
                out[f"{k}_ci"] = [_clean(self.ci[k][0]), _clean(self.ci[k][1])]
        out.update({"n": self.n, "dof": self.dof, "r2": _clean(self.r2), "sy_x": _clean(self.sy_x),
                    "ci_method": self.ci_method, "warnings": list(self.warnings)})
        return out

    def __str__(self) -> str:
        lines = [f"{self.model}: {self.equation}",
                 f"n={self.n} dof={self.dof} R2={self.r2:.5g} Sy.x={self.sy_x:.5g} "
                 f"({self.ci_method} {int(self.level * 100)}% CI)"]
        for k, v in self.params.items():
            se = self.se.get(k)
            ci = self.ci.get(k)
            dep = self.dependency.get(k)
            row = f"  {k:<13}{v:>14.6g}"
            row += f"  SE {se:<11.4g}" if se is not None and np.isfinite(se) else " " * 16
            row += f"  CI [{ci[0]:.5g}, {ci[1]:.5g}]" if ci else ""
            row += f"  dep {dep:.4f}" if dep is not None and np.isfinite(dep) else ""
            row += f"   # {self.definitions.get(k, '')}" if self.definitions.get(k) else ""
            lines.append(row)
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        if self.outliers:
            lines.append(f"  outliers removed (ROUT): rows {self.outliers}")
        return "\n".join(lines)

    __repr__ = __str__


def _clean(v):
    if v is None:
        return None
    v = float(v)
    return v if math.isfinite(v) else None


# ==============================================================================
# Engine
# ==============================================================================

def _sigma_rule(weights, y_like):
    if weights is None:
        return None
    if isinstance(weights, str):
        w = weights.replace(" ", "").lower()
        if w in ("1/y^2", "1/y2", "relative"):
            return lambda v: np.maximum(np.abs(v), 1e-300)
        if w in ("1/y", "poisson"):
            return lambda v: np.sqrt(np.maximum(np.abs(v), 1e-300))
        raise ValueError("weights: None, '1/y^2', '1/y', or an array of standard deviations")
    arr = np.asarray(weights, float)
    return lambda v, arr=arr: arr


def _solve(resid, theta0, lo, hi, n_starts, seed=0, loss="linear", f_scale=1.0):
    rng = np.random.default_rng(seed)
    best = None
    starts = [np.asarray(theta0, float)]
    for _ in range(max(0, n_starts - 1)):
        jitter = np.exp(rng.normal(0, 0.5, size=len(theta0)))
        starts.append(np.asarray(theta0, float) * jitter)
    for s in starts:
        s = s.copy()
        below = np.isfinite(lo) & (s <= lo)
        s[below] = lo[below] + 1e-9 * (1 + np.abs(lo[below]))
        above = np.isfinite(hi) & (s >= hi)
        s[above] = hi[above] - 1e-9 * (1 + np.abs(hi[above]))
        try:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                r = optimize.least_squares(resid, s, bounds=(lo, hi), method="trf", loss=loss,
                                           f_scale=f_scale, x_scale="jac", max_nfev=20000,
                                           xtol=1e-15, ftol=1e-15, gtol=1e-15)
        except (ValueError, FloatingPointError, OverflowError):
            continue
        if not np.all(np.isfinite(r.fun)):
            continue
        if best is None or r.cost < best.cost * (1 - 1e-12):
            best = r
    if best is None:
        raise RuntimeError("the fit did not converge from any starting point; give p0= closer to the answer")
    # polish with LM when unbounded and unconstrained (matches NIST precision)
    if loss == "linear" and not np.any(np.isfinite(lo)) and not np.any(np.isfinite(hi)):
        try:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                r2 = optimize.least_squares(resid, best.x, method="lm", x_scale="jac", max_nfev=20000,
                                            xtol=1e-15, ftol=1e-15, gtol=1e-15)
            if np.all(np.isfinite(r2.fun)) and r2.cost <= best.cost:
                best = r2
        except (ValueError, FloatingPointError):
            pass
    return best


def _jac(resid, theta):
    r0 = resid(theta)
    J = np.empty((r0.size, theta.size))
    for j in range(theta.size):
        step = 1e-7 * max(abs(theta[j]), 1e-3)
        tp = theta.copy(); tm = theta.copy()
        tp[j] += step; tm[j] -= step
        J[:, j] = (resid(tp) - resid(tm)) / (2 * step)
    return J


def fit(model, x, y, *, p0=None, replicates: str = "all", weights=None,
        fixed: Optional[dict] = None, bounds: Optional[dict] = None,
        constants: Optional[dict] = None, x_scale: Optional[str] = None,
        ci: str = "wald", level: float = 0.95, n_starts: int = 8,
        loss: str = "linear", f_scale: float = 1.0, _seed: int = 0) -> FitResult:
    """Fit `model` to (x, y) and return a FitResult.

    model     a name from labkit.MODELS ('4pl', 'one_phase_decay', ...), a
              labkit.fit.Model, or any callable f(x, a, b, ...) (then give p0).
    y         1D, or 2D with one column per replicate (NaN for missing).
    replicates 'all' (Prism default: each replicate is a point) or 'mean'.
    weights   None, '1/y^2' (relative), '1/y', or an array of SDs.
    fixed     {"Bottom": 0}: hold a parameter at a value (Prism "constant").
    bounds    {"HillSlope": (0, None)}: constrain a parameter.
    constants values a model needs that are not fitted (morrison: {"Et": 5}).
    x_scale   dose-response models only, REQUIRED: 'log' if x is already
              log10(concentration), 'linear' if x is the concentration.
    ci        'wald' (Prism default, asymptotic) or 'profile' (likelihood).
    """
    m = _resolve_model(model, p0)
    if m.dose_response:
        if x_scale not in ("log", "linear"):
            raise ValueError(
                f"{m.name}: say whether x is already log10(concentration) (x_scale='log') or the "
                f"concentration itself (x_scale='linear'). The model is defined on log10 X.")
        if x_scale == "linear":
            xa = np.asarray(x, float)
            if np.any(xa[np.isfinite(xa)] <= 0):
                raise ValueError("x_scale='linear' but some concentrations are <= 0: their log is undefined. "
                                 "Drop the zero-dose rows (or fit them as a separate baseline) and say so.")
            x = np.log10(xa)
    consts = dict(constants or {})
    missing = [c for c in m.constants if c not in consts]
    if missing:
        raise ValueError(f"{m.name} needs constants={{{', '.join(repr(c) + ': ...' for c in missing)}}}")
    xx, yy = _as_xy(x, y, replicates)
    names = list(m.params)
    fixed = dict(fixed or {})
    for k in list(fixed) + list(bounds or {}):
        if k not in names:
            raise ValueError(f"unknown parameter {k!r}; {m.name} has {names}")
    free = [k for k in names if k not in fixed]

    if p0 is None:
        if m.guess is None:
            raise ValueError("give p0 (initial values) for a custom model")
        g = m.guess(xx, yy)
        start = dict(zip(names, g))
    elif isinstance(p0, dict):
        start = {**dict(zip(names, m.guess(xx, yy) if m.guess else [1.0] * len(names))), **p0}
    else:
        start = dict(zip(names, p0))
    bnd = {**m.bounds, **(bounds or {})}
    lo = np.array([(bnd.get(k, (None, None))[0] if bnd.get(k, (None, None))[0] is not None else -np.inf)
                   for k in free], float)
    hi = np.array([(bnd.get(k, (None, None))[1] if bnd.get(k, (None, None))[1] is not None else np.inf)
                   for k in free], float)

    def full(theta):
        d = dict(fixed)
        d.update(zip(free, theta))
        return [d[k] for k in names]

    def f(xv, theta):
        return np.asarray(m.func(xv, *full(theta), **consts), float)

    sigma_fn = _sigma_rule(weights, yy)
    sig = sigma_fn(yy) if sigma_fn else None
    if sig is not None and np.size(sig) not in (1, yy.size):
        raise ValueError("weights array must have one SD per fitted point")

    def resid(theta):
        r = f(xx, theta) - yy
        return r / sig if sig is not None else r

    theta0 = np.array([start[k] for k in free], float)
    sol = _solve(resid, theta0, lo, hi, n_starts, _seed, loss=loss, f_scale=f_scale)
    theta = sol.x
    return _finish(m, names, free, fixed, theta, resid, f, xx, yy, sigma_fn, sig, ci, level, lo, hi, consts)


def _resolve_model(model, p0) -> Model:
    if isinstance(model, Model):
        return model
    if isinstance(model, str):
        key = model.lower().replace("-", "_").replace(" ", "_")
        aliases = {"mm": "michaelis_menten", "decay": "one_phase_decay", "exp_decay": "one_phase_decay",
                   "two_phase": "two_phase_decay", "line": "linear", "straight_line": "linear"}
        key = aliases.get(key, key)
        if key not in MODELS:
            raise ValueError(f"unknown model {model!r}; known: {sorted(MODELS)} (or pass a callable)")
        return MODELS[key]
    if callable(model):
        sig = inspect.signature(model)
        pnames = list(sig.parameters)[1:]
        if p0 is None:
            raise ValueError("custom model: give p0 (a list or dict of initial values)")
        return Model(getattr(model, "__name__", "custom"), model, pnames, "custom", {}, None)
    raise TypeError("model must be a name, a labkit Model, or a callable")


def _finish(m, names, free, fixed, theta, resid, f, xx, yy, sigma_fn, sig, ci, level, lo, hi, consts):
    n = int(yy.size)
    k = len(free)
    dof = n - k
    if dof <= 0:
        raise ValueError(f"{n} points for {k} free parameters: nothing left to estimate the error from")
    r = resid(theta)
    rss = float(r @ r)
    s2 = rss / dof
    J = _jac(resid, theta)
    warn: list[str] = []
    # Covariance from the column-scaled Jacobian: parameters spanning many
    # orders of magnitude (NIST Hahn1, Bennett5) make J'J ill-conditioned
    # unscaled, and a truncated pseudo-inverse then gives wrong SEs.
    norms = np.linalg.norm(J, axis=0)
    norms[norms == 0] = 1.0
    Js = J / norms
    try:
        u, sv, vt = np.linalg.svd(Js, full_matrices=False)
        tol = sv.max() * max(Js.shape) * np.finfo(float).eps if sv.size else 0.0
        if sv.size == 0 or sv.min() <= tol:
            warn.append("the parameters are not all determined by the data (singular fit): "
                        "at least one is redundant or unconstrained; CIs are unreliable")
            inv_s2 = np.where(sv > tol, 1.0 / np.maximum(sv, 1e-300) ** 2, np.inf)
        else:
            inv_s2 = 1.0 / sv ** 2
        with np.errstate(invalid="ignore"):
            cov_s = (vt.T * inv_s2) @ vt
        cov = cov_s / np.outer(norms, norms) * s2
    except np.linalg.LinAlgError:
        cov = np.full((k, k), np.nan)
        warn.append("covariance could not be computed (singular fit)")
    se_free = np.sqrt(np.maximum(np.diag(cov), 0))
    tval = stats.t.ppf(0.5 + level / 2, dof)

    # dependency (Prism): 1 - 1/(C_ii * (C^-1)_ii)
    dep = {}
    try:
        # (C^-1)_ii = (J'J)_ii / s2, so C_ii*(C^-1)_ii needs no second inverse
        jtj_diag = norms ** 2 / s2 if s2 > 0 else np.full(k, np.nan)
        for j, name in enumerate(free):
            v = cov[j, j] * jtj_diag[j]
            dep[name] = float(1 - 1 / v) if np.isfinite(v) and v > 0 else float("nan")
    except (np.linalg.LinAlgError, FloatingPointError):
        dep = {name: float("nan") for name in free}
    for name, d in dep.items():
        if np.isfinite(d) and d > 0.9999:
            warn.append(f"{name}: dependency {d:.5f}: ambiguous fit, the data do not determine {name} "
                        f"(it trades off against another parameter). Report it as not identifiable "
                        f"or constrain a parameter.")
        elif np.isfinite(d) and d > 0.999:
            warn.append(f"{name}: dependency {d:.4f} is high: {name} is poorly determined by these data")

    params = dict(fixed)
    params.update({nm: float(v) for nm, v in zip(free, theta)})
    params = {nm: params[nm] for nm in names}
    se = {nm: float(s) for nm, s in zip(free, se_free)}
    cis: dict[str, tuple] = {}
    if ci == "wald":
        for j, nm in enumerate(free):
            cis[nm] = (float(theta[j] - tval * se_free[j]), float(theta[j] + tval * se_free[j]))
    elif ci == "profile":
        for j, nm in enumerate(free):
            cis[nm] = _profile(resid, theta, j, rss, dof, level, se_free[j], lo, hi)
    else:
        raise ValueError("ci must be 'wald' or 'profile'")
    for j, nm in enumerate(free):
        lo_j, hi_j = lo[j], hi[j]
        if (np.isfinite(lo_j) and abs(theta[j] - lo_j) < 1e-6 * (1 + abs(lo_j))) or \
                (np.isfinite(hi_j) and abs(theta[j] - hi_j) < 1e-6 * (1 + abs(hi_j))):
            warn.append(f"{nm} ended at its constraint ({theta[j]:.6g}); its CI is not meaningful")
        if not np.isfinite(se_free[j]) or se_free[j] > 1e6 * max(abs(theta[j]), 1e-12):
            warn.append(f"{nm}: standard error is huge or undefined: not determined by the data")

    defs = dict(m.definitions)
    if m.derived:
        for d in m.derived(params):
            params[d.name] = float(d.value)
            defs[d.name] = d.definition
            if d.of is not None and d.of in cis:
                a, b = d.transform(cis[d.of][0]), d.transform(cis[d.of][1])
                cis[d.name] = (min(a, b), max(a, b))
                if d.of in se and ci == "wald":
                    # report the delta-method SE alongside the transformed CI
                    step = 1e-6 * max(abs(params[d.of]), 1e-12)
                    dv = (d.transform(params[d.of] + step) - d.transform(params[d.of] - step)) / (2 * step)
                    se[d.name] = abs(dv) * se[d.of]
            elif d.grad is not None:
                gv = np.array([d.grad.get(nm, 0.0) for nm in free], float)
                if np.all(np.isfinite(gv)):
                    var = float(gv @ cov @ gv)
                    s = math.sqrt(max(var, 0))
                    se[d.name] = s
                    cis[d.name] = (d.value - tval * s, d.value + tval * s)

    yhat = f(xx, theta)
    tss = float(np.sum((yy - yy.mean()) ** 2))
    r2 = 1 - float(np.sum((yy - yhat) ** 2)) / tss if tss > 0 else float("nan")
    kk = k + 1
    aicc = n * math.log(rss / n) + 2 * kk + (2 * kk * (kk + 1)) / (n - kk - 1) if n - kk - 1 > 0 and rss > 0 else float("nan")
    return FitResult(model=m.name, equation=m.equation, params=params, se=se, ci=cis, definitions=defs,
                     n=n, dof=dof, rss=rss, sy_x=math.sqrt(s2), r2=r2, aicc=aicc, dependency=dep,
                     warnings=warn, ci_method=ci, level=level, fitted=free, _cov=cov, _f=f,
                     _theta=theta, _sigma_fn=sigma_fn, _x=xx, _y=yy)


def _profile(resid, theta, j, rss, dof, level, se, lo, hi):
    """Profile-likelihood CI for parameter j (F-based threshold, as Prism)."""
    thresh = rss * (1 + stats.f.ppf(level, 1, dof) / dof)
    others = [i for i in range(theta.size) if i != j]

    def prof(v):
        if not others:
            t = theta.copy(); t[j] = v
            r = resid(t)
            return float(r @ r)

        def sub(o):
            t = theta.copy(); t[j] = v; t[others] = o
            return resid(t)
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            s = optimize.least_squares(sub, theta[others], bounds=(lo[others], hi[others]), method="trf",
                                       x_scale="jac", max_nfev=5000)
        return float(2 * s.cost)

    out = []
    step0 = se if np.isfinite(se) and se > 0 else max(abs(theta[j]) * 0.1, 1e-6)
    for direction in (-1, 1):
        a = theta[j]
        step = step0
        b = None
        for _ in range(60):
            cand = a + direction * step
            if (direction < 0 and cand <= lo[j]) or (direction > 0 and cand >= hi[j]):
                break
            if prof(cand) > thresh:
                b = cand
                break
            step *= 1.6
        if b is None:
            out.append(-np.inf if direction < 0 else np.inf)
            continue
        inner = theta[j] if abs(b - theta[j]) <= step else b - direction * step / 1.6
        try:
            out.append(float(optimize.brentq(lambda v: prof(v) - thresh, *sorted((inner, b)), xtol=1e-10 * max(1, abs(b)))))
        except ValueError:
            out.append(float(b))
    return (out[0], out[1])


# ==============================================================================
# Global fits, comparison, outliers, interpolation
# ==============================================================================

def fit_global(model, datasets: Sequence[tuple], *, shared: Sequence[str], p0=None,
               fixed: Optional[dict] = None, bounds: Optional[dict] = None,
               constants: Optional[dict] = None, x_scale: Optional[str] = None,
               level: float = 0.95, n_starts: int = 8) -> dict:
    """Fit one model to several datasets at once, sharing some parameters.

    datasets  [(x1, y1), (x2, y2), ...]; y may hold replicate columns.
    shared    parameter names with ONE value for all datasets; the rest are
              fitted per dataset.
    Returns {"shared": FitResult-like dict, "per_dataset": [dict, ...],
    "result": FitResult over the flat parameter vector}.
    """
    m = _resolve_model(model, p0)
    names = list(m.params)
    for s in shared:
        if s not in names:
            raise ValueError(f"shared parameter {s!r} is not in {names}")
    fixed = dict(fixed or {})
    consts = dict(constants or {})
    data = []
    for x, y in datasets:
        if m.dose_response:
            if x_scale not in ("log", "linear"):
                raise ValueError("dose-response model: give x_scale='log' or 'linear'")
            if x_scale == "linear":
                x = np.log10(np.asarray(x, float))
        data.append(_as_xy(x, y, "all"))
    flat_names, index = [], []
    for nm in names:
        if nm in fixed:
            continue
        if nm in shared:
            flat_names.append(nm)
    for di in range(len(data)):
        for nm in names:
            if nm not in shared and nm not in fixed:
                flat_names.append(f"{nm}[{di}]")
    pos = {nm: i for i, nm in enumerate(flat_names)}

    def unpack(theta, di):
        vals = []
        for nm in names:
            if nm in fixed:
                vals.append(fixed[nm])
            elif nm in shared:
                vals.append(theta[pos[nm]])
            else:
                vals.append(theta[pos[f"{nm}[{di}]"]])
        return vals

    starts = []
    for di, (xx, yy) in enumerate(data):
        g = (dict(zip(names, m.guess(xx, yy))) if m.guess else {})
        if isinstance(p0, dict):
            g.update(p0)
        starts.append(g)
    theta0 = np.empty(len(flat_names))
    for nm, i in pos.items():
        if "[" in nm:
            base, di = nm[:-1].split("[")
            theta0[i] = starts[int(di)][base]
        else:
            theta0[i] = float(np.median([s[nm] for s in starts]))
    bnd = {**m.bounds, **(bounds or {})}
    lo = np.array([(bnd.get(nm.split("[")[0], (None, None))[0] if bnd.get(nm.split("[")[0], (None, None))[0] is not None else -np.inf) for nm in flat_names])
    hi = np.array([(bnd.get(nm.split("[")[0], (None, None))[1] if bnd.get(nm.split("[")[0], (None, None))[1] is not None else np.inf) for nm in flat_names])

    xs_all = [d[0] for d in data]
    ys_all = np.concatenate([d[1] for d in data])

    def f(_x, theta):
        return np.concatenate([np.asarray(m.func(xs_all[di], *unpack(theta, di), **consts), float)
                               for di in range(len(data))])

    def resid(theta):
        return f(None, theta) - ys_all

    sol = _solve(resid, theta0, lo, hi, n_starts)
    flat_model = Model(f"{m.name} (global)", lambda x, *t: f(None, np.asarray(t)), flat_names, m.equation, {})
    res = _finish(flat_model, flat_names, flat_names, {}, sol.x, resid, f, None, ys_all, None, None,
                  "wald", level, lo, hi, {})
    per = []
    for di in range(len(data)):
        d = {}
        for nm in names:
            key = nm if nm in shared else f"{nm}[{di}]"
            if nm in fixed:
                d[nm] = fixed[nm]
                continue
            d[nm] = res.params[key]
            d[f"{nm}_ci"] = list(res.ci[key])
        per.append(d)
    return {"shared": {s: res.params[s] for s in shared if s in res.params},
            "shared_ci": {s: list(res.ci[s]) for s in shared if s in res.ci},
            "per_dataset": per, "result": res}


def compare(simpler: FitResult, complex_: FitResult) -> dict:
    """Extra sum-of-squares F test (nested models) and AICc for two fits of
    the SAME data. Returns the preferred model and the evidence."""
    if simpler.n != complex_.n:
        raise ValueError("the two fits must use the same data points")
    out: dict[str, Any] = {}
    if complex_.dof < simpler.dof:
        num = (simpler.rss - complex_.rss) / (simpler.dof - complex_.dof)
        den = complex_.rss / complex_.dof
        F = num / den if den > 0 else float("inf")
        p = float(stats.f.sf(F, simpler.dof - complex_.dof, complex_.dof))
        out.update({"F": F, "dfn": simpler.dof - complex_.dof, "dfd": complex_.dof, "p": p,
                    "f_test_prefers": complex_.model if p < 0.05 else simpler.model})
    d = complex_.aicc - simpler.aicc
    prob_simpler = 1 / (1 + math.exp(-0.5 * d)) if np.isfinite(d) else float("nan")
    out.update({"delta_aicc": d, "prob_simpler_correct": prob_simpler,
                "aicc_prefers": simpler.model if d > 0 else complex_.model})
    return out


def rout(model, x, y, *, Q: float = 0.01, **fit_kwargs) -> FitResult:
    """ROUT outlier removal (Motulsky and Brown 2006), then an ordinary fit.

    Q is the false discovery rate as a fraction (Prism's default 1% = 0.01).
    The returned FitResult has .outliers = indices into the flattened data
    (row-major over replicate columns) that were removed.
    """
    replicates = fit_kwargs.pop("replicates", "all")
    m = _resolve_model(model, fit_kwargs.get("p0"))
    if isinstance(x, tuple):
        raise ValueError("rout: single-X models only")
    if m.dose_response:
        if fit_kwargs.get("x_scale") not in ("log", "linear"):
            raise ValueError(f"{m.name}: give x_scale='log' or 'linear'")
        if fit_kwargs["x_scale"] == "linear":
            xa = np.asarray(x, float)
            if np.any(xa[np.isfinite(xa)] <= 0):
                raise ValueError("x_scale='linear' with concentrations <= 0: drop them first")
            x = np.log10(xa)
        fit_kwargs = {**fit_kwargs, "x_scale": "log"}
    xx, yy = _as_xy(x, y, replicates)
    kw = {k: v for k, v in fit_kwargs.items() if k not in ("loss", "f_scale", "n_starts", "p0")}
    base = fit(m, xx, yy, p0=fit_kwargs.get("p0"), **kw)
    n, k = yy.size, len(base.fitted)
    if n - k < 2:
        raise ValueError("too few points for ROUT")
    scale = base.sy_x or 1.0
    robust = base
    for _ in range(4):
        robust = fit(m, xx, yy, p0={nm: robust.params[nm] for nm in robust.fitted}, loss="cauchy",
                     f_scale=scale, n_starts=1, **kw)
        res = np.abs(yy - robust.predict(xx))
        new_scale = float(np.percentile(res, 68.27)) * n / (n - k)
        if new_scale <= 0 or abs(new_scale - scale) < 1e-9 * scale:
            break
        scale = new_scale
    t = res / scale if scale > 0 else np.zeros_like(res)
    p = 2 * stats.t.sf(t, n - k)
    # Benjamini-Hochberg at rate Q (step-up): the largest residual is tested
    # at Q/N, the k-th largest at k*Q/N; all up to the last pass are outliers.
    order = np.argsort(p)
    passed = [rank for rank, i in enumerate(order) if p[i] <= Q * (rank + 1) / n]
    out_idx = [int(i) for i in order[: passed[-1] + 1]] if passed else []
    keep = np.ones(n, bool)
    keep[out_idx] = False
    final = fit(m, xx[keep], yy[keep], p0={nm: base.params[nm] for nm in base.fitted}, **kw)
    final.outliers = sorted(out_idx)
    if out_idx:
        final.warnings.append(f"ROUT (Q={Q * 100:g}%) removed {len(out_idx)} point(s): "
                              + ", ".join(f"x={xx[i]:g} y={yy[i]:g}" for i in sorted(out_idx)))
    return final


def interpolate(standard: FitResult, unknowns, *, average: str = "readings") -> dict:
    """Read unknowns off a standard curve.

    unknowns  {name: reading} or {name: [replicate readings]}.
    average   'readings' (default): average the replicate readings, then
              interpolate once; 'results': interpolate each, then average.
    A reading whose X falls outside the range of the standards' X is
    returned as None (extrapolation is not allowed), with the reason in
    the "warnings" list.
    """
    xs = standard._x if not isinstance(standard._x, tuple) else standard._x[0]
    xmin, xmax = float(np.min(xs)), float(np.max(xs))
    out: dict[str, Any] = {}
    warn = []

    def solve(yv):
        grid = np.linspace(xmin, xmax, 2001)
        g = standard.predict(grid) - yv
        sgn = np.sign(g)
        idx = np.where(sgn[:-1] * sgn[1:] <= 0)[0]
        if idx.size == 0:
            return None
        i = int(idx[0])
        if g[i] == 0:
            return float(grid[i])
        return float(optimize.brentq(lambda v: float(standard.predict(np.array([v]))[0]) - yv, grid[i], grid[i + 1]))

    for name, val in dict(unknowns).items():
        vals = [float(v) for v in (val if isinstance(val, (list, tuple, np.ndarray)) else [val])
                if v is not None and np.isfinite(float(v))]
        if not vals:
            out[name] = None
            continue
        if average == "readings":
            x = solve(float(np.mean(vals)))
        elif average == "results":
            xs_ = [solve(v) for v in vals]
            x = None if any(v is None for v in xs_) else float(np.mean(xs_))
        else:
            raise ValueError("average must be 'readings' or 'results'")
        if x is None:
            warn.append(f"{name}: reading outside the range of the standards; not extrapolated (None)")
        out[name] = x
    out["warnings"] = warn
    return out
