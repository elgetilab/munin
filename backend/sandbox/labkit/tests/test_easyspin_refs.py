"""labkit.epr against EasySpin's stored reference spectra (pepper, garlic).

The references and their transcribed parameters live in data/easyspin/ (see
the README there; MIT, EasySpin's LICENSE alongside). Each in-slice case is
simulated with labkit on EasySpin's field axis and compared after
normalising both spectra to max |y| = 1. A case passes when
max |ref - labkit| <= TOL[case][method].

Runs under pytest (one test per case and method) or standalone:

    python backend/sandbox/labkit/tests/test_easyspin_refs.py

Cases skip cleanly while labkit lacks the slice-1 API (epr.SpinSystem,
epr.Nucleus, epr.simulate_powder; simulate_iso with exact=).

Two properties of the references matter for the comparison (details in each
case's JSON "note"):
- pepper (projection path, no strains) returns ref[i] ~ true(x[i] + dx/2), so
  labkit is evaluated at x + field_offset_steps * dx (0.5 there, 0 for the
  g-strain case).
- garlic snaps every line to the nearest grid point before convolution, so an
  analytic-line simulator cannot get closer than the snapping error (measured
  with an independent exact simulation: 2.6e-2, 1.4e-1, 4.4e-3 of max for
  simplest, manynuclei, modamp). Their tolerances sit just above that floor.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
DATA = HERE / "data" / "easyspin"

# ------------------------------------------------------------------------------
# Tolerances: max |ref - labkit| after max-normalisation, per case and method.
# "perturb" = second-order perturbation (labkit default), "exact" = full
# diagonalisation (pepper) or Breit-Rabi (garlic / simulate_iso).
# ------------------------------------------------------------------------------
TOL = {
    # garlic: floor set by EasySpin's nearest-grid snapping (see docstring)
    "garlic_simplest":        {"exact": 3e-2},
    "garlic_manynuclei":      {"exact": 1.5e-1},
    "garlic_modamp":          {"exact": 5e-3},
    # pepper, no nuclei: perturbation is exact for g only
    "pepper_axiallw":         {"perturb": 1e-3},
    "pepper_rhombiclw":       {"perturb": 1e-3},
    "pepper_gstrain":         {"perturb": 1e-3},
    "pepper_iso_12c":         {"perturb": 1e-3},
    "pepper_modamp":          {"perturb": 1e-3},
    "pepper_pt_gonly":        {"perturb": 1e-3},
    "pepper_ordering_axial":  {"perturb": 1e-3},
    # pepper, EasySpin itself used Method = 'perturb'
    "pepper_pt_ga1":          {"perturb": 1e-3},
    "pepper_pt_ga2":          {"perturb": 1e-3},
    "pepper_iso_customabund": {"perturb": 1e-3},
    "pepper_iso_twonucs":     {"perturb": 1e-3},
    # pepper, EasySpin used full matrix diagonalisation with a large A (400 MHz):
    # our perturbation default gets the looser 2e-2, method="exact" the tight one
    "pepper_iso_cu":          {"perturb": 2e-2, "exact": 1e-3},
}


class SkipCase(Exception):
    pass


def load_cases() -> dict:
    return {p.stem: json.loads(p.read_text()) for p in sorted(DATA.glob("*.json"))}


CASES = load_cases()
IN_SLICE = sorted(n for n, c in CASES.items() if c["in_slice1"])
PARAMS = [(n, m) for n in IN_SLICE for m in TOL.get(n, {})]


def load_ref(case: dict):
    """(field axis in mT, spectrum) from the vendored .mat (v5 files only)."""
    from scipy.io import loadmat
    d = loadmat(DATA / f"{case['case']}.mat", squeeze_me=True, struct_as_record=False)["data"]
    y = np.asarray(getattr(d, case["mat"]["spectrum_var"]), float)
    if "spectrum_row" in case:
        y = y[case["spectrum_row"]]
    fv = case["mat"].get("field_var")
    if fv:
        x = np.asarray(getattr(d, fv), float)
    else:
        e = case["exp"]
        x = np.linspace(e["range_mT"][0], e["range_mT"][1], e["nPoints"])
    assert x.shape == y.shape, (x.shape, y.shape)
    return x, y


def _epr():
    try:
        from labkit import epr
    except Exception as exc:  # labkit not importable here
        raise SkipCase(f"labkit.epr not importable: {exc}")
    return epr


def _need_powder(epr):
    missing = [a for a in ("Nucleus", "SpinSystem", "simulate_powder") if not hasattr(epr, a)]
    if missing:
        raise SkipCase("labkit.epr lacks " + ", ".join(missing))


def _need_iso(epr):
    if not hasattr(epr, "Nucleus"):
        raise SkipCase("labkit.epr lacks Nucleus")
    if "exact" not in inspect.signature(epr.simulate_iso).parameters:
        raise SkipCase("labkit.epr.simulate_iso has no exact= parameter yet")


def _widths(lw: dict) -> dict:
    """SpinSystem width kwargs from the JSON lw block: dBpp_mT where EasySpin
    was given lwpp, fwhm_mT where it was given lw."""
    g, l = lw.get("gaussian_fwhm_mT", 0.0), lw.get("lorentzian_fwhm_mT", 0.0)
    if g and l:
        raise SkipCase("Voigt (Gaussian + Lorentzian) widths: harness mapping not defined")
    shape = "lorentzian" if l else "gaussian"
    if lw.get("easyspin_field") == "Sys.lwpp":
        return {"dBpp_mT": lw["lorentzian_dBpp_mT"] if l else lw["gaussian_dBpp_mT"], "shape": shape}
    return {"fwhm_mT": l if l else g, "shape": shape}


def _nucleus(epr, n: dict, name=None, A=None):
    return epr.Nucleus(name or n["name"], A_MHz=A if A is not None else n["A_MHz"],
                       euler_deg=n.get("A_frame_euler_deg"), n=n.get("n", 1))


def _as_array(out):
    return np.asarray(out[-1] if isinstance(out, tuple) else out, float)


def simulate(case: dict, x_eval: np.ndarray, method: str) -> np.ndarray:
    epr = _epr()
    s, e = case["sys"], case["exp"]
    harmonic, modamp = e.get("harmonic", 1), e.get("modamp_mT")
    if case["program"] == "garlic":
        _need_iso(epr)
        w = _widths(s["lw"])
        lw_pp = w.get("dBpp_mT")
        if lw_pp is None:  # FWHM -> derivative peak-to-peak for simulate_iso
            lw_pp = w["fwhm_mT"] / (np.sqrt(3) if w["shape"] == "lorentzian" else np.sqrt(2 * np.log(2)))
        return _as_array(epr.simulate_iso(
            x_eval, e["mwFreq_GHz"], s["g"][0], nuclei=[_nucleus(epr, n) for n in s["nuclei"]],
            lw_mT=lw_pp, shape=w["shape"], harmonic=harmonic, modamp_mT=modamp, exact=(method == "exact")))

    _need_powder(epr)
    width = _widths(s["lw"])

    def one(nuclei):
        sys_ = epr.SpinSystem(g=s["g"], nuclei=nuclei, g_strain=s.get("g_strain_fwhm"),
                              A_strain_MHz=s.get("A_strain_MHz"), **width)
        return _as_array(epr.simulate_powder(sys_, x_eval, e["mwFreq_GHz"], harmonic=harmonic,
                                             modamp_mT=modamp, method=method))

    custom = [n for n in s["nuclei"] if n["name"].startswith("(")]
    if custom:  # custom isotope mixture: weighted sum of isotopologues
        if len(s["nuclei"]) != 1:
            raise SkipCase("custom mixture combined with other nuclei: not mapped")
        return sum(iso["weight"] * one([_nucleus(epr, custom[0], iso["name"], iso["A_MHz"])])
                   for iso in custom[0]["isotopologues"])
    return one([_nucleus(epr, n) for n in s["nuclei"]])


def _norm(y):
    return y / np.max(np.abs(y))


def check_case(name: str, method: str) -> float:
    """Returns the max-normalised residual; raises AssertionError or SkipCase."""
    case = CASES[name]
    x, ref = load_ref(case)
    dx = x[1] - x[0]
    off = case.get("comparison", {}).get("field_offset_steps", 0.0)
    y = simulate(case, x + off * dx, method)
    assert y.shape == ref.shape, f"{name}: labkit returned shape {y.shape}, reference {ref.shape}"
    assert np.all(np.isfinite(y)) and np.max(np.abs(y)) > 0, f"{name}: labkit spectrum is empty or not finite"
    a, b = _norm(ref), _norm(y)
    resid = float(np.max(np.abs(a - b)))
    tol = TOL[name][method]
    if resid > tol:
        i = int(np.argmax(np.abs(a - b)))
        corr = float(np.corrcoef(a, b)[0, 1])
        raise AssertionError(
            f"{name} [{method}]: max |ref - labkit| = {resid:.3e} > tol {tol:.1e} "
            f"(at {x[i]:.4f} mT, ref {a[i]:+.4f}, labkit {b[i]:+.4f}; corr {corr:.6f}; "
            f"axis offset used {off} steps)")
    return resid


def test_vendored_data_integrity():
    """Every .mat has a JSON and the in-slice JSONs describe their .mat. Needs scipy only."""
    mats = {p.stem for p in DATA.glob("*.mat")}
    assert mats == set(CASES), mats ^ set(CASES)
    assert (DATA / "LICENSE").read_text().startswith("MIT License")
    for name in IN_SLICE:
        case = CASES[name]
        x, y = load_ref(case)
        assert np.all(np.isfinite(y)) and np.max(np.abs(y)) > 0, name
        assert np.all(np.diff(x) > 0), name
        assert name in TOL, f"{name} is in slice 1 but has no TOL entry"


try:
    import pytest
except ImportError:  # sandbox image: no pytest, use __main__
    pytest = None

if pytest is not None:
    @pytest.mark.parametrize("name,method", PARAMS, ids=[f"{n}-{m}" for n, m in PARAMS])
    def test_easyspin_reference(name, method):
        try:
            check_case(name, method)
        except SkipCase as exc:
            pytest.skip(str(exc))


def main() -> int:
    n_fail = 0
    test_vendored_data_integrity()
    print("data integrity: ok")
    for name, method in PARAMS:
        try:
            r = check_case(name, method)
            print(f"PASS {name} [{method}] resid {r:.2e} (tol {TOL[name][method]:.1e})")
        except SkipCase as exc:
            print(f"SKIP {name} [{method}]: {exc}")
        except AssertionError as exc:
            n_fail += 1
            print(f"FAIL {exc}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
