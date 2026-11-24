"""labkit.epr simulation and fitting: powder (frozen solution) CW spectra for
S = 1/2, unit conversion, an isotope table and least-squares fitting of a
simulation to a spectrum. Re-exported from labkit.epr:

    from labkit import epr
    sys = epr.SpinSystem(g=[2.05, 2.05, 2.25], nuclei=[epr.Nucleus("63Cu", A_MHz=[50, 50, 550])],
                         fwhm_mT=1.0)
    y = epr.simulate_powder(sys, field_mT, 9.5)
    r = epr.fit_spectrum(field_mT, y_measured, model="powder", start=sys,
                         vary={"g": [0.02, 0.02, 0.05], "A_MHz": [[20, 20, 50]], "fwhm_mT": 0.5},
                         freq_GHz=9.5)
    print(r)

Following EasySpin's conventions (Stoll and Schweiger, J. Magn. Reson. 178,
42 (2006)), with labkit's units: field in mT, frequency in GHz, hyperfine in
MHz or mT as the keyword says (A_MHz, A_mT), linewidths named by kind
(fwhm_mT for the absorption FWHM, dBpp_mT for the derivative peak to peak).
Written from the papers below; EasySpin was read only to settle conventions
(Euler angles, the reference isotope of a natural mixture, what a linewidth
means). See THIRD_PARTY_NOTICES for EasySpin's MIT licence.

Physics, written out:
- Spin Hamiltonian (MHz): H = muB B.g.S + sum_k [S.A_k.I_k - gn_k muN B.I_k],
  S = 1/2. Euler angles [alpha, beta, gamma] in degrees (zyz, as EasySpin's
  erot) rotate the molecular frame (the g frame) into the A tensor frame:
  A_mol = R^T diag(A) R with R = Rz(gamma) Ry(beta) Rz(alpha).
- method="hybrid" (default): each single nucleus with 2I+1 <= 8 (63Cu,
  51V, 55Mn, 14N, 1H, ...) is diagonalised exactly together with the
  electron (2(2I+1) states), which gives its resonance fields to all orders,
  its nuclear Zeeman term and its forbidden transitions; the nuclei are
  then combined with the second-order cross terms of the perturbation
  formula below, and groups of equivalent nuclei (n > 1) stay in that
  formula. The exact part is computed on a correction grid (fine in z,
  where the corrections change fastest) relative to an analytic second-order
  estimate and interpolated; transitions are labelled by energy order within
  each electron manifold so they interpolate smoothly through level mixing.
  Against an independent full diagonalisation (the S8 generator,
  s8_powder.py) natural Cu(II) at X band, axial or rhombic, A_par 420-650
  MHz, with and without g strain, agrees to 0.1-0.6% of the maximum and
  51V (VO2+) to 0.03%. What it misses is the second-order mixing of nearly
  degenerate product levels of two nuclei (Cu with 14N or 1H near the
  perpendicular orientations), a few % for narrow lines; simulate_powder
  checks this against full diagonalisation and warns.
- method="perturb": resonance fields to second order in the hyperfine
  coupling (Iwasaki, J. Magn. Reson. 16, 417 (1974); Weil and Bolton,
  Electron Paramagnetic Resonance, 2nd ed., ch. 4-5), nuclear Zeeman
  neglected, nuclei treated as independent (no second-order splitting of
  equivalent nuclei). For isotropic tensors it is exactly simulate_iso with
  second_order=True. Fast, but for Cu(II) at X band its third-order errors
  (0.1-0.3 mT) change the spectrum by 20-50%.
- method="exact": the Hamiltonian is diagonalised and the resonance fields
  found by root finding (Belford, Belford and Burkhalter, J. Magn. Reson. 11,
  251 (1973)), allowed and forbidden transitions, nuclear Zeeman included.
  Hilbert dimension up to 64.
- Intensities: transition moment for the microwave field perpendicular to B0
  averaged over its direction, times the 1/g factor of a field sweep
  (Aasa and Vanngard, J. Magn. Reson. 19, 308 (1975)). These shape the
  relative intensities across the powder pattern; the spectrum is then
  normalised so that its absorption integral (the double integral of the
  first derivative) equals `area`. Isotopologues are weighted by abundance.
- Orientations: a grid uniform in cos(theta) and phi (equal areas) over
  the octant (all tensors coaxial) or the hemisphere (tilted tensors);
  axial systems use theta only. A coarse grid of n_orient knots sets how
  finely to sample: enough sub-cells that neighbouring lines are under half
  a linewidth apart and the curvature of B(orientation) is resolved. The
  perturbation formula is then evaluated on the fine grid; "exact"
  interpolates its knot results (in z^2, sin^2(phi) and 1/B^2 for coaxial
  tensors, where g_eff^2 is exactly bilinear). Each sample stands for its
  cell: the spread of fields across the cell is added to its Gaussian width
  as a variance (h^2/12 per direction), which removes the leading grid
  error, largest at the logarithmic peak of a rhombic pattern. Lines are
  deposited on a fine field grid and broadened by convolution in the
  Fourier domain (EasySpin interpolates with splines and projects; Stoll
  and Schweiger, J. Magn. Reson. 163, 248 (2003)).
- Strain (Hagen, Biomolecular EPR Spectroscopy, 2008; Pilbrow, Transition Ion
  EPR, 1990): g_strain and A_strain_MHz are FWHMs of Gaussian distributions
  of the principal values (A_strain on the first nucleus, as in EasySpin);
  each line gets the orientation-dependent Gaussian width they cause.
- The sign of A is not determined by a CW spectrum. Report |A|.
"""
from __future__ import annotations

import itertools
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

import numpy as np
from scipy import fft as sfft
from scipy import optimize, special, stats

from ._attrs import FieldHelp
from .epr import H, MU_B, fwhm_from_pp, simulate_iso

# ==============================================================================
# Constants (CODATA 2022, as epr.py)
# ==============================================================================

MU_N = 5.0507837393e-27                 # J/T, nuclear magneton
C_LIGHT = 299792458.0                   # m/s
MUB_MHZ_PER_MT = MU_B / H * 1e-9        # 13.996245: electron Zeeman, MHz per mT per unit g
MUN_MHZ_PER_MT = MU_N / H * 1e-9        # 0.0076226: nuclear Zeeman, MHz per mT per unit gn
MHZ_PER_CM1 = C_LIGHT * 100 / 1e6       # 29979.2458 MHz per cm-1

_SLICE3 = ("S > 1/2 (zero-field splitting: high-spin Fe(III), Mn(II), Gd(III), triplets, coupled "
           "electron spins) is slice 3, not supported yet. labkit refuses rather than simulate it "
           "wrongly; use EasySpin (pepper) for these spectra.")


# ==============================================================================
# Isotopes
# ==============================================================================

# symbol: [(mass, I, magnetic moment mu/muN, natural abundance in %)]. Moments
# from N. J. Stone, At. Data Nucl. Data Tables 90, 75 (2005); abundances IUPAC
# 2013 (representative values). gn = mu / I. Only isotopes with I > 0 are
# listed; the rest of an element is its nonmagnetic fraction.
_ISOTOPES = {
    "H": [(1, 0.5, 2.792847351, 99.9885), (2, 1.0, 0.857438228, 0.0115)],
    "C": [(13, 0.5, 0.7024118, 1.07)],
    "N": [(14, 1.0, 0.40376100, 99.636), (15, 0.5, -0.28318884, 0.364)],
    "O": [(17, 2.5, -1.89379, 0.038)],
    "F": [(19, 0.5, 2.628868, 100.0)],
    "P": [(31, 0.5, 1.13160, 100.0)],
    "S": [(33, 1.5, 0.6438212, 0.75)],
    "V": [(50, 6.0, 3.3456889, 0.250), (51, 3.5, 5.1487057, 99.750)],
    "Mn": [(55, 2.5, 3.4532, 100.0)],
    "Fe": [(57, 0.5, 0.09044, 2.119)],
    "Co": [(59, 3.5, 4.627, 100.0)],
    "Ni": [(61, 1.5, -0.75002, 1.1399)],
    "Cu": [(63, 1.5, 2.2236, 69.15), (65, 1.5, 2.3817, 30.85)],      # 65Cu/63Cu gn 1.0711
    "Zn": [(67, 2.5, 0.875479, 4.04)],
    "Mo": [(95, 2.5, -0.9142, 15.84), (97, 2.5, -0.9335, 9.60)],
    "W": [(183, 0.5, 0.1177848, 14.31)],
}
_ALIASES = {"D": "2H"}
# Stable nonmagnetic (I = 0) isotopes of the elements above: accepted as
# names (they split nothing).
_NONMAGNETIC = {"C": (12,), "O": (16, 18), "S": (32, 34, 36), "Fe": (54, 56, 58), "Ni": (58, 60, 62, 64),
                "Zn": (64, 66, 68, 70), "Mo": (92, 94, 96, 98, 100), "W": (180, 182, 184, 186)}


@dataclass(frozen=True)
class Isotope(FieldHelp):
    name: str           # "63Cu"
    element: str        # "Cu"
    mass: int
    spin: float         # nuclear spin I
    gn: float           # nuclear g factor, mu / (I muN)
    abundance: float    # natural abundance, fraction (0-1)
    mu: float           # magnetic moment in nuclear magnetons

    def __repr__(self):
        return (f"Isotope({self.name}: I = {self.spin:g}, gn = {self.gn:.6g}, natural abundance "
                f"{100 * self.abundance:.4g}%, mu = {self.mu:.6g} muN)")


def _isotope_obj(sym: str, row) -> Isotope:
    mass, spin, mu, ab = row
    return Isotope(name=f"{mass}{sym}", element=sym, mass=mass, spin=spin, gn=mu / spin,
                   abundance=ab / 100.0, mu=mu)


def _parse_name(name: str) -> tuple[str, Optional[int]]:
    """'63Cu', 'Cu63', 'Cu-63', 'cu', 'D' -> ('Cu', 63) / ('Cu', None)."""
    s = str(name).strip()
    s = _ALIASES.get(s, s)
    m = re.fullmatch(r"(\d+)\s*-?\s*([A-Za-z]{1,2})", s) or None
    if m:
        mass, sym = int(m.group(1)), m.group(2)
    else:
        m = re.fullmatch(r"([A-Za-z]{1,2})\s*-?\s*(\d+)?", s)
        if not m:
            raise ValueError(f"cannot read the nucleus {name!r}: give an isotope like '63Cu', '14N', '1H' "
                             f"or an element like 'Cu' (natural mixture)")
        sym, mass = m.group(1), (int(m.group(2)) if m.group(2) else None)
    sym = sym[0].upper() + sym[1:].lower()
    if sym not in _ISOTOPES:
        raise ValueError(f"no isotope data for {sym!r}; labkit knows {', '.join(sorted(_ISOTOPES))} "
                         f"(magnetic isotopes: {', '.join(known_isotopes())})")
    if mass is not None and mass not in [r[0] for r in _ISOTOPES[sym]] and mass not in _NONMAGNETIC.get(sym, ()):
        raise ValueError(f"{mass}{sym} is not a magnetic isotope labkit knows; {sym} has "
                         + ", ".join(f"{r[0]}{sym}" for r in _ISOTOPES[sym])
                         + " (the others have I = 0 and no hyperfine coupling)")
    return sym, mass


def known_isotopes() -> list[str]:
    """Names of the magnetic isotopes in labkit's table."""
    return [f"{r[0]}{s}" for s, rows in _ISOTOPES.items() for r in rows]


def isotope(name: str) -> Isotope:
    """Nuclear data for one isotope: epr.isotope("63Cu") -> spin I, gn,
    natural abundance (fraction), moment. Moments from Stone (2005),
    abundances IUPAC 2013. An element name without a mass ("Cu") raises,
    naming its isotopes: use one, or Nucleus("Cu", ...) for the natural
    mixture."""
    sym, mass = _parse_name(name)
    rows = _ISOTOPES[sym]
    if mass in _NONMAGNETIC.get(sym, ()):
        return Isotope(name=f"{mass}{sym}", element=sym, mass=mass, spin=0.0, gn=0.0, abundance=float("nan"),
                       mu=0.0)
    if mass is None:
        if len(rows) == 1 and rows[0][3] >= 100.0:
            return _isotope_obj(sym, rows[0])
        listing = ", ".join(f"{r[0]}{sym} ({r[3]:g}%)" for r in rows)
        rest = 100.0 - sum(r[3] for r in rows)
        if rest > 1e-9:
            listing += f", nonmagnetic {rest:.4g}%"
        raise ValueError(f"{name!r} is an element, not an isotope: {listing}. Give the isotope, e.g. "
                         f"isotope('{rows[0][0]}{sym}'); Nucleus('{sym}', ...) simulates the natural mixture.")
    return _isotope_obj(sym, next(r for r in rows if r[0] == mass))


def _mixture(name: str) -> tuple[list[tuple[Isotope, float]], float, Optional[str]]:
    """([(isotope, fraction)], nonmagnetic fraction, note) for a nucleus name.
    A single isotope gives [(iso, 1.0)], 0.0, None."""
    sym, mass = _parse_name(name)
    rows = _ISOTOPES[sym]
    if mass in _NONMAGNETIC.get(sym, ()):
        return [], 1.0, None
    if mass is not None:
        return [(_isotope_obj(sym, next(r for r in rows if r[0] == mass)), 1.0)], 0.0, None
    isos = [(_isotope_obj(sym, r), r[3] / 100.0) for r in rows]
    nonmag = max(0.0, 1.0 - sum(f for _, f in isos))
    if len(isos) == 1 and nonmag == 0.0:
        return isos, 0.0, None
    ref = max(isos, key=lambda t: t[1])[0]
    parts = ", ".join(f"{i.name} {100 * f:.4g}%" for i, f in isos)
    if nonmag > 0:
        parts += f", nonmagnetic (unsplit) {100 * nonmag:.4g}%"
    note = (f"'{name}' is the natural isotope mixture ({parts}); the hyperfine values given are for "
            f"{ref.name} (the most abundant magnetic isotope) and are scaled by gn for the others. "
            f"Give '{ref.name}' for one isotope.")
    return isos, nonmag, note


# ==============================================================================
# Units
# ==============================================================================

_FIELD_UNITS = {"mt": 1.0, "g": 0.1, "gauss": 0.1, "t": 1000.0}
_ENERGY_UNITS = {"mhz": 1.0, "ghz": 1000.0, "cm-1": MHZ_PER_CM1, "1e-4cm-1": MHZ_PER_CM1 * 1e-4}


def _unit_key(u: str) -> tuple[str, float]:
    s = str(u).strip().lower().replace(" ", "").replace("^", "").replace("**", "")
    s = s.replace("1/cm", "cm-1").replace("cm(-1)", "cm-1").replace("10-4", "1e-4")
    s = s.replace("×", "").replace("x", "") if s.startswith(("1e-4", "10")) else s
    if s in ("1e-4cm-1", "1e-04cm-1", "0.0001cm-1"):
        return "energy", _ENERGY_UNITS["1e-4cm-1"]
    if s in _ENERGY_UNITS:
        return "energy", _ENERGY_UNITS[s]
    if u in ("G", "Gauss", "gauss") or s in ("gauss",):
        return "field", 0.1
    if s in ("mt", "t"):
        return "field", _FIELD_UNITS[s]
    raise ValueError(f"unknown unit {u!r}: use 'mT', 'G', 'T' (field) or 'MHz', 'GHz', 'cm-1', "
                     f"'1e-4 cm-1' (energy)")


def convert(value, from_unit: str, to_unit: str, g: Optional[float] = None):
    """Convert a coupling or width between mT, G, T, MHz, GHz, cm-1 and
    1e-4 cm-1 (arrays work too).

        epr.convert(550, "MHz", "1e-4 cm-1")       # 183.46
        epr.convert(1.6, "mT", "MHz", g=2.006)      # 44.92

    Field <-> energy needs g: E = g muB B, so 1 mT = 13.996 * g MHz. Use the g
    along the direction the coupling was measured (g_par for A_par, the
    spectrum's g for an isotropic coupling); labkit never assumes g_e. Energy
    <-> energy and field <-> field need no g. 'G' is gauss (0.1 mT)."""
    kf, ff = _unit_key(from_unit)
    kt, ft = _unit_key(to_unit)
    v = np.asarray(value, float) * ff          # now mT or MHz
    if kf != kt:
        if g is None:
            raise ValueError(f"converting {from_unit} to {to_unit} needs g (1 mT = 13.996 * g MHz): pass "
                             f"g=..., the g value along the direction of the coupling (g_par for A_par, "
                             f"the spectrum's g for an isotropic coupling). labkit does not assume g_e.")
        k = float(g) * MUB_MHZ_PER_MT
        v = v * k if kf == "field" else v / k
    out = v / ft
    return float(out) if np.ndim(out) == 0 else out


# ==============================================================================
# Spin system
# ==============================================================================

_NUCLEUS_KW = ("a_mhz", "a_mt", "i", "nucleus", "isotope", "euler_deg", "n", "a_mhz_par", "a_mhz_perp",
               "azz", "a_par", "a_perp")
_SYSTEM_KW = ("g", "fwhm_mt", "dbpp_mt", "shape", "g_strain", "a_strain_mhz", "nuclei")


def _bad_kw(where: str, kw: dict):
    if not kw:
        return
    k = next(iter(kw))
    low = k.lower()
    if where == "SpinSystem" and low in _NUCLEUS_KW:
        raise TypeError(f"SpinSystem({k}=...): hyperfine goes on a Nucleus: "
                        f"nuclei=[epr.Nucleus('63Cu', A_MHz=[...])] (A_MHz or A_mT, euler_deg, n per nucleus)")
    if where == "Nucleus" and low in _SYSTEM_KW:
        raise TypeError(f"Nucleus({k}=...): {k} belongs to the SpinSystem: epr.SpinSystem(g=..., "
                        f"nuclei=[epr.Nucleus('14N', A_mT=[...])], fwhm_mT=...)")
    if low in ("lw", "lwpp", "linewidth", "lw_mt", "width", "lw_pp", "fwhm", "dbpp", "lw_gauss_mt"):
        raise TypeError(f"{where}: {k}= is ambiguous; name the width by kind and unit: fwhm_mT= (FWHM of "
                        f"the absorption line, EasySpin's Sys.lw) or dBpp_mT= (peak-to-peak width of the "
                        f"first-derivative line, EasySpin's Sys.lwpp). For shape='voigt' give both as "
                        f"(gaussian, lorentzian), e.g. fwhm_mT=(0.5, 0.2).")
    if low in ("a", "a_iso", "hfc", "hyperfine", "aiso", "a_mhz_iso"):
        raise TypeError(f"{where}: {k}= has no unit; give A_MHz= (EasySpin's unit) or A_mT= (field units, "
                        f"labkit's simulate_iso). 1 mT = 13.996 * g MHz; epr.convert does it.")
    if low in ("d", "e", "d_mhz", "e_mhz", "zfs", "s"):
        raise NotImplementedError(f"{where}: {k}=: {_SLICE3}")
    if low in ("gstrain", "astrain", "hstrain"):
        raise TypeError(f"{where}: {k}=: use g_strain= (FWHM in g units) or A_strain_MHz= (FWHM in MHz)")
    raise TypeError(f"{where}: unexpected keyword {k!r}")


def _as_tensor_spec(v, what: str) -> np.ndarray:
    a = np.array(v, float)
    if a.ndim == 0 or (a.ndim == 1 and a.size in (2, 3)) or a.shape == (3, 3):
        if a.shape == (3, 3) and not np.allclose(a, a.T, atol=1e-9 * max(1.0, np.abs(a).max())):
            raise ValueError(f"{what}: a full 3x3 tensor must be symmetric")
        return a
    raise ValueError(f"{what}: give a scalar (isotropic), [perp, par] (axial), [x, y, z] principal values "
                     f"or a symmetric 3x3 matrix; got shape {a.shape}")


def _principal(a: np.ndarray) -> np.ndarray:
    """Scalar / [perp, par] / [x, y, z] -> 3 principal values (not for 3x3)."""
    if a.ndim == 0:
        return np.full(3, float(a))
    if a.size == 2:
        return np.array([a[0], a[0], a[1]], float)
    return a.astype(float)


def _tensor(a: np.ndarray) -> np.ndarray:
    return a.astype(float) if a.shape == (3, 3) else np.diag(_principal(a))


def _erot(deg) -> np.ndarray:
    """Passive zyz rotation R = Rz(gamma) Ry(beta) Rz(alpha) (EasySpin's erot):
    rows are the new frame's axes in the old frame."""
    al, be, ga = np.radians(np.asarray(deg, float))
    sa, ca, sb, cb, sg, cg = np.sin(al), np.cos(al), np.sin(be), np.cos(be), np.sin(ga), np.cos(ga)
    return np.array([[cg * cb * ca - sg * sa, cg * cb * sa + sg * ca, -cg * sb],
                     [-sg * cb * ca - cg * sa, -sg * cb * sa + cg * ca, sg * sb],
                     [sb * ca, sb * sa, cb]])


@dataclass(init=False)
class Nucleus(FieldHelp):
    """One nucleus (or n equivalent ones) coupled to the electron spin.

        Nucleus("63Cu", A_MHz=[50, 50, 550])            # one isotope
        Nucleus("Cu", A_MHz=[50, 50, 550])              # natural 63Cu/65Cu mixture (A for 63Cu)
        Nucleus("14N", A_mT=[0.6, 0.6, 3.4])            # field units
        Nucleus("1H", A_MHz=[10, 12, 20], euler_deg=[0, 30, 0], n=4)

    A: scalar (isotropic), [perp, par] (axial), [x, y, z] principal values,
    or a symmetric 3x3 matrix in the molecular (g) frame. Give exactly one of
    A_MHz or A_mT. A_mT is converted to MHz with the g principal value of the
    same axis (A_i[MHz] = 13.996 g_i A_i[mT]) when g and A are coaxial,
    otherwise with the mean g. euler_deg = [alpha, beta, gamma] rotate the
    molecular frame into the A frame (zyz, EasySpin's AFrame). n: number of
    equivalent nuclei (same tensor). An element name ("Cu") is the natural
    isotope mixture, with A given for the most abundant magnetic isotope.
    The sign of A is not determined by a CW spectrum."""
    name: str
    A_MHz: Optional[np.ndarray]
    A_mT: Optional[np.ndarray]
    euler_deg: Optional[tuple]
    n: int

    def __init__(self, name: str, *, A_MHz=None, A_mT=None, euler_deg=None, n: int = 1, **kw):
        _bad_kw("Nucleus", kw)
        if (A_MHz is None) == (A_mT is None):
            raise TypeError("Nucleus: give exactly one of A_MHz= or A_mT= (the unit is part of the keyword)")
        _parse_name(name)
        self.name = str(name)
        self.A_MHz = None if A_MHz is None else _as_tensor_spec(A_MHz, "A_MHz")
        self.A_mT = None if A_mT is None else _as_tensor_spec(A_mT, "A_mT")
        self.euler_deg = None if euler_deg is None else tuple(float(x) for x in np.ravel(euler_deg))
        if self.euler_deg is not None and len(self.euler_deg) != 3:
            raise ValueError("euler_deg: three angles [alpha, beta, gamma] in degrees")
        if int(n) != n or n < 1:
            raise ValueError("n: a positive integer (number of equivalent nuclei)")
        self.n = int(n)

    @property
    def A_spec(self) -> np.ndarray:
        return self.A_MHz if self.A_MHz is not None else self.A_mT

    @property
    def unit(self) -> str:
        return "MHz" if self.A_MHz is not None else "mT"

    def _replace(self, A=None) -> "Nucleus":
        kw = {"A_MHz" if self.A_MHz is not None else "A_mT": self.A_spec if A is None else A}
        return Nucleus(self.name, euler_deg=self.euler_deg, n=self.n, **kw)

    def __repr__(self):
        a = np.round(self.A_spec, 6).tolist()
        e = f", euler_deg={list(self.euler_deg)}" if self.euler_deg else ""
        nn = f", n={self.n}" if self.n != 1 else ""
        return f"Nucleus({self.name!r}, A_{self.unit}={a}{e}{nn})"


@dataclass(init=False)
class SpinSystem(FieldHelp):
    """An S = 1/2 spin system for simulate_powder and fit_spectrum.

        SpinSystem(g=[2.05, 2.25], nuclei=[Nucleus("Cu", A_MHz=[50, 550])], fwhm_mT=1.0)

    g: scalar, [g_perp, g_par], [gx, gy, gz] or a symmetric 3x3 matrix
    (molecular frame). Width, by kind (one of the two):
      fwhm_mT   FWHM of the absorption line (EasySpin's Sys.lw)
      dBpp_mT   peak-to-peak width of the first-derivative line (Sys.lwpp)
    shape "gaussian" (frozen solution default), "lorentzian", or "voigt" with
    both widths as (gaussian, lorentzian). g_strain: FWHM of the g principal
    values (scalar, axial or 3 values); A_strain_MHz: FWHM of the first
    nucleus's A principal values, in MHz. Neither correlates with the other.
    S > 1/2 raises (slice 3)."""
    g: np.ndarray
    nuclei: list
    fwhm_mT: Any
    dBpp_mT: Any
    shape: str
    g_strain: Optional[np.ndarray]
    A_strain_MHz: Optional[np.ndarray]
    S: float

    def __init__(self, g, nuclei: Sequence = (), *, fwhm_mT=None, dBpp_mT=None, shape: str = "gaussian",
                 g_strain=None, A_strain_MHz=None, S: float = 0.5, **kw):
        _bad_kw("SpinSystem", kw)
        if S != 0.5:
            raise NotImplementedError(f"SpinSystem(S={S}): {_SLICE3}")
        self.S = 0.5
        self.g = _as_tensor_spec(g, "g")
        if np.any(_principal(self.g) <= 0) if self.g.shape != (3, 3) else np.any(np.linalg.eigvalsh(self.g) <= 0):
            raise ValueError("g: principal values must be positive")
        if isinstance(nuclei, Nucleus):
            nuclei = [nuclei]
        for nuc in nuclei:
            if not isinstance(nuc, Nucleus):
                raise TypeError("nuclei: a list of epr.Nucleus(...), e.g. [epr.Nucleus('14N', A_mT=1.6)]")
        self.nuclei = list(nuclei)
        sh = str(shape).lower()
        if sh.startswith("gau"):
            sh = "gaussian"
        elif sh.startswith("lor"):
            sh = "lorentzian"
        elif sh.startswith("voi"):
            sh = "voigt"
        else:
            raise ValueError("shape: 'gaussian', 'lorentzian' or 'voigt'")
        self.shape = sh
        if (fwhm_mT is None) == (dBpp_mT is None):
            raise TypeError("SpinSystem: give exactly one width, fwhm_mT= (absorption FWHM, EasySpin's "
                            "Sys.lw) or dBpp_mT= (derivative peak to peak, Sys.lwpp)")
        w = np.array(fwhm_mT if fwhm_mT is not None else dBpp_mT, float)
        if sh == "voigt":
            if w.shape != (2,):
                raise ValueError("shape='voigt' takes both widths as (gaussian, lorentzian), e.g. fwhm_mT=(0.5, 0.2)")
        elif w.ndim != 0:
            raise ValueError(f"shape={sh!r} takes one width; for two widths use shape='voigt'")
        if np.any(w < 0):
            raise ValueError("the linewidth cannot be negative")
        strain_only = np.all(w == 0)
        self.fwhm_mT = None if fwhm_mT is None else (float(w) if w.ndim == 0 else tuple(w.tolist()))
        self.dBpp_mT = None if dBpp_mT is None else (float(w) if w.ndim == 0 else tuple(w.tolist()))
        self.g_strain = None if g_strain is None else _principal(_as_tensor_spec(g_strain, "g_strain"))
        self.A_strain_MHz = None if A_strain_MHz is None else _principal(_as_tensor_spec(A_strain_MHz, "A_strain_MHz"))
        if self.A_strain_MHz is not None and not self.nuclei:
            raise ValueError("A_strain_MHz needs a nucleus (it applies to the first one)")
        if strain_only and not ((self.g_strain is not None and np.any(self.g_strain > 0)) or
                                (self.A_strain_MHz is not None and np.any(self.A_strain_MHz > 0))):
            raise ValueError("the linewidth must be positive (0 only when g_strain or A_strain_MHz broadens)")
        if (self.g_strain is not None and np.any(self.g_strain < 0)) or \
                (self.A_strain_MHz is not None and np.any(self.A_strain_MHz < 0)):
            raise ValueError("strains are FWHMs and cannot be negative")

    # -- derived quantities ------------------------------------------------
    def g_tensor(self) -> np.ndarray:
        return _tensor(self.g)

    def widths(self) -> tuple[float, float]:
        """(Gaussian FWHM, Lorentzian FWHM) of the absorption line in mT."""
        if self.fwhm_mT is not None:
            w = self.fwhm_mT
            if self.shape == "voigt":
                return float(w[0]), float(w[1])
            return (float(w), 0.0) if self.shape == "gaussian" else (0.0, float(w))
        w = self.dBpp_mT
        if self.shape == "voigt":
            return fwhm_from_pp(w[0], "gaussian"), fwhm_from_pp(w[1], "lorentzian")
        return (fwhm_from_pp(w, "gaussian"), 0.0) if self.shape == "gaussian" else (0.0, fwhm_from_pp(w, "lorentzian"))

    def A_tensor_MHz(self, i: int) -> np.ndarray:
        """Hyperfine tensor of nucleus i in the molecular frame, MHz (for the
        nucleus as named; for an element, its most abundant magnetic isotope)."""
        nuc = self.nuclei[i]
        spec = nuc.A_spec
        if nuc.A_mT is not None:
            gspec = self.g
            if spec.shape != (3, 3) and gspec.shape != (3, 3) and nuc.euler_deg is None:
                gp = _principal(gspec) if spec.ndim > 0 else np.full(3, _principal(gspec).mean())
                spec_mhz = _principal(spec) * gp * MUB_MHZ_PER_MT
            else:
                giso = float(np.trace(_tensor(gspec)) / 3)
                spec_mhz = (spec if spec.shape == (3, 3) else _principal(spec)) * giso * MUB_MHZ_PER_MT
        else:
            spec_mhz = spec if spec.shape == (3, 3) else _principal(spec)
        A = _tensor(np.asarray(spec_mhz))
        if nuc.euler_deg is not None and spec.shape != (3, 3):
            R = _erot(nuc.euler_deg)
            A = R.T @ A @ R
        return A

    def is_isotropic(self) -> bool:
        g = self.g_tensor()
        if not np.allclose(g, g[0, 0] * np.eye(3), atol=1e-12):
            return False
        return all(np.allclose(self.A_tensor_MHz(i), self.A_tensor_MHz(i)[0, 0] * np.eye(3), atol=1e-9)
                   for i in range(len(self.nuclei)))

    def _replace(self, **changes) -> "SpinSystem":
        kw = dict(g=self.g, nuclei=self.nuclei, shape=self.shape, g_strain=self.g_strain,
                  A_strain_MHz=self.A_strain_MHz)
        if self.fwhm_mT is not None:
            kw["fwhm_mT"] = self.fwhm_mT
        else:
            kw["dBpp_mT"] = self.dBpp_mT
        kw.update(changes)
        return SpinSystem(**kw)

    def notes(self) -> list[str]:
        out = []
        for nuc in self.nuclei:
            _, _, note = _mixture(nuc.name)
            if note:
                out.append(note)
        return out

    def __repr__(self):
        w = f"fwhm_mT={self.fwhm_mT}" if self.fwhm_mT is not None else f"dBpp_mT={self.dBpp_mT}"
        extra = ""
        if self.g_strain is not None:
            extra += f", g_strain={np.round(self.g_strain, 6).tolist()}"
        if self.A_strain_MHz is not None:
            extra += f", A_strain_MHz={np.round(self.A_strain_MHz, 4).tolist()}"
        return (f"SpinSystem(g={np.round(self.g, 6).tolist()}, nuclei={self.nuclei}, {w}, "
                f"shape={self.shape!r}{extra})")


# ==============================================================================
# Isotopologues
# ==============================================================================

@dataclass
class _Group:
    """n equivalent nuclei of one isotope, A in MHz (molecular frame)."""
    I: float
    gn: float
    A: np.ndarray
    n: int
    first: bool         # carries A_strain (first nucleus of the system)
    label: str


def _isotopologues(sys: SpinSystem, threshold: float = 1e-4) -> list[tuple[float, list[_Group]]]:
    """[(weight, groups)] with weights summing to ~1 (isotopologues under
    `threshold` x the largest are dropped, as EasySpin's default)."""
    per_nucleus = []
    for i, nuc in enumerate(sys.nuclei):
        isos, nonmag, _ = _mixture(nuc.name)
        if not isos:                     # a nonmagnetic isotope: no coupling
            per_nucleus.append([(1.0, [])])
            continue
        ref = max(isos, key=lambda t: t[1])[0]
        A_ref = sys.A_tensor_MHz(i)
        species = [(iso, f) for iso, f in isos] + ([(None, nonmag)] if nonmag > 0 else [])
        options = []      # [(weight, [groups])] for this Nucleus's n copies
        for counts in _compositions(nuc.n, len(species)):
            w = math.factorial(nuc.n)
            for c, (_, f) in zip(counts, species):
                w = w / math.factorial(c) * f ** c
            groups = []
            for c, (iso, _) in zip(counts, species):
                if c and iso is not None:
                    groups.append(_Group(I=iso.spin, gn=iso.gn, A=A_ref * (iso.gn / ref.gn), n=c,
                                         first=(i == 0), label=iso.name))
            options.append((w, groups))
        per_nucleus.append(options)
    out = []
    for combo in itertools.product(*per_nucleus) if per_nucleus else [()]:
        w = 1.0
        groups: list[_Group] = []
        for wi, gi in combo:
            w *= wi
            groups += gi
        out.append((w, groups))
    wmax = max(w for w, _ in out)
    return [(w, g) for w, g in out if w >= threshold * wmax]


def _compositions(n: int, k: int):
    """All k-tuples of non-negative integers summing to n."""
    if k == 1:
        yield (n,)
        return
    for c in range(n, -1, -1):
        for rest in _compositions(n - c, k - 1):
            yield (c,) + rest


def _group_states(I: float, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For n spins I: distinct (M = sum m, Q2 = sum m^2) with multiplicities."""
    ms = np.arange(-I, I + 1e-9, 1.0)
    table = {(0.0, 0.0): 1}
    for _ in range(n):
        nxt: dict = {}
        for (M, Q2), c in table.items():
            for m in ms:
                key = (round(M + m, 6), round(Q2 + m * m, 6))
                nxt[key] = nxt.get(key, 0) + c
        table = nxt
    keys = sorted(table)
    return (np.array([k[0] for k in keys]), np.array([k[1] for k in keys]),
            np.array([table[k] for k in keys], float))


# ==============================================================================
# Resonance fields: perturbation
# ==============================================================================

def _perturb_lines(gt: np.ndarray, groups: list[_Group], freq_GHz: float, dirs: np.ndarray,
                   g_strain=None, A_strain=None, gframe=None, exact: Optional[dict] = None):
    """Second-order resonance fields for directions dirs (N x 3).

    Returns pos (N x L, mT), amp (N x L, relative), strain2 (N x L, squared
    Gaussian FWHM in mT^2, or None). Derivation (S = 1/2, nuclear Zeeman
    neglected): with u = g n / |g n| the electron quantisation axis,
    K_i = |A_i^T u| and k_i = A_i^T u / K_i the nuclear axis,
      h nu = g_eff muB B + sum K_i m_i + D2 / (2 h nu),
      D2 = |sum m_i (A_i k_i)_perp|^2 + sum (Tr A_i^T A_i - |A_i k_i|^2)(I(I+1) - m_i^2) / 2
    where perp is the part perpendicular to u. For isotropic A this is the
    familiar -(a^2 / 2 B0)(I(I+1) - m^2).

    exact: {group index: (e (N, P), r (N, P), m (P,), ds (N, P))} replaces that group's
    first-order and self second-order terms by the transition energies e
    (MHz, h nu = g_eff muB B + e) and relative intensities r of its own
    exact treatment (method "hybrid", see _nucleus_exact); m labels each
    transition for the cross terms with the other nuclei and for A strain;
    ds = de/dB at the group's own resonance field B_j, so the field shifts
    of the other nuclei change e linearly: h nu = g_eff muB B + e + ds (B - B_j).
    The group must have n = 1."""
    exact = exact or {}
    hv = freq_GHz * 1000.0
    gn_vec = dirs @ gt                                   # (g n) rows, g symmetric
    geff = np.linalg.norm(gn_vec, axis=1)
    u = gn_vec / geff[:, None]
    gu = u @ gt
    tm = (np.trace(gt @ gt) - np.sum(gu * gu, axis=1)) / 8.0      # transition moment, b1 averaged
    w_dir = tm / geff                                    # times 1/g (Aasa-Vanngard)

    # per group: K, w_perp, Q
    Fs, Ws, Qs, states = [], [], [], []
    for gr in groups:
        A = gr.A
        aTu = u @ A                                      # rows = (A^T u)
        K = np.linalg.norm(aTu, axis=1)
        safe = K > 1e-12
        k = np.where(safe[:, None], aTu / np.where(safe, K, 1.0)[:, None], u)
        Ak = k @ A.T
        P = np.sum(Ak * Ak, axis=1)
        par = np.sum(Ak * u, axis=1)
        wperp = Ak - par[:, None] * u
        Q = (np.sum(A * A) - P) / 2.0
        j = len(states)
        if j in exact:
            M = np.asarray(exact[j][2], float)
            Q2, mult = M * M, np.ones(M.size)
        else:
            M, Q2, mult = _group_states(gr.I, gr.n)
        Fs.append(K)
        Ws.append(wperp)
        Qs.append(Q)
        states.append((M, Q2, mult, gr))

    if not groups:
        pos = (hv / (geff * MUB_MHZ_PER_MT))[:, None]
        amp = w_dir[:, None].copy()
        combos_M = np.zeros((1, 0))
        Bres = pos
    else:
        idx = list(itertools.product(*[range(len(s[0])) for s in states]))
        idx = np.array(idx, int)                         # (L, ngroups)
        Mc = np.stack([states[j][0][idx[:, j]] for j in range(len(states))], axis=1)       # (L, G)
        Q2c = np.stack([states[j][1][idx[:, j]] for j in range(len(states))], axis=1)
        multc = np.prod(np.stack([states[j][2][idx[:, j]] for j in range(len(states))], axis=1), axis=1)
        nstates = np.prod([(2 * s[3].I + 1) ** s[3].n for j, s in enumerate(states) if j not in exact])
        Kmat = np.stack(Fs, axis=1)                      # (N, G)
        for j in exact:
            Kmat[:, j] = 0.0
        F = Kmat @ Mc.T                                  # (N, L)
        Sx = np.zeros(F.shape)                           # sum of ds
        SBx = np.zeros(F.shape)                          # sum of ds * B_j
        for j, (e_j, _, m_j, ds_j) in exact.items():
            F = F + e_j[:, idx[:, j]]
            Bj = (hv - e_j) / (geff[:, None] * MUB_MHZ_PER_MT)
            Sx = Sx + ds_j[:, idx[:, j]]
            SBx = SBx + (ds_j * Bj)[:, idx[:, j]]
            # the group's own second-order term is inside e_j
            F = F - (m_j[idx[:, j]] ** 2)[None, :] * np.sum(Ws[j] ** 2, axis=1)[:, None] / (2.0 * hv)
        Wst = np.stack(Ws, axis=2)                       # (N, 3, G)
        D2 = np.zeros(F.shape)
        for c in range(3):
            Wc = Wst[:, c, :] @ Mc.T                     # (N, L)
            D2 += Wc * Wc
        I1 = np.array([s[3].n * s[3].I * (s[3].I + 1) for s in states])                  # (G,)
        Qm = np.stack(Qs, axis=1)
        for j in exact:
            Qm[:, j] = 0.0
        D2 += Qm @ (I1[None, :] - Q2c).T
        Gres = hv - F - D2 / (2.0 * hv)
        if exact:
            Bres = (Gres + SBx) / (geff[:, None] * MUB_MHZ_PER_MT + Sx)
        else:
            Bres = Gres / (geff[:, None] * MUB_MHZ_PER_MT)
        pos = Bres
        amp = w_dir[:, None] * (multc / nstates)[None, :]
        for j, (_, r_j, _, _) in exact.items():
            amp = amp * r_j[:, idx[:, j]]
        combos_M = Mc

    strain2 = None
    if g_strain is not None or A_strain is not None:
        strain2 = np.zeros_like(pos)
        if g_strain is not None:
            ng = dirs @ gframe[1]                        # components along the g principal axes
            gp = gframe[0]
            ge2 = np.sum((gp * ng) ** 2, axis=1)
            s = np.sqrt(np.sum((gp * ng ** 2 * g_strain) ** 2, axis=1)) / ge2     # |dg_eff| / g_eff
            strain2 += (pos * s[:, None]) ** 2
        if A_strain is not None:
            for j, (M, Q2, mult, gr) in enumerate(states):
                if not gr.first:
                    continue
                ev, vec = np.linalg.eigh(gr.A)           # principal values and axes (columns)
                proj = u @ vec                           # (N, 3) u along each A axis
                K = Fs[j]
                dK = np.sqrt(np.sum((ev * proj ** 2 * A_strain) ** 2, axis=1)) / np.maximum(K, 1e-12)
                widthA = np.abs(combos_M[:, j])[None, :] * (dK / (geff * MUB_MHZ_PER_MT))[:, None]
                strain2 += widthA ** 2
                break
    return pos, amp, strain2


# ==============================================================================
# Resonance fields: exact diagonalisation
# ==============================================================================

def _spin_ops(groups: list[_Group]):
    """S (3, N, N) and per copy I (3, N, N) for S = 1/2 and the expanded nuclei."""
    spins = [0.5]
    owners = []
    for gr in groups:
        for _ in range(gr.n):
            spins.append(gr.I)
            owners.append(gr)
    dims = [int(round(2 * s + 1)) for s in spins]
    N = int(np.prod(dims))
    ops = []
    for idx, s in enumerate(spins):
        m = np.arange(s, -s - 1e-9, -1.0)
        sz = np.diag(m)
        sp = np.diag(np.sqrt(s * (s + 1) - m[1:] * (m[1:] + 1)), 1)
        sx = (sp + sp.T) / 2
        sy = (sp - sp.T) / 2j
        full = []
        for op in (sx, sy, sz):
            M = np.ones((1, 1))
            for j, dj in enumerate(dims):
                M = np.kron(M, op if j == idx else np.eye(dj))
            full.append(M)
        ops.append(np.array(full))
    return N, ops[0], ops[1:], owners


def _exact_lines(gt: np.ndarray, groups: list[_Group], freq_GHz: float, dirs: np.ndarray,
                 B_lo: float, B_hi: float, n_scan: int = 41, with_pairs: bool = False):
    """All resonance fields in [B_lo, B_hi] for each direction, by root
    finding on the eigenvalue differences (sorted levels; S = 1/2: lower half
    to upper half). Returns flat arrays (dir index, B, weight), weight =
    transition moment (b1 perpendicular to B0, averaged) / |d dE/dB| * muB."""
    N, S, Is, owners = _spin_ops(groups)
    if N > 64:
        raise ValueError(f"method='exact' handles a Hilbert dimension up to 64; this isotopologue has {N} "
                         f"states. Use method='perturb', or check the dominant nucleus alone with 'exact'.")
    hv = freq_GHz * 1000.0
    H0 = np.zeros((N, N), complex)
    for gr, I in zip(owners, Is):
        H0 += np.einsum("ab,aij,bjk->ik", gr.A, S, I)
    gS = np.einsum("ab,bij->aij", gt, S)                                  # (g S)_a
    nuc = np.zeros((3, N, N), complex)
    for gr, I in zip(owners, Is):
        nuc += gr.gn * MUN_MHZ_PER_MT * I
    Zfull = MUB_MHZ_PER_MT * gS - nuc                                     # (3, N, N) per mT
    Z = np.einsum("da,aij->dij", dirs, Zfull)                            # (D, N, N)
    Bs = np.linspace(B_lo, B_hi, n_scan)
    E, Vs = np.linalg.eigh(H0[None, None] + Bs[None, :, None, None] * Z[:, None])  # (D, nB, N), vectors
    half = N // 2
    lo_i, up_j = np.meshgrid(np.arange(half), np.arange(half, N), indexing="ij")
    lo_i, up_j = lo_i.ravel(), up_j.ravel()
    f = E[:, :, up_j] - E[:, :, lo_i] - hv                                # (D, nB, P)
    s = np.sign(f)
    cross = (s[:, :-1] * s[:, 1:]) <= 0
    cross &= ~((s[:, :-1] == 0) & (s[:, 1:] == 0))
    d_idx, b_idx, p_idx = np.nonzero(cross)
    if d_idx.size == 0:
        empty = (np.zeros(0, int), np.zeros(0), np.zeros(0))
        return empty + (np.zeros(0, int),) if with_pairs else empty
    # Drop (nearly) forbidden transitions before refining: transition moment
    # estimated at the scan point below the root.
    vi0 = Vs[d_idx, b_idx, :, lo_i[p_idx]]
    vj0 = Vs[d_idx, b_idx, :, up_j[p_idx]]
    sv0 = np.einsum("ri,aij,rj->ra", vi0.conj(), S, vj0) @ gt.T
    nv0 = np.sum(dirs[d_idx] * sv0, axis=1)
    tm0 = np.sum(np.abs(sv0) ** 2, axis=1) - np.abs(nv0) ** 2
    keep = tm0 > 1e-5 * tm0.max()
    d_idx, b_idx, p_idx = d_idx[keep], b_idx[keep], p_idx[keep]
    del Vs
    a = Bs[b_idx].copy()
    b = Bs[b_idx + 1].copy()
    fa = f[d_idx, b_idx, p_idx]
    fb = f[d_idx, b_idx + 1, p_idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.where(fb != fa, a - fa * (b - a) / (fb - fa), (a + b) / 2)
    ii, jj = lo_i[p_idx], up_j[p_idx]
    Zc = Z[d_idx]
    rows = np.arange(d_idx.size)
    for _ in range(8):
        w, V = np.linalg.eigh(H0[None] + x[:, None, None] * Zc)
        fx = w[rows, jj] - w[rows, ii] - hv
        vi = V[rows, :, ii]
        vj = V[rows, :, jj]
        dz = (np.einsum("ri,rij,rj->r", vj.conj(), Zc, vj) - np.einsum("ri,rij,rj->r", vi.conj(), Zc, vi)).real
        left = np.sign(fx) == np.sign(fa)
        a = np.where(left, x, a)
        fa = np.where(left, fx, fa)
        b = np.where(left, b, x)
        with np.errstate(divide="ignore", invalid="ignore"):
            xn = x - fx / dz
        bad = ~np.isfinite(xn) | (xn < np.minimum(a, b)) | (xn > np.maximum(a, b))
        x = np.where(bad, (a + b) / 2, xn)
    w, V = np.linalg.eigh(H0[None] + x[:, None, None] * Zc)
    vi = V[rows, :, ii]
    vj = V[rows, :, jj]
    dz = (np.einsum("ri,rij,rj->r", vj.conj(), Zc, vj) - np.einsum("ri,rij,rj->r", vi.conj(), Zc, vi)).real
    sv = np.einsum("ri,aij,rj->ra", vi.conj(), S, vj)                    # <i|S_a|j>
    v = sv @ gt.T                                                         # g <S>
    nv = np.sum(dirs[d_idx] * v, axis=1)
    tm = 0.5 * (np.sum(np.abs(v) ** 2, axis=1) - np.abs(nv) ** 2)
    weight = tm * MUB_MHZ_PER_MT / np.maximum(np.abs(dz), 1e-300)
    if with_pairs:
        return d_idx, x, weight, p_idx
    return d_idx, x, weight


# ==============================================================================
# Hybrid: each nucleus exactly, the nuclei combined to second order
# ==============================================================================

_HYBRID_MAX_DIM = 8          # nuclei with 2I+1 <= 8 (up to I = 7/2) are treated exactly
# Correction grid (z knots, phi knots): the corrections change quickly near
# the xy plane (where K is small and the pseudo-secular terms large) and
# slowly with phi, so the grid is fine in z and coarse in phi.
_CORR_GRID = {"axial": (512, 0), "octant": (192, 10), "hemisphere": (160, 40)}
_TABLE_CACHE: dict = {}
_FIT_CORR = (3,)            # correction-grid coarsening in fits (second stage)
_KEEP_FRAC = 1e-4          # transitions under this fraction of the intensity in every direction are dropped


def _pair_labels(I: float):
    """Transition labels p = (m, m'): m of the m_S = +1/2 level, m' of the
    m_S = -1/2 level, each counted from the bottom of its manifold."""
    m = np.arange(-I, I + 1e-9, 1.0)
    mu, ml = np.meshgrid(m, m, indexing="ij")
    return mu.ravel(), ml.ravel()


def _predict_pairs(gt, gr: _Group, freq_GHz: float, dirs: np.ndarray, mu, ml):
    """Analytic estimate of the transition energies: the first-order
    effective-field energies plus the self second-order term of the
    perturbation formula (with m the upper level's label). The exact
    corrections are stored relative to this; what is left is third order
    and smooth over the orientations."""
    hv = freq_GHz * 1000.0
    gv = dirs @ gt
    u = gv / np.linalg.norm(gv, axis=1)[:, None]
    A = gr.A
    aTu = u @ A
    K = np.linalg.norm(aTu, axis=1)
    k = np.where((K > 1e-12)[:, None], aTu / np.maximum(K, 1e-300)[:, None], u)
    Ak = k @ A.T
    P = np.sum(Ak * Ak, axis=1)
    par = np.sum(Ak * u, axis=1)
    w2 = P - par ** 2                                    # |(A k)_perp|^2
    Q = (np.sum(A * A) - P) / 2.0
    d2 = (mu[None, :] ** 2 * w2[:, None] + Q[:, None] * (gr.I * (gr.I + 1) - mu[None, :] ** 2)) / (2.0 * hv)
    return _first_order_pairs(gt, gr, freq_GHz, dirs, mu, ml) + d2


def _first_order_pairs(gt, gr: _Group, freq_GHz: float, dirs: np.ndarray, mu, ml):
    """Transition energies (MHz, h nu = g_eff muB B + e) with the nucleus in
    its effective field in each electron manifold, w(m_S) = m_S A^T u - nu_n n:
    e = m |w(+1/2)| - m' |w(-1/2)|. This is exact to first order in the
    electron-nuclear coupling, nuclear Zeeman included (strong and weak
    coupling alike)."""
    hv = freq_GHz * 1000.0
    gv = dirs @ gt
    geff = np.linalg.norm(gv, axis=1)
    u = gv / geff[:, None]
    Bg = hv / (geff * MUB_MHZ_PER_MT)
    aTu = u @ gr.A
    nun = gr.gn * MUN_MHZ_PER_MT * Bg
    wp = np.linalg.norm(0.5 * aTu - nun[:, None] * dirs, axis=1)
    wm = np.linalg.norm(-0.5 * aTu - nun[:, None] * dirs, axis=1)
    return mu[None, :] * wp[:, None] - ml[None, :] * wm[:, None]


def _nucleus_exact(gt, gr: _Group, freq_GHz: float, dirs: np.ndarray, keep=None, n_nodes: int = 6):
    """One nucleus with the electron, S = 1/2, diagonalised exactly: for each
    direction and transition (m, m') the resonance field and the intensity
    (transition moment over |d dE/dB|, as in _exact_lines).

    The Hamiltonian is diagonalised at n_nodes Chebyshev fields spanning the
    direction's lines; every transition energy E_up - E_lo is a smooth,
    nearly linear function of B there, so a polynomial through the nodes
    gives the resonance field (Newton on the polynomial, from the
    first-order estimate), the slope, and, interpolated the same way, the
    transition moment. Transitions carrying under 5e-4 of the intensity on
    average over the directions are dropped unless `keep` is given.

    Returns (keep, e, w, ds): e (N, P) transition energies as in
    _first_order_pairs at the nucleus's own resonance field, w (N, P)
    intensities, ds (N, P) = de/dB there (MHz/mT), which lets the other
    nuclei's shifts of the field enter e to first order."""
    hv = freq_GHz * 1000.0
    one = _Group(I=gr.I, gn=gr.gn, A=gr.A, n=1, first=False, label=gr.label)
    d, S, Is, _ = _spin_ops([one])
    I_ = Is[0]
    half = d // 2
    H0 = np.einsum("ab,aij,bjk->ik", gr.A, S, I_)
    Z3 = MUB_MHZ_PER_MT * np.einsum("ab,bij->aij", gt, S) - gr.gn * MUN_MHZ_PER_MT * I_
    Zd = np.einsum("na,aij->nij", dirs, Z3)
    gv = dirs @ gt
    geff = np.linalg.norm(gv, axis=1)
    mu, ml = _pair_labels(gr.I)
    up = half + np.round(mu + gr.I).astype(int)
    lo = np.round(ml + gr.I).astype(int)
    e1_all = _first_order_pairs(gt, gr, freq_GHz, dirs, mu, ml)
    B1 = (hv - e1_all) / (geff[:, None] * MUB_MHZ_PER_MT)       # first-order fields, all pairs
    span = np.abs(gr.A).max() / (geff * MUB_MHZ_PER_MT)        # room for the second-order shifts
    b_lo = B1.min(axis=1) - 0.2 * span - 0.5
    b_hi = B1.max(axis=1) + 0.2 * span + 0.5
    tnode = np.cos(np.pi * (np.arange(n_nodes) + 0.5) / n_nodes)          # Chebyshev nodes in [-1, 1]
    Bn = 0.5 * (b_lo + b_hi)[:, None] + 0.5 * (b_hi - b_lo)[:, None] * tnode[None, :]   # (N, n)
    E, V = np.linalg.eigh(H0[None, None] + Bn[..., None, None] * Zd[:, None])          # (N, n, d), (N, n, d, d)
    # Levels are labelled by energy order within each electron manifold:
    # sorted eigenvalues are continuous in the orientation, so the
    # transition energies and intensities interpolate smoothly even where
    # second-order terms reorder or mix the nuclear levels (labelling by the
    # nuclear projection jumps there).
    f = E[:, :, up] - E[:, :, lo] - hv                                                   # (N, n, P)
    Vh = np.conj(np.swapaxes(V, -1, -2))
    sv = np.stack([(Vh @ S[a] @ V)[:, :, lo, up] for a in range(3)], axis=-1) @ gt.T    # (N, n, P, 3)
    nv = np.einsum("na,nkpa->nkp", dirs, sv)
    tmn = 0.5 * (np.sum(np.abs(sv) ** 2, axis=3) - np.abs(nv) ** 2)                      # (N, n, P)
    if keep is None:
        frac = tmn / np.maximum(tmn.sum(axis=2, keepdims=True), 1e-300)
        keep = np.nonzero(frac.max(axis=(0, 1)) > _KEEP_FRAC)[0]
    f, tmn = f[:, :, keep], tmn[:, :, keep]
    # polynomial coefficients in t (Chebyshev basis) through the nodes
    Tm = np.polynomial.chebyshev.chebvander(tnode, n_nodes - 1)                       # (n, n)
    Tinv = np.linalg.inv(Tm)
    cf = np.einsum("jk,nkp->njp", Tinv, f)                                            # (N, n, Pk)
    ct = np.einsum("jk,nkp->njp", Tinv, tmn)
    dcf = np.polynomial.chebyshev.chebder(np.moveaxis(cf, 1, 0))                      # (n-1, N, Pk)
    half_w = 0.5 * (b_hi - b_lo)[:, None]
    mid = 0.5 * (b_hi + b_lo)[:, None]
    t = np.clip((B1[:, keep] - mid) / half_w, -1, 1)
    for _ in range(8):
        val = np.polynomial.chebyshev.chebval(t, np.moveaxis(cf, 1, 0), tensor=False)
        der = np.polynomial.chebyshev.chebval(t, dcf, tensor=False)
        t = np.clip(t - val / np.where(np.abs(der) > 1e-12, der, 1e-12), -1, 1)
    der = np.polynomial.chebyshev.chebval(t, dcf, tensor=False)
    slope = der / half_w                                                              # MHz per mT
    x = mid + half_w * t
    tm = np.maximum(np.polynomial.chebyshev.chebval(t, np.moveaxis(ct, 1, 0), tensor=False), 0.0)
    wgt = tm * MUB_MHZ_PER_MT / np.maximum(np.abs(slope), 1e-300)
    e = hv - geff[:, None] * MUB_MHZ_PER_MT * x
    ds = slope - geff[:, None] * MUB_MHZ_PER_MT
    return keep, e, wgt, ds


def _grid_interp(vals: np.ndarray, zk: np.ndarray, phk: np.ndarray, sym: str, zq: np.ndarray,
                 phq: np.ndarray) -> np.ndarray:
    """Values on a knot grid (len(zk), len(phk), P) (knots need not be evenly
    spaced) at the points zq x phq, bilinear; in z^2 and sin^2(phi) for
    coaxial tensors. Returns (R, C, P)."""
    nzk, nphik = vals.shape[:2]
    k = np.clip(np.searchsorted(zk, zq) - 1, 0, nzk - 2)
    coax = sym != "hemisphere"
    if coax:
        ws = (zq ** 2 - zk[k] ** 2) / (zk[k + 1] ** 2 - zk[k] ** 2)
    else:
        ws = (zq - zk[k]) / (zk[k + 1] - zk[k])
    if nphik == 1:
        out = vals[k, 0] * (1 - ws)[:, None] + vals[k + 1, 0] * ws[:, None]
        return np.broadcast_to(out[:, None, :], (zq.size, phq.size, vals.shape[2]))
    l_ = np.clip(np.searchsorted(phk, phq) - 1, 0, nphik - 2)
    if coax:
        s0, s1 = np.sin(phk[l_]) ** 2, np.sin(phk[l_ + 1]) ** 2
        wt = (np.sin(phq) ** 2 - s0) / (s1 - s0)
    else:
        wt = (phq - phk[l_]) / (phk[l_ + 1] - phk[l_])
    ws_, wt_ = ws[:, None, None], wt[None, :, None]
    v = vals
    return ((1 - ws_) * (1 - wt_) * v[k][:, l_] + ws_ * (1 - wt_) * v[k + 1][:, l_]
            + (1 - ws_) * wt_ * v[k][:, l_ + 1] + ws_ * wt_ * v[k + 1][:, l_ + 1])


def _corr_knots(sym: str, coarse: int = 1):
    """Knots of the correction grid: z = t^2 (dense near the xy plane, where
    K is small and the corrections change fastest), and for coaxial tensors
    phi dense near the x and y axes. Returns (zk, phk, dirs (nz, nphi, 3))."""
    nz, nphi = _CORR_GRID[sym]
    nz = max(8, nz // coarse)
    zk = np.linspace(0, 1, nz + 1) ** 2
    st = np.sqrt(1 - zk ** 2)
    if sym == "axial":
        phk = np.zeros(1)
    else:
        nphi = max(4, nphi // coarse)
        t = np.linspace(0, 1, nphi + 1)
        phk = (math.pi / 4) * (1 - np.cos(math.pi * t)) if sym == "octant" else 2 * math.pi * t
    d = np.stack([st[:, None] * np.cos(phk)[None, :], st[:, None] * np.sin(phk)[None, :],
                  np.broadcast_to(zk[:, None], (zk.size, phk.size))], axis=2)
    return zk, phk, d


def _hybrid_tables(gt, groups: list[_Group], freq_GHz: float, sym: str, coarse: int = 1):
    """For each exactly treated group (n = 1, 2I+1 <= 8): the transitions kept,
    their labels, and on a correction grid the difference between the exact
    and the first-order energies and the relative intensities. These are
    smooth and small, so a coarse grid interpolates them well."""
    out = {}
    targets = [j for j, gr in enumerate(groups) if gr.n == 1 and 2 * gr.I + 1 <= _HYBRID_MAX_DIM]
    if not targets:
        return out
    # the same system is simulated twice by simulate_powder (the grid check)
    key = (np.asarray(gt).tobytes(), float(freq_GHz), sym, coarse,
           tuple((g.I, g.gn, g.n, g.A.tobytes()) for g in groups))
    if key in _TABLE_CACHE:
        return _TABLE_CACHE[key]
    zk, phk, kd = _corr_knots(sym, coarse)
    dirs = kd.reshape(-1, 3)
    shp = kd.shape[:2]
    for j in targets:
        gr = groups[j]
        keep, e, w, ds = _nucleus_exact(gt, gr, freq_GHz, dirs)
        mu, ml = _pair_labels(gr.I)
        e1 = _predict_pairs(gt, gr, freq_GHz, dirs, mu[keep], ml[keep])
        r = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-300)
        out[j] = (keep, mu[keep], ml[keep], (e - e1).reshape(shp + (-1,)), r.reshape(shp + (-1,)),
                  ds.reshape(shp + (-1,)), zk, phk)
    if len(_TABLE_CACHE) > 8:
        _TABLE_CACHE.clear()
    _TABLE_CACHE[key] = out
    return out


def _hybrid_direct(gt, groups, freq_GHz, dirs, perturb_only=(), **kw):
    """Hybrid lines at arbitrary directions (no grid, no interpolation): for
    checks and small direction sets. Groups whose index is in perturb_only
    stay in the second-order treatment."""
    exact = {}
    for j, gr in enumerate(groups):
        if gr.n == 1 and 2 * gr.I + 1 <= _HYBRID_MAX_DIM and j not in perturb_only:
            keep, e, w, ds = _nucleus_exact(gt, gr, freq_GHz, dirs)
            mu, _ = _pair_labels(gr.I)
            exact[j] = (e, w / np.maximum(w.sum(axis=1, keepdims=True), 1e-300), mu[keep], ds)
    return _perturb_lines(gt, groups, freq_GHz, dirs, exact=exact, **kw)


# ==============================================================================
# Orientation grid
# ==============================================================================

def _symmetry(sys: SpinSystem, groups_list, strain_axial: bool = False) -> str:
    """'axial' (all tensors diagonal and x = y), 'octant' (all diagonal), or
    'hemisphere'. strain_axial: treat equal x and y strains as one axial
    distribution (a cheaper approximation, used in the fit's first stage)."""
    mats = [sys.g_tensor()] + [gr.A for _, gs in groups_list for gr in gs]
    diag = all(np.allclose(m, np.diag(np.diag(m)), atol=1e-9 * max(1.0, np.abs(m).max())) for m in mats)
    if not diag:
        return "hemisphere"
    ax = all(abs(m[0, 0] - m[1, 1]) <= 1e-9 * max(1.0, np.abs(m).max()) for m in mats)
    for st in (sys.g_strain, sys.A_strain_MHz):
        # Strains of x and y are independent distributions: their width
        # depends on phi (cos^4 + sin^4) even when the two FWHMs are equal,
        # so any x/y strain needs the 2D grid.
        if st is not None and (st[0] > 0 or st[1] > 0) and not (strain_axial and abs(st[0] - st[1]) < 1e-12):
            ax = False
    return "axial" if ax else "octant"


def _knots(sym: str, n_orient: int):
    """Grid knots uniform in z = cos(theta) and phi. Returns (nz, nphi, dirs
    of the knots, shape (nz+1, nphi+1) or (nz+1, 1))."""
    if sym == "axial":
        nz = max(8, int(n_orient))
        z = np.linspace(0, 1, nz + 1)
        st = np.sqrt(1 - z ** 2)
        d = np.stack([st, np.zeros_like(z), z], axis=1)[:, None, :]
        return nz, 0, d
    span = math.pi / 2 if sym == "octant" else 2 * math.pi
    nz = max(4, int(round(math.sqrt(n_orient / span))))
    nphi = max(4, int(round(nz * span)))
    z = np.linspace(0, 1, nz + 1)
    phi = np.linspace(0, span, nphi + 1)
    st = np.sqrt(1 - z ** 2)
    d = np.stack([st[:, None] * np.cos(phi)[None, :], st[:, None] * np.sin(phi)[None, :],
                  np.broadcast_to(z[:, None], (nz + 1, nphi + 1))], axis=2)
    return nz, nphi, d


def _cell_midpoints(sym: str, n_orient: int) -> np.ndarray:
    nz, nphi, d = _knots(sym, n_orient)
    if sym == "axial":
        z = (np.arange(nz) + 0.5) / nz
        st = np.sqrt(1 - z ** 2)
        return np.stack([st, np.zeros_like(z), z], axis=1)
    span = math.pi / 2 if sym == "octant" else 2 * math.pi
    z = (np.arange(nz) + 0.5) / nz
    phi = (np.arange(nphi) + 0.5) * span / nphi
    st = np.sqrt(1 - z ** 2)
    return np.stack([(st[:, None] * np.cos(phi)[None, :]).ravel(), (st[:, None] * np.sin(phi)[None, :]).ravel(),
                     np.repeat(z, nphi)], axis=1)


# ==============================================================================
# Broadening
# ==============================================================================

class _Axis:
    """Fine, padded, uniform field grid; user points are every `ov`-th point."""

    def __init__(self, x: np.ndarray, pad_mT: float, min_sigma: float):
        self.x = x
        self.dx = (x[-1] - x[0]) / (x.size - 1)
        self.ov = int(np.clip(math.ceil(16 * self.dx / max(min_sigma, 1e-12)), 1, 16))
        self.dxf = self.dx / self.ov
        self.npad = int(math.ceil(pad_mT / self.dx)) + 2
        nf = self.ov * (2 * self.npad + x.size - 1) + 1
        self.nf = sfft.next_fast_len(nf, real=True)
        self.x0f = x[0] - self.npad * self.dx
        self.take = self.ov * (self.npad + np.arange(x.size))

    def deposit(self, pos: np.ndarray, amp: np.ndarray, out: np.ndarray, cls=None):
        """Add sticks to `out` (flat, n_classes * nf) with 4-point Lagrange
        weights, so the deposit's Fourier transform matches the exact stick
        positions to O((k dx)^4)."""
        idx = (pos.ravel() - self.x0f) / self.dxf
        w = amp.ravel()
        i = np.floor(idx)
        f = idx - i
        i = i.astype(np.int64)
        ok = (i >= 1) & (i <= self.nf - 3) & np.isfinite(idx)
        if not np.all(ok):
            i, f, w = i[ok], f[ok], w[ok]
            if cls is not None:
                cls = cls.ravel()[ok]
        elif cls is not None:
            cls = cls.ravel()
        base = i if cls is None else i + cls * self.nf
        fm1, fp1, fm2 = f - 1, f + 1, f - 2
        out += np.bincount(base - 1, w * (-f * fm1 * fm2 / 6), minlength=out.size)
        out += np.bincount(base, w * (fp1 * fm1 * fm2 / 2), minlength=out.size)
        out += np.bincount(base + 1, w * (-fp1 * f * fm2 / 2), minlength=out.size)
        out += np.bincount(base + 2, w * (fp1 * f * fm1 / 6), minlength=out.size)


def _broaden(axis: _Axis, hist: np.ndarray, gauss_fwhm, lor_fwhm: float, harmonic: int,
             modamp_mT: Optional[float]) -> np.ndarray:
    """hist: (n_classes, nf) stick deposits; gauss_fwhm: per class Gaussian
    FWHM. Convolution in the Fourier domain with analytic line shapes."""
    k = sfft.rfftfreq(axis.nf, d=axis.dxf)
    H = sfft.rfft(hist, axis=1)
    sig = np.asarray(gauss_fwhm, float) / (2 * math.sqrt(2 * math.log(2)))
    kern = np.exp(-2 * math.pi ** 2 * sig[:, None] ** 2 * k[None, :] ** 2)
    S = np.sum(H * kern, axis=0)
    if lor_fwhm > 0:
        S = S * np.exp(-math.pi * lor_fwhm * k)
    if harmonic >= 1:
        if modamp_mT:
            a = modamp_mT / 2
            S = S * (2j / a) * special.j1(2 * math.pi * k * a)
            if harmonic == 2:
                S = S * (2j * math.pi * k)
        else:
            S = S * (2j * math.pi * k) ** harmonic
    y = sfft.irfft(S, n=axis.nf) / axis.dxf
    return y[axis.take]


# ==============================================================================
# simulate_powder
# ==============================================================================

_WIDTH_RATIO = 1.02   # spacing of the Gaussian width classes
_STICK = 1.0      # neighbouring sticks at most this many sigma apart
_CURV = 0.005     # B'' h^2 at most this many sigma
_DEFAULT_N_ORIENT = {"hybrid": 5000, "perturb": 5000, "exact": 1000}


def _prepare(sys: SpinSystem):
    gt = sys.g_tensor()
    gframe = None
    if sys.g_strain is not None:
        ev, vec = np.linalg.eigh(gt)
        if sys.g.shape != (3, 3):                        # keep the given x, y, z order
            ev, vec = np.diag(gt).copy(), np.eye(3)
        gframe = (ev, vec)
    return gt, gframe


def _simulate(sys: SpinSystem, x: np.ndarray, freq_GHz: float, harmonic: int, modamp_mT, method: str,
              n_orient: Optional[int], area: float, interp: Optional[int] = None, info: Optional[dict] = None,
              max_points: Optional[float] = None, grid: Optional[dict] = None, corr_coarse: int = 1,
              strain_axial: bool = False):
    """Core simulation on a uniform increasing axis; no checks. `grid`: a
    dict that fixes the discrete choices (sub-cells, oversampling, padding,
    width classes) after the first call, so a fit sees a simulation that is
    smooth in its parameters; filled in when empty."""
    gt, gframe = _prepare(sys)
    gw, lw = sys.widths()
    isos = _isotopologues(sys)
    sym = _symmetry(sys, isos, strain_axial)
    if not n_orient:
        n_orient = _DEFAULT_N_ORIENT[method]
        if sym == "hemisphere" and method in ("hybrid", "perturb"):
            # tilted tensors: four times the octant's solid angle at the same density
            n_orient *= 4
    n_orient = int(n_orient)
    # pass 1: line positions per isotopologue
    pieces = []          # (pos, amp, strain2) flat arrays at the fine sampling points
    if method not in ("hybrid", "perturb", "exact"):
        raise ValueError("method: 'hybrid' (default: each nucleus exact, combined to second order), "
                         "'perturb' (second order throughout) or 'exact' (full diagonalisation)")
    nz, nphi, kd = _knots(sym, n_orient)
    shp = kd.shape[:2]
    kdirs = kd.reshape(-1, 3)
    knot_data = []       # per isotopologue: (pos, amp, strain2), shape (nz+1, nphi+1, lines)
    if method in ("hybrid", "perturb"):
        for wiso, groups in isos:
            pos, amp, st2 = _perturb_lines(gt, groups, freq_GHz, kdirs, sys.g_strain, sys.A_strain_MHz, gframe)
            L = pos.shape[1]
            knot_data.append((pos.reshape(shp + (L,)), amp.reshape(shp + (L,)) * wiso,
                              None if st2 is None else st2.reshape(shp + (L,))))
    else:
        # Lines are labelled by the pair of sorted levels (continuous in the
        # orientation), so they can be interpolated between knots like the
        # perturbation lines; a pair without a resonance at a knot is NaN
        # there and its cells are dropped.
        lo, hi = _field_range(sys, x, freq_GHz, isos, gt)
        for wiso, groups in isos:
            d_idx, B, wt, p_idx = _exact_lines(gt, groups, freq_GHz, kdirs, lo, hi, with_pairs=True)
            keep = wt > 1e-6 * (wt.max() if wt.size else 1.0)
            d_idx, B, wt, p_idx = d_idx[keep], B[keep], wt[keep], p_idx[keep]
            pairs, col = np.unique(p_idx, return_inverse=True)
            pos = np.full((kdirs.shape[0], pairs.size), np.nan)
            amp = np.zeros((kdirs.shape[0], pairs.size))
            pos[d_idx, col] = B
            amp[d_idx, col] = wt * wiso
            st2 = None
            if sys.g_strain is not None or sys.A_strain_MHz is not None:
                st2 = np.zeros_like(amp)
                st2[d_idx, col] = _exact_strain(sys, gt, gframe, groups, kdirs[d_idx], B, freq_GHz)
            L = pairs.size
            knot_data.append((pos.reshape(shp + (L,)), amp.reshape(shp + (L,)),
                              None if st2 is None else st2.reshape(shp + (L,))))
    # Sub-cells per cell: enough that neighbouring sticks are under half the
    # narrowest line's sigma apart (no "grass"), within max_points.
    sig = [gw / 2.3548 if gw > 0 else math.inf, lw / 2 if lw > 0 else math.inf]
    for _, _, st2 in knot_data:
        if st2 is not None and (gw > 0 or np.nanmin(st2) > 0):
            sig.append(math.sqrt(max(float(np.nanmin(st2)), 0.0) + gw ** 2) / 2.3548)
    sig_min = min(sig) if min(sig) < math.inf else 1.0

    def spread(p, axis):
        d = np.abs(np.diff(p, axis=axis))
        d = d[np.isfinite(d)]
        return float(np.percentile(d, 99)) if d.size else 0.0

    def curvature(p, axis):
        if p.shape[axis] < 3:
            return 0.0
        d = np.abs(np.diff(p, n=2, axis=axis))
        d = d[np.isfinite(d)]
        return float(np.percentile(d, 99.9)) if d.size else 0.0
    dz = max(spread(p, 0) for p, _, _ in knot_data)
    dphi = max((spread(p, 1) if p.shape[1] > 1 else 0.0) for p, _, _ in knot_data)
    cz = max(curvature(p, 0) for p, _, _ in knot_data)
    cphi = max(curvature(p, 1) for p, _, _ in knot_data)

    def n_sub(d, c):
        # (a) sticks under half a sigma apart; (b) the midpoint-rule bias at
        # turning points, about (B'' h^2) / sigma, small after the Richardson
        # step below: B'' h^2 <= 0.003 sigma with h the sub-cell size.
        f = max(math.ceil(d / (_STICK * sig_min)), math.ceil(math.sqrt(c / (_CURV * sig_min))), 4)
        return int(min(f + (f % 2), 32))
    fz = n_sub(dz, cz)
    fphi = n_sub(dphi, cphi) if shp[1] > 1 else 1
    if interp is not None:
        fz, fphi = int(interp), (int(interp) if shp[1] > 1 else 1)
    if grid is not None and "fz" in grid:
        fz, fphi = grid["fz"], grid["fphi"]
    hybs = ([_hybrid_tables(gt, groups, freq_GHz, sym, corr_coarse) for _, groups in isos]
            if method == "hybrid" else [None] * len(isos))
    if method == "hybrid":             # lines per orientation, forbidden transitions included
        n_lines = sum(int(np.prod([len(hb[j][1]) if j in hb else len(_group_states(g.I, g.n)[0])
                                   for j, g in enumerate(groups)])) if groups else 1
                      for hb, (_, groups) in zip(hybs, isos))
    else:
        n_lines = sum(p.shape[2] for p, _, _ in knot_data)
    cells = nz * max(nphi, 1)
    if max_points is None:
        # line samples per simulation: generous for few lines on a tilted
        # (hemisphere) grid, which needs four times the samples of an octant
        # for narrow lines; capped otherwise to keep the time within budget
        max_points = 1.5e7 if (n_lines <= 40 and sym == "hemisphere") else 5e6
    while cells * fz * fphi * n_lines > max_points and max(fz, fphi) > 1:
        if fz >= fphi:
            fz -= 1
        else:
            fphi -= 1
    if grid is not None:
        grid.setdefault("fz", fz)
        grid.setdefault("fphi", fphi)
    if method in ("hybrid", "perturb"):
        # The perturbation formula is cheap: evaluate it on the fine grid
        # itself (interpolating the knots is exact only for g). Hybrid: the
        # exact per-nucleus corrections come from a coarser grid.
        for (wiso, groups), hyb in zip(isos, hybs):
            pieces.append(_perturb_fine(gt, groups, freq_GHz, sym, nz * fz, max(nphi, 1) * fphi,
                                        sys.g_strain, sys.A_strain_MHz, gframe, wiso, hyb=hyb))
    else:
        for pos, amp, st2 in knot_data:
            P, A, S2 = _interpolate(pos, amp, st2, fz, fphi, sym)
            bad = ~np.isfinite(P)
            if np.any(bad):
                P, A = P[~bad], A[~bad]
                S2 = None if S2 is None else S2[~bad]
            if S2 is not None:
                S2 = np.nan_to_num(S2, nan=0.0, posinf=0.0)
            pieces.append((P, A, S2))

    total = sum(float(np.sum(p[1])) for p in pieces)
    if total <= 0:
        raise ValueError("no resonance lines: check g, freq_GHz and the field range")
    max_strain = max((float(np.sqrt(np.max(p[2]))) if p[2] is not None and p[2].size else 0.0) for p in pieces)
    widest = math.hypot(gw, max_strain) + lw
    min_strain = min((float(np.sqrt(np.min(p[2]))) if p[2] is not None and p[2].size else math.inf) for p in pieces)
    # Lorentzian lines have more high-frequency content: sample them twice as finely
    cands = [w for w in (gw / 2.3548 if gw > 0 else None, lw / 4 if lw > 0 else None) if w]
    if not cands:                                  # strain-only broadening
        cands = [max(min_strain, 1e-3 * max(max_strain, 1e-9)) / 2.3548]
    min_sigma = min(cands)
    pad = max(10 * widest, 0.02 * (x[-1] - x[0]))
    if lw > 0:
        pad = max(pad, min(400 * lw, 20 * (x[-1] - x[0])))
    if grid is not None:
        pad = grid.setdefault("pad", 2 * pad)
        min_sigma = grid.setdefault("min_sigma", 0.5 * min_sigma)
    axis = _Axis(x, pad, min_sigma)
    if any(p[2] is not None for p in pieces):
        # Gaussian width classes (2% apart in log width); each line is split
        # between its two neighbouring classes so the result is smooth in the
        # strain parameters.
        wmin = max(gw, 1e-3 * widest, 1e-9) if gw > 0 else max(min(min_strain, widest), 1e-3 * widest, 1e-9)
        if grid is not None:
            wmin = grid.setdefault("wmin", 0.25 * wmin)
        ratio = grid.get("ratio", _WIDTH_RATIO) if grid is not None else _WIDTH_RATIO
        # Each line is split between its two neighbouring width classes
        # (linear in log width): the result is smooth in the parameters, and
        # the small grid-cell spreads (a few % of the width) are not lost to
        # rounding. Only the classes in use are allocated and transformed.
        cls = []
        for pos, amp, st2 in pieces:
            wt = np.sqrt(gw ** 2 + (st2 if st2 is not None else 0.0))
            c = np.log(np.maximum(wt, wmin) / wmin) / math.log(ratio)
            c0 = np.floor(c).astype(np.int64)
            cls.append((c0, c - c0))
        cmin = min(int(c0.min()) for c0, _ in cls if c0.size)
        cmax = max(int(c0.max()) for c0, _ in cls if c0.size) + 1
        ncls = cmax - cmin + 1
        cls_w = wmin * ratio ** np.arange(cmin, cmax + 1)
        hist = np.zeros(ncls * axis.nf)
        for (pos, amp, _), (c0, fr) in zip(pieces, cls):
            axis.deposit(pos, amp * (1 - fr), hist, c0 - cmin)
            axis.deposit(pos, amp * fr, hist, c0 - cmin + 1)
        hist = hist.reshape(ncls, axis.nf)
        used = np.any(hist != 0, axis=1)
        hist, cls_w = hist[used], cls_w[used]
        y = _broaden(axis, hist, cls_w, lw, harmonic, modamp_mT)
    else:
        hist = np.zeros(axis.nf)
        for pos, amp, _ in pieces:
            axis.deposit(pos, amp, hist)
        y = _broaden(axis, hist[None, :], [gw], lw, harmonic, modamp_mT)
    if info is not None:
        allpos = np.concatenate([np.ravel(p[0]) for p in pieces])
        allamp = np.concatenate([np.ravel(p[1]) for p in pieces])
        out = (allpos < x[0]) | (allpos > x[-1])
        info.update(symmetry=sym, n_orient=n_orient, subcells=(fz, fphi), n_lines=n_lines, isotopologues=len(isos), oversampling=axis.ov,
                    outside_fraction=float(np.sum(allamp[out]) / total),
                    line_range_mT=(float(np.min(allpos)), float(np.max(allpos))), min_fwhm=min_sigma)
    return y * (area / total)


def _perturb_fine(gt, groups, freq_GHz, sym, nzf: int, nphif: int, g_strain, A_strain, gframe, weight,
                  chunk: float = 2e6, hyb: Optional[dict] = None):
    """Perturbation lines at the midpoints of an nzf x nphif grid (uniform in
    z and phi, equal areas), each stick carrying the spread of its cell as
    extra Gaussian variance (dB/di^2 + dB/dj^2) / 12 from central
    differences on the grid (see _interpolate). Works in blocks of z rows
    with a one-row halo. Returns flat (pos, amp, strain2).

    hyb: tables from _hybrid_tables; those groups get the first-order
    energies evaluated here plus the interpolated exact corrections."""
    hyb = hyb or {}
    span = math.pi / 2 if sym != "hemisphere" else 2 * math.pi
    zc = (np.arange(nzf) + 0.5) / nzf
    if sym == "axial":
        phic = np.zeros(1)
        nphif = 1
    else:
        phic = (np.arange(nphif) + 0.5) * span / nphif
    L = int(np.prod([len(hyb[j][1]) if j in hyb else len(_group_states(gr.I, gr.n)[0])
                     for j, gr in enumerate(groups)])) if groups else 1
    rows = max(1, int(chunk // max(nphif * L, 1)))
    k8 = 8 * math.log(2) / 12.0
    P, A, S2 = [], [], []
    for a in range(0, nzf, rows):
        b = min(a + rows, nzf)
        lo, hi = max(a - 1, 0), min(b + 1, nzf)
        z = zc[lo:hi]
        st = np.sqrt(1 - z ** 2)
        d = np.stack([(st[:, None] * np.cos(phic)[None, :]), (st[:, None] * np.sin(phic)[None, :]),
                      np.broadcast_to(z[:, None], (z.size, phic.size))], axis=2).reshape(-1, 3)
        exact = {}
        for j, (keep, mu_k, ml_k, c_kn, r_kn, ds_kn, zk_, phk_) in hyb.items():
            e1 = _predict_pairs(gt, groups[j], freq_GHz, d, mu_k, ml_k)
            P_ = mu_k.size
            both = _grid_interp(np.concatenate([c_kn, r_kn, ds_kn], axis=2), zk_, phk_, sym, z,
                                phic).reshape(-1, 3 * P_)
            c, r, ds = both[:, :P_], np.maximum(both[:, P_:2 * P_], 0.0), both[:, 2 * P_:]
            r = r / np.maximum(r.sum(axis=1, keepdims=True), 1e-300)
            exact[j] = (e1 + c, r, mu_k, ds)
        pos, amp, st2 = _perturb_lines(gt, groups, freq_GHz, d, g_strain, A_strain, gframe, exact=exact)
        shp = (z.size, phic.size, pos.shape[1])
        pos = pos.reshape(shp)
        gz = np.gradient(pos, axis=0) if z.size > 1 else np.zeros_like(pos)
        var = gz ** 2
        if phic.size > 1:
            var = var + np.gradient(pos, axis=1) ** 2
        extra = k8 * var
        inner = slice(a - lo, a - lo + (b - a))
        P.append(pos[inner].ravel())
        A.append((amp.reshape(shp)[inner] * weight).ravel())
        tot = extra if st2 is None else extra + st2.reshape(shp)
        S2.append(tot[inner].ravel())
    return np.concatenate(P), np.concatenate(A), np.concatenate(S2)


def _interpolate(pos, amp, st2, fz: int, fphi: int, sym: str, spread: bool = True):
    """Interpolate knot values (nzk, nphik, L) to fz x fphi sub-cell
    midpoints of every cell (all cells have equal area, uniform in z and
    phi). For coaxial tensors (octant, axial) the interpolation variables
    are z^2 and sin^2(phi) and the field enters as 1/B^2: g_eff^2 is exactly
    bilinear in those, so turning points come out right on coarse grids.
    Tilted tensors (hemisphere) interpolate B in z and phi.

    spread: each stick stands for its sub-cell, whose fields spread over
    about |dB/dz| h_z and |dB/dphi| h_phi. That spread is added to the
    stick's Gaussian width as its variance (h^2 / 12 per direction), which
    removes the leading O(h^2) error of point sampling (largest at the
    logarithmic singularity of a rhombic pattern). Returns S2 (squared
    Gaussian FWHM, mT^2) including it."""
    nzk, nphik = pos.shape[:2]
    nz, nphi = nzk - 1, max(nphik - 1, 1)
    zk = np.linspace(0, 1, nzk)
    span = math.pi / 2 if sym != "hemisphere" else 2 * math.pi
    phik = np.linspace(0, span, nphik) if nphik > 1 else np.zeros(1)
    coaxial = sym != "hemisphere"
    q = 1.0 / pos ** 2 if coaxial else pos
    hz = 1.0 / (nz * fz)
    hphi = span / (nphi * fphi)
    k8 = 8 * math.log(2) / 12.0                  # variance h^2/12 as a squared FWHM
    P, A, S2 = [], [], []
    for si in (np.arange(fz) + 0.5) / fz:
        z = zk[:-1] + si / nz
        if coaxial:
            den = (zk[1:] ** 2 - zk[:-1] ** 2)
            ws = ((z ** 2 - zk[:-1] ** 2) / den)[:, None, None]
            dws = (2 * z / den)[:, None, None]       # d ws / dz
        else:
            ws = si
            dws = float(nz)
        qz = q[:-1] * (1 - ws) + q[1:] * ws
        dqz = (q[1:] - q[:-1]) * dws                  # d q / dz along the knot columns
        az = amp[:-1] * (1 - ws) + amp[1:] * ws
        sz = None if st2 is None else st2[:-1] * (1 - ws) + st2[1:] * ws
        if nphik == 1:
            parts = [(qz, dqz, None, az, sz)]
        else:
            parts = []
            for ti in (np.arange(fphi) + 0.5) / fphi:
                ph = phik[:-1] + ti * span / nphi
                if coaxial:
                    s0, s1 = np.sin(phik[:-1]) ** 2, np.sin(phik[1:]) ** 2
                    wt = ((np.sin(ph) ** 2 - s0) / (s1 - s0))[None, :, None]
                    dwt = (2 * np.sin(ph) * np.cos(ph) / (s1 - s0))[None, :, None]
                else:
                    wt = ti
                    dwt = nphi / span
                qq = qz[:, :-1] * (1 - wt) + qz[:, 1:] * wt
                dq_z = dqz[:, :-1] * (1 - wt) + dqz[:, 1:] * wt
                dq_p = (qz[:, 1:] - qz[:, :-1]) * dwt
                parts.append((qq, dq_z, dq_p, az[:, :-1] * (1 - wt) + az[:, 1:] * wt,
                              None if sz is None else sz[:, :-1] * (1 - wt) + sz[:, 1:] * wt))
        for qq, dq_z, dq_p, aa, ss in parts:
            if coaxial:
                b = 1.0 / np.sqrt(qq)
                conv = -0.5 * b ** 3                  # dB/dq for q = 1/B^2
            else:
                b = qq
                conv = 1.0
            P.append(b)
            A.append(aa)
            if spread:
                var = (conv * dq_z * hz) ** 2
                if dq_p is not None:
                    var = var + (conv * dq_p * hphi) ** 2
                extra = k8 * var
                S2.append(extra if ss is None else ss + extra)
            elif ss is not None:
                S2.append(ss)
    P = np.concatenate([p.ravel() for p in P])
    A = np.concatenate([a.ravel() for a in A])
    S2 = np.concatenate([q_.ravel() for q_ in S2]) if S2 else None
    return P, A, S2


def _field_range(sys, x, freq_GHz, isos, gt):
    """Field window for the exact root search: the sweep, widened to cover
    every perturbation line plus a margin."""
    dirs = _cell_midpoints(_symmetry(sys, isos), 200)
    lo, hi = x[0], x[-1]
    for _, groups in isos:
        p, _, _ = _perturb_lines(gt, groups, freq_GHz, dirs)
        lo, hi = min(lo, float(p.min())), max(hi, float(p.max()))
    gw, lw = sys.widths()
    m = 0.1 * (hi - lo) + 10 * (gw + lw) + 1.0
    return max(lo - m, 1e-3), hi + m


def _exact_strain(sys, gt, gframe, groups, dirs, B, freq_GHz):
    """Strain widths for exact lines: g strain from the direction and field;
    A strain approximated with |m| of the first nucleus from the
    perturbation picture (nearest first-order line)."""
    st2 = np.zeros_like(B)
    gn_vec = dirs @ gt
    geff = np.linalg.norm(gn_vec, axis=1)
    if sys.g_strain is not None:
        ng = dirs @ gframe[1]
        gp = gframe[0]
        ge2 = np.sum((gp * ng) ** 2, axis=1)
        s = np.sqrt(np.sum((gp * ng ** 2 * sys.g_strain) ** 2, axis=1)) / ge2
        st2 += (B * s) ** 2
    if sys.A_strain_MHz is not None:
        first = [gr for gr in groups if gr.first]
        if first:
            gr = first[0]
            u = gn_vec / geff[:, None]
            aTu = u @ gr.A
            K = np.maximum(np.linalg.norm(aTu, axis=1), 1e-12)
            B0 = freq_GHz * 1000.0 / (geff * MUB_MHZ_PER_MT)
            m = np.clip(np.abs(np.round((B0 - B) * geff * MUB_MHZ_PER_MT / K * 2) / 2), 0, gr.I * gr.n)
            ev, vec = np.linalg.eigh(gr.A)
            proj = u @ vec
            dK = np.sqrt(np.sum((ev * proj ** 2 * sys.A_strain_MHz) ** 2, axis=1)) / K
            st2 += (m * dK / (geff * MUB_MHZ_PER_MT)) ** 2
    return st2


def _uniform(field_mT):
    x = np.asarray(field_mT, float).ravel()
    if x.size < 8:
        raise ValueError("field_mT: give the field axis (at least 8 points)")
    rev = x[-1] < x[0]
    xs = x[::-1] if rev else x
    d = np.diff(xs)
    if np.any(d <= 0):
        raise ValueError("field_mT must be strictly monotonic")
    uniform = np.max(np.abs(d - d.mean())) <= 1e-6 * d.mean()
    xu = xs if uniform else np.linspace(xs[0], xs[-1], xs.size)
    return xs, xu, rev, uniform


def _on_user_axis(y, xs, xu, rev, uniform):
    if not uniform:
        y = np.interp(xs, xu, y)
    return y[::-1] if rev else y


def _perturb_error(sys: SpinSystem, freq_GHz: float, isos) -> tuple[float, str]:
    """Largest line-position error of the perturbation result, checked
    against exact diagonalisation for each nucleus alone (most abundant
    isotopologue, one copy) at 9 orientations. Errors of several nuclei are
    added. Returns (error in mT, description)."""
    if not sys.nuclei:
        return 0.0, ""
    gt = sys.g_tensor()
    ev, vec = np.linalg.eigh(gt)
    dirs = [vec[:, 0], vec[:, 1], vec[:, 2]]
    for d in ([1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1], [1, -1, 2], [2, 1, -1]):
        v = vec @ np.array(d, float)
        dirs.append(v / np.linalg.norm(v))
    dirs = np.array(dirs)
    _, groups = max(isos, key=lambda t: t[0])
    total = 0.0
    worst = ""
    seen = set()
    for gr in groups:
        key = (gr.label, round(float(np.sum(np.abs(gr.A))), 6))
        if key in seen:
            continue
        seen.add(key)
        one = _Group(I=gr.I, gn=gr.gn, A=gr.A, n=1, first=False, label=gr.label)
        p, a, _ = _perturb_lines(gt, [one], freq_GHz, dirs)
        lo, hi = float(p.min()), float(p.max())
        m = 0.2 * (hi - lo) + 2.0
        d_idx, B, w = _exact_lines(gt, [one], freq_GHz, dirs, max(lo - m, 1e-3), hi + m, n_scan=61)
        err = 0.0
        for k in range(dirs.shape[0]):
            sel = d_idx == k
            if not np.any(sel):
                continue
            Bk, wk = B[sel], w[sel]
            strong = Bk[wk > 0.2 * wk.max()]
            if strong.size == 0:
                continue
            for b in p[k]:
                err = max(err, float(np.min(np.abs(strong - b))))
        if err > 0:
            total += err
            worst = f"{gr.label}: {err:.3g} mT"
    return total, worst


def _check_dirs(gt: np.ndarray) -> np.ndarray:
    ev, vec = np.linalg.eigh(gt)
    dirs = [vec[:, 0], vec[:, 1], vec[:, 2]]
    for d in ([1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1], [1, -1, 2], [2, 1, -1], [1, 0, 12], [12, 1, 0.3]):
        v = vec @ np.array(d, float)
        dirs.append(v / np.linalg.norm(v))
    return np.array(dirs)


def _line_error(pos, amp, d_idx, B, w) -> float:
    """Largest distance from a strong approximate line to the nearest strong
    exact line, over the check directions."""
    err = 0.0
    for k in range(pos.shape[0]):
        sel = d_idx == k
        if not np.any(sel):
            continue
        strong = B[sel][w[sel] > 0.1 * w[sel].max()]
        mine = pos[k][amp[k] > 0.1 * amp[k].max()]
        if strong.size and mine.size:
            err = max(err, float(np.max(np.min(np.abs(mine[:, None] - strong[None, :]), axis=1))))
    return err


def _hybrid_error(sys: SpinSystem, freq_GHz: float, isos) -> tuple[float, str]:
    """Largest line-position error of the hybrid result against exact
    diagonalisation at 17 orientations (most abundant isotopologue). The
    whole system when it has at most 64 states; otherwise each group that
    stays in second order (equivalent nuclei) is checked as one copy next to
    the exactly treated nuclei, and the errors are added."""
    gt = sys.g_tensor()
    dirs = _check_dirs(gt)
    _, groups = max(isos, key=lambda t: t[0])
    if not groups:
        return 0.0, ""

    def dim(gs):
        return 2 * int(np.prod([(2 * g.I + 1) ** g.n for g in gs]))

    def compare(gs, perturb_only=()):
        p, a, _ = _hybrid_direct(gt, gs, freq_GHz, dirs, perturb_only=perturb_only)
        lo, hi = float(p.min()), float(p.max())
        m = 0.2 * (hi - lo) + 2.0
        d_idx, B, w = _exact_lines(gt, gs, freq_GHz, dirs, max(lo - m, 1e-3), hi + m, n_scan=61)
        return _line_error(p, a, d_idx, B, w)

    if dim(groups) <= 64:
        e = compare(groups)
        return e, f"whole system: {e:.3g} mT"
    exact_groups = [g for g in groups if g.n == 1 and 2 * g.I + 1 <= _HYBRID_MAX_DIM]
    exact_groups.sort(key=lambda g: -float(np.abs(g.A).max()))
    total, parts = 0.0, []
    others = [g for g in groups if g not in exact_groups]
    if not others:                     # many distinct single nuclei: the strongest with each other one
        others = exact_groups[1:]
        exact_groups = exact_groups[:1]
    for g in others:
        one = _Group(I=g.I, gn=g.gn, A=g.A, n=1, first=False, label=g.label)
        base = []
        for eg in exact_groups:
            if dim(base + [eg, one]) <= 64:
                base.append(eg)
        sub = base + [one]
        perturb_only = (len(sub) - 1,) if g.n > 1 or 2 * g.I + 1 > _HYBRID_MAX_DIM else ()
        e = compare(sub, perturb_only)
        total += e
        parts.append(f"{g.label}: {e:.3g} mT")
    return total, ", ".join(parts)


def simulate_powder(sys: SpinSystem, field_mT, freq_GHz: float, *, harmonic: int = 1,
                    modamp_mT: Optional[float] = None, method: str = "hybrid",
                    n_orient: Optional[int] = None, area: float = 1.0, return_info: bool = False):
    """Powder (frozen solution) CW spectrum of an S = 1/2 SpinSystem,
    following EasySpin's conventions (pepper; Stoll and Schweiger, J. Magn.
    Reson. 178, 42 (2006)).

    field_mT: field axis (mT); freq_GHz: microwave frequency. harmonic 0:
    absorption, 1: first derivative (default), 2: second derivative.
    modamp_mT: peak-to-peak field modulation amplitude (exact first-harmonic
    kernel). method "hybrid" (default: each nucleus diagonalised exactly
    with the electron, nuclei combined to second order; see the module
    docstring), "perturb" (plain second-order perturbation, Iwasaki 1974,
    fastest, 0.1-0.3 mT off for Cu at X band) or "exact" (full
    diagonalisation and root finding, Hilbert dimension up to 64; its
    knot interpolation limits it to about 1% for rhombic Cu). n_orient:
    approximate number of grid knots (default 5000; 20000 for tilted
    tensors; 1000 exact).

    Intensities carry the transition moment and the 1/g factor (Aasa and
    Vanngard 1975) across the pattern; the absorption integral (double
    integral of the derivative) is normalised to `area` over all lines,
    so it equals `area` when every line lies inside the sweep.

    Returns the spectrum (numpy array). Warnings are printed; with
    return_info=True it returns (spectrum, info), info a dict holding
    "warnings" and the grid details. Warnings:
    error of the approximate method (hybrid or perturb, checked against full
    diagonalisation at 17 orientations) changing the spectrum by more than
    about 1%, grid not converged (half the
    sampling density differs by > 0.8% of the maximum), linewidth under 3 field steps,
    lines outside the sweep, natural isotope mixtures."""
    if not isinstance(sys, SpinSystem):
        raise TypeError("simulate_powder(sys, field_mT, freq_GHz): sys is an epr.SpinSystem(...)")
    if harmonic not in (0, 1, 2):
        raise ValueError("harmonic: 0 (absorption), 1 (first derivative) or 2")
    if modamp_mT and harmonic == 0:
        raise ValueError("modamp_mT applies to field-modulated (harmonic >= 1) spectra")
    xs, xu, rev, uniform = _uniform(field_mT)
    info: dict = {"method": method}
    y = _simulate(sys, xu, float(freq_GHz), harmonic, modamp_mT, method, n_orient, area, info=info)
    warns = list(sys.notes())
    dx = (xu[-1] - xu[0]) / (xu.size - 1)
    gw, lw = sys.widths()
    lw_min = min([w for w in (gw, lw) if w > 0] or [info["min_fwhm"] * 2.3548])
    lw_tot = gw + lw if gw + lw > 0 else lw_min
    # grid convergence: half the grid density in each direction
    n_used = info["n_orient"]
    fz, fphi = info["subcells"]
    if method in ("hybrid", "perturb"):   # same knots, half the sampling density in each direction
        y_half = _simulate(sys, xu, float(freq_GHz), harmonic, modamp_mT, method, n_used, area,
                           grid={"fz": max(fz // 2, 1), "fphi": max(fphi // 2, 1)})
    else:                         # exact: half the knot density, same sub-cells
        half_n = n_used // 2 if info["symmetry"] == "axial" else n_used // 4
        y_half = _simulate(sys, xu, float(freq_GHz), harmonic, modamp_mT, method, half_n, area,
                           grid={"fz": fz, "fphi": fphi})
    ref = float(np.max(np.abs(y))) or 1.0
    dev = float(np.max(np.abs(y - y_half))) / ref
    info["grid_deviation"] = dev
    # Measured against converged references, the default grid's own error is
    # 1/2 to 1/15 of this half-density deviation; 0.8% flags errors from
    # about 0.3% upwards without firing on converged defaults (up to 0.67%).
    if dev > 0.008:
        warns.append(f"orientation grid not converged: the spectrum at half the grid density differs by "
                     f"{100 * dev:.2g}% of its maximum (the error is roughly a third of that); pass a larger "
                     f"n_orient (now {n_used})")
    if lw_min < 3 * dx:
        warns.append(f"linewidth {lw_min:.3g} mT is under 3 field steps ({dx:.3g} mT): the simulated shape "
                     f"is undersampled; use a finer field axis or check the width")
    if info["outside_fraction"] > 0.005:
        lo, hi = info["line_range_mT"]
        warns.append(f"{100 * info['outside_fraction']:.2g}% of the intensity lies outside the sweep "
                     f"{xs[0]:.4g}-{xs[-1]:.4g} mT (lines span {lo:.4g}-{hi:.4g} mT): the absorption integral "
                     f"of the spectrum is less than `area`")
    if method in ("hybrid", "perturb"):
        isos_ = _isotopologues(sys)
        err, worst = (_hybrid_error if method == "hybrid" else _perturb_error)(sys, float(freq_GHz), isos_)
        info["perturbation_error_mT"] = err
        # A shift d of a line of FWHM w changes a derivative spectrum by
        # about 2 d / w of its maximum (measured on Cu(II) against 'exact').
        est = 2.0 * err / lw_tot
        info["perturbation_spectrum_error"] = est
        if est > 0.01:
            N = 2 * int(np.prod([(2 * max([i.spin for i, _ in _mixture(n.name)[0]] or [0]) + 1) ** n.n
                                 for n in sys.nuclei]))
            fix = ("use method='exact'" if N <= 64 else
                   f"method='exact' handles up to 64 states (this system has {N}); check the dominant "
                   f"nucleus alone with method='exact'")
            what = ("the hybrid treatment (each nucleus exact, the nuclei combined to second order)"
                    if method == "hybrid" else "second-order perturbation")
            warns.append(f"{what} misplaces lines by up to {err:.3g} mT ({worst}), which "
                         f"changes the spectrum by roughly {100 * est:.2g}% of its maximum: {fix}")
    for w in warns:
        print(f"labkit.epr.simulate_powder WARNING: {w}")
    info["warnings"] = warns
    out = _on_user_axis(y, xs, xu, rev, uniform)
    return (out, info) if return_info else out


# ==============================================================================
# Start estimates from the spectrum
# ==============================================================================

@dataclass
class PowderStarts(FieldHelp):
    kind: str
    values: dict          # the estimates, e.g. {"g_par": 2.24, "A_par_MHz": 580}
    how: str              # how they were read off the spectrum

    def __repr__(self):
        vals = ", ".join(f"{k}={v:.5g}" for k, v in self.values.items())
        return f"PowderStarts({self.kind}: {vals}; {self.how})"


def _cu_grid_starts(x, yy, freq_GHz, modamp_mT, line_guess):
    """Cu(II) starts by scoring quick simulations (plain second order,
    coarse grid, scale and offset solved linearly) against the spectrum:
    g_perp by a scan near the strong feature, then g_par x A_par on a grid,
    then a finer grid around the best; the parallel-line estimate (if any)
    competes as one more candidate. Returns (g_perp, g_par, A_par_MHz)."""
    hv_mt = freq_GHz * 1000.0 / MUB_MHZ_PER_MT
    xu = np.linspace(x[0], x[-1], min(x.size, 1024))
    yu = np.interp(xu, x, yy)
    D1 = np.ones((xu.size, 1))
    grid: dict = {"ratio": 1.1}

    region = {"m": np.ones(xu.size, bool)}

    def cost(gp, gz, az):
        sys_ = SpinSystem(g=[gp, gz], nuclei=[Nucleus("63Cu", A_MHz=[40.0, az])], fwhm_mT=1.2,
                          g_strain=[0.004, 0.004, 0.010])
        m = _simulate(sys_, xu, freq_GHz, 1, modamp_mT, "perturb", 300, 1.0, max_points=3e4, grid=grid,
                      strain_axial=True)
        k = region["m"]
        D = np.column_stack([m[k], D1[k]])
        c, *_ = np.linalg.lstsq(D, yu[k], rcond=None)
        r = yu[k] - D @ c
        return float(r @ r)
    imx, imn = int(np.argmax(yy)), int(np.argmin(yy))
    a, b = sorted((imx, imn))
    gp0 = hv_mt / (0.5 * (x[a] + x[b]))
    gz0, az0 = (line_guess[1], line_guess[2]) if line_guess else (2.25, 550.0)
    # g_perp: the strong feature is at or above the perpendicular field
    gps = gp0 + np.linspace(-0.01, 0.045, 23)
    gp = min(gps, key=lambda v: cost(v, gz0, az0))
    # g_par and A_par: scored on the parallel region only (below the
    # perpendicular feature), where they decide the spectrum
    region["m"] = xu < hv_mt / gp - 4.0
    if np.count_nonzero(region["m"]) < 50:
        region["m"] = np.ones(xu.size, bool)
    coarse = [(cost(gp, gz, az), gz, az) for gz in np.arange(2.15, 2.385, 0.01)
              for az in np.arange(350.0, 710.0, 15.0)]
    if line_guess:
        coarse.append((cost(gp, line_guess[1], line_guess[2]), line_guess[1], line_guess[2]))
    coarse.sort()
    # refine the best three distinct candidates (a ladder one line off fits
    # three of the four parallel lines and can win on a coarse grid)
    picked = []
    for c_, gz, az in coarse:
        if all(abs(gz - q[1]) > 0.02 or abs(az - q[2]) > 30 for q in picked):
            picked.append((c_, gz, az))
        if len(picked) == 3:
            break
    fine = [(cost(gp, gz + dg, az + da), gz + dg, az + da)
            for _, gz, az in picked
            for dg in np.linspace(-0.01, 0.01, 9) for da in np.linspace(-15.0, 15.0, 7)]
    _, gz, az = min(fine)
    region["m"] = np.ones(xu.size, bool)
    gp = min(gp + np.linspace(-0.006, 0.006, 7), key=lambda v: cost(v, gz, az))
    return float(gp), float(gz), float(az)


def powder_starts(field_mT, y, freq_GHz: float, kind: str, modamp_mT: Optional[float] = None) -> PowderStarts:
    """Start values for fit_spectrum read off a first-derivative powder
    spectrum (estimates, not results: refine them with fit_spectrum).

    kind="cu": axial Cu(II) (g_par > g_perp). The parallel hyperfine lines are
        the low-field features below the strong perpendicular one; their
        spacing is A_par/(g_par muB), and taking the highest of them as
        m = -1/2 gives a first g_par. That guess and a grid of g_par x A_par
        (2.15-2.38, 350-710 MHz) are then scored by quick simulations
        against the spectrum (g_perp first, near the strong feature), and
        the best is refined on a finer grid: this does not depend on
        resolving the parallel lines or on which m they are. About 1 s.
        Pass modamp_mT when the modulation is large.
    kind="nitroxide": rigid-limit nitroxide: Azz = half the splitting of the
        outer extrema (first maximum, last minimum).
    kind="rhombic": S = 1/2 rhombic g: g_max at the first (low-field)
        maximum, g_mid at the zero crossing of the central feature, g_min at
        the last minimum."""
    from .epr import _turning_points
    x = np.asarray(field_mT, float).ravel()
    yy = np.real(np.asarray(y)).astype(float).ravel()
    if x[-1] < x[0]:
        x, yy = x[::-1], yy[::-1]
    hv_mt = freq_GHz * 1000.0 / MUB_MHZ_PER_MT          # g * B in mT
    mx, mn = _turning_points(x, yy, 0.03)
    k = kind.lower()
    if k == "nitroxide":
        if mx.size == 0 or mn.size == 0:
            raise ValueError("no extrema found")
        lo, hi = float(mx.min()), float(mn.max())
        return PowderStarts("nitroxide", {"Azz_mT": (hi - lo) / 2, "g_iso": hv_mt / ((hi + lo) / 2)},
                            "Azz = half the outer-extrema splitting (first maximum to last minimum)")
    if k == "rhombic":
        if mx.size < 2 or mn.size < 1:
            raise ValueError("need at least two maxima and one minimum for a rhombic pattern")
        b_max = float(mx.min())
        b_min = float(mn.max())
        # central feature: the strongest max/min pair between the outer two
        imx, imn = int(np.argmax(yy)), int(np.argmin(yy))
        inner = [(i, j) for i in np.searchsorted(x, mx) for j in np.searchsorted(x, mn)
                 if b_max < x[i] < x[j] < b_min]
        if inner:
            i, j = max(inner, key=lambda t: yy[t[0]] - yy[t[1]])
        else:
            i, j = sorted((imx, imn))
        seg = slice(min(i, j), max(i, j) + 1)
        zc = np.nonzero(np.diff(np.sign(yy[seg])))[0]
        b_mid = float(x[seg][zc[0]]) if zc.size else 0.5 * (x[i] + x[j])
        return PowderStarts("rhombic", {"g_max": hv_mt / b_max, "g_mid": hv_mt / b_mid, "g_min": hv_mt / b_min},
                            "g_max at the first maximum, g_mid at the zero crossing of the central feature, "
                            "g_min at the last minimum")
    if k == "cu":
        imx, imn = int(np.argmax(yy)), int(np.argmin(yy))
        a, b = sorted((imx, imn))
        zc = np.nonzero(np.diff(np.sign(yy[a:b + 1])))[0]
        b_perp = float(x[a + zc[0]]) if zc.size else 0.5 * (x[a] + x[b])
        # parallel lines: maxima at least 5 mT below the strong feature,
        # counted against the low-field region's own height (they are weak
        # next to the perpendicular line)
        low = x < b_perp - 5.0
        guess = None
        if np.count_nonzero(low) > 20:
            lm, _ = _turning_points(x[low], yy[low], 0.15)
            par = np.sort(lm)
            if par.size >= 2:
                spacing = float(par[-1] - par[-2])
                g_par = hv_mt / (float(par[-1]) - spacing / 2)
                if 2.1 < g_par < 2.45 and 8.0 < spacing < 25.0:
                    guess = (hv_mt / b_perp, g_par, spacing * g_par * MUB_MHZ_PER_MT)
        gp, gz, az = _cu_grid_starts(x, yy, float(freq_GHz), modamp_mT, guess)
        how = ("g_perp scanned near the strong perpendicular feature; g_par and A_par from a grid "
               "(2.15-2.38, 350-710 MHz) scored by quick simulations, refined"
               + ("; the parallel-line spacing estimate (highest line as m = -1/2) competed as a candidate"
                  if guess else "; the parallel lines were not resolved, so the grid alone decides"))
        return PowderStarts("cu", {"g_perp": gp, "g_par": gz, "A_par_MHz": az}, how)
    raise ValueError("kind: 'cu', 'nitroxide' or 'rhombic'")


def _auto_start(start_sys: "SpinSystem", params: list, x, y, freq_GHz, modamp_mT=None) -> Optional[dict]:
    """{param index: value} from powder_starts when the system is clearly
    one of its kinds (axial g with one I >= 3/2 nucleus whose largest A is
    along g_par: "cu"; one 14N with the largest A along z and g near 2:
    "nitroxide"); None otherwise."""
    try:
        g = np.asarray(start_sys.g, float)
        nuc = start_sys.nuclei
        if len(nuc) != 1:
            return None
        isos, _, _ = _mixture(nuc[0].name)
        spin = max([i.spin for i, _ in isos] or [0])
        A = np.asarray(nuc[0].A_spec, float)
        if A.ndim == 0 or A.shape == (3, 3) or g.ndim == 0 or g.shape == (3, 3):
            return None
        gp = _principal(g)
        Ap = _principal(A)
        out = {}
        if spin >= 1.5 and abs(gp[0] - gp[1]) < 0.02 and gp[2] > gp[0] and np.argmax(np.abs(Ap)) == 2:
            est = powder_starts(x, y, freq_GHz, "cu", modamp_mT=modamp_mT).values
            for j, p in enumerate(params):
                if p.key == "g" and p.comp in (0, None) and g.size == 2:
                    out[j] = est["g_perp"]
                elif p.key == "g" and ((g.size == 2 and p.comp == 1) or (g.size == 3 and p.comp == 2)):
                    out[j] = est["g_par"]
                elif p.key == "g" and g.size == 3 and p.comp in (0, 1):
                    out[j] = est["g_perp"]
                elif p.key == "A_MHz" and ((A.size == 2 and p.comp == 1) or (A.size == 3 and p.comp == 2)):
                    out[j] = est["A_par_MHz"] * np.sign(Ap[2] or 1)
        elif spin == 1.0 and np.all(np.abs(gp - 2.005) < 0.01) and np.argmax(np.abs(Ap)) == 2:
            est = powder_starts(x, y, freq_GHz, "nitroxide").values
            giso = float(np.mean(gp))
            for j, p in enumerate(params):
                if p.key == "A_mT" and ((A.size == 2 and p.comp == 1) or (A.size == 3 and p.comp == 2)):
                    out[j] = est["Azz_mT"]
                elif p.key == "A_MHz" and ((A.size == 2 and p.comp == 1) or (A.size == 3 and p.comp == 2)):
                    out[j] = est["Azz_mT"] * giso * MUB_MHZ_PER_MT
        return out or None
    except (ValueError, IndexError):
        return None


# ==============================================================================
# fit_spectrum
# ==============================================================================

@dataclass
class _Param:
    name: str
    key: str                 # 'g', 'A_MHz', 'A_mT', 'fwhm_mT', 'dBpp_mT', 'g_strain', 'A_strain_MHz' or a callable's name
    nucleus: Optional[int]
    comp: Optional[int]      # index into the flattened spec, None for a scalar
    start: float
    half: float
    lo: float
    hi: float
    phys_lo: float = -math.inf   # physical limit: a range is never widened past it
    phys_hi: float = math.inf


_SUFFIX = {1: [""], 2: ["_perp", "_par"], 3: ["_x", "_y", "_z"]}
_VARY_KEYS = ("g", "A_MHz", "A_mT", "fwhm_mT", "dBpp_mT", "g_strain", "A_strain_MHz")


def _spec_params(name, key, nucleus, start_arr, half, positive):
    start_arr = np.atleast_1d(np.asarray(start_arr, float)).ravel()
    half = np.atleast_1d(np.asarray(half, float)).ravel()
    if start_arr.size == 9:
        raise ValueError(f"vary[{key!r}]: a full 3x3 tensor cannot be varied; give principal values and euler_deg")
    if half.size == 1 and start_arr.size > 1:
        half = np.full(start_arr.size, half[0])
    if half.size != start_arr.size:
        raise ValueError(f"vary[{key!r}] for {name}: {half.size} ranges for {start_arr.size} values "
                         f"(the start has {start_arr.tolist()})")
    out = []
    sfx = _SUFFIX.get(start_arr.size, [f"_{i}" for i in range(start_arr.size)])
    for i, (s, h) in enumerate(zip(start_arr, half)):
        if h < 0:
            raise ValueError(f"vary[{key!r}]: ranges are half-widths and must be >= 0")
        if h == 0:
            continue
        lo, hi = s - h, s + h
        plo = -math.inf
        if positive:
            plo = 1e-3 * max(abs(s), 1e-6) if s > 0 else 0.0
            lo = max(lo, plo)
        out.append(_Param(name=name + sfx[i], key=key, nucleus=nucleus,
                          comp=(None if start_arr.size == 1 else i), start=float(s), half=float(h),
                          lo=float(lo), hi=float(hi), phys_lo=float(plo)))
    return out


def _per_nucleus(rng, n_nuclei: int) -> list:
    """vary['A_MHz'] as one entry per nucleus; a single nucleus may be given
    without the outer list (vary={'A_MHz': [20, 50]}), and a scalar means
    that half-range for every component of every nucleus."""
    if np.ndim(rng) == 0:
        return [rng] * n_nuclei
    if n_nuclei == 1 and (np.ndim(rng) == 1 and rng[0] is not None
                          and not isinstance(rng[0], (list, tuple, np.ndarray))):
        return [rng]
    return list(rng)


def _g_ranges(g: np.ndarray, rng):
    """vary['g'] shaped like the start g. A scalar is every component; a
    list that does not match is accepted when it is unambiguous: equal
    values for a scalar g, [perp, par] for an axial g written as three
    values with x = y."""
    r = np.atleast_1d(np.asarray(rng, float)).ravel()
    gs = np.asarray(g, float)
    if gs.shape == (3, 3):
        raise ValueError("vary['g']: a full 3x3 g cannot be varied; give principal values")
    n = gs.size
    if r.size in (1, n):
        return r if r.size == n else r[0]
    if n == 1 and np.allclose(r, r[0]):
        return float(r[0])
    if n == 3 and r.size == 2 and np.isclose(gs[0], gs[1]):
        return np.array([r[0], r[0], r[1]])
    shape = {1: "a scalar", 2: "[perp, par]", 3: "[x, y, z]"}[n]
    raise ValueError(f"vary['g']: {r.size} ranges for a g given as {gs.tolist()}; give {shape} (or one "
                     f"number for every component)")


def _default_vary(start: "SpinSystem") -> dict:
    """vary when none is given (or vary='all'): g +-0.05 per component, A
    +-max(50%, 50 MHz) per component (in mT for a nucleus given in mT, 50 MHz
    converted at the mean g), the linewidth +-80%, strains that the start
    sets from 0 to 0.02 (g) or 0 to 2x their value (A, at least 20 MHz)."""
    v: dict = {"g": 0.05 if np.ndim(start.g) == 0 else [0.05] * np.size(start.g)}
    giso = float(np.trace(start.g_tensor()) / 3)
    for key in ("A_MHz", "A_mT"):
        ent = []
        for nuc in start.nuclei:
            if nuc.unit != key.split("_")[1]:
                ent.append(None)
                continue
            a = np.abs(np.asarray(nuc.A_spec, float))
            if a.shape == (3, 3):
                ent.append(None)
                continue
            floor = 50.0 if key == "A_MHz" else 50.0 / (giso * MUB_MHZ_PER_MT)
            ent.append(np.maximum(0.5 * a, floor).tolist() if a.ndim else max(0.5 * float(a), floor))
        if any(e is not None for e in ent):
            v[key] = ent
    wkey = "fwhm_mT" if start.fwhm_mT is not None else "dBpp_mT"
    w = np.asarray(getattr(start, wkey), float)
    v[wkey] = (0.8 * w).tolist() if w.ndim else 0.8 * float(w)
    if start.g_strain is not None:
        gsr = np.asarray(start.g_strain, float)
        # half-range centred to cover 0 to 0.02; [perp, par] when the start is axial
        gp = _principal(np.asarray(start.g, float)) if np.ndim(start.g) < 2 else None
        rng_ = [max(0.02 - x, x) for x in gsr]
        if gp is not None and abs(gp[0] - gp[1]) < 1e-12 and abs(gsr[0] - gsr[1]) < 1e-12:
            rng_ = [rng_[0], rng_[2]]
        v["g_strain"] = rng_
    if start.A_strain_MHz is not None:
        v["A_strain_MHz"] = [max(x, 20.0) for x in np.asarray(start.A_strain_MHz, float)]
    return v


def _system_params(start: SpinSystem, vary: dict) -> tuple[SpinSystem, list[_Param]]:
    params: list[_Param] = []
    labels = []
    for i, nuc in enumerate(start.nuclei):
        lab = nuc.name
        if lab in labels:
            lab = f"{lab}#{labels.count(nuc.name) + 1}"
        labels.append(nuc.name)
    for key, rng in vary.items():
        if key not in _VARY_KEYS:
            _bad_kw("fit_spectrum vary", {key: rng})
            raise ValueError(f"vary: unknown parameter {key!r}; use {', '.join(_VARY_KEYS)}")
    nuclei = list(start.nuclei)
    for key in ("A_MHz", "A_mT"):
        if key not in vary:
            continue
        rng = vary[key]
        rng = _per_nucleus(rng, len(nuclei))
        if len(rng) != len(nuclei):
            raise ValueError(f"vary[{key!r}]: one entry per nucleus ({len(nuclei)}), None for a fixed one")
        for i, r in enumerate(rng):
            if r is None:
                continue
            nuc = nuclei[i]
            if nuc.unit != key.split("_")[1]:
                # re-express the nucleus in the unit being varied, at the start g
                if key == "A_MHz":
                    A = start.A_tensor_MHz(i)
                    spec = nuc.A_spec
                    if spec.shape == (3, 3) or nuc.euler_deg is not None:
                        raise ValueError(f"vary[{key!r}]: nucleus {i} was given A_mT with a tilted or full tensor; "
                                         f"vary 'A_mT' instead")
                    if spec.ndim == 0:
                        val = float(A[0, 0])
                    else:
                        val = np.diag(A)[:spec.size] if spec.size == 3 else np.array([A[0, 0], A[2, 2]])
                    nuclei[i] = Nucleus(nuc.name, A_MHz=val, euler_deg=nuc.euler_deg, n=nuc.n)
                else:
                    raise ValueError(f"vary['A_mT']: nucleus {i} was given A_MHz; vary 'A_MHz'")
    start = start._replace(nuclei=nuclei)
    for key, rng in vary.items():
        if key == "g":
            params += _spec_params("g", "g", None, start.g, _g_ranges(start.g, rng), True)
        elif key in ("A_MHz", "A_mT"):
            rr = rng
            rr = _per_nucleus(rr, len(nuclei))
            for i, r in enumerate(rr):
                if r is None:
                    continue
                lab = labels[i]
                ps = _spec_params(f"{key}({lab})", key, i, nuclei[i].A_spec, r, False)
                for p_ in ps:            # the sign of A is not measured: keep A on its side of 0
                    if p_.start >= 0:
                        p_.phys_lo, p_.lo = 0.0, max(p_.lo, 0.0)
                    else:
                        p_.phys_hi, p_.hi = 0.0, min(p_.hi, 0.0)
                params += ps
        elif key in ("fwhm_mT", "dBpp_mT"):
            cur = getattr(start, key)
            if cur is None:
                other = "dBpp_mT" if key == "fwhm_mT" else "fwhm_mT"
                raise ValueError(f"vary[{key!r}]: the start system gives {other}; vary {other!r}")
            nm = key if start.shape != "voigt" else key
            ps = _spec_params(nm, key, None, cur, rng, True)
            if start.shape == "voigt":
                for p_ in ps:
                    p_.name = key + ("_gauss" if p_.comp == 0 else "_lorentz")
            params += ps
        elif key in ("g_strain", "A_strain_MHz"):
            cur = getattr(start, key)
            if cur is None:
                cur = np.zeros(3)
            axial = np.size(rng) == 2        # [perp, par], as for an axial g
            if np.ndim(rng) == 0:            # one isotropic strain parameter
                cur = float(np.mean(cur))
            elif axial:
                cur = np.asarray(cur, float).ravel()
                if not np.isclose(cur[0], cur[1]):
                    raise ValueError(f"vary[{key!r}]: two ranges mean [perp, par], but the start's x and y "
                                     f"strains differ ({cur.tolist()}); give three ranges")
                cur = np.array([cur[0], cur[2]])
            ps = _spec_params(key, key, None, cur, rng, False)
            for p_ in ps:
                p_.lo = max(p_.lo, 0.0)
                p_.phys_lo = 0.0
                if axial:                    # perp sets x and y, par sets z
                    p_.comp = -1 if p_.comp == 0 else 2
            params += ps
    if not params:
        raise ValueError("vary: nothing to fit (all ranges are 0 or None)")
    return start, params


def _apply(start: SpinSystem, params: list[_Param], theta: np.ndarray) -> SpinSystem:
    g = np.array(start.g, float)
    nuclei = list(start.nuclei)
    A_new = {i: np.array(n.A_spec, float) for i, n in enumerate(nuclei)}
    widths: dict = {}
    strains: dict = {}
    for p, v in zip(params, theta):
        if p.key == "g":
            if p.comp is None:
                g = np.array(v)
            else:
                g = g.copy()
                g.flat[p.comp] = v
        elif p.key in ("A_MHz", "A_mT"):
            a = A_new[p.nucleus]
            if p.comp is None:
                A_new[p.nucleus] = np.array(v)
            else:
                a = a.copy()
                a.flat[p.comp] = v
                A_new[p.nucleus] = a
        elif p.key in ("fwhm_mT", "dBpp_mT"):
            cur = np.atleast_1d(np.array(getattr(start, p.key), float)) if p.key not in widths else widths[p.key]
            cur = cur.copy()
            cur[p.comp or 0] = v
            widths[p.key] = cur
        else:
            cur = strains.get(p.key)
            if cur is None:
                base = getattr(start, p.key)
                cur = np.zeros(3) if base is None else np.array(base, float)
            cur = cur.copy()
            if p.comp is None:
                cur[:] = v
            elif p.comp == -1:               # axial perp strain: x and y together
                cur[0] = cur[1] = v
            else:
                cur.flat[p.comp] = v
            strains[p.key] = cur
    nuclei = [nuc._replace(A=A_new[i]) for i, nuc in enumerate(nuclei)]
    ch: dict = {"g": g, "nuclei": nuclei}
    for k, w in widths.items():
        ch[k] = float(w[0]) if w.size == 1 else tuple(w.tolist())
    ch.update(strains)
    return start._replace(**ch)


def _runs_z(r: np.ndarray) -> float:
    """Wald-Wolfowitz runs test on the residual signs: z < -3 means far fewer
    sign changes than noise gives (structure left in the residual)."""
    s = np.sign(r[r != 0])
    n1, n2 = int(np.sum(s > 0)), int(np.sum(s < 0))
    n = n1 + n2
    if n1 == 0 or n2 == 0 or n < 20:
        return 0.0
    runs = 1 + int(np.sum(s[1:] != s[:-1]))
    mu = 2.0 * n1 * n2 / n + 1
    var = (mu - 1) * (mu - 2) / (n - 1)
    return (runs - mu) / math.sqrt(var) if var > 0 else 0.0


class _OutOfTime(Exception):
    pass


@dataclass
class SpectrumFit(FieldHelp):
    model: str
    params: dict                  # fitted (varied) parameters
    se: dict
    ci: dict                      # 95% CI from the Jacobian
    fit: np.ndarray               # the fitted spectrum (scale and baseline included)
    residual: np.ndarray
    residual_rms: float
    warnings: list
    notes: list
    system: Optional[SpinSystem]  # the fitted SpinSystem (model "iso"/"powder")
    scale: float
    baseline: np.ndarray          # polynomial coefficients (Legendre, field mapped to [-1, 1])
    correlation: dict             # {(a, b): r} for |r| > 0.5
    starts: list                  # [{"cost": ..., "params": {...}}] per start
    n_eval: int
    elapsed_s: float
    stopped_early: bool

    def to_dict(self) -> dict:
        return {"model": self.model, "params": dict(self.params), "se": dict(self.se),
                "ci": {k: list(v) for k, v in self.ci.items()}, "residual_rms": self.residual_rms,
                "warnings": list(self.warnings), "system": repr(self.system) if self.system else None}

    def __repr__(self):
        out = [f"fit_spectrum ({self.model}): {len(self.params)} parameters, residual rms "
               f"{self.residual_rms:.4g}, {self.n_eval} simulations in {self.elapsed_s:.1f} s"]
        for k, v in self.params.items():
            se = self.se.get(k)
            ci = self.ci.get(k)
            row = f"  {k:<22}{v:>14.7g}"
            if se is not None and np.isfinite(se):
                row += f"  SE {se:<10.3g} 95% CI [{ci[0]:.6g}, {ci[1]:.6g}]"
            if k.startswith("A_MHz"):
                row += f"   (= {convert(abs(v), 'MHz', '1e-4 cm-1'):.4g} x 1e-4 cm-1)"
            out.append(row)
        if self.system is not None:
            out.append(f"  system: {self.system!r}")
        out.append(f"  scale {self.scale:.5g}, baseline coefficients {np.round(self.baseline, 6).tolist()}")
        for n in self.notes:
            out.append(f"  note: {n}")
        if self.warnings:
            out.append("  WARNINGS (report them): " + " | ".join(self.warnings))
        return "\n".join(out)

    __str__ = __repr__


def _iso_nuclei_mixture(nuclei, g: float) -> list[tuple[float, list]]:
    """simulate_iso's nuclei given as Nucleus objects (isotropic A) and/or
    (I, a_mT, n) tuples -> [(weight, [(I, a_mT, n)])] over isotopologues."""
    objs = [n for n in nuclei if isinstance(n, Nucleus)]
    tuples = [tuple(n) for n in nuclei if not isinstance(n, Nucleus)]
    sys = SpinSystem(g=g, nuclei=objs, fwhm_mT=1.0)
    for i in range(len(objs)):
        A = sys.A_tensor_MHz(i)
        if not np.allclose(A, A[0, 0] * np.eye(3), atol=1e-9 * max(1.0, abs(A[0, 0]))):
            raise ValueError(f"simulate_iso: {objs[i]!r} is anisotropic; use simulate_powder")
    out = []
    isos = _isotopologues(sys)
    wsum = sum(w for w, _ in isos)
    for w, groups in isos:
        w = w / wsum
        out.append((w, tuples + [(gr.I, gr.A[0, 0] / (g * MUB_MHZ_PER_MT), gr.n) for gr in groups]))
    return out


def _iso_model(sys: SpinSystem, x, freq_GHz, harmonic, modamp_mT, exact: bool = True):
    """Isotropic spectrum of a SpinSystem through simulate_iso, summed over
    isotopologues, normalised to unit absorption area."""
    if not sys.is_isotropic():
        raise ValueError("model='iso' needs isotropic g and A (scalars); use model='powder'")
    if sys.g_strain is not None or sys.A_strain_MHz is not None:
        raise ValueError("model='iso' has no strain; use model='powder' or drop g_strain/A_strain_MHz")
    g = float(sys.g_tensor()[0, 0])
    gw, lw = sys.widths()
    if sys.shape == "gaussian":
        kw = dict(lw_mT=gw / math.sqrt(2 * math.log(2)), shape="gaussian")
    elif sys.shape == "lorentzian":
        kw = dict(lw_mT=lw / math.sqrt(3), shape="lorentzian")
    else:
        kw = dict(lw_mT=lw / math.sqrt(3), shape="lorentzian", lw_gauss_mT=gw / math.sqrt(2 * math.log(2)))
    from . import epr as _epr
    y = 0.0
    isos = _isotopologues(sys)
    wsum = sum(w for w, _ in isos)
    # modulation quadrature: 256 points are needed for over-modulation only;
    # for a modulation well under the linewidth 24 are exact to 1e-6
    lw_min = min([w_ for w_ in (gw, lw) if w_ > 0] or [1.0])
    n_theta = 256 if (modamp_mT or 0) > 0.5 * lw_min else 24
    old = _epr._ISO_MOD_THETA
    _epr._ISO_MOD_THETA = n_theta
    try:
        y = _iso_sum(sys, x, freq_GHz, harmonic, modamp_mT, exact, isos, wsum, g, kw)
    finally:
        _epr._ISO_MOD_THETA = old
    return y


def _iso_sum(sys, x, freq_GHz, harmonic, modamp_mT, exact, isos, wsum, g, kw):
    y = 0.0
    for w, groups in isos:
        w = w / wsum
        nuc = [(gr.I, gr.A[0, 0] / (g * MUB_MHZ_PER_MT), gr.n) for gr in groups]
        y = y + w * simulate_iso(x, freq_GHz, g, nuclei=nuc, harmonic=harmonic, modamp_mT=modamp_mT,
                                 exact=exact, **kw)
    return y


def fit_spectrum(field_mT, y, *, model="powder", start=None, vary: Optional[dict] = None,
                 freq_GHz: Optional[float] = None, harmonic: int = 1, modamp_mT: Optional[float] = None,
                 baseline_order: int = 0, n_starts: Optional[int] = None, max_time_s: float = 20.0,
                 method: str = "hybrid", n_orient: Optional[int] = None, seed: int = 0) -> SpectrumFit:
    """Least-squares fit of a simulation to a CW spectrum (following
    EasySpin's esfit conventions: start values plus ranges).

        r = epr.fit_spectrum(B, y, model="powder", start=sys, freq_GHz=9.4,
                             vary={"g": [0.02, 0.05], "A_MHz": [[0, 50]], "fwhm_mT": 0.5})
        print(r); r.params, r.ci, r.system

    model: "powder" (simulate_powder), "iso" (simulate_iso with exact
    Breit-Rabi positions; isotropic g and A) or a callable f(field_mT,
    **params) with `start` a dict of values. vary: half-ranges around the
    start, {"g": ..., "A_MHz": [per nucleus, None if fixed], "A_mT": ...,
    "fwhm_mT"/"dBpp_mT": ..., "g_strain": ..., "A_strain_MHz": ...}, shaped
    like the start values (0 keeps a component fixed). Bounds are start +-
    range. vary left out (or vary="all"): g +-0.05 per component, A
    +-max(50%, 50 MHz), the linewidth +-80%, strains the start sets 0 to
    0.02. A flat list is fine for one nucleus, and a scalar means every
    component. A parameter that ends within 2% of its range edge gets a
    range reaching past that edge (twice the old width ahead of the value,
    then four times) and the fit is redone, at most twice; the result's notes say so, and a
    parameter still at an edge is warned about ("ended at its bound").
    The scale and a polynomial baseline of `baseline_order` are solved
    linearly at every step. The first start is `start`; n_starts - 1 (default
    8 starts in all) more are drawn inside the ranges (seeded, Latin
    hypercube). Powder fits run in stages: (0) a scan with plain
    second-order perturbation on a coarse grid (15 Latin-hypercube points per
    parameter, plus two cycles of one-parameter scans from `start`), whose
    best points become the starts; (1) a few optimiser steps from every
    start on that cheap simulation; (2) the best one (two if time allows)
    refined with the full method. The fit stops at max_time_s and returns
    the best so far, with a warning; for long fits pass run_python a
    timeout_s above max_time_s.

    CIs (95%) come from the Jacobian at the optimum. Warnings: a parameter at
    its bound, two parameters correlated above 0.95, starts that end in
    different minima, structure in the residual (runs test), early stop, and
    the simulation's own warnings at the optimum."""
    t0 = time.monotonic()
    x = np.asarray(field_mT, float).ravel()
    yy = np.real(np.asarray(y)).astype(float).ravel()
    if x.size != yy.size:
        raise ValueError(f"field_mT has {x.size} points and y {yy.size}")
    callable_model = callable(model) and not isinstance(model, str)
    if callable_model and (vary is None or isinstance(vary, str) or not vary):
        raise ValueError("vary: a callable model needs the parameters to fit with half-ranges, "
                         "e.g. vary={'b0': 0.5, 'w': 0.2}")
    if not callable_model and (vary is None or (isinstance(vary, str) and vary.lower() == "all")):
        if not isinstance(start, SpinSystem):
            raise TypeError("start: an epr.SpinSystem(...) with the starting values")
        vary = _default_vary(start)
    elif isinstance(vary, str):
        raise ValueError(f"vary={vary!r}: give a dict of half-ranges, or vary='all' (or leave it out) for the "
                         f"default ranges")
    simulate_quick: Optional[Callable] = None     # cheap first-stage simulation (powder)
    notes: list[str] = []
    warns: list[str] = []
    if callable(model) and not isinstance(model, str):
        if not isinstance(start, dict):
            raise TypeError("a callable model takes start= as a dict of parameter values")
        params = []
        for k, h in vary.items():
            if k not in start:
                raise ValueError(f"vary[{k!r}] is not in start ({', '.join(start)})")
            params += _spec_params(k, k, None, start[k], h, False)
        base = dict(start)

        def simulate(theta):
            p = dict(base)
            for q, v in zip(params, theta):
                p[q.name] = v
            return np.asarray(model(x, **p), float)
        model_name = getattr(model, "__name__", "callable")
        start_sys = None
    else:
        if model not in ("powder", "iso"):
            raise ValueError("model: 'powder', 'iso' or a callable f(field_mT, **params) "
                             "('slow' for slow motion is slice 2, not built yet)")
        if not isinstance(start, SpinSystem):
            raise TypeError("start: an epr.SpinSystem(...) with the starting values")
        if freq_GHz is None:
            raise ValueError("freq_GHz: the measured microwave frequency (MWFQ in a BES3T file, in Hz; d.freq_GHz)")
        start_sys, params = _system_params(start, vary)
        xs, xu, rev, uniform = _uniform(x)
        notes += start_sys.notes()
        if any(p.key in ("A_MHz", "A_mT") for p in params):
            notes.append("the sign of A is not determined by a CW spectrum; report |A|")

        if model == "powder":
            n_fit = n_orient or (1500 if method != "exact" else 600)
            grid: dict = {"ratio": 1.05}
            grid_q: dict = {"ratio": 1.05}

            def simulate(theta):
                s = _apply(start_sys, params, theta)
                yv = _simulate(s, xu, float(freq_GHz), harmonic, modamp_mT, method, n_fit, 1.0,
                               max_points=3e5, grid=grid, corr_coarse=_FIT_CORR[0])
                return _on_user_axis(yv, xs, xu, rev, uniform)

            if method != "exact":
                # First stage of the multistart: plain second-order
                # perturbation on a coarse grid, about 10x cheaper. Its line
                # positions are off by up to a few tenths of a mT for Cu, which
                # is enough to find the basin; the second stage refines with
                # the full method.
                def _quick(theta):
                    s = _apply(start_sys, params, theta)
                    yv = _simulate(s, xu, float(freq_GHz), harmonic, modamp_mT, "perturb", 400, 1.0,
                                   max_points=5e4, grid=grid_q, strain_axial=True)
                    return _on_user_axis(yv, xs, xu, rev, uniform)
                simulate_quick = _quick
        else:
            if not start_sys.is_isotropic():
                raise ValueError("model='iso' needs isotropic g and A (scalars); use model='powder'")

            def simulate(theta):
                s = _apply(start_sys, params, theta)
                return _iso_model(s, x, float(freq_GHz), harmonic, modamp_mT)
        model_name = model

    k_par = len(params)
    lo = np.array([p.lo for p in params])
    hi = np.array([p.hi for p in params])
    th0 = np.array([p.start for p in params])
    h = np.array([p.half for p in params])
    ulo, uhi = (lo - th0) / h, (hi - th0) / h
    xn = (x - x.min()) / max(np.ptp(x), 1e-300) * 2 - 1
    basis = [np.polynomial.legendre.legval(xn, np.eye(baseline_order + 1)[j]) for j in range(baseline_order + 1)]
    Bmat = np.array(basis).T if baseline_order >= 0 else np.zeros((x.size, 0))
    yscale = float(np.std(yy)) or 1.0

    state = {"n": 0, "best": None, "best_quick": None}

    def linear(s):
        D = np.column_stack([s, Bmat])
        c, *_ = np.linalg.lstsq(D, yy, rcond=None)
        return c, D @ c

    # The starts stop at 80% of max_time_s, leaving time for the Jacobian
    # and the simulation check at the optimum.
    budget = 0.8 * max_time_s

    def make_resid(sim, key, limit):
        """Residual function that stops (raises _OutOfTime) after the
        monotonic time `limit`."""
        def resid(u):
            if time.monotonic() > limit:
                raise _OutOfTime
            s = sim(th0 + u * h)
            state["n"] += 1
            c, f = linear(s)
            r = (yy - f) / yscale
            cost = float(r @ r)
            if state[key] is None or cost < state[key][0]:
                state[key] = (cost, u.copy())
            return r

        return resid

    if n_starts is None:
        n_starts = 8
    rng = np.random.default_rng(seed)

    def qcost_of(sim):
        def qcost(uc):
            sv = sim(th0 + uc * h)
            state["n"] += 1
            _, fv = linear(sv)
            return float(np.sum((yy - fv) ** 2))
        return qcost

    auto = None
    if model_name == "powder" and start_sys is not None and max_time_s >= 5:
        auto = _auto_start(start_sys, params, x, yy, float(freq_GHz), modamp_mT)

    # pairs (g component, A component) on the same principal axis
    gA_pairs = []
    for i_g, pg in enumerate(params):
        if pg.key != "g":
            continue
        for i_a, pa in enumerate(params):
            if pa.key in ("A_MHz", "A_mT") and pa.comp == pg.comp and pg.comp is not None:
                gA_pairs.append((i_g, i_a))
    gA_pairs.sort(key=lambda t: -abs(params[t[1]].start))

    def search(blo, bhi, u_first, t_mid, t_end, n_starts_, scan_per, cycles):
        """One multistart search inside the box [blo, bhi] (u units): the
        scans and the cheap first stage by t_mid, the refinement by t_end.
        Returns (results, stage-one results, stopped, number of starts)."""
        t_s = time.monotonic()
        span = max(t_mid - t_s, 1e-3)
        k = k_par
        starts = [np.clip(u_first, blo, bhi)]
        if auto:
            # a start read off the spectrum (powder_starts), when it is inside the box
            ue = starts[0].copy()
            inside = True
            for j, v in auto.items():
                ue[j] = (v - th0[j]) / h[j]
                inside &= blo[j] <= ue[j] <= bhi[j]
            if inside:
                starts.append(ue)
        n_rand = max(0, int(n_starts_) - 1)
        quick = simulate_quick if simulate_quick is not None else simulate
        staged = simulate_quick is not None or model_name == "iso"
        if n_rand and staged:
            # Stage 0: score a Latin hypercube (scan_per points per
            # parameter) and run one-parameter scans from the first start
            # (each parameter in turn set to the best of 15 values across
            # its range). Spectra with hyperfine ladders have many local
            # minima (a ladder assigned one line off); the scans find the
            # valleys (g_perp, the g_par/A_par combination), the optimiser
            # then follows them.
            qcost = qcost_of(quick)
            n_scan = scan_per * k
            lhs = np.stack([(rng.permutation(n_scan) + rng.random(n_scan)) / n_scan for _ in range(k)], axis=1)
            cand = blo + (bhi - blo) * (0.02 + 0.96 * lhs)
            scores = []
            for uc in cand:
                if time.monotonic() - t_s > 0.2 * span:
                    break
                scores.append(qcost(uc))
            uc = starts[0].copy()
            best_c = qcost(uc)

            def scan_1d(uc, best_c):
                for j in range(k):
                    # g is the sharpest parameter of a powder spectrum (its
                    # basin is a few thousandths wide): scan it finer
                    for v in np.linspace(blo[j], bhi[j], 41 if params[j].key == "g" else 21):
                        if time.monotonic() - t_s > 0.55 * span:
                            raise _OutOfTime
                        trial = uc.copy()
                        trial[j] = v
                        cst = qcost(trial)
                        if cst < best_c:
                            best_c, uc = cst, trial
                return uc, best_c

            def scan_2d(uc, best_c):
                # g and A on the same axis together: the g_par/A_par valley of
                # a hyperfine ladder is diagonal, which one-parameter scans
                # cross only slowly
                for n_pair, (i_g, i_a) in enumerate(gA_pairs):
                    base = uc.copy()
                    m = 31 if n_pair == 0 else 13          # the largest coupling first, finest
                    for vg in np.linspace(blo[i_g], bhi[i_g], m):
                        for va in np.linspace(blo[i_a], bhi[i_a], m):
                            if time.monotonic() - t_s > 0.55 * span:
                                raise _OutOfTime
                            trial = base.copy()
                            trial[i_g], trial[i_a] = vg, va
                            cst = qcost(trial)
                            if cst < best_c:
                                best_c, uc = cst, trial
                return uc, best_c
            try:
                for _cycle in range(cycles):
                    uc, best_c = scan_1d(uc, best_c)
                    uc, best_c = scan_2d(uc, best_c)
            except _OutOfTime:
                pass
            starts.append(uc)
            for i in np.argsort(scores)[:max(0, n_rand - 1)]:
                starts.append(cand[i])
        elif n_rand:
            lhs = np.stack([(rng.permutation(n_rand) + rng.random(n_rand)) / n_rand for _ in range(k)], axis=1)
            for row in lhs:
                starts.append(blo + (bhi - blo) * (0.1 + 0.8 * row))
        results_, stopped_ = [], False
        resid_full = make_resid(simulate, "best", t_end)
        if simulate_quick is not None:
            # Two stages: a few optimiser steps from every start on the cheap
            # simulation, then the best one (two if time allows) refined on
            # the full one. The start comparison (several minima) uses stage one.
            resid_q = make_resid(simulate_quick, "best_quick", t_s + span)
            stage1 = []
            for u0 in starts:
                try:
                    r = optimize.least_squares(resid_q, u0, bounds=(blo, bhi), method="trf", x_scale=1.0,
                                               diff_step=1e-4, max_nfev=10, xtol=1e-5, ftol=1e-6)
                    stage1.append({"u": r.x, "cost": float(2 * r.cost), "jac": None})
                except _OutOfTime:
                    # the cheap stage has its own share of the time; running out
                    # of it is not an early stop of the fit (the refinement follows)
                    break
            if not stage1 and state["best_quick"] is not None:
                stage1.append({"u": state["best_quick"][1], "cost": state["best_quick"][0], "jac": None})
            cands = sorted(stage1, key=lambda d: d["cost"])
            picked = cands[:1]
            for d in cands[1:]:
                if d["cost"] <= 1.3 * cands[0]["cost"] and np.max(np.abs(d["u"] - cands[0]["u"])) > 0.1:
                    picked.append(d)
                    break
            for i_p, d in enumerate(picked):
                if i_p and time.monotonic() > t_s + 0.5 * (t_end - t_s):
                    break
                try:
                    r = optimize.least_squares(resid_full, d["u"], bounds=(blo, bhi), method="trf", x_scale=1.0,
                                               diff_step=1e-4, max_nfev=40, xtol=1e-6, ftol=1e-8)
                    results_.append({"u": r.x, "cost": float(2 * r.cost), "jac": r.jac})
                except _OutOfTime:
                    stopped_ = True
                    break
            return results_, stage1, stopped_, len(starts)
        for u0 in starts:
            try:
                r = optimize.least_squares(resid_full, u0, bounds=(blo, bhi), method="trf", x_scale=1.0,
                                           diff_step=1e-4, max_nfev=60 * (k + 1), xtol=1e-8, ftol=1e-10)
                results_.append({"u": r.x, "cost": float(2 * r.cost), "jac": r.jac})
            except _OutOfTime:
                stopped_ = True
                break
        return results_, results_, stopped_, len(starts)

    def pick_best(results_):
        if results_:
            b = min(results_, key=lambda d: d["cost"])
            if state["best"] is not None and state["best"][0] < b["cost"] * (1 - 1e-9):
                b = {"u": state["best"][1], "cost": state["best"][0], "jac": None}
            return b
        if state["best"] is None:
            if state["best_quick"] is None:
                # nothing finished in time: report the start itself
                make_resid(simulate, "best", math.inf)(np.clip(np.zeros(k_par), ulo, uhi))
            else:
                state["best"] = state["best_quick"]
        return {"u": state["best"][1], "cost": state["best"][0], "jac": None}

    ulo0, uhi0 = ulo.copy(), uhi.copy()
    plo_u = np.array([(p.phys_lo - p.start) / p.half for p in params])
    phi_u = np.array([(p.phys_hi - p.start) / p.half for p in params])
    widen_log = []
    if auto:
        # The spectrum's own estimate (powder_starts) outside a range moves
        # that range to include it: a start far from the spectrum is the
        # usual reason a powder fit ends in the wrong basin.
        for j, v in auto.items():
            ue = (v - th0[j]) / h[j]
            if not (ulo[j] <= ue <= uhi[j]):
                old = (th0[j] + ulo[j] * h[j], th0[j] + uhi[j] * h[j])
                margin = 0.25 * (uhi0[j] - ulo0[j])
                ulo[j] = max(min(ulo[j], ue - margin), plo_u[j])
                uhi[j] = min(max(uhi[j], ue + margin), phi_u[j])
                widen_log.append(f"{params[j].name}: the range [{old[0]:.5g}, {old[1]:.5g}] did not contain the "
                                 f"value read off the spectrum ({v:.5g}, powder_starts); used "
                                 f"[{th0[j] + ulo[j] * h[j]:.5g}, {th0[j] + uhi[j] * h[j]:.5g}]")
    # The first search's scans and cheap stage get 45% of the budget, so that
    # a widening (below) still has time; its refinement may use all of it.
    results, ms_results, stopped, n_started = search(ulo, uhi, np.zeros(k_par), t0 + 0.45 * budget,
                                                     t0 + budget, n_starts, 15, 2)
    best = pick_best(results)

    # Bounds that widen: a parameter that ends within 2% of its range edge
    # (not a physical limit) gets a range that reaches past that edge (the
    # old half-width behind the value, twice the old full width ahead of it,
    # then four times), and the fit is redone from there with the same
    # multistart (at most two times, inside max_time_s).
    def at_edge(u_, blo, bhi):
        w = bhi - blo
        lo_e = (u_ - blo < 0.02 * w) & (blo > plo_u + 1e-9)
        hi_e = (bhi - u_ < 0.02 * w) & (bhi < phi_u - 1e-9)
        return lo_e | hi_e

    for round_ in (1, 2):
        edge = at_edge(best["u"], ulo, uhi)
        if not np.any(edge) or time.monotonic() - t0 > 0.85 * budget:
            break
        nlo, nhi = ulo.copy(), uhi.copy()
        for j in np.nonzero(edge)[0]:
            # extend past the edge that was hit: the old half-width behind
            # the value, 2x (then 4x) the full width ahead of it
            half0 = (uhi0[j] - ulo0[j]) / 2
            ahead = 2 * half0 * 2 ** round_
            if best["u"][j] - ulo[j] < uhi[j] - best["u"][j]:     # lower edge
                nlo[j], nhi[j] = best["u"][j] - ahead, best["u"][j] + half0
            else:
                nlo[j], nhi[j] = best["u"][j] - half0, best["u"][j] + ahead
            nlo[j], nhi[j] = max(nlo[j], plo_u[j]), min(nhi[j], phi_u[j])
            p_ = params[j]
            widen_log.append(f"{p_.name} ended at the edge of [{th0[j] + ulo[j] * h[j]:.5g}, "
                             f"{th0[j] + uhi[j] * h[j]:.5g}]; refitted with [{th0[j] + nlo[j] * h[j]:.5g}, "
                             f"{th0[j] + nhi[j] * h[j]:.5g}]")
        ulo, uhi = nlo, nhi
        t_left = t0 + budget - time.monotonic()
        t_round = time.monotonic() + (t_left / 2 if round_ == 1 else t_left)
        r2, ms2, st2, n2 = search(ulo, uhi, best["u"], time.monotonic() + 0.6 * (t_round - time.monotonic()),
                                  t_round, n_starts, 15, 2)
        # a round's own deadline is not the fit's: only report an early stop
        # when the whole budget is used up
        stopped = stopped or (st2 and time.monotonic() > t0 + 0.95 * budget)
        if r2 or state["best"] is not None:
            best = pick_best(r2 + ([best] if best.get("jac") is not None else []))
        results = r2 or results
        if ms2:
            ms_results, n_started = ms2, n2
    for p_, (lo_u, hi_u) in zip(params, zip(ulo, uhi)):
        p_.lo, p_.hi = float(p_.start + lo_u * p_.half), float(p_.start + hi_u * p_.half)
    if widen_log:
        notes.append("ranges moved by fit_spectrum (a parameter at the edge of its range, or a range that "
                     "missed the value read off the spectrum): " + "; ".join(widen_log))
    if stopped:
        warns.append(f"stopped at max_time_s={max_time_s:g} s after {state['n']} simulations "
                     f"({len(ms_results)} of {n_started} starts finished): the best fit so far is reported; "
                     f"raise max_time_s (and run_python's timeout_s) or narrow the ranges")
    u = best["u"]
    theta = th0 + u * h
    s = simulate(theta)
    c, fitv = linear(s)
    res = yy - fitv
    n = yy.size
    dof = max(n - k_par - Bmat.shape[1] - 1, 1)
    rms = float(np.sqrt(np.mean(res ** 2)))
    J = best["jac"]
    if J is None:                  # stopped early: one-sided differences, one simulation each
        J = np.empty((n, k_par))
        r0 = resid_free(simulate, linear, yy, th0, h, u, yscale)
        for j in range(k_par):
            up = u.copy()
            step = 1e-4 if u[j] + 1e-4 <= uhi[j] else -1e-4
            up[j] = u[j] + step
            J[:, j] = (resid_free(simulate, linear, yy, th0, h, up, yscale) - r0) / step
    J = np.asarray(J, float) * yscale                       # d residual / d u, data units
    s2 = float(res @ res) / dof
    try:
        cov_u = np.linalg.pinv(J.T @ J) * s2
    except np.linalg.LinAlgError:
        cov_u = np.full((k_par, k_par), np.nan)
    cov = cov_u * np.outer(h, h)
    se_arr = np.sqrt(np.maximum(np.diag(cov), 0))
    tq = stats.t.ppf(0.975, dof)
    names = [p.name for p in params]
    pv = {nm: float(v) for nm, v in zip(names, theta)}
    se = {nm: float(v) for nm, v in zip(names, se_arr)}
    ci = {nm: (float(v - tq * e), float(v + tq * e)) for nm, v, e in zip(names, theta, se_arr)}
    corr = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        R = cov / np.outer(se_arr, se_arr)
    for i in range(k_par):
        for j in range(i + 1, k_par):
            if np.isfinite(R[i, j]) and abs(R[i, j]) > 0.5:
                corr[(names[i], names[j])] = float(R[i, j])
            if np.isfinite(R[i, j]) and abs(R[i, j]) > 0.95:
                warns.append(f"{names[i]} and {names[j]} are correlated (r = {R[i, j]:+.3f}): the spectrum does "
                             f"not separate them; their CIs are wide, consider fixing one")
    for j, p in enumerate(params):
        at_lo = abs(u[j] - ulo[j]) < 1e-3
        at_hi = abs(u[j] - uhi[j]) < 1e-3
        physical = (at_lo and ulo[j] <= plo_u[j] + 1e-9) or (at_hi and uhi[j] >= phi_u[j] - 1e-9)
        if physical:
            notes.append(f"{p.name} = {theta[j]:.6g} is at its physical limit (no CI on that side)")
        elif at_lo or at_hi:
            warns.append(f"{p.name} = {theta[j]:.6g} ended at its bound [{p.lo:.6g}, {p.hi:.6g}] after "
                         f"{len(widen_log)} automatic range move(s): start it from an estimate (guide('epr-sim')) "
                         f"or widen its range in vary; do not report this value or a typical one; its CI is "
                         f"not meaningful")
    # multistart agreement (on the first stage when there are two)
    if len(ms_results) > 1:
        ms_best = min(ms_results, key=lambda d: d["cost"])
        bc = ms_best["cost"]
        u_ms = ms_best["u"]
        others = [d for d in ms_results if d is not ms_best]
        worse = [d for d in others if d["cost"] > bc * 1.05 + 1e-12]
        twins = [d for d in others if d["cost"] <= bc * 1.05 + 1e-12
                 and np.any(np.abs((d["u"] - u_ms) * h) > np.maximum(3 * se_arr, 0.05 * h))]
        if twins:
            d = twins[0]
            diff = ", ".join(f"{nm} {th0[j] + d['u'][j] * h[j]:.5g}" for j, nm in enumerate(names)
                             if abs((d["u"][j] - u_ms[j]) * h[j]) > max(3 * se_arr[j], 0.05 * h[j]))
            warns.append(f"another start fits about as well (cost within 5%) with different values ({diff}): "
                         f"more than one minimum; the data may not decide between them")
        first = ms_results[0]
        if first is not ms_best and first["cost"] > bc * 1.05 + 1e-12:
            if simulate_quick is not None:
                warns.append(f"the given start did not lead to the best fit (after the first stage its cost was "
                             f"{first['cost'] / max(bc, 1e-300):.3g}x the best); the reported fit came from the "
                             f"parameter scan or another start. Powder fits have several minima: check the result "
                             f"against the spectrum")
            else:
                warns.append(f"the given start ended in a local minimum (cost {first['cost'] / max(bc, 1e-300):.3g}x "
                             f"the best); a random start inside the ranges found the reported fit "
                             f"({len(worse)} of {len(ms_results)} starts ended in worse minima). The fit has several "
                             f"minima: check the result against the spectrum")
    z = _runs_z(res)
    if z < -3 and rms > 1e-9 * (np.ptp(yy) or 1.0):
        extra = ""
        if model_name == "powder" and start_sys is not None and start_sys.g_strain is None \
                and np.ptp(_principal(np.asarray(start_sys.g, float)) if np.ndim(start_sys.g) < 2 else [0]) > 0.05:
            extra = ("; frozen metal spectra usually need g strain (the parallel lines are broader): add "
                     "g_strain=[0.004, 0.004, 0.01] to the SpinSystem and vary it")
        warns.append(f"the residual has structure (runs test z = {z:.1f}: long stretches of one sign): the "
                     f"model misses features of the spectrum (wrong model, a missing nucleus or species, "
                     f"slow motion in a powder fit, or a baseline){extra}")
    fit_sys = None
    if start_sys is not None:
        fit_sys = _apply(start_sys, params, theta)
        if model_name == "powder" and time.monotonic() - t0 > max_time_s:
            notes.append("the simulation checks at the optimum (grid, perturbation error) were skipped: "
                         "max_time_s was used up; run simulate_powder(r.system, ...) to see them")
        elif model_name == "powder":
            try:
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    _, sinfo = simulate_powder(fit_sys, x, float(freq_GHz), harmonic=harmonic,
                                               modamp_mT=modamp_mT, method=method, n_orient=n_orient,
                                               return_info=True)
                warns += [f"simulation at the optimum: {w}" for w in sinfo["warnings"] if w not in notes]
            except ValueError as e:  # pragma: no cover
                warns.append(f"simulation check failed: {e}")
    res_out = SpectrumFit(model=model_name, params=pv, se=se, ci=ci, fit=fitv, residual=res, residual_rms=rms,
                          warnings=warns, notes=notes, system=fit_sys, scale=float(c[0]),
                          baseline=np.asarray(c[1:], float), correlation=corr,
                          starts=[{"cost": d["cost"], "params": {nm: float(th0[j] + d["u"][j] * h[j])
                                                                 for j, nm in enumerate(names)}} for d in ms_results],
                          n_eval=state["n"], elapsed_s=time.monotonic() - t0, stopped_early=stopped)
    return res_out


def resid_free(simulate, linear, yy, th0, h, u, yscale):
    s = simulate(th0 + u * h)
    _, f = linear(s)
    return (yy - f) / yscale
