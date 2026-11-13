"""
Primer design engine (docs/agent-track/PRIMER-DESIGN-PLAN.md).

Deterministic, dependency-light machinery for designing and verifying PCR,
cloning and sequencing primers. The model never computes these numbers; it
calls in here and reports what comes back.

Modules land in the build order set by the plan:
  thermo.py       nearest-neighbour thermodynamics, Tm, IUPAC handling  [built]
  rules.py        the rule table, thresholds with citations             [built]
  verify.py       in-silico PCR: the self-check AND the eval oracle     [built]
  structure.py    hairpin / homodimer / heterodimer, 3' end stability
  select.py       candidate enumeration + penalty scoring
  tails.py        overlap-assembly and restriction tails
  specificity.py  BLAST+ against a reference, Primer-BLAST settings
"""

from .rules import (
    HARD,
    SOFT,
    CheckResult,
    Constraints,
    constraints_for,
    evaluate_pair,
    evaluate_primer,
    normalise_mode,
    summarise,
)
from .thermo import (
    IUPAC_CODES,
    SaltCorrection,
    SequenceError,
    degeneracy,
    expand_ambiguous,
    gc_fraction,
    is_ambiguous,
    nn_thermodynamics,
    normalise,
    reverse_complement,
    tm_nn,
    tm_range,
    tm_wallace,
)
from .verify import (
    MINUS,
    MULTIPLE_PRODUCTS,
    NO_PRODUCT,
    PLUS,
    SINGLE_PRODUCT,
    Amplicon,
    BindingSite,
    PcrResult,
    bases_compatible,
    find_binding_sites,
    simulate_pcr,
    verify_pair,
)

__all__ = [
    # thermo
    "IUPAC_CODES",
    "SaltCorrection",
    "SequenceError",
    "degeneracy",
    "expand_ambiguous",
    "gc_fraction",
    "is_ambiguous",
    "nn_thermodynamics",
    "normalise",
    "reverse_complement",
    "tm_nn",
    "tm_range",
    "tm_wallace",
    # rules
    "HARD",
    "SOFT",
    "CheckResult",
    "Constraints",
    "constraints_for",
    "evaluate_pair",
    "evaluate_primer",
    "normalise_mode",
    "summarise",
    # verify
    "MINUS",
    "MULTIPLE_PRODUCTS",
    "NO_PRODUCT",
    "PLUS",
    "SINGLE_PRODUCT",
    "Amplicon",
    "BindingSite",
    "PcrResult",
    "bases_compatible",
    "find_binding_sites",
    "simulate_pcr",
    "verify_pair",
]
