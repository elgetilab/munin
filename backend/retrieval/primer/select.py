"""
Candidate enumeration, penalty scoring and pair selection.

This is the module that composes the other four: `thermo` for melting
temperatures, `rules` for what counts as acceptable, `structure` for hairpins
and dimers, and `verify` for the in-silico PCR that confirms a chosen pair
actually produces the product it claims.

The pipeline, cheapest filter first
-----------------------------------
Enumerating every window of every allowed length over a few kilobases gives
tens of thousands of candidates, and the structure search is an O(n^2) dynamic
program. Running it on all of them would be slow for no benefit, so the order
is deliberate:

  1. enumerate windows in the regions where a primer may bind
  2. cheap rejects: length, GC, homopolymer runs, template ambiguity
  3. melting temperature, which costs a pass over the sequence
  4. rank by intrinsic penalty and keep the best `structure_pool` of them
  5. hairpin and self-dimer, the expensive checks, on survivors only
  6. pair up, score the pair, heterodimer-check the best combinations
  7. VERIFY with `verify.simulate_pcr`: a pair that does not yield exactly one
     product of the predicted size is rejected no matter how well it scored

Step 7 is the point of the whole design. A candidate that looks perfect by
every thermodynamic measure and then amplifies three places in the template is
not a primer pair, and nothing before step 7 can see that.

Penalty scoring
---------------
Primer3's approach, re-derived rather than copied: a weighted sum of absolute
deviations from the optimum, plus a fixed charge per soft rule failure. Lower
is better and 0 is unreachable in practice. The weights are in `Weights` and
are tuning parameters, not physics; they are documented as such because a
reader could otherwise mistake them for something measured.

A HARD rule failure is not scored, it disqualifies. That split lives in
`rules.py` and this module only honours it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from . import rules, structure, thermo, verify
from .rules import CheckResult, Constraints
from .thermo import SequenceError

#: How many intrinsically-good candidates per strand survive to the expensive
#: structure checks. Raising it costs roughly linear time in the DP.
DEFAULT_STRUCTURE_POOL = 40

#: How many pair combinations get a heterodimer check and an in-silico PCR.
DEFAULT_PAIR_POOL = 60

#: Minimum distance between the 3' ends of two RETURNED primers on the same
#: strand. Without it the top results are the same primer shifted by a base,
#: which looks like a choice and is not one: near-identical primers share
#: their specificity and their failure modes, so offering five of them gives a
#: user nothing to pick between. The 3' end is the right coordinate because it
#: is what sets both the product and the mispriming behaviour. Primer3 has the
#: same control (PRIMER_MIN_THREE_PRIME_DISTANCE).
DEFAULT_MIN_THREE_PRIME_DISTANCE = 3


@dataclass(frozen=True)
class Weights:
    """
    Penalty weights. Tuning parameters, NOT measured quantities.

    Units are "penalty points per unit of deviation", so `tm` at 1.0 means one
    degree off the optimum costs the same as one base off the optimal length
    at `length` 1.0. The defaults make Tm and length roughly comparable and
    make GC cheap, which matches how a human weighs them in practice.
    """

    tm: float = 1.0
    length: float = 0.5
    gc: float = 0.2
    soft_failure: float = 2.0
    pair_tm_diff: float = 1.5
    product_size: float = 0.01
    end_stability: float = 0.5

    # Structure penalties are charged per degree of structure Tm above a
    # comfort threshold well below the hard limit, so a primer with a mild
    # hairpin ranks below a clean one without being disqualified.
    structure_comfort_tm: float = 25.0
    structure: float = 0.2


DEFAULT_WEIGHTS = Weights()


@dataclass(frozen=True)
class Candidate:
    """One possible primer, with its coordinates on the plus strand."""

    seq: str
    strand: str          # verify.PLUS for a forward primer, verify.MINUS for a reverse
    start: int           # 0-based half-open, plus-strand, as in verify.py
    end: int
    tm: float
    gc: float
    penalty: float
    checks: tuple[CheckResult, ...] = ()

    @property
    def length(self) -> int:
        return len(self.seq)

    @property
    def three_prime(self) -> int:
        """Plus-strand index of the 3'-most base. The end that matters."""
        return self.end - 1 if self.strand == verify.PLUS else self.start

    @property
    def usable(self) -> bool:
        return not any(c.severity == rules.HARD and not c.passed
                       for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "sequence": self.seq,
            "strand": self.strand,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "tm": round(self.tm, 2),
            "gc_percent": round(self.gc, 1),
            "penalty": round(self.penalty, 3),
            "checks": [c.as_dict() for c in self.checks],
        }


@dataclass(frozen=True)
class PairCandidate:
    """A forward and reverse primer, with the product they were verified to give."""

    forward: Candidate
    reverse: Candidate
    product_size: int
    amplicon: str
    penalty: float
    checks: tuple[CheckResult, ...] = ()
    verified: dict = field(default_factory=dict)

    @property
    def tm_difference(self) -> float:
        return abs(self.forward.tm - self.reverse.tm)

    @property
    def usable(self) -> bool:
        if any(c.severity == rules.HARD and not c.passed for c in self.checks):
            return False
        return self.forward.usable and self.reverse.usable

    def as_dict(self) -> dict:
        return {
            "forward": self.forward.as_dict(),
            "reverse": self.reverse.as_dict(),
            "product_size": self.product_size,
            "tm_difference": round(self.tm_difference, 2),
            "penalty": round(self.penalty, 3),
            "checks": [c.as_dict() for c in self.checks],
            "verified": self.verified,
        }


# --------------------------------------------------------------------------
# Cheap screening
# --------------------------------------------------------------------------


def _cheap_reject(seq: str, c: Constraints) -> bool:
    """
    True when a window can be discarded without computing anything expensive.

    IMPORTANT, and not the same thing as the rule table: this filter treats
    the constraint bounds as HARD boundaries on the search space, while
    `rules.py` classifies several of them (length, GC, Tm) as SOFT. So the
    search deliberately never considers a primer outside the preferred GC
    window, even though such a primer would only pick up a soft failure if it
    were evaluated.

    That is intentional. A search has to be bounded somewhere, and the
    preferred window is the honest place. But it has a visible consequence:
    `design_pairs` can return nothing on a template where relaxing GC or Tm
    would have found something, and the empty result does not say so. A caller
    that wants the wider net should widen the `Constraints`, which is the one
    knob that moves both this filter and the reported thresholds together.
    """
    if len(seq) < c.length_min or len(seq) > c.length_max:
        return True
    # Template ambiguity: a window containing N cannot be ordered as a primer.
    if thermo.degeneracy(seq) > c.max_degeneracy:
        return True
    run, _ = rules.longest_run(seq)
    if run > c.max_poly_run:
        return True
    try:
        gc = 100.0 * thermo.gc_fraction(seq)
    except SequenceError:
        return True
    return not (c.gc_min <= gc <= c.gc_max)


def _intrinsic_penalty(seq: str, tm: float, gc: float, c: Constraints,
                       w: Weights) -> float:
    gc_mid = (c.gc_min + c.gc_max) / 2.0
    return (
        w.tm * abs(tm - c.tm_opt)
        + w.length * abs(len(seq) - c.length_opt)
        + w.gc * abs(gc - gc_mid)
    )


def _soft_penalty(checks: Iterable[CheckResult], w: Weights) -> float:
    return w.soft_failure * sum(
        1 for c in checks if c.severity == rules.SOFT and not c.passed)


def _structure_penalty(tm_value: Optional[float], w: Weights) -> float:
    if tm_value is None:
        return 0.0
    excess = tm_value - w.structure_comfort_tm
    return w.structure * excess if excess > 0 else 0.0


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------


def enumerate_candidates(
    template: str,
    *,
    strand: str,
    constraints: Constraints = rules.PCR,
    region: Optional[tuple[int, int]] = None,
    weights: Weights = DEFAULT_WEIGHTS,
    structure_pool: int = DEFAULT_STRUCTURE_POOL,
    evaluate_structure: bool = True,
) -> list[Candidate]:
    """
    Every acceptable primer on one strand, best first.

    `region` is a half-open plus-strand window the primer must lie entirely
    within. For a forward primer that is the stretch upstream of the target;
    for a reverse primer, downstream of it.

    Only the best `structure_pool` candidates get hairpin and self-dimer
    checks, because those are the expensive ones. The rest are returned with
    those checks marked "not computed", which `rules.py` reports honestly
    rather than passing silently.
    """
    if strand not in (verify.PLUS, verify.MINUS):
        raise ValueError(f"strand must be '+' or '-', not {strand!r}")
    seq = thermo.normalise(template)
    lo, hi = (0, len(seq)) if region is None else region
    lo = max(0, lo)
    hi = min(len(seq), hi)

    c = constraints
    scored: list[tuple[float, str, int, int, float, float]] = []

    for length in range(c.length_min, c.length_max + 1):
        for start in range(lo, hi - length + 1):
            end = start + length
            window = seq[start:end]
            primer = window if strand == verify.PLUS else \
                thermo.reverse_complement(window)
            if _cheap_reject(primer, c):
                continue
            tm = thermo.tm_nn(primer, **c.tm_kwargs())
            if not (c.tm_min <= tm <= c.tm_max):
                continue
            gc = 100.0 * thermo.gc_fraction(primer)
            scored.append((
                _intrinsic_penalty(primer, tm, gc, c, weights),
                primer, start, end, tm, gc,
            ))

    scored.sort(key=lambda t: (t[0], t[2]))

    out: list[Candidate] = []
    for i, (base_penalty, primer, start, end, tm, gc) in enumerate(scored):
        do_structure = evaluate_structure and i < structure_pool
        hairpin_tm = self_tm = end_dg = None
        if do_structure:
            hairpin_tm = structure.hairpin(primer).tm
            self_tm = structure.homodimer(primer).tm
            end_dg = structure.end_stability(primer).dg

        checks = rules.evaluate_primer(
            primer, c,
            hairpin_tm=hairpin_tm,
            self_dimer_tm=self_tm,
            end_stability_dg=end_dg,
        )
        if any(ch.severity == rules.HARD and not ch.passed for ch in checks):
            continue

        penalty = (
            base_penalty
            + _soft_penalty(checks, weights)
            + _structure_penalty(hairpin_tm, weights)
            + _structure_penalty(self_tm, weights)
        )
        out.append(Candidate(
            seq=primer, strand=strand, start=start, end=end,
            tm=tm, gc=gc, penalty=penalty, checks=tuple(checks),
        ))

    out.sort(key=lambda c_: (c_.penalty, c_.start))
    return out


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------


def design_pairs(
    template: str,
    *,
    constraints: Constraints = rules.PCR,
    target: Optional[tuple[int, int]] = None,
    product_range: Optional[tuple[int, int]] = None,
    circular: bool = False,
    n: int = 5,
    weights: Weights = DEFAULT_WEIGHTS,
    structure_pool: int = DEFAULT_STRUCTURE_POOL,
    pair_pool: int = DEFAULT_PAIR_POOL,
    min_three_prime_distance: int = DEFAULT_MIN_THREE_PRIME_DISTANCE,
) -> list[PairCandidate]:
    """
    Design primer pairs amplifying `template`, best first.

    `target` is a half-open window the product must contain, which is the
    usual way to say "amplify this exon" or "cover this SNP". Without it the
    whole template is fair game and the product-size range does the steering.

    Every returned pair has been through `verify.simulate_pcr` and produced
    exactly one product of the stated size. A pair that scores well and then
    fails verification is dropped, not returned with a warning: the envelope
    promises a verified design.
    """
    seq = thermo.normalise(template)
    c = constraints
    pmin, pmax = product_range or (c.product_min, c.product_max)
    if pmin > pmax:
        raise ValueError(f"product_range is inverted: {pmin} > {pmax}")

    if target is not None:
        t_start, t_end = target
        if not (0 <= t_start < t_end <= len(seq)):
            raise ValueError(
                f"target {target} is outside the template (0..{len(seq)})")
        fwd_region = (max(0, t_start - pmax), t_start)
        rev_region = (t_end, min(len(seq), t_end + pmax))
    else:
        fwd_region = (0, len(seq))
        rev_region = (0, len(seq))

    forwards = enumerate_candidates(
        seq, strand=verify.PLUS, constraints=c, region=fwd_region,
        weights=weights, structure_pool=structure_pool)
    reverses = enumerate_candidates(
        seq, strand=verify.MINUS, constraints=c, region=rev_region,
        weights=weights, structure_pool=structure_pool)

    combos: list[tuple[float, Candidate, Candidate, int]] = []
    for f in forwards:
        for r in reverses:
            if r.end <= f.start:
                continue                      # reverse must sit downstream
            size = r.end - f.start
            if not (pmin <= size <= pmax):
                continue
            if target is not None and not (f.end <= target[0]
                                           and r.start >= target[1]):
                continue
            combos.append((
                f.penalty + r.penalty
                + weights.pair_tm_diff * abs(f.tm - r.tm)
                + weights.product_size * abs(size - (pmin + pmax) / 2.0),
                f, r, size,
            ))

    # (penalty, forward, reverse, size): tie-break on coordinates and size,
    # never on the Candidate objects themselves, which are not orderable.
    combos.sort(key=lambda t: (t[0], t[1].start, t[2].start, t[3]))

    results: list[PairCandidate] = []

    def _too_similar(f: Candidate, r: Candidate) -> bool:
        """A pair is redundant when BOTH its primers sit on top of an accepted one."""
        return any(
            abs(f.three_prime - p.forward.three_prime) < min_three_prime_distance
            and abs(r.three_prime - p.reverse.three_prime) < min_three_prime_distance
            for p in results
        )

    for base_penalty, f, r, size in combos[:pair_pool]:
        if _too_similar(f, r):
            continue
        hetero = structure.heterodimer(f.seq, r.seq)
        checks = rules.evaluate_pair(
            f.seq, r.seq, c,
            product_size=size,
            hetero_dimer_tm=hetero.tm,
        )
        if any(ch.severity == rules.HARD and not ch.passed for ch in checks):
            continue

        # The gate: does this pair actually amplify once, at the stated size?
        result = verify.simulate_pcr(f.seq, r.seq, seq, circular=circular)
        if not result.is_single:
            continue
        product = result.product
        if product is None or product.length != size:
            continue

        penalty = (base_penalty
                   + _soft_penalty(checks, weights)
                   + _structure_penalty(hetero.tm, weights))
        results.append(PairCandidate(
            forward=f, reverse=r, product_size=size,
            amplicon=product.sequence, penalty=penalty,
            checks=tuple(checks),
            verified=verify.verify_pair(
                f.seq, r.seq, seq, circular=circular, expected_size=size),
        ))
        if len(results) >= n:
            break

    results.sort(key=lambda p: (p.penalty, p.forward.start))
    return results[:n]


def best_pair(template: str, **kwargs) -> Optional[PairCandidate]:
    """The single best verified pair, or None when nothing survives."""
    found = design_pairs(template, n=1, **kwargs)
    return found[0] if found else None
