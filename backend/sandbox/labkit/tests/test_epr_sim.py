"""labkit.epr powder simulation, units, isotopes and fit_spectrum (epr_sim.py).

Analytic limits from EASYSPIN-PORT-PLAN.md, Validation item 2. The
tolerances are the ones achieved, with a margin; the comment on each says
what limits it.
"""
from __future__ import annotations

import contextlib
import io
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import special

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import epr, epr_sim  # noqa: E402
from labkit.epr_sim import MUB_MHZ_PER_MT  # noqa: E402

_trapz = getattr(np, "trapezoid", None) or np.trapz


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def run_info(sys_, x, nu, **kw):
    """simulate_powder with return_info, printed warnings captured."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        y, info = epr.simulate_powder(sys_, x, nu, return_info=True, **kw)
    return y, info, buf.getvalue()


def nmax(y):
    return y / np.max(np.abs(y))


# ==============================================================================
# Isotropic limit: simulate_powder == simulate_iso
# ==============================================================================

@pytest.mark.parametrize("shape", ["lorentzian", "gaussian"])
def test_isotropic_powder_equals_simulate_iso(shape):
    # Achieved ~2e-6 of the maximum (cubic deposition and the finite FFT
    # window of the Lorentzian tails); 1e-6 is not reachable with a
    # histogram method, 1e-5 is the test tolerance.
    x = np.linspace(330, 350, 2048)
    s = epr.SpinSystem(g=2.0, fwhm_mT=0.2, shape=shape)
    y = quiet(epr.simulate_powder, s, x, 9.5)
    pp = epr.pp_from_fwhm(0.2, shape)
    r = epr.simulate_iso(x, 9.5, 2.0, lw_mT=pp, shape=shape)
    assert np.max(np.abs(y - r)) / np.max(np.abs(r)) < 1e-5


def test_isotropic_powder_with_nucleus_equals_second_order_iso():
    # Isotropic A: the powder perturbation formula is exactly simulate_iso's
    # second-order shift -(a^2/2B0)(I(I+1) - m^2). Achieved 1.3e-6.
    x = np.linspace(330, 350, 2048)
    s = epr.SpinSystem(g=2.0, nuclei=[epr.Nucleus("14N", A_mT=1.6)], fwhm_mT=0.2)
    y = quiet(epr.simulate_powder, s, x, 9.5, method="perturb")
    r = epr.simulate_iso(x, 9.5, 2.0, nuclei=[(1, 1.6, 1)], lw_mT=epr.pp_from_fwhm(0.2, "gaussian"),
                         shape="gaussian", second_order=True)
    assert np.max(np.abs(y - r)) / np.max(np.abs(r)) < 1e-5


def test_isotropic_hybrid_equals_breit_rabi():
    # The default (hybrid) treats the nucleus exactly: isotropic tensors give
    # the Breit-Rabi positions of simulate_iso(exact=True), up to the 14N
    # nuclear Zeeman term that simulate_iso leaves out. Achieved 1.6e-4 for
    # 31P (a = 3.5 mT), where the third-order shift is visible.
    x = np.linspace(330, 350, 2048)
    s = epr.SpinSystem(g=2.0, nuclei=[epr.Nucleus("31P", A_mT=3.5)], fwhm_mT=0.2)
    y = quiet(epr.simulate_powder, s, x, 9.5)
    pp = epr.pp_from_fwhm(0.2, "gaussian")
    r = epr.simulate_iso(x, 9.5, 2.0, nuclei=[(0.5, 3.5, 1)], lw_mT=pp, shape="gaussian", exact=True)
    r2 = epr.simulate_iso(x, 9.5, 2.0, nuclei=[(0.5, 3.5, 1)], lw_mT=pp, shape="gaussian", second_order=True)
    assert np.max(np.abs(y - r)) / np.max(np.abs(r)) < 1e-3
    assert np.max(np.abs(y - r2)) / np.max(np.abs(r2)) > 3 * np.max(np.abs(y - r)) / np.max(np.abs(r))


# ==============================================================================
# Axial and rhombic g: closed forms
# ==============================================================================

def _axial_reference(x, gp, gz, nu, fwhm, n=200000):
    """Closed-form axial pattern: B(z) = h nu / (muB sqrt(gz^2 z^2 + gp^2 (1-z^2))),
    weight = transition moment / g_eff, integrated over z = cos(theta) by
    the midpoint rule, each orientation a Gaussian of the given FWHM."""
    z = (np.arange(n) + 0.5) / n
    ge = np.sqrt(gz ** 2 * z ** 2 + gp ** 2 * (1 - z ** 2))
    gu2 = (gz ** 4 * z ** 2 + gp ** 4 * (1 - z ** 2)) / ge ** 2
    w = (2 * gp ** 2 + gz ** 2 - gu2) / 8 / ge
    B = nu * 1000 / (ge * MUB_MHZ_PER_MT)
    sig = fwhm / (2 * math.sqrt(2 * math.log(2)))
    out = np.zeros_like(x)
    for i in range(0, n, 4000):
        d = x[:, None] - B[None, i:i + 4000]
        out += np.sum(np.exp(-0.5 * (d / sig) ** 2) * w[None, i:i + 4000], axis=1)
    return out


def test_axial_g_turning_points_and_shape():
    gp, gz, nu = 2.0, 2.2, 9.5
    x = np.linspace(295, 350, 1500)
    s = epr.SpinSystem(g=[gp, gz], fwhm_mT=0.3)
    y = quiet(epr.simulate_powder, s, x, nu, harmonic=0)
    ref = _axial_reference(x, gp, gz, nu, 0.3)
    # Achieved 3e-6 of the maximum.
    assert np.max(np.abs(nmax(y) - nmax(ref))) < 1e-4
    # Turning points at h nu / (muB g): the absorption steps up at B_par and
    # peaks at B_perp (the derivative crosses zero there for a narrow line).
    b_par, b_perp = epr.field_for_g(gz, nu), epr.field_for_g(gp, nu)
    i_peak = np.argmax(y)
    assert abs(x[i_peak] - b_perp) < 0.2
    low = x < (b_par + b_perp) / 2
    edge = x[low][np.argmax(np.gradient(y, x)[low])]
    assert abs(edge - b_par) < 0.2


def _kneubuhl_density(q, a):
    """Density of q = sum a_i n_i^2 for n uniform on the sphere (a1<a2<a3):
    the elliptic-integral form of Kneubuhl, J. Chem. Phys. 33, 1074 (1960)."""
    a1, a2, a3 = a
    lo = q < a2
    k_lo = np.clip((q - a1) * (a3 - a2) / ((a3 - q) * (a2 - a1)), 0, 1 - 1e-16)
    k_hi = np.clip((a3 - q) * (a2 - a1) / ((q - a1) * (a3 - a2)), 0, 1 - 1e-16)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(lo, special.ellipk(k_lo) / (math.pi * np.sqrt((a3 - q) * (a2 - a1))),
                        special.ellipk(k_hi) / (math.pi * np.sqrt((q - a1) * (a3 - a2))))


def test_rhombic_g_follows_kneubuhl(monkeypatch):
    # Kneubuhl's shape is the distribution of resonance fields with equal
    # weight per orientation, so the transition-moment and 1/g weights are
    # switched off for this test.
    orig = epr_sim._perturb_lines

    def flat(*a, **k):
        pos, amp, st2 = orig(*a, **k)
        return pos, np.ones_like(amp), st2
    monkeypatch.setattr(epr_sim, "_perturb_lines", flat)
    g = np.array([2.0, 2.1, 2.2])
    nu = 9.5
    c = nu * 1000 / MUB_MHZ_PER_MT
    x = np.linspace(300, 345, 900)
    fwhm = 0.6
    y = quiet(epr.simulate_powder, epr.SpinSystem(g=list(g), fwhm_mT=fwhm), x, nu, harmonic=0)
    # reference: density in B from the density in q = g_eff^2 = (c/B)^2,
    # convolved with the Gaussian on a fine grid (log singularity at B_y).
    Bf = np.linspace(c / g[2] + 1e-7, c / g[0] - 1e-7, 400001)
    q = (c / Bf) ** 2
    pB = _kneubuhl_density(q, g ** 2) * 2 * c ** 2 / Bf ** 3
    pB = np.nan_to_num(pB, posinf=0.0)
    sig = fwhm / (2 * math.sqrt(2 * math.log(2)))
    dB = Bf[1] - Bf[0]
    ref = np.array([np.sum(pB * np.exp(-0.5 * ((xi - Bf) / sig) ** 2)) * dB for xi in x])
    ref /= sig * math.sqrt(2 * math.pi)
    # Achieved 2e-4 of the maximum (the reference's quadrature across the
    # logarithmic singularity at B_y limits it).
    assert np.max(np.abs(nmax(y) - nmax(ref))) < 1e-3
    assert abs(_trapz(y, x) - 1.0) < 1e-3


# ==============================================================================
# Normalisation, Breit-Rabi, grid
# ==============================================================================

@pytest.mark.parametrize("modamp", [None, 0.5])
def test_double_integral_equals_area(modamp):
    # The transition moment and 1/g weights shape relative intensities; the
    # total absorption integral is normalised to `area`.
    x = np.linspace(250, 370, 4096)
    s = epr.SpinSystem(g=[2.03, 2.07, 2.26], nuclei=[epr.Nucleus("Cu", A_MHz=[40, 60, 520])], fwhm_mT=1.5)
    y = quiet(epr.simulate_powder, s, x, 9.5, area=2.5, modamp_mT=modamp)
    first = np.concatenate([[0], np.cumsum((y[1:] + y[:-1]) / 2 * np.diff(x))])
    di = _trapz(first, x)
    # Achieved 2e-6 relative.
    assert abs(di - 2.5) / 2.5 < 1e-4


def test_breit_rabi_reduces_to_first_order_for_small_A():
    x = np.linspace(335, 345, 4000)
    nuc = [(1.5, 0.02, 1), (0.5, 0.01, 2)]
    a = epr.simulate_iso(x, 9.5, 2.0, nuclei=nuc, lw_mT=0.02, exact=True)
    b = epr.simulate_iso(x, 9.5, 2.0, nuclei=nuc, lw_mT=0.02)
    # Achieved 1e-6 (the second-order shift a^2/2B0 is 6e-7 mT).
    assert np.max(np.abs(a - b)) / np.max(np.abs(b)) < 1e-4


def test_breit_rabi_shift_orders():
    # exact - (first + second order) scales as a^3 (third order)
    g, nu = 2.0, 9.5
    b0 = epr.field_for_g(g, nu)
    errs = []
    for a in (2.0, 4.0, 8.0):
        for m in (1.5,):
            ex = epr._breit_rabi_shift(1.5, m, a, g, nu)
            pt = -m * a - a * a / (2 * b0) * (1.5 * 2.5 - m * m)
            errs.append(abs(ex - pt))
    assert 6 < errs[1] / errs[0] < 10 and 6 < errs[2] / errs[1] < 10
    # equivalent nuclei: exact keeps the total area and gives the second-order
    # splitting of the degenerate first-order lines (more distinct lines)
    pos1, amp1 = epr._lines(nu, g, [(0.5, 2.0, 2)])
    pos2, amp2 = epr._lines_exact(nu, g, [(0.5, 2.0, 2)])
    assert abs(amp2.sum() - 1) < 1e-12
    assert len(np.unique(np.round(pos2, 6))) == 4 and len(np.unique(np.round(pos1, 6))) == 3


def test_simulate_iso_accepts_nucleus_objects():
    x = np.linspace(335, 345, 2000)
    a = epr.simulate_iso(x, 9.5, 2.006, nuclei=[epr.Nucleus("14N", A_mT=1.6)], lw_mT=0.1)
    b = epr.simulate_iso(x, 9.5, 2.006, nuclei=[(1, 1.6, 1)], lw_mT=0.1)
    assert np.allclose(a, b, atol=1e-12 * np.max(np.abs(b)))
    # A_MHz converted with the given g; an element is its isotope mixture
    c = epr.simulate_iso(x, 9.5, 2.006, nuclei=[epr.Nucleus("14N", A_MHz=epr.convert(1.6, "mT", "MHz", g=2.006))],
                         lw_mT=0.1)
    assert np.allclose(c, b, atol=1e-9 * np.max(np.abs(b)))
    d = epr.simulate_iso(x, 9.5, 2.006, nuclei=[epr.Nucleus("N", A_mT=1.6)], lw_mT=0.1, exact=True)
    e = epr.simulate_iso(x, 9.5, 2.006, nuclei=[(1, 1.6, 1)], lw_mT=0.1, exact=True)
    # natural N: 99.636% 14N, the rest 15N (A x -1.40, I = 1/2)
    assert 1e-4 < np.max(np.abs(d - e)) / np.max(np.abs(e)) < 0.01
    with pytest.raises(ValueError, match="anisotropic"):
        epr.simulate_iso(x, 9.5, 2.0, nuclei=[epr.Nucleus("14N", A_mT=[0.6, 3.4])])


def test_grid_converges_and_warns_when_coarse():
    x = np.linspace(290, 350, 1024)
    s = epr.SpinSystem(g=[2.0, 2.1, 2.2], fwhm_mT=0.5)
    y, info, out = run_info(s, x, 9.5)
    y2 = quiet(epr.simulate_powder, s, x, 9.5, n_orient=20000)
    # Achieved 2e-4 between 5000 and 20000 knots; half-grid deviation 1e-5.
    assert np.max(np.abs(nmax(y) - nmax(y2))) < 2e-3
    assert info["grid_deviation"] < 0.01 and "not converged" not in out
    # a tilted tensor (hemisphere grid) on a very coarse grid without
    # interpolation (method='exact' samples cell midpoints only)
    s2 = epr.SpinSystem(g=[2.0, 2.1, 2.2], nuclei=[epr.Nucleus("63Cu", A_MHz=[30, 60, 300], euler_deg=[0, 40, 0])],
                        fwhm_mT=0.5)
    _, info2, out2 = run_info(s2, x, 9.5, n_orient=300, method="exact")
    assert info2["symmetry"] == "hemisphere"
    assert "grid not converged" in out2
    # the default grid for the same system: within 0.5% of a much denser one
    # (achieved 2.5e-3; its half-density deviation, 1.4%, does warn)
    y3 = quiet(epr.simulate_powder, s2, x, 9.5)
    ref3 = epr_sim._simulate(s2, x, 9.5, 1, None, "hybrid", 40000, 1.0, interp=12, max_points=5e8)
    assert np.max(np.abs(nmax(y3) - nmax(ref3))) < 5e-3


def test_default_grid_converged_rhombic_and_tilted():
    # The default grid against a much denser one (16x the orientations and
    # sub-cells fixed at 24 per cell): within 1e-3 of the maximum. Achieved
    # 1.4e-4 (rhombic g), 3e-5 (rhombic Cu), 5e-4 (tilted Cu, hemisphere).
    x = np.linspace(300, 350, 1024)
    s = epr.SpinSystem(g=[2.0, 2.1, 2.2], fwhm_mT=1.0)
    y = quiet(epr.simulate_powder, s, x, 9.5)
    ref = epr_sim._simulate(s, x, 9.5, 1, None, "hybrid", 80000, 1.0, interp=16, max_points=3e8)
    assert np.max(np.abs(nmax(y) - nmax(ref))) < 1e-3
    x2 = np.linspace(250, 360, 2048)
    for nuc in (epr.Nucleus("63Cu", A_MHz=[30, 60, 550]), epr.Nucleus("63Cu", A_MHz=[30, 60, 550], euler_deg=[0, 25, 0])):
        s2 = epr.SpinSystem(g=[2.03, 2.06, 2.25], nuclei=[nuc], fwhm_mT=1.0)
        y2 = quiet(epr.simulate_powder, s2, x2, 9.5)
        ref2 = epr_sim._simulate(s2, x2, 9.5, 1, None, "hybrid", 20000, 1.0, interp=24, max_points=3e8)
        assert np.max(np.abs(nmax(y2) - nmax(ref2))) < 1e-3


def test_euler_rotation_matches_relabelled_axes():
    # euler [0, 90, 0]: the A frame's z axis is the molecular x axis (EasySpin
    # erot convention), so A = [a, b, c] there equals diag(c, b, a) unrotated.
    x = np.linspace(300, 360, 1024)
    tilted = epr.SpinSystem(g=[2.0, 2.05, 2.2], nuclei=[epr.Nucleus("63Cu", A_MHz=[30, 60, 400], euler_deg=[0, 90, 0])],
                            fwhm_mT=1.0)
    plain = epr.SpinSystem(g=[2.0, 2.05, 2.2], nuclei=[epr.Nucleus("63Cu", A_MHz=[400, 60, 30])], fwhm_mT=1.0)
    A = tilted.A_tensor_MHz(0)
    assert np.allclose(A, np.diag([400, 60, 30]), atol=1e-9)
    a = quiet(epr.simulate_powder, tilted, x, 9.5)
    b = quiet(epr.simulate_powder, plain, x, 9.5)
    assert np.max(np.abs(nmax(a) - nmax(b))) < 1e-3


def test_perturb_matches_exact_for_cu():
    # Cu(II), A_par = 200 MHz, 1 mT lines: plain second order and
    # diagonalisation agree to 2.8% of the maximum (mostly the 63Cu nuclear
    # Zeeman term near the perpendicular orientations); the default hybrid
    # method to 1e-3.
    x = np.linspace(280, 345, 1024)
    s = epr.SpinSystem(g=[2.05, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[20, 200])], fwhm_mT=1.0)
    yp, ip, _ = run_info(s, x, 9.5, method="perturb")
    ye, ie, _ = run_info(s, x, 9.5, method="exact", n_orient=3000)
    yh, ih, out = run_info(s, x, 9.5)
    assert np.max(np.abs(nmax(yp) - nmax(ye))) < 0.04
    assert np.max(np.abs(nmax(yh) - nmax(ye))) < 3e-3
    assert "misplaces" not in out


def test_hybrid_single_nucleus_lines_are_exact():
    # Each nucleus alone is diagonalised exactly: line positions equal full
    # diagonalisation for Cu(II) with A_par 650 MHz and for VO2+ (51V,
    # I = 7/2), where plain second order is off by 0.3-0.5 mT.
    rng = np.random.default_rng(3)
    dirs = rng.normal(size=(20, 3))
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    for gt, gr in ((np.diag([2.03, 2.06, 2.25]), epr_sim._Group(1.5, 1.4824, np.diag([30.0, 60, 650]), 1, True, "63Cu")),
                   (np.diag([1.98, 1.98, 1.94]), epr_sim._Group(3.5, 1.47106, np.diag([190.0, 190, 530]), 1, True, "51V"))):
        p, a, _ = epr_sim._hybrid_direct(gt, [gr], 9.4, dirs)
        d, B, w = epr_sim._exact_lines(gt, [gr], 9.4, dirs, 200, 420, 81)
        assert epr_sim._line_error(p, a, d, B, w) < 1e-3
        pp, ap, _ = epr_sim._perturb_lines(gt, [gr], 9.4, dirs)
        assert epr_sim._line_error(pp, ap, d, B, w) > 0.1


def test_default_cu_against_exact_and_warning_estimate():
    # Natural Cu(II), A_par 580 MHz, g strain, modulation: the default agrees
    # with full diagonalisation; plain second order warns.
    x = np.linspace(250, 360, 2048)
    s = epr.SpinSystem(g=[2.06, 2.245], nuclei=[epr.Nucleus("Cu", A_MHz=[35, 580])], fwhm_mT=1.0,
                       g_strain=[0.003, 0.003, 0.012])
    yh, ih, out = run_info(s, x, 9.45, modamp_mT=0.4)
    assert ih["perturbation_spectrum_error"] < 0.01 and "misplaces" not in out
    # against diagonalisation without the strain (an axial grid, where the
    # exact method's own interpolation is accurate); with the strain the
    # default agrees with the independent S8 simulator to 3e-3
    s0 = s._replace(g_strain=None)
    yh0 = quiet(epr.simulate_powder, s0, x, 9.45, modamp_mT=0.4)
    ye0 = quiet(epr.simulate_powder, s0, x, 9.45, modamp_mT=0.4, method="exact", n_orient=3000)
    assert np.max(np.abs(nmax(yh0) - nmax(ye0))) < 5e-3


def test_perturbation_error_warns_for_large_A():
    # A_par = 550 MHz at X band: third-order shifts of ~0.1 mT near the
    # perpendicular orientations, visible on a 0.8 mT line.
    x = np.linspace(250, 360, 2048)
    s = epr.SpinSystem(g=[2.05, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[50, 550])], fwhm_mT=0.8)
    yp, info, out = run_info(s, x, 9.5, method="perturb")
    assert "perturbation misplaces lines" in out and "method='exact'" in out
    ye = quiet(epr.simulate_powder, s, x, 9.5, method="exact")
    actual = np.max(np.abs(nmax(yp) - nmax(ye)))
    # the warning's estimate is within a factor of 3 of the real difference
    assert actual / 3 < info["perturbation_spectrum_error"] < 3 * actual


def test_perturb_cross_terms_two_nuclei_against_exact():
    # Two tilted, anisotropic nuclei: the second-order cross term between
    # nuclei is in the formula; lines agree with diagonalisation to the
    # third-order size (A^3 / (h nu)^2 ~ 0.02 mT here).
    gt = np.diag([2.03, 2.06, 2.25])
    R = epr_sim._erot([20, 30, 40])
    g1 = epr_sim._Group(1.5, 0.0, np.diag([30.0, 60, 250]), 1, True, "Cu")
    g2 = epr_sim._Group(1.0, 0.0, R.T @ np.diag([40.0, 45, 60]) @ R, 1, False, "N")
    rng = np.random.default_rng(0)
    dirs = rng.normal(size=(20, 3))
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    p, _, _ = epr_sim._perturb_lines(gt, [g1, g2], 9.5, dirs)
    d, B, w = epr_sim._exact_lines(gt, [g1, g2], 9.5, dirs, 250, 360, 81)
    err = 0.0
    for k in range(dirs.shape[0]):
        bk = B[d == k][w[d == k] > 0.2 * w[d == k].max()]
        err = max(err, max(np.min(np.abs(bk - b)) for b in p[k]))
    assert err < 0.03


def test_strain_only_broadening_and_strain_widths():
    x = np.linspace(290, 350, 1024)
    s = epr.SpinSystem(g=[2.0, 2.1, 2.2], fwhm_mT=0.0, g_strain=[0.01, 0.02, 0.03])
    y, info, _ = run_info(s, x, 9.5)
    assert np.all(np.isfinite(y)) and info["grid_deviation"] < 0.01
    with pytest.raises(ValueError, match="positive"):
        epr.SpinSystem(g=2.0, fwhm_mT=0.0)
    # g strain widens the parallel feature more (strain 0.03 at g 2.2)
    narrow = quiet(epr.simulate_powder, epr.SpinSystem(g=[2.0, 2.2], fwhm_mT=0.5), x, 9.5)
    wide = quiet(epr.simulate_powder, epr.SpinSystem(g=[2.0, 2.2], fwhm_mT=0.5, g_strain=[0.002, 0.03]), x, 9.5)
    par = (x > 305) & (x < 312)
    assert np.max(np.abs(wide[par])) < 0.6 * np.max(np.abs(narrow[par]))
    # A strain on Cu: the outer lines broaden more than the inner ones
    s_a = epr.SpinSystem(g=[2.05, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[20, 550])], fwhm_mT=0.8,
                         A_strain_MHz=[0, 60])
    ya = quiet(epr.simulate_powder, s_a, np.linspace(250, 350, 2048), 9.5)
    assert np.all(np.isfinite(ya))


def test_simulation_warnings():
    x = np.linspace(330, 345, 256)                 # step 0.059 mT
    s = epr.SpinSystem(g=[2.0, 2.2], fwhm_mT=0.1)
    _, info, out = run_info(s, x, 9.5)
    assert "under 3 field steps" in out
    assert "outside the sweep" in out and info["outside_fraction"] > 0.3
    assert info["warnings"] and all(w in out for w in info["warnings"])
    _, _, out = run_info(epr.SpinSystem(g=2.0, nuclei=[epr.Nucleus("Cu", A_MHz=300)], fwhm_mT=1), np.linspace(300, 380, 1024), 9.5)
    assert "natural isotope mixture" in out and "63Cu" in out and "scaled by gn" in out


def test_cu_mixture_scaling():
    s = epr.SpinSystem(g=2.1, nuclei=[epr.Nucleus("Cu", A_MHz=[50, 500])], fwhm_mT=1)
    isos = epr_sim._isotopologues(s)
    assert len(isos) == 2 and abs(sum(w for w, _ in isos) - 1) < 1e-9
    a63 = [g[0].A for w, g in isos if g[0].label == "63Cu"][0]
    a65 = [g[0].A for w, g in isos if g[0].label == "65Cu"][0]
    assert abs(a65[2, 2] / a63[2, 2] - epr.isotope("65Cu").gn / epr.isotope("63Cu").gn) < 1e-12
    # 12C: a nonmagnetic isotope is accepted and splits nothing
    y12 = quiet(epr.simulate_powder, epr.SpinSystem(g=[2, 2.2], nuclei=[epr.Nucleus("12C", A_MHz=[50, 400])],
                                                    fwhm_mT=1), np.linspace(290, 350, 512), 9.5)
    y0 = quiet(epr.simulate_powder, epr.SpinSystem(g=[2, 2.2], fwhm_mT=1), np.linspace(290, 350, 512), 9.5)
    assert np.allclose(y12, y0)


# ==============================================================================
# Keywords, units, isotopes
# ==============================================================================

def test_unit_named_keywords():
    with pytest.raises(TypeError, match="fwhm_mT.*dBpp_mT"):
        epr.SpinSystem(g=2.0, lw=1.0)
    with pytest.raises(TypeError, match="A_MHz.*A_mT"):
        epr.Nucleus("14N", A=1.6)
    with pytest.raises(TypeError, match="exactly one of A_MHz"):
        epr.Nucleus("14N", A_MHz=40, A_mT=1.4)
    with pytest.raises(TypeError, match="exactly one width"):
        epr.SpinSystem(g=2.0, fwhm_mT=1, dBpp_mT=1)
    with pytest.raises(NotImplementedError, match="slice 3, not supported yet"):
        epr.SpinSystem(g=2.0, S=1, fwhm_mT=1)
    with pytest.raises(NotImplementedError, match="slice 3"):
        epr.SpinSystem(g=2.0, fwhm_mT=1, D_MHz=300)
    with pytest.raises(ValueError, match="voigt"):
        epr.SpinSystem(g=2.0, fwhm_mT=(1, 0.5))
    v = epr.SpinSystem(g=2.0, fwhm_mT=(0.4, 0.2), shape="voigt")
    assert v.widths() == (0.4, 0.2)
    d = epr.SpinSystem(g=2.0, dBpp_mT=1.0)
    assert abs(d.widths()[0] - math.sqrt(2 * math.log(2))) < 1e-12
    with pytest.raises(AttributeError, match="did you mean 'fwhm_mT'"):
        d.fwhm_mt


def test_convert():
    assert abs(epr.convert(550, "MHz", "1e-4 cm-1") - 183.4602) < 1e-3
    assert abs(epr.convert(1, "cm-1", "MHz") - 29979.2458) < 1e-6
    mhz = epr.convert(1.6, "mT", "MHz", g=2.006)
    assert abs(mhz - 1.6 * 2.006 * 13.996244936) < 1e-5
    assert abs(epr.convert(mhz, "MHz", "mT", g=2.006) - 1.6) < 1e-12
    assert abs(epr.convert(10, "G", "mT") - 1.0) < 1e-12
    arr = np.array([1.0, 2.0])
    assert np.allclose(epr.convert(epr.convert(arr, "mT", "1e-4 cm-1", g=2.1), "1e-4 cm-1", "mT", g=2.1), arr)
    with pytest.raises(ValueError, match="needs g"):
        epr.convert(1.6, "mT", "MHz")
    with pytest.raises(ValueError, match="unknown unit"):
        epr.convert(1, "eV", "MHz")


def test_isotopes():
    cu = epr.isotope("63Cu")
    assert cu.spin == 1.5 and abs(cu.gn - 1.4824) < 1e-3 and abs(cu.abundance - 0.6915) < 1e-3
    assert epr.isotope("Cu63") == cu == epr.isotope("cu-63")
    assert epr.isotope("D").spin == 1.0
    assert epr.isotope("14N").spin == 1.0 and epr.isotope("15N").gn < 0
    assert epr.isotope("P").spin == 0.5           # monoisotopic element
    with pytest.raises(ValueError, match="element.*63Cu"):
        epr.isotope("Cu")
    with pytest.raises(ValueError, match="not a magnetic isotope"):
        epr.isotope("64Cu")
    names = ["1H", "2H", "13C", "14N", "15N", "17O", "19F", "31P", "33S", "51V", "55Mn", "57Fe", "59Co",
             "61Ni", "63Cu", "65Cu", "67Zn", "95Mo", "97Mo", "183W"]
    for nm in names:
        iso = epr.isotope(nm)
        assert iso.spin > 0 and 0 < iso.abundance <= 1


# ==============================================================================
# fit_spectrum
# ==============================================================================

def _noisy(y, rel, seed):
    rng = np.random.default_rng(seed)
    return y + rng.normal(0, rel * np.max(np.abs(y)), y.size)


def test_fit_recovers_cu_powder():
    x = np.linspace(250, 360, 2048)
    true = epr.SpinSystem(g=[2.05, 2.25], nuclei=[epr.Nucleus("Cu", A_MHz=[50, 550])], fwhm_mT=1.5)
    y = 3 * quiet(epr.simulate_powder, true, x, 9.5) + 0.01
    y = _noisy(y, 0.02, 1)
    start = epr.SpinSystem(g=[2.06, 2.24], nuclei=[epr.Nucleus("Cu", A_MHz=[40, 520])], fwhm_mT=1.2)
    r = epr.fit_spectrum(x, y, model="powder", start=start, freq_GHz=9.5,
                         vary={"g": [0.02, 0.05], "A_MHz": [[20, 60]], "fwhm_mT": 0.8})
    p = r.params
    assert abs(p["g_perp"] - 2.05) < 5e-4 and abs(p["g_par"] - 2.25) < 3e-3
    assert abs(p["A_MHz(Cu)_par"] - 550) < 10 and abs(p["fwhm_mT"] - 1.5) < 0.05
    assert r.ci["g_par"][0] < p["g_par"] < r.ci["g_par"][1]
    assert abs(r.scale - 3) < 0.1 and r.system is not None
    text = repr(r)
    assert "g_par" in text and "1e-4 cm-1" in text and "sign of A" in text
    assert r.elapsed_s < 20


def test_fit_axial_strain_two_ranges():
    # vary["g_strain"] = [perp, par] for an axial start: one parameter sets
    # x and y together, the other z.
    x = np.linspace(260, 360, 1024)
    true = epr.SpinSystem(g=[2.06, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[40, 560])], fwhm_mT=1.0,
                          g_strain=[0.004, 0.004, 0.012])
    y = _noisy(quiet(epr.simulate_powder, true, x, 9.4), 0.003, 11)
    start = true._replace(g_strain=[0.007, 0.007, 0.006])
    r = epr.fit_spectrum(x, y, model="powder", start=start, freq_GHz=9.4,
                         vary={"g_strain": [0.005, 0.01], "fwhm_mT": 0.4}, n_starts=2)
    assert set(r.params) == {"g_strain_perp", "g_strain_par", "fwhm_mT"}
    gs = r.system.g_strain
    assert gs[0] == gs[1]
    assert abs(r.params["g_strain_par"] - 0.012) < 0.002 and abs(r.params["g_strain_perp"] - 0.004) < 0.002
    with pytest.raises(ValueError, match="perp, par"):
        epr.fit_spectrum(x, y, model="powder", start=true._replace(g_strain=[0.004, 0.006, 0.012]), freq_GHz=9.4,
                         vary={"g_strain": [0.005, 0.01]})


def test_fit_recovers_isotropic_radical():
    x = np.linspace(334, 346, 2048)
    true = epr.SpinSystem(g=2.0035, nuclei=[epr.Nucleus("14N", A_mT=0.52, n=2), epr.Nucleus("1H", A_mT=0.31, n=4)],
                          dBpp_mT=0.05, shape="lorentzian")
    y = _noisy(epr_sim._iso_model(true, x, 9.5, 1, None), 0.02, 2)
    start = epr.SpinSystem(g=2.0035, nuclei=[epr.Nucleus("14N", A_mT=0.5, n=2), epr.Nucleus("1H", A_mT=0.33, n=4)],
                           dBpp_mT=0.06, shape="lorentzian")
    r = epr.fit_spectrum(x, y, model="iso", start=start, freq_GHz=9.5,
                         vary={"g": 0.0005, "A_mT": [0.05, 0.05], "dBpp_mT": 0.03})
    assert abs(r.params["A_mT(14N)"] - 0.52) < 0.002 and abs(r.params["A_mT(1H)"] - 0.31) < 0.002
    assert abs(r.params["dBpp_mT"] - 0.05) < 0.002
    for k in ("A_mT(14N)", "A_mT(1H)"):
        lo, hi = r.ci[k]
        assert lo < {"A_mT(14N)": 0.52, "A_mT(1H)": 0.31}[k] < hi
    assert not [w for w in r.warnings if "structure" in w]


def test_fit_callable_model_and_warnings():
    x = np.linspace(-5, 5, 400)

    def two_lines(field_mT, b0, w, w2):
        # second width barely matters: correlated / undetermined
        return np.exp(-((field_mT - b0) / w) ** 2) + 1e-6 * w2

    y = _noisy(two_lines(x, 0.3, 1.0, 0.0), 0.01, 3)
    r = epr.fit_spectrum(x, y, model=two_lines, start={"b0": 0.0, "w": 1.2, "w2": 0.0},
                         vary={"b0": 0.2, "w": 0.5, "w2": 1.0})
    assert any("ended at its bound" in w and "b0" in w for w in r.warnings)
    # residual structure: fit a single Gaussian to two separated lines
    y2 = two_lines(x, -1.0, 0.5, 0) + two_lines(x, 1.5, 0.5, 0)
    r2 = epr.fit_spectrum(x, _noisy(y2, 0.002, 4), model=two_lines, start={"b0": 0.0, "w": 1.0, "w2": 0.0},
                          vary={"b0": 2.0, "w": 0.9})
    assert any("residual has structure" in w for w in r2.warnings)


def test_fit_correlation_warning():
    x = np.linspace(-5, 5, 300)

    def model(field_mT, a, b):
        return (a + b) * np.exp(-field_mT ** 2)     # only a + b is determined

    y = _noisy(model(x, 1.0, 1.0), 0.01, 5)
    r = epr.fit_spectrum(x, y, model=model, start={"a": 1.0, "b": 1.0}, vary={"a": 0.5, "b": 0.5},
                         baseline_order=-1, n_starts=2)
    assert any("correlated" in w for w in r.warnings)


def test_fit_stops_at_max_time():
    x = np.linspace(250, 360, 2048)
    true = epr.SpinSystem(g=[2.05, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[50, 550])], fwhm_mT=1.5)
    y = _noisy(quiet(epr.simulate_powder, true, x, 9.5), 0.02, 6)
    start = epr.SpinSystem(g=[2.06, 2.24], nuclei=[epr.Nucleus("63Cu", A_MHz=[40, 520])], fwhm_mT=1.2)
    r = epr.fit_spectrum(x, y, model="powder", start=start, freq_GHz=9.5, max_time_s=0.3,
                         vary={"g": [0.02, 0.05], "A_MHz": [[20, 60]], "fwhm_mT": 0.8})
    assert r.stopped_early and any("max_time_s" in w for w in r.warnings)
    assert np.all(np.isfinite(r.fit))


def test_fit_multiple_minima_warning():
    x = np.linspace(-10, 10, 600)

    def model(field_mT, c):
        return np.exp(-(field_mT - c) ** 2) + np.exp(-(field_mT + c) ** 2)   # c and -c identical

    y = _noisy(model(x, 3.0), 0.01, 7)
    r = epr.fit_spectrum(x, y, model=model, start={"c": -3.5}, vary={"c": 7.0}, n_starts=6, seed=1)
    assert any("another start fits about as well" in w for w in r.warnings)


def test_fit_keyword_errors():
    x = np.linspace(300, 360, 256)
    s = epr.SpinSystem(g=[2.05, 2.25], fwhm_mT=1)
    with pytest.raises(TypeError, match="fwhm_mT"):
        epr.fit_spectrum(x, x * 0, start=s, freq_GHz=9.5, vary={"lw": 0.5})
    with pytest.raises(TypeError, match="A_MHz"):
        epr.fit_spectrum(x, x * 0, start=s, freq_GHz=9.5, vary={"A": 10})
    with pytest.raises(ValueError, match="freq_GHz"):
        epr.fit_spectrum(x, x * 0, start=s, vary={"g": 0.01})
    with pytest.raises(ValueError, match="slice 2"):
        epr.fit_spectrum(x, x * 0, model="slow", start=s, freq_GHz=9.5, vary={"g": 0.01})
