"""
Primer design engine (docs/agent-track/PRIMER-DESIGN-PLAN.md).

Deterministic, dependency-light machinery for designing and verifying PCR,
cloning and sequencing primers. The model never computes these numbers; it
calls in here and reports what comes back.

Modules land in the build order set by the plan:
  thermo.py       nearest-neighbour thermodynamics, Tm, IUPAC handling  [built]
  structure.py    hairpin / homodimer / heterodimer, 3' end stability
  rules.py        the rule table, thresholds with citations
  select.py       candidate enumeration + penalty scoring
  tails.py        overlap-assembly and restriction tails
  specificity.py  BLAST+ against a reference, Primer-BLAST settings
  verify.py       in-silico PCR: the self-check AND the eval oracle
"""

from .thermo import (
    IUPAC_CODES,
    SaltCorrection,
    expand_ambiguous,
    gc_fraction,
    is_ambiguous,
    nn_thermodynamics,
    reverse_complement,
    tm_nn,
    tm_range,
    tm_wallace,
)

__all__ = [
    "IUPAC_CODES",
    "SaltCorrection",
    "expand_ambiguous",
    "gc_fraction",
    "is_ambiguous",
    "nn_thermodynamics",
    "reverse_complement",
    "tm_nn",
    "tm_range",
    "tm_wallace",
]
