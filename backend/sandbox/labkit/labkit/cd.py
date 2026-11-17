"""Circular dichroism conversions with the units written out.

    from labkit import cd
    mre = cd.mean_residue_ellipticity(theta_mdeg=-28.06, mw_da=14300, n_residues=129,
                                      conc_mg_ml=0.2, path_cm=0.1)
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def mean_residue_weight(mw_da: float, n_residues: int) -> float:
    """MRW = MW / (N - 1): N residues have N - 1 peptide bonds."""
    if n_residues < 2:
        raise ValueError("n_residues must be at least 2")
    return mw_da / (n_residues - 1)


def mean_residue_ellipticity(theta_mdeg, *, conc_mg_ml: float, path_cm: float,
                             mw_da: Optional[float] = None, n_residues: Optional[int] = None,
                             mrw: Optional[float] = None):
    """[theta]_MRW in deg cm^2 dmol^-1 = theta(mdeg) * MRW / (10 * c(mg/mL) * l(cm)).

    Give mrw directly, or mw_da and n_residues (then MRW = MW/(N-1)).
    theta in MILLIdegrees, the concentration in mg/mL, the path in cm.
    """
    if mrw is None:
        if mw_da is None or n_residues is None:
            raise ValueError("give mrw, or both mw_da and n_residues")
        mrw = mean_residue_weight(mw_da, n_residues)
    if conc_mg_ml <= 0 or path_cm <= 0:
        raise ValueError("conc_mg_ml and path_cm must be positive")
    return np.asarray(theta_mdeg, float) * mrw / (10.0 * conc_mg_ml * path_cm)


def ht_cutoff(wavelength_nm, ht_v, *, max_v: float = 600.0) -> float:
    """Shortest wavelength reached from the long-wavelength end before the
    HT (detector voltage) first exceeds max_v. Below it the data are not
    trustworthy."""
    w = np.asarray(wavelength_nm, float)
    h = np.asarray(ht_v, float)
    order = np.argsort(-w)
    w, h = w[order], h[order]
    last = None
    for wi, hi in zip(w, h):
        if hi > max_v:
            break
        last = wi
    if last is None:
        raise ValueError(f"HT exceeds {max_v} V already at the longest wavelength")
    return float(last)
