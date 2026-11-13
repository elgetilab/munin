"""
The primer design rule table: thresholds, their sources, and evaluation.

This module is deliberately data-first. Every threshold is a named field on
`Constraints` carrying a citation and a severity, and evaluating a primer
produces a list of `CheckResult` rather than a bare boolean. That list IS the
`checks` block in the tool envelope (plan section 3.3): the model cannot report
a success the rules did not grant, and the eval suite reads exactly what the
user reads.

Severity
--------
HARD   a violation makes the primer unusable, so a candidate failing one is
       rejected rather than ranked low.
SOFT   a preference. Published sources disagree about most of these, which is
       the honest reason they are not hard. A soft failure is reported and
       costs penalty score; it does not disqualify.

Sources, and where they disagree
--------------------------------
Length 18-25, GC 40-60%, Tm 52-65: Dieffenbach CW et al. (1993) PCR Methods
    Appl 3:S30, the general-purpose design guidance everyone descends from.
    Primer3's own defaults are wider (18-27 nt, GC 20-80%, Tm 57-63).
Pair Tm difference: IDT advise <= 2 C, other vendors tolerate <= 5 C, and
    Primer3 ships `PRIMER_PAIR_MAX_DIFF_TM` at 100, which is effectively off.
    We take 5 as the ceiling and 2 as the preference, and say so.
GC clamp and 3' end composition: Premier Biosoft guidance, 1-2 G/C within the
    last 5 bases. Primer3 instead caps it, `PRIMER_MAX_END_GC` default 5.
Mononucleotide runs <= 4: Premier Biosoft. Primer3 permits 5
    (`PRIMER_MAX_POLY_X`). We treat 5 as the hard ceiling and 4 as preferred.
3' end stability: Rychlik W (1990) in "PCR Protocols"; an over-stable 3' end
    promotes mispriming. Primer3's manual suggests 9 kcal/mol as a maximum.
Structure thresholds: Primer3's `PRIMER_MAX_SELF_ANY_TH` family default to
    47 and are STRUCTURE MELTING TEMPERATURES IN CELSIUS, not dG. IDT's widely
    quoted "dimer dG >= -9 kcal/mol" is a different quantity entirely. Both
    conventions are supported and labelled; do not conflate them.

Secondary-structure checks accept values computed elsewhere (primer/structure.py)
rather than importing it, so the rule table stays usable on its own and the two
modules can evolve independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from . import thermo

# --------------------------------------------------------------------------
# Severity
# --------------------------------------------------------------------------

HARD = "hard"
SOFT = "soft"


@dataclass(frozen=True)
class CheckResult:
    """One evaluated rule. Serialises straight into the tool envelope."""

    rule: str               # stable identifier, e.g. "primer_length"
    label: str              # human-readable, e.g. "primer length 18-25 nt"
    severity: str           # HARD | SOFT
    passed: bool
    value: Optional[float]  # what was measured, None when not computable
    threshold: str          # the bound, as text, e.g. "18-25" or "<= 47 C"
    source: str             # citation
    detail: str = ""        # why it failed, when it failed
    # False when the rule could not be evaluated at all, because its input was
    # never supplied (a structure temperature nobody computed, a Tm that has
    # no single value because the primer is degenerate).
    #
    # This is a FIELD and not a prose convention on purpose. It used to be
    # inferred from `detail == "not computed"`, which was both fragile for any
    # consumer and quietly wrong here: one site writes a longer explanation,
    # so exact-matching missed it and `summarise` under-reported. An
    # uncomputed check keeps `passed=True` so it cannot disqualify a primer,
    # which makes conflating the two states the most dangerous mistake a
    # caller can make: it reads as "we checked this and it was fine" when
    # nothing was checked. Consumers MUST branch on `computed`, not on text.
    computed: bool = True

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "label": self.label,
            "severity": self.severity,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "source": self.source,
            "detail": self.detail,
            "computed": self.computed,
        }


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Constraints:
    """
    Numeric thresholds for one design mode.

    Defaults are the general-purpose PCR set. Mode presets below adjust them.
    """

    # length
    length_min: int = 18
    length_max: int = 25
    length_opt: int = 20

    # composition
    gc_min: float = 40.0
    gc_max: float = 60.0

    # melting temperature
    tm_min: float = 52.0
    tm_max: float = 65.0
    tm_opt: float = 60.0

    # pairing
    pair_tm_diff_max: float = 5.0        # hard ceiling
    pair_tm_diff_preferred: float = 2.0  # soft preference

    # 3' end
    gc_clamp_min: int = 1    # at least this many G/C in the last `gc_clamp_window`
    gc_clamp_window: int = 5
    max_end_gc: int = 3      # at most this many G/C in that window

    # runs
    max_poly_run: int = 5        # hard: no run longer than this
    preferred_poly_run: int = 4  # soft

    # 3' end stability, kcal/mol as a magnitude (Primer3 manual convention)
    max_end_stability: float = 9.0

    # secondary structure, Primer3 convention (melting temp of the structure)
    max_hairpin_tm: float = 47.0
    max_self_dimer_tm: float = 47.0
    max_hetero_dimer_tm: float = 47.0

    # secondary structure, IDT convention (free energy)
    min_dimer_dg: float = -9.0

    # product
    product_min: int = 100
    product_max: int = 1000

    # degeneracy: how many oligos a single primer may denote
    max_degeneracy: int = 1

    # salt / concentration conditions used for every Tm in this rule set
    na: float = 50.0
    mg: float = 1.5
    dntp: float = 0.6
    dnac1: float = 250.0
    dnac2: float = 250.0

    def tm_kwargs(self) -> dict:
        return {
            "na": self.na,
            "mg": self.mg,
            "dntp": self.dntp,
            "dnac1": self.dnac1,
            "dnac2": self.dnac2,
        }


#: Standard PCR / genotyping.
PCR = Constraints()

#: Sequencing and colony-check primers: a single primer, read toward a
#: feature, so pairing and product rules do not apply and the window is wider.
SEQUENCING = replace(
    PCR,
    length_min=17,
    length_max=25,
    tm_min=50.0,
    tm_max=65.0,
    product_min=0,
    product_max=0,
)

#: Overlap assembly (alias: gibson). The ANNEALING portion is judged by these;
#: the 5' homology tail is judged by tails.py against the NEB overlap rules
#: (15-20 nt for 2-3 fragments, 20-30 for 4-6, hard minimum 15).
OVERLAP_ASSEMBLY = replace(PCR, length_min=18, length_max=30)

#: Restriction cloning. Same annealing requirements; the 5' tail carries the
#: site plus a spacer sized per NEB's cleavage-close-to-the-end table.
RESTRICTION = replace(PCR, length_min=18, length_max=30)

MODES: dict[str, Constraints] = {
    "pcr": PCR,
    "sequencing": SEQUENCING,
    "overlap_assembly": OVERLAP_ASSEMBLY,
    "restriction": RESTRICTION,
}

#: `gibson` is accepted and normalised to `overlap_assembly`. "GIBSON ASSEMBLY"
#: is a registered trademark of Codex DNA, so the generic name is canonical and
#: the familiar one is an alias (plan section 3.3).
MODE_ALIASES: dict[str, str] = {"gibson": "overlap_assembly"}


def normalise_mode(mode: str) -> str:
    """Canonical mode name. Raises on an unknown one."""
    m = (mode or "").strip().lower()
    m = MODE_ALIASES.get(m, m)
    if m not in MODES:
        known = ", ".join(sorted(MODES) + sorted(MODE_ALIASES))
        raise ValueError(f"unknown mode {mode!r}; expected one of {known}")
    return m


def constraints_for(mode: str) -> Constraints:
    return MODES[normalise_mode(mode)]


# --------------------------------------------------------------------------
# Measurements
# --------------------------------------------------------------------------


def longest_run(seq: str) -> tuple[int, str]:
    """Length of the longest single-base run, and which base."""
    s = thermo.normalise(seq)
    best, best_base = 1, s[0]
    run, base = 1, s[0]
    for ch in s[1:]:
        if ch == base:
            run += 1
        else:
            run, base = 1, ch
        if run > best:
            best, best_base = run, base
    return best, best_base


def end_gc_count(seq: str, window: int = 5) -> int:
    """How many G or C in the last `window` bases. S counts; other codes do not."""
    s = thermo.normalise(seq)[-window:]
    return sum(1 for b in s if b in "GCS")


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _r(rule, label, severity, passed, value, threshold, source, detail="",
       computed=True):
    return CheckResult(rule, label, severity, passed, value, threshold,
                       source, detail, computed)


def evaluate_primer(
    seq: str,
    constraints: Constraints = PCR,
    *,
    hairpin_tm: Optional[float] = None,
    self_dimer_tm: Optional[float] = None,
    end_stability_dg: Optional[float] = None,
) -> list[CheckResult]:
    """
    Every rule evaluated against one primer, in a stable order.

    Structure values are optional because primer/structure.py computes them;
    a rule whose input is absent is reported as not-computed rather than
    silently passing.
    """
    c = constraints
    s = thermo.normalise(seq)
    checks: list[CheckResult] = []

    # --- length ---
    n = len(s)
    checks.append(_r(
        "primer_length", f"length {c.length_min}-{c.length_max} nt", SOFT,
        c.length_min <= n <= c.length_max, float(n),
        f"{c.length_min}-{c.length_max}", "Dieffenbach 1993",
        "" if c.length_min <= n <= c.length_max else f"{n} nt is outside the range",
    ))

    # --- degeneracy ---
    deg = thermo.degeneracy(s)
    deg_ok = deg <= c.max_degeneracy
    checks.append(_r(
        "degeneracy", f"denotes at most {c.max_degeneracy} oligo(s)", SOFT,
        deg_ok, float(deg), f"<= {c.max_degeneracy}", "design choice",
        "" if deg_ok else
        f"IUPAC codes make this {deg} distinct oligos; Tm is a range, not a value",
    ))

    # --- GC content ---
    try:
        gc = 100.0 * thermo.gc_fraction(s)
        gc_ok = c.gc_min <= gc <= c.gc_max
        checks.append(_r(
            "gc_content", f"GC {c.gc_min:.0f}-{c.gc_max:.0f}%", SOFT, gc_ok,
            round(gc, 1), f"{c.gc_min:.0f}-{c.gc_max:.0f}%", "Dieffenbach 1993; IDT",
            "" if gc_ok else f"{gc:.1f}% is outside the range",
        ))
    except thermo.SequenceError as exc:
        checks.append(_r("gc_content", "GC content", SOFT, False, None,
                         f"{c.gc_min:.0f}-{c.gc_max:.0f}%", "Dieffenbach 1993",
                         str(exc)))

    # --- melting temperature ---
    if deg == 1:
        tm = thermo.tm_nn(s, **c.tm_kwargs())
        tm_ok = c.tm_min <= tm <= c.tm_max
        checks.append(_r(
            "tm", f"Tm {c.tm_min:.0f}-{c.tm_max:.0f} C", SOFT, tm_ok,
            round(tm, 2), f"{c.tm_min:.0f}-{c.tm_max:.0f} C",
            "SantaLucia 1998 nearest-neighbour",
            "" if tm_ok else f"{tm:.1f} C is outside the range",
        ))
    else:
        lo, hi = thermo.tm_range(s, **c.tm_kwargs())
        tm_ok = c.tm_min <= lo and hi <= c.tm_max
        checks.append(_r(
            "tm", f"Tm {c.tm_min:.0f}-{c.tm_max:.0f} C (whole degenerate set)",
            SOFT, tm_ok, round(lo, 2), f"{c.tm_min:.0f}-{c.tm_max:.0f} C",
            "SantaLucia 1998 nearest-neighbour",
            "" if tm_ok else
            f"the {deg} oligos span {lo:.1f}-{hi:.1f} C, which leaves the range",
        ))

    # --- homopolymer runs ---
    run, base = longest_run(s)
    checks.append(_r(
        "poly_run", f"no run longer than {c.max_poly_run}", HARD,
        run <= c.max_poly_run, float(run), f"<= {c.max_poly_run}",
        "Premier Biosoft; Primer3 PRIMER_MAX_POLY_X",
        "" if run <= c.max_poly_run else f"a run of {run} {base}",
    ))
    checks.append(_r(
        "poly_run_preferred", f"prefer no run longer than {c.preferred_poly_run}",
        SOFT, run <= c.preferred_poly_run, float(run),
        f"<= {c.preferred_poly_run}", "Premier Biosoft",
        "" if run <= c.preferred_poly_run else f"a run of {run} {base}",
    ))

    # --- 3' end composition ---
    egc = end_gc_count(s, c.gc_clamp_window)
    clamp_ok = egc >= c.gc_clamp_min
    checks.append(_r(
        "gc_clamp",
        f"at least {c.gc_clamp_min} G/C in the last {c.gc_clamp_window}",
        SOFT, clamp_ok, float(egc), f">= {c.gc_clamp_min}", "Premier Biosoft",
        "" if clamp_ok else "no GC clamp; the 3' end may breathe",
    ))
    end_ok = egc <= c.max_end_gc
    checks.append(_r(
        "max_end_gc",
        f"at most {c.max_end_gc} G/C in the last {c.gc_clamp_window}",
        SOFT, end_ok, float(egc), f"<= {c.max_end_gc}",
        "Primer3 PRIMER_MAX_END_GC",
        "" if end_ok else "a GC-rich 3' end promotes mispriming",
    ))

    # --- structure, supplied from outside ---
    checks.append(_structure_check(
        "hairpin_tm", "hairpin melting temperature", hairpin_tm,
        c.max_hairpin_tm, "Primer3 PRIMER_MAX_HAIRPIN_TH"))
    checks.append(_structure_check(
        "self_dimer_tm", "self-dimer melting temperature", self_dimer_tm,
        c.max_self_dimer_tm, "Primer3 PRIMER_MAX_SELF_ANY_TH"))

    if end_stability_dg is None:
        checks.append(_r(
            "end_stability", "3' end stability", SOFT, True, None,
            f"|dG| <= {c.max_end_stability} kcal/mol", "Rychlik 1990",
            "not computed", computed=False,
        ))
    else:
        ok = abs(end_stability_dg) <= c.max_end_stability
        checks.append(_r(
            "end_stability", "3' end stability", SOFT, ok,
            round(end_stability_dg, 2),
            f"|dG| <= {c.max_end_stability} kcal/mol", "Rychlik 1990",
            "" if ok else "an over-stable 3' end promotes mispriming",
        ))

    return checks


def _structure_check(rule, label, value, limit, source) -> CheckResult:
    """A Primer3-convention structure check: the melting temp of the structure."""
    if value is None:
        return _r(rule, label, HARD, True, None, f"<= {limit} C", source,
                  "not computed", computed=False)
    ok = value <= limit
    return _r(rule, label, HARD, ok, round(value, 2), f"<= {limit} C", source,
              "" if ok else f"{value:.1f} C exceeds {limit} C")


def evaluate_pair(
    forward: str,
    reverse: str,
    constraints: Constraints = PCR,
    *,
    product_size: Optional[int] = None,
    hetero_dimer_tm: Optional[float] = None,
) -> list[CheckResult]:
    """Rules that only make sense for a pair. Per-primer rules run separately."""
    c = constraints
    checks: list[CheckResult] = []

    f_deg = thermo.degeneracy(forward)
    r_deg = thermo.degeneracy(reverse)
    if f_deg == 1 and r_deg == 1:
        tm_f = thermo.tm_nn(forward, **c.tm_kwargs())
        tm_r = thermo.tm_nn(reverse, **c.tm_kwargs())
        diff = abs(tm_f - tm_r)
        checks.append(_r(
            "pair_tm_diff", f"pair Tm difference <= {c.pair_tm_diff_max:.0f} C",
            HARD, diff <= c.pair_tm_diff_max, round(diff, 2),
            f"<= {c.pair_tm_diff_max:.0f} C", "IDT; Thermo",
            "" if diff <= c.pair_tm_diff_max else
            f"{tm_f:.1f} C vs {tm_r:.1f} C will not anneal together cleanly",
        ))
        checks.append(_r(
            "pair_tm_diff_preferred",
            f"prefer pair Tm difference <= {c.pair_tm_diff_preferred:.0f} C",
            SOFT, diff <= c.pair_tm_diff_preferred, round(diff, 2),
            f"<= {c.pair_tm_diff_preferred:.0f} C", "IDT",
        ))
    else:
        checks.append(_r(
            "pair_tm_diff", "pair Tm difference", HARD, True, None,
            f"<= {c.pair_tm_diff_max:.0f} C", "IDT; Thermo",
            "not computed: a degenerate primer has a Tm range, not a value",
            computed=False,
        ))

    checks.append(_structure_check(
        "hetero_dimer_tm", "primer-dimer melting temperature", hetero_dimer_tm,
        c.max_hetero_dimer_tm, "Primer3 PRIMER_PAIR_MAX_COMPL_ANY_TH"))

    if c.product_max > 0:
        if product_size is None:
            checks.append(_r(
                "product_size", "product size", HARD, True, None,
                f"{c.product_min}-{c.product_max} bp", "design choice",
                "not computed", computed=False,
            ))
        else:
            ok = c.product_min <= product_size <= c.product_max
            checks.append(_r(
                "product_size", "product size", HARD, ok, float(product_size),
                f"{c.product_min}-{c.product_max} bp", "design choice",
                "" if ok else f"{product_size} bp is outside the range",
            ))

    return checks


def summarise(checks: list[CheckResult]) -> dict:
    """Envelope-shaped summary: counts plus the failures, worst first."""
    hard_failed = [c for c in checks if not c.passed and c.severity == HARD]
    soft_failed = [c for c in checks if not c.passed and c.severity == SOFT]
    not_computed = [c for c in checks if not c.computed]
    return {
        "total": len(checks),
        # An uncomputed check is NOT a pass. Counting it as one made the
        # header contradict the per-check rows it summarised.
        "passed": sum(1 for c in checks if c.passed and c.computed),
        "hard_failures": len(hard_failed),
        "soft_failures": len(soft_failed),
        "not_computed": len(not_computed),
        "usable": not hard_failed,
        "failures": [c.as_dict() for c in hard_failed + soft_failed],
    }
