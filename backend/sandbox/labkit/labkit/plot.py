"""Figure helpers: exact physical size, reproducible jitter.

    from labkit.plot import save_figure, jitter
    save_figure(fig, "fig1.png", width_mm=85, height_mm=60, dpi=300)
    ax.scatter(jitter(i, len(vals), seed=0), vals)
"""
from __future__ import annotations

from typing import Optional

import numpy as np

MM_PER_INCH = 25.4


def save_figure(fig, path: str, *, width_mm: Optional[float] = None, height_mm: Optional[float] = None,
                dpi: int = 300, **savefig_kwargs) -> dict:
    """Save at exactly width_mm x height_mm (no bbox_inches='tight', which
    changes the size). Uses constrained layout so labels fit inside.
    Returns the saved size in mm and pixels."""
    if "bbox_inches" in savefig_kwargs:
        raise ValueError("bbox_inches changes the saved size; leave it out and adjust the layout instead")
    w_in, h_in = fig.get_size_inches()
    if width_mm is not None:
        w_in = width_mm / MM_PER_INCH
    if height_mm is not None:
        h_in = height_mm / MM_PER_INCH
    fig.set_size_inches(w_in, h_in)
    try:
        fig.set_layout_engine("constrained")
    except Exception:  # older matplotlib or a figure with a fixed engine
        pass
    fig.savefig(path, dpi=dpi, **savefig_kwargs)
    out = {"width_mm": round(w_in * MM_PER_INCH, 2), "height_mm": round(h_in * MM_PER_INCH, 2), "dpi": dpi}
    try:  # report what was written, not a computed estimate (matplotlib truncates)
        from PIL import Image
        with Image.open(path) as im:
            out["width_px"], out["height_px"] = im.size
    except Exception:
        out["width_px"], out["height_px"] = int(w_in * dpi), int(h_in * dpi)
    return out


def jitter(center: float, n: int, *, width: float = 0.08, seed: int = 0) -> np.ndarray:
    """x positions for n points scattered around `center`, the same on
    every run (seeded), so a re-run reproduces the figure pixel for pixel."""
    rng = np.random.default_rng(seed + int(round(center * 1000)))
    return center + rng.uniform(-width, width, size=n)
