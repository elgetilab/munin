"""S8 EPR, frozen-solution (powder) and isotropic-fit cases (docs/agent-track/EASYSPIN-PORT-PLAN.md,
"New S8 cases", slice 1).

This file is an independent reference simulator. It does not import or read
labkit: labkit.epr computes powder spectra by perturbation theory, this file by
exact diagonalisation, so the two methods share no code and no approximations.
Truths are the generating parameters, never a fit.

Method (S = 1/2, any number of nuclei, g and A tensors coaxial):
- Spin Hamiltonian in MHz, field in mT:
      H = muB/h B n.g.S + sum_k [ S.A_k.I_k - gn_k muN/h B n.I_k ]
  built on the full product space and diagonalised with numpy.linalg.eigh.
- Resonance fields per orientation n: for every pair (lower-manifold level i,
  upper-manifold level j; the 2 x prod(2I+1) levels sorted by energy split into
  ms = -1/2 and +1/2 halves at X-band fields), Newton iteration on
  E_j(B) - E_i(B) = h nu, with dE/dB from the Hellmann-Feynman theorem. All
  pairs are kept, so forbidden transitions enter with their own intensity.
- Intensity: |<j| e.g.S |i>|^2 averaged over the microwave field direction e
  in the plane perpendicular to B0 (two orthogonal directions), divided by
  |d(E_j - E_i)/dB| (the field-swept Jacobian, which gives the usual 1/g
  weighting in the high-field limit). Populations are taken as equal (hv << kT).
- Orientations: one octant (D2h symmetry of coaxial tensors), a coarse
  theta x phi grid solved exactly, then resonance fields and intensities
  interpolated by bicubic splines onto a dense midpoint grid weighted by
  sin(theta). The spline error is checked against exact points in selftest().
- Line shape: each line is spread over a fine field grid (linear,
  area-conserving binning), then convolved with a Gaussian or Lorentzian of
  the given FWHM. g-strain (FWHM in g, uncorrelated along x, y, z) adds an
  orientation-dependent Gaussian width B_res * sigma_geff / g_eff, with
  sigma_geff = sqrt(sum (n_a^2 g_a sigma_a)^2) / g_eff; lines are grouped by
  total width (2% bins) and each group convolved with its own kernel.
- Natural copper: 63Cu (69.15%, gn 1.4824) and 65Cu (30.85%, gn 1.5878)
  simulated separately and summed; the stated A is for 63Cu and 65Cu's is
  scaled by the gn ratio.
- Isotropic radicals: exact Breit-Rabi resonance fields per nucleus
  (closed-form 2 x 2 blocks, solved with brentq), combined additively over
  nuclei. The missing cross terms are of order a_i a_j / B0 (below 0.002 mT
  here, against a 0.005 mT tolerance).
- Output: the first-harmonic (field-modulated) derivative of the absorption,
  with the exact modulation kernel from s8.first_harmonic.

Clean spectra are normalised and quantised to 1e-8 of their maximum before the
noise is added, so last-bit differences in eigh between BLAS builds do not
change the written bytes.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy import optimize, signal
from scipy.interpolate import RectBivariateSpline

import s8
from common import write_case
from s8 import H, MU_B, MT_PER_GHZ, bes3t, first_harmonic

MU_N = 5.0507837393e-27                  # J/T (CODATA 2022)
BE = MU_B / H * 1e-9                     # MHz per mT per unit g (13.99624...)
BN = MU_N / H * 1e-9                     # MHz per mT per unit gn (0.0076226...)
MHZ_PER_1E4_CM = 2.99792458              # 1e-4 cm-1 = c * 1e-2 m-1 * 1e-4 ... = 2.99792458 MHz

# Nuclear spin and gn (Stone 2014 / IAEA table, as used by every EPR program).
NUC = {"1H": (0.5, 5.58569468), "14N": (1.0, 0.40376100), "31P": (0.5, 2.26320),
       "63Cu": (1.5, 1.4824), "65Cu": (1.5, 1.5878)}
CU_ABUNDANCE = {"63Cu": 0.6915, "65Cu": 0.3085}


# -- spin operators ---------------------------------------------------------------

def _ops(I: float):
    m = np.arange(I, -I - 1e-9, -1.0)
    d = len(m)
    ip = np.zeros((d, d))
    for k in range(1, d):
        ip[k - 1, k] = math.sqrt(I * (I + 1) - m[k] * (m[k] + 1))
    return [(ip + ip.T) / 2, (ip - ip.T) / 2j, np.diag(m).astype(complex)]


def _system_ops(nuclei):
    """Electron and nuclear operators on the product space (electron first)."""
    spins = [0.5] + [I for I, _, _ in nuclei]
    dims = [int(round(2 * s + 1)) for s in spins]
    full = []
    for idx, s in enumerate(spins):
        ops = _ops(s)
        lifted = []
        for o in ops:
            m = np.ones((1, 1), complex)
            for j, dj in enumerate(dims):
                m = np.kron(m, o if j == idx else np.eye(dj))
            lifted.append(m)
        full.append(lifted)
    return full[0], full[1:], int(np.prod(dims))


# -- exact resonance fields -------------------------------------------------------------

def resonances(g, nuclei, nu_mhz, dirs, tol=1e-9, max_iter=60):
    """Exact resonance fields (mT) and intensities for orientations `dirs` (N, 3).
    g: principal values (3,); nuclei: [(I, gn, A_MHz principal (3,))].
    Returns B (N, P), intensity (N, P) for P = n^2 level pairs."""
    S, Ik, d = _system_ops(nuclei)
    g = np.asarray(g, float)
    h0 = np.zeros((d, d), complex)
    for (I, gn, A), ops in zip(nuclei, Ik):
        for a in range(3):
            h0 += A[a] * S[a] @ ops[a]
    zax = []
    for a in range(3):
        z = BE * g[a] * S[a]
        for (I, gn, A), ops in zip(nuclei, Ik):
            z = z - BN * gn * ops[a]
        zax.append(z)
    dirs = np.asarray(dirs, float)
    hz = np.einsum("na,aij->nij", dirs, np.array(zax))             # (N, d, d)
    n = d // 2
    lo, up = np.meshgrid(np.arange(n), np.arange(n, d), indexing="ij")
    lo, up = lo.ravel(), up.ravel()
    P = len(lo)
    geff = np.sqrt(np.sum((dirs * g) ** 2, axis=1))
    B = np.repeat((nu_mhz / (BE * geff))[:, None], P, axis=1)       # (N, P) start
    for _ in range(max_iter):
        w, vl, vu, slope = _pair_states(h0, hz, B, lo, up)
        de = np.take_along_axis(w, up[None, :, None].repeat(len(B), 0), 2)[..., 0] \
            - np.take_along_axis(w, lo[None, :, None].repeat(len(B), 0), 2)[..., 0]
        step = (de - nu_mhz) / slope
        B = B - step
        if np.max(np.abs(step)) < tol:
            break
    else:
        raise RuntimeError("resonance field iteration did not converge")
    w, vl, vu, slope = _pair_states(h0, hz, B, lo, up)      # states at the converged fields
    e1, e2 = _perp_basis(dirs)
    sel = np.array([np.einsum("npi,ij,npj->np", vu.conj(), S[a], vl) for a in range(3)])  # (3, N, P)
    t1 = np.einsum("na,anp->np", e1 * g, sel)
    t2 = np.einsum("na,anp->np", e2 * g, sel)
    inten = 0.5 * (np.abs(t1) ** 2 + np.abs(t2) ** 2) / np.abs(slope)
    return B, inten


def _pair_states(h0, hz, B, lo, up):
    """Eigenvalues, the (lower, upper) eigenvectors of every pair, and
    d(E_up - E_lo)/dB by Hellmann-Feynman, at fields B (N, P)."""
    w, v = np.linalg.eigh(h0[None, None] + B[..., None, None] * hz[:, None])
    d = v.shape[-1]
    vl = np.take_along_axis(v, np.broadcast_to(lo[None, :, None, None], v.shape[:2] + (d, 1)), 3)[..., 0]
    vu = np.take_along_axis(v, np.broadcast_to(up[None, :, None, None], v.shape[:2] + (d, 1)), 3)[..., 0]
    slope = (np.einsum("npi,nij,npj->np", vu.conj(), hz, vu)
             - np.einsum("npi,nij,npj->np", vl.conj(), hz, vl)).real
    return w, vl, vu, slope


def _perp_basis(dirs):
    ref = np.where(np.abs(dirs[:, 2:3]) < 0.9, np.array([[0.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 0.0]]))
    e1 = np.cross(dirs, ref)
    e1 /= np.linalg.norm(e1, axis=1, keepdims=True)
    e2 = np.cross(dirs, e1)
    return e1, e2


def _dirs(theta, phi):
    th, ph = np.meshgrid(theta, phi, indexing="ij")
    return np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)], -1).reshape(-1, 3)


# -- powder spectrum ------------------------------------------------------------------

COARSE = (61, 31)          # theta 0..90 deg in 1.5 deg, phi 0..90 deg in 3 deg
FINE = (1800, 720)         # midpoint grid for the spline-interpolated lines


def powder_lines(g, nuclei, nu_ghz, coarse=COARSE, fine=FINE):
    """(B, weight, g_eff, dirs-squared) for all lines on the dense grid."""
    nu = nu_ghz * 1e3
    tc = np.linspace(0, math.pi / 2, coarse[0])
    pc = np.linspace(0, math.pi / 2, coarse[1])
    Bc, Ic = resonances(g, nuclei, nu, _dirs(tc, pc))
    tf = (np.arange(fine[0]) + 0.5) * (math.pi / 2) / fine[0]
    pf = (np.arange(fine[1]) + 0.5) * (math.pi / 2) / fine[1]
    sinw = np.repeat(np.sin(tf)[:, None], fine[1], axis=1).ravel()
    Bs, Ws = [], []
    for p in range(Bc.shape[1]):
        bsp = RectBivariateSpline(tc, pc, Bc[:, p].reshape(coarse))
        isp = RectBivariateSpline(tc, pc, Ic[:, p].reshape(coarse))
        Bs.append(bsp(tf, pf).ravel())
        Ws.append(np.clip(isp(tf, pf).ravel(), 0, None) * sinw)
    df = _dirs(tf, pf)
    return np.concatenate(Bs), np.concatenate(Ws), np.tile(df, (Bc.shape[1], 1))


def _bin(xf, B, W):
    """Area-conserving linear binning of lines onto the uniform grid xf."""
    dx = xf[1] - xf[0]
    u = (B - xf[0]) / dx
    i = np.floor(u).astype(int)
    f = u - i
    ok = (i >= 0) & (i < len(xf) - 1)
    out = np.bincount(i[ok], W[ok] * (1 - f[ok]), minlength=len(xf))
    out += np.bincount(i[ok] + 1, W[ok] * f[ok], minlength=len(xf))
    return out[:len(xf)] / dx


def _conv(y, xf, fwhm, shape):
    dx = xf[1] - xf[0]
    half = int(math.ceil((8 * fwhm if shape == "gaussian" else 60 * fwhm) / dx))
    k = np.arange(-half, half + 1) * dx
    if shape == "gaussian":
        s = fwhm / (2 * math.sqrt(2 * math.log(2)))
        ker = np.exp(-0.5 * (k / s) ** 2)
    else:
        gam = fwhm / 2
        ker = gam / (k ** 2 + gam ** 2)
    ker /= ker.sum()
    return signal.fftconvolve(y, ker, mode="same")


def powder_absorption(xf, g, nuclei_sets, nu_ghz, fwhm_mT, shape="gaussian", g_strain=None):
    """Absorption on the uniform fine grid xf (mT). nuclei_sets: [(weight, nuclei)]
    for isotopologues (one entry with weight 1 for a single species)."""
    g = np.asarray(g, float)
    total = np.zeros_like(xf)
    for wt, nuclei in nuclei_sets:
        B, W, n = powder_lines(g, nuclei, nu_ghz)
        if g_strain is None:
            total += wt * _conv(_bin(xf, B, W), xf, fwhm_mT, shape)
            continue
        assert shape == "gaussian", "g-strain is combined with a Gaussian line"
        sg = np.asarray(g_strain, float) / (2 * math.sqrt(2 * math.log(2)))
        geff = np.sqrt(np.sum((n * g) ** 2, axis=1))
        sgeff = np.sqrt(np.sum((n ** 2 * g * sg) ** 2, axis=1)) / geff
        s_lw = fwhm_mT / (2 * math.sqrt(2 * math.log(2)))
        s_tot = np.sqrt(s_lw ** 2 + (B * sgeff / geff) ** 2)
        edges = s_lw * 1.02 ** np.arange(0, 400)
        grp = np.clip(np.searchsorted(edges, s_tot) - 1, 0, len(edges) - 2)
        for gi in np.unique(grp):
            m = grp == gi
            s_mid = math.sqrt(edges[gi] * edges[gi + 1])
            total += wt * _conv(_bin(xf, B[m], W[m]), xf, s_mid * 2 * math.sqrt(2 * math.log(2)), "gaussian")
    return total


def derivative_spectrum(x_mT, absorption_fn, modamp_mT):
    return first_harmonic(absorption_fn, x_mT, modamp_mT)


def _quantise(y):
    y = y / np.max(np.abs(y))
    return np.round(y * 1e8) / 1e8


# -- isotropic: Breit-Rabi ---------------------------------------------------------------

def breit_rabi_fields(g, I, gn, a_mhz, nu_mhz):
    """Exact allowed resonance fields (mT), one per mI (+I ... -I), for one
    nucleus and isotropic g and a, from the closed-form 2 x 2 blocks."""
    def energy(B, M, sign):
        ze, zn = BE * g * B, BN * gn * B
        if abs(abs(M) - (I + 0.5)) < 1e-9:
            ms = 0.5 if M > 0 else -0.5
            mi = M - ms
            return ms * ze - zn * mi + a_mhz * ms * mi
        avg = -zn * M - a_mhz / 4
        dd = (ze + zn) / 2 + a_mhz * M / 2
        off = (a_mhz / 2) * math.sqrt((I + 0.5) ** 2 - M ** 2)
        return avg + sign * math.sqrt(dd * dd + off * off)

    out = []
    b0 = nu_mhz / (BE * g)
    for k in range(int(round(2 * I)) + 1):
        mi = I - k
        f = lambda B: energy(B, mi + 0.5, +1) - energy(B, mi - 0.5, -1) - nu_mhz
        lo, hi = b0 - abs(a_mhz) * (I + 1) / (BE * g) - 5, b0 + abs(a_mhz) * (I + 1) / (BE * g) + 5
        out.append(optimize.brentq(f, lo, hi, xtol=1e-13, rtol=1e-15))
    return np.array(out)


def iso_absorption(x_mT, g, nuclei, nu_ghz, fwhm_mT, shape="lorentzian"):
    """Isotropic spectrum: nuclei [(I, gn, a_MHz, count)]; per-nucleus exact
    Breit-Rabi shifts added over nuclei, intensities from the binomial /
    multinomial statistics of equivalent nuclei."""
    nu = nu_ghz * 1e3
    b0 = nu / (BE * g)
    pos, amp = np.array([b0]), np.array([1.0])
    for I, gn, a, count in nuclei:
        shifts = breit_rabi_fields(g, I, gn, a, nu) - b0
        for _ in range(count):
            pos = (pos[:, None] + shifts[None, :]).ravel()
            amp = np.repeat(amp, len(shifts)) / len(shifts)
    if shape == "lorentzian":
        gam = fwhm_mT / 2
        return np.sum(amp[None, :] * gam / math.pi / ((x_mT[:, None] - pos[None, :]) ** 2 + gam ** 2), axis=1)
    s = fwhm_mT / (2 * math.sqrt(2 * math.log(2)))
    return np.sum(amp[None, :] * np.exp(-0.5 * ((x_mT[:, None] - pos[None, :]) / s) ** 2), axis=1) / (s * math.sqrt(2 * math.pi))


# -- self-checks -----------------------------------------------------------------------

def selftest(verbose=False):
    rep = {}
    nu = 9500.0
    rng = np.random.default_rng(12345)
    dirs = rng.normal(size=(5, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    # 1. isotropic limit equals the closed-form Breit-Rabi positions (Cu-like, large A)
    gI, a = 2.10, 600.0
    B, W = resonances([gI] * 3, [(1.5, 1.4824, np.array([a] * 3))], nu, dirs)
    br = np.sort(breit_rabi_fields(gI, 1.5, 1.4824, a, nu))
    err = 0.0
    for k in range(len(dirs)):
        allowed = np.sort(B[k][W[k] > 1e-3 * W[k].max()])
        assert len(allowed) == 4, allowed
        err = max(err, float(np.max(np.abs(allowed - br))))
    assert err < 1e-7, f"isotropic exact vs Breit-Rabi differ by {err} mT"
    rep["iso_vs_breit_rabi_mT"] = err
    # 2. small A: first-order positions (hv - mI a) / (g muB), error below a^2 / hv scale
    a = 5.0
    B, W = resonances([gI] * 3, [(1.0, 0.403761, np.array([a] * 3))], nu, dirs[:1])
    allowed = np.sort(B[0][W[0] > 1e-3 * W[0].max()])
    first = np.sort((nu - np.array([1, 0, -1]) * a) / (BE * gI))
    err1 = float(np.max(np.abs(allowed - first)))
    assert err1 < 2 * a * a / nu / (BE * gI) + 1e-3, err1
    rep["first_order_mT"] = err1
    # 3. axial g, no hyperfine: turning points h nu / (muB g_par), h nu / (muB g_perp)
    gpar, gperp = 2.25, 2.05
    tc = np.linspace(0, math.pi / 2, COARSE[0]); pc = np.linspace(0, math.pi / 2, COARSE[1])
    Bc, Ic = resonances([gperp, gperp, gpar], [], nu, _dirs(tc, pc))
    bpar, bperp = nu / (BE * gpar), nu / (BE * gperp)
    tp = max(abs(Bc.min() - bpar), abs(Bc.max() - bperp))
    assert tp < 1e-8, tp
    rep["axial_turning_points_mT"] = tp
    # 3b. the spectrum against the 1D closed form: B(theta) = hv / (muB g(theta)),
    #     weight |<+|e.g.S|->|^2 averaged over e, from the effective-field formula,
    #     times 1 / (muB g(theta)) (field-swept Jacobian)
    xf = np.arange(bpar - 5, bperp + 5, 0.01)
    sim = powder_absorption(xf, [gperp, gperp, gpar], [(1.0, [])], nu / 1e3, 0.5)
    th = (np.arange(200000) + 0.5) * (math.pi / 2) / 200000
    gt = np.sqrt(gpar ** 2 * np.cos(th) ** 2 + gperp ** 2 * np.sin(th) ** 2)
    # e1 = (cos th, 0, -sin th), e2 = (0, 1, 0) for n = (sin th, 0, cos th); k = g n / |g n|
    k = np.stack([gperp * np.sin(th), np.zeros_like(th), gpar * np.cos(th)], 1) / gt[:, None]
    v1 = np.stack([gperp * np.cos(th), np.zeros_like(th), -gpar * np.sin(th)], 1)
    v2 = np.stack([np.zeros_like(th), gperp * np.ones_like(th), np.zeros_like(th)], 1)
    p = 0.5 * sum((np.sum(v * v, 1) - np.sum(v * k, 1) ** 2) / 4 for v in (v1, v2))
    ref = _conv(_bin(xf, nu / (BE * gt), p / (BE * gt) * np.sin(th)), xf, 0.5, "gaussian")
    shape_err = float(np.max(np.abs(sim / sim.max() - ref / ref.max())))
    assert shape_err < 5e-3, shape_err
    rep["axial_shape_maxdev"] = shape_err
    peak = xf[np.argmax(_conv(_bin(xf, nu / (BE * gt), p / (BE * gt) * np.sin(th)), xf, 0.02, "gaussian"))]
    assert abs(peak - bperp) < 0.05, (peak, bperp)
    # 4. spline interpolation against exact points off the coarse grid (Cu, both isotopes' worst case)
    nucs = [(1.5, 1.5878, np.array([45.0, 45.0, 600.0]))]
    Bc, Ic = resonances([2.05, 2.05, 2.25], nucs, nu, _dirs(tc, pc))
    tt = np.array([0.123, 0.61, 1.1, 1.43]); pp = np.array([0.2, 0.9, 1.3])
    Bx, Ix = resonances([2.05, 2.05, 2.25], nucs, nu, _dirs(tt, pp))
    sp = max(float(np.max(np.abs(RectBivariateSpline(tc, pc, Bc[:, q].reshape(COARSE))(tt, pp).ravel()
                                 - Bx[:, q]))) for q in range(Bc.shape[1]))
    assert sp < 5e-3, sp
    rep["spline_vs_exact_mT"] = sp
    if verbose:
        print(rep)
    return rep


# -- cases ---------------------------------------------------------------------------

def _fine_axis(x_mT, step):
    return np.arange(x_mT[0] - 10, x_mT[-1] + 10 + step / 2, step)


def _spectrum(x_mT, xf, absorption, modamp_mT):
    absf = lambda bb: np.interp(bb, xf, absorption)
    return _quantise(derivative_spectrum(x_mT, absf, modamp_mT))


def cw_cu_powder(root: Path):
    rng = s8._rng(80201)
    q = s8._p("cu_powder", {"nu": 9.4021, "gpar": 2.252, "gperp": 2.054, "Apar": 552.0, "Aperp": 42.0,
                            "fwhm": 1.10},
              {"nu": ("rel", 0.004), "gpar": ("abs", 0.03), "gperp": ("abs", 0.012),
               "Apar": ("rel", 0.08), "Aperp": ("rel", 0.3), "fwhm": ("rel", 0.2)})
    nu, gpar, gperp, apar, aperp = q["nu"], q["gpar"], q["gperp"], q["Apar"], q["Aperp"]
    ratio = NUC["65Cu"][1] / NUC["63Cu"][1]
    sets = [(CU_ABUNDANCE[iso], [(1.5, NUC[iso][1], np.array([aperp, aperp, apar]) * (ratio if iso == "65Cu" else 1.0))])
            for iso in ("63Cu", "65Cu")]
    x_g = np.linspace(2500.0, 3600.0, 2048)
    x = x_g / 10
    xf = _fine_axis(x, 0.01)
    absb = powder_absorption(xf, [gperp, gperp, gpar], sets, nu, q["fwhm"], g_strain=[0.004, 0.004, 0.010])
    y = _spectrum(x, xf, absb, 0.4) * 2.0e4
    y = y + s8._noise(rng, y, 250)
    dsc, dta = bes3t(x_g, y, xunit="G", title="Cu_complex_frozen",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "2.000e-03", "AVGS": 16,
                          "RCAG": "60.00", "SPTP": "0.08192", "STMP": "77"},
                     dsl={"ModAmp": "4.000 G", "ModFreq": "100.00 kHz", "Attenuation": "20.0 dB"})
    apar_cm = apar / MHZ_PER_1E4_CM
    write_case(root, s8._id("s8_cw_cu_powder"), "S8", [{
        "prompt": "cu_frozen.DSC / cu_frozen.DTA (Bruker BES3T) is an X-band CW-EPR spectrum of a "
                  "Cu(II) complex in frozen solution at 77 K, natural-abundance copper. We need the "
                  "spin Hamiltonian parameters for a table: g_par (g_par), g_perp (g_perp) and the "
                  "parallel copper hyperfine coupling for 63Cu as a magnitude, in MHz (A_par_MHz) and "
                  "in units of 1e-4 cm-1 (A_par_1e-4cm-1).",
        "checks": [{"type": "numeric", "key": "g_par", "truth": "truth:g_par", "atol": 0.003},
                   {"type": "numeric", "key": "g_perp", "truth": "truth:g_perp", "atol": 0.003},
                   {"type": "numeric", "key": "A_par_MHz", "truth": "truth:A_par_MHz", "rtol": 0.05},
                   {"type": "numeric", "key": "A_par_1e-4cm-1", "truth": "truth:A_par_1e-4cm-1",
                    "rtol": 0.05}]}],
        files={"cu_frozen.DSC": dsc, "cu_frozen.DTA": dta},
        results_keys={"g_par": "", "g_perp": "", "A_par_MHz": "MHz", "A_par_1e-4cm-1": "1e-4 cm-1"},
        truth={"g_par": gpar, "g_perp": gperp, "A_par_MHz": apar, "A_par_1e-4cm-1": apar_cm,
               "A_perp_MHz": aperp, "A_par_65Cu_MHz": apar * ratio, "freq_GHz": nu,
               "A_par_MHz_naive_ge": apar / (gpar * BE) * (s8.G_E * BE)},
        tags=["format:bes3t", "trap:A_units", "trap:g_par_not_zero_crossing", "trap:isotope_mixture",
              "sim:powder"],
        provenance="synthetic powder spectrum, exact diagonalisation (s8_powder.cw_cu_powder): 63Cu/65Cu "
                   "69.15/30.85 with A scaled by gn, coaxial axial g and A, g-strain FWHM "
                   "(0.004, 0.004, 0.010), Gaussian FWHM line, 0.4 mT modulation")


def cw_nitroxide_rigid(root: Path):
    rng = s8._rng(80202)
    q = s8._p("nitroxide_rigid", {"nu": 9.4318, "Azz": 3.52, "Axx": 0.58, "Ayy": 0.50, "fwhm": 0.62},
              {"nu": ("rel", 0.004), "Azz": ("abs", 0.15), "Axx": ("rel", 0.15), "Ayy": ("rel", 0.15),
               "fwhm": ("rel", 0.2)})
    nu = q["nu"]
    g = np.array([2.0084, 2.0061, 2.0022])
    a_mt = np.array([q["Axx"], q["Ayy"], q["Azz"]])
    a_mhz = a_mt * g * BE                       # each principal value converted at its own g
    b0 = MT_PER_GHZ * nu / 2.0055
    x_g = np.linspace(round(b0 * 10) - 120, round(b0 * 10) + 120, 2048)
    x = x_g / 10
    xf = _fine_axis(x, 0.004)
    absb = powder_absorption(xf, g, [(1.0, [(1.0, NUC["14N"][1], a_mhz)])], nu, q["fwhm"])
    y = _spectrum(x, xf, absb, 0.2) * 5.0e3
    y = y + s8._noise(rng, y, 300)
    lines = [f"# frozen spin-labelled protein in glycerol/buffer, T = 77 K, nu = {nu:.4f} GHz",
             "# modulation 2.0 G, 100 kHz; power 0.2 mW", "Field [G]\tIntensity"]
    lines += [f"{a:.3f}\t{b:.6e}" for a, b in zip(x_g, y)]
    write_case(root, s8._id("s8_cw_nitroxide_rigid"), "S8", [{
        "prompt": "frozen_77K.txt is an X-band CW-EPR spectrum (first derivative, exported as text) of a "
                  "nitroxide spin label on a protein in frozen glycerol/buffer at 77 K, where the label "
                  "is immobilised. Report the principal hyperfine value Azz of the 14N in mT (Azz_mT); "
                  "we use it as a polarity marker for the label site.",
        "checks": [{"type": "numeric", "key": "Azz_mT", "truth": "truth:Azz_mT", "atol": 0.08}]}],
        files={"frozen_77K.txt": ("\n".join(lines) + "\n").encode()},
        results_keys={"Azz_mT": "mT"},
        truth={"Azz_mT": q["Azz"], "Axx_mT": q["Axx"], "Ayy_mT": q["Ayy"], "g": list(g), "freq_GHz": nu,
               "fwhm_mT": q["fwhm"]},
        tags=["format:ascii", "trap:half_outer_splitting", "trap:units_G_mT", "sim:powder"],
        provenance="synthetic rigid-limit powder spectrum, exact diagonalisation "
                   "(s8_powder.cw_nitroxide_rigid): rhombic g (2.0084, 2.0061, 2.0022), coaxial 14N A, "
                   "A_mT -> MHz at each principal g, Gaussian FWHM line, 0.2 mT modulation")


def cw_rhombic(root: Path):
    rng = s8._rng(80203)
    q = s8._p("rhombic", {"nu": 9.3982, "g1": 2.418, "g2": 2.253, "g3": 1.914},
              {"nu": ("rel", 0.004), "g1": ("abs", 0.04), "g2": ("abs", 0.03), "g3": ("abs", 0.03)})
    nu = q["nu"]
    g = np.array([q["g3"], q["g2"], q["g1"]])           # x, y, z: smallest to largest
    x_g = np.linspace(2500.0, 3800.0, 2048)
    x = x_g / 10
    xf = _fine_axis(x, 0.01)
    absb = powder_absorption(xf, g, [(1.0, [])], nu, 1.2, g_strain=[0.030, 0.012, 0.020])
    y = _spectrum(x, xf, absb, 0.6) * 1.0e4
    y = y + s8._noise(rng, y, 200)
    dsc, dta = bes3t(x_g, y, xunit="G", title="lowspin_heme",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "6.325e-03", "AVGS": 9,
                          "RCAG": "60.00", "SPTP": "0.08192", "STMP": "10"},
                     dsl={"ModAmp": "6.000 G", "ModFreq": "100.00 kHz", "Attenuation": "15.0 dB"})
    write_case(root, s8._id("s8_cw_rhombic"), "S8", [{
        "prompt": "heme.DSC / heme.DTA (Bruker BES3T) is an X-band CW-EPR spectrum at 10 K of a "
                  "low-spin ferric heme protein (S = 1/2) in frozen solution. Report the three "
                  "principal g-values, from largest to smallest (g_max, g_mid, g_min).",
        "checks": [{"type": "numeric", "key": "g_max", "truth": "truth:g_max", "atol": 0.002},
                   {"type": "numeric", "key": "g_mid", "truth": "truth:g_mid", "atol": 0.002},
                   {"type": "numeric", "key": "g_min", "truth": "truth:g_min", "atol": 0.002}]}],
        files={"heme.DSC": dsc, "heme.DTA": dta},
        results_keys={"g_max": "", "g_mid": "", "g_min": ""},
        truth={"g_max": q["g1"], "g_mid": q["g2"], "g_min": q["g3"], "g_iso": float(np.mean(g)),
               "freq_GHz": nu},
        tags=["format:bes3t", "trap:centre_crossing_not_giso", "trap:g_strain_edges", "sim:powder"],
        provenance="synthetic powder spectrum, exact diagonalisation (s8_powder.cw_rhombic): rhombic g, "
                   "g-strain FWHM (0.030, 0.012, 0.020) along (min, mid, max), 1.2 mT Gaussian, 0.6 mT "
                   "modulation")


def cw_radical_fit(root: Path):
    rng = s8._rng(80204)
    q = s8._p("radical_fit", {"nu": 9.4473, "g": 2.0036, "aN": 0.721, "aH": 0.264, "dbpp": 0.045},
              {"nu": ("rel", 0.004), "g": ("abs", 0.0008), "aN": ("rel", 0.12), "aH": ("rel", 0.15),
               "dbpp": ("rel", 0.2)})
    nu, g = q["nu"], q["g"]
    b0 = MT_PER_GHZ * nu / g
    nuclei = [(1.0, NUC["14N"][1], q["aN"] * g * BE, 2), (0.5, NUC["1H"][1], q["aH"] * g * BE, 4)]
    x_g = np.linspace(round(b0 * 10) - 40, round(b0 * 10) + 40, 4096)
    x = x_g / 10
    xf = np.arange(x[0] - 1, x[-1] + 1 + 1e-4, 0.0005)
    fwhm = math.sqrt(3) * q["dbpp"]            # Lorentzian: FWHM = sqrt(3) dBpp
    absb = iso_absorption(xf, g, nuclei, nu, fwhm, "lorentzian")
    y = _spectrum(x, xf, absb, 0.01) * 3.0e4
    y = y + s8._noise(rng, y, 400)
    dsc, dta = bes3t(x_g, y, xunit="G", title="radical_anion",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "6.325e-04", "AVGS": 4,
                          "RCAG": "60.00", "SPTP": "0.04096", "STMP": "295"},
                     dsl={"ModAmp": "0.100 G", "ModFreq": "100.00 kHz", "Attenuation": "25.0 dB"})
    write_case(root, s8._id("s8_cw_radical_fit"), "S8", [{
        "prompt": "anion.DSC / anion.DTA (Bruker BES3T) is the CW-EPR spectrum of the pyrazine "
                  "radical anion in fluid solution at room temperature (two equivalent 14N and four "
                  "equivalent 1H). Report the isotropic hyperfine couplings in mT: aN_mT for the "
                  "nitrogens and aH_mT for the protons.",
        "checks": [{"type": "numeric", "key": "aN_mT", "truth": "truth:aN_mT", "atol": 0.005},
                   {"type": "numeric", "key": "aH_mT", "truth": "truth:aH_mT", "atol": 0.005}]}],
        files={"anion.DSC": dsc, "anion.DTA": dta},
        results_keys={"aN_mT": "mT", "aH_mT": "mT"},
        truth={"aN_mT": q["aN"], "aH_mT": q["aH"], "g_iso": g, "dBpp_mT": q["dbpp"], "freq_GHz": nu},
        tags=["format:bes3t", "trap:multiplet_counting", "trap:fit_not_peak_picking", "trap:units_G_mT",
              "sim:iso"],
        provenance="synthetic isotropic spectrum (s8_powder.cw_radical_fit): exact Breit-Rabi per nucleus, "
                   "shifts added over 2 x 14N + 4 x 1H, a_mT -> MHz at g_iso, Lorentzian lines")


def cw_units(root: Path):
    rng = s8._rng(80205)
    q = s8._p("units", {"nu": 9.3817, "g": 2.100, "aP": 2.85, "dbpp": 0.55},
              {"nu": ("rel", 0.004), "g": ("abs", 0.04), "aP": ("rel", 0.2), "dbpp": ("rel", 0.2)})
    nu, g = q["nu"], q["g"]
    a_mhz = q["aP"] * g * BE
    b0 = MT_PER_GHZ * nu / g
    br = breit_rabi_fields(g, 0.5, NUC["31P"][1], a_mhz, nu * 1e3)
    a_read = round(float(abs(br[1] - br[0])), 2)        # the splitting as read, 0.01 mT
    x_g = np.linspace(round(b0 * 10) - 300, round(b0 * 10) + 300, 2048)
    x = x_g / 10
    xf = np.arange(x[0] - 5, x[-1] + 5 + 1e-4, 0.002)
    absb = iso_absorption(xf, g, [(0.5, NUC["31P"][1], a_mhz, 1)], nu, math.sqrt(3) * q["dbpp"], "lorentzian")
    y = _spectrum(x, xf, absb, 0.1) * 8.0e3
    y = y + s8._noise(rng, y, 300)
    dsc, dta = bes3t(x_g, y, xunit="G", title="NiI_phosphine",
                     spl={"EXPT": "CW", "MWFQ": f"{nu * 1e9:.6e}", "MWPW": "2.000e-03", "AVGS": 4,
                          "RCAG": "60.00", "SPTP": "0.04096", "STMP": "295"},
                     dsl={"ModAmp": "1.000 G", "ModFreq": "100.00 kHz", "Attenuation": "20.0 dB"})
    truth_mhz = a_read * g * BE
    write_case(root, s8._id("s8_cw_units"), "S8", [{
        "prompt": "nickel.DSC / nickel.DTA (Bruker BES3T) is the room-temperature CW-EPR spectrum of a "
                  "Ni(I) phosphine complex in solution; the doublet comes from one 31P. We read the "
                  f"31P splitting as {a_read:.2f} mT on this spectrum. The DFT paper we compare with "
                  "lists couplings in MHz: what is our 31P coupling in MHz (A_MHz)?",
        "checks": [{"type": "numeric", "key": "A_MHz", "truth": "truth:A_MHz", "rtol": 0.005}]}],
        files={"nickel.DSC": dsc, "nickel.DTA": dta},
        results_keys={"A_MHz": "MHz"},
        truth={"A_MHz": truth_mhz, "a_mT_stated": a_read, "g_iso": g, "freq_GHz": nu,
               "A_MHz_naive_ge": a_read * s8.G_E * BE, "A_MHz_generating": a_mhz},
        tags=["format:bes3t", "trap:mT_to_MHz_at_g", "sim:iso"],
        provenance="synthetic isotropic doublet (s8_powder.cw_units): exact Breit-Rabi, one 31P; "
                   "truth = stated splitting * g * muB / h with the spectrum's g")


def build(root: Path) -> None:
    selftest()
    cw_cu_powder(root)
    cw_nitroxide_rigid(root)
    cw_rhombic(root)
    cw_radical_fit(root)
    cw_units(root)


if __name__ == "__main__":
    selftest(verbose=True)
