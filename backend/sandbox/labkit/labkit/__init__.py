"""labkit: vetted helpers for lab data analysis (Apache-2.0).

Start with labkit.guide() and labkit.guide('fit'); EPR: labkit.guide('epr'), labkit.guide('deer').
"""
from .fit import MODELS, FitResult, Model, compare, fit, fit_global, interpolate, rout
from .guide import guide
from .read import UnsupportedFormat, read, read_gen5, read_jasco, read_nanodrop, read_plate, read_table
from . import cd, epr, nmr, plot, stats

__version__ = "0.2.0"

__all__ = [
    "MODELS", "FitResult", "Model", "compare", "fit", "fit_global", "interpolate", "rout",
    "guide", "UnsupportedFormat", "read", "read_gen5", "read_jasco", "read_nanodrop",
    "read_plate", "read_table", "cd", "epr", "nmr", "plot", "stats",
]
