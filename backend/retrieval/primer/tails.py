"""
5' tails: overlap-assembly homology, restriction sites, and the rules on them.

A cloning primer is two sequences glued together. The 3' ANNEALING region binds
the template and is judged by `rules.evaluate_primer` like any other primer. The
5' TAIL does not bind anything on the first cycle; it carries either homology to
a neighbouring fragment or a restriction site, and it is judged here.

Why this module exists in this shape
------------------------------------
The published failure mode is specific. In the Bioinfoysis re-grade
(arXiv 2609.03871, plan section 1) the dominant residual error was assembly
primer pairs with **no valid overlap**: junctions that look right and do not
join. Assembly primers scored 1/7 against 14/19 for plain primer design. So the
central function here is not the designer, it is `validate_overlap`, and it
settles the question by **exact string identity** between the end of one
fragment and the start of the next. Not a Tm heuristic, not an alignment score.
A junction either matches or it does not, and a one-base near miss is a failure.

Trademark note
--------------
"GIBSON ASSEMBLY" is a registered trademark of Codex DNA; NEB brands its own
product NEBuilder HiFi DNA Assembly. The canonical mode name is therefore
`overlap_assembly` throughout, with `gibson` accepted as an input alias by
`rules.normalise_mode`. The familiar term appears below only descriptively.

Rendering convention
--------------------
`TailedPrimer.display()` writes the 5' tail in lowercase and the annealing
region in uppercase, which is the convention of pydna's `primers.fas`
(BSD-3-Clause, plan section 4): `taattaaTTTGTAATTAAAACTTAGATTAGATTGC`. Our
plan already cites that file as ground truth for tail-versus-anneal
segmentation, so emitting the same shape keeps the two comparable.
`TailedPrimer.from_display` parses it back.

Thresholds: what is sourced and what is a default
-------------------------------------------------
Sourced, and reproduced from the plan's rule table (section 3.2):

* Overlap 15-20 nt for 2-3 fragments, 20-30 nt for 4-6, **hard minimum 15**,
  and 20 is preferred over 15. NEB's assembly guidance.
* Restriction tails need extra bases 5' of the site for the enzyme to cut near
  a fragment end: **6 bp by default**, and **AccI needs at least 13**. NEB's
  "cleavage close to the end of DNA fragments" guidance.

Defaulted, chosen here, and labelled as such wherever they appear:

* The spacer for every enzyme other than AccI is the 6 bp default. NEB publishes
  a per-enzyme table, but neb.com returns HTTP 403 to automated clients (plan
  section 4), so those figures could not be re-verified first-hand and are NOT
  transcribed from memory. `Enzyme.spacer_sourced` says which is which, and a
  caller who knows a published figure can pass `spacer=` explicitly.
* Overlap Tm >= 48 C is NEB's figure for the assembly reaction, which incubates
  at 50 C. It could not be re-fetched for the same 403 reason, and our Tm is
  computed in the rule table's PCR buffer rather than NEB's calculator
  conditions, so the two numbers are not strictly comparable. It is SOFT.
* Overlap GC 30-70%, total oligo length <= 60 nt, and the sequencing read
  offset window 50-150 nt are conventions, not published thresholds. All SOFT.
  The sequencing window exists because the first tens of bases of a Sanger read
  are low quality; the exact number varies by provider.
* The 25 nt default overlap for 4-6 fragments is the middle of NEB's published
  20-30 range. The range is sourced; picking 25 inside it is ours.

Not handled, said plainly rather than approximated
--------------------------------------------------
Reading frame. A restriction tail for an N- or C-terminal fusion has to keep
the insert in frame with the vector's codons, and that depends on the vector,
which this module is not given. Callers doing fusion work must check the frame
themselves. Buffer compatibility for a double digest is likewise out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

from . import rules, thermo, verify
from .rules import HARD, SOFT, CheckResult

__all__ = [
    "AssemblyError",
    "Enzyme",
    "ENZYMES",
    "OverlapDesign",
    "RestrictionDesign",
    "SequencingDesign",
    "TailedPrimer",
    "OVERLAP_MIN_NT",
    "OVERLAP_PREFERRED_NT",
    "DEFAULT_SPACER_BP",
    "assemble",
    "check_internal_sites",
    "design_overlap_tails",
    "design_restriction_tails",
    "design_sequencing_primer",
    "longest_exact_overlap",
    "lookup_enzyme",
    "recommended_overlap",
    "validate_overlap",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Hard minimum overlap for assembly. NEB. Below this the junction does not
#: anneal reliably after exonuclease chew-back, whatever the sequence says.
OVERLAP_MIN_NT = 15

#: Preferred overlap: NEB's guidance is that 20 beats 15.
OVERLAP_PREFERRED_NT = 20

#: NEB's published ranges, by how many fragments go into one reaction.
OVERLAP_RANGE_FEW = (15, 20)      # 2-3 fragments
OVERLAP_RANGE_MANY = (20, 30)     # 4-6 fragments

#: SOFT. NEB's Gibson guidance pairs with a 50 C assembly incubation; see the
#: module docstring for why this is not re-verified and not exactly comparable.
OVERLAP_MIN_TM = 48.0

#: SOFT, a design choice: an overlap at either GC extreme anneals badly.
OVERLAP_GC_MIN = 30.0
OVERLAP_GC_MAX = 70.0

#: SOFT, a design choice: standard oligo synthesis gets expensive past here.
MAX_OLIGO_LENGTH = 60

#: NEB's default number of bases 5' of a restriction site.
DEFAULT_SPACER_BP = 6

#: SOFT. Sanger reads open with tens of low-quality bases, so a sequencing
#: primer is placed this far before the region of interest. Convention.
READ_OFFSET_MIN = 50
READ_OFFSET_MAX = 150
READ_OFFSET_DEFAULT = 100

#: Cap on how far back `longest_exact_overlap` looks. An assembly overlap is
#: tens of bases; searching a whole plasmid would be quadratic for no gain.
MAX_OVERLAP_SEARCH = 500


class AssemblyError(ValueError):
    """Fragments were handed over that do not actually join."""


def _r(rule, label, severity, passed, value, threshold, source, detail=""):
    return CheckResult(rule, label, severity, passed, value, threshold,
                       source, detail)


# --------------------------------------------------------------------------
# The shared result type
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TailedPrimer:
    """
    One primer, with the tail and the annealing region kept apart.

    `sequence` is what gets ordered. `anneal` is the part that binds the
    template and is the part `rules.evaluate_primer` judges; `tail` is the
    5' addition and is judged by this module. Keeping them separate is the
    whole point: a Tm computed over the full oligo is not the Tm that governs
    the first annealing step, and a tail silently counted as template homology
    is exactly the bookkeeping error this engine exists to catch.
    """

    anneal: str
    tail: str = ""
    name: str = ""
    role: str = ""      # "forward" | "reverse" | "sequencing"
    mode: str = ""      # canonical mode name, e.g. "overlap_assembly"
    purpose: str = ""   # free text for the envelope
    checks: tuple[CheckResult, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "anneal", thermo.normalise(self.anneal))
        object.__setattr__(
            self, "tail", thermo.normalise(self.tail) if self.tail else "")
        object.__setattr__(self, "checks", tuple(self.checks))

    # --- the sequence ----------------------------------------------------
    @property
    def sequence(self) -> str:
        """The full oligo, 5'->3'. Tail first, because the tail is the 5' end."""
        return self.tail + self.anneal

    @property
    def length(self) -> int:
        return len(self.tail) + len(self.anneal)

    @property
    def tail_length(self) -> int:
        return len(self.tail)

    @property
    def anneal_length(self) -> int:
        return len(self.anneal)

    def tail_homology(self) -> str:
        """
        The tail read on the plus strand of the product it creates.

        A reverse primer's tail is written 5'->3' on the oligo, which is the
        minus strand of the assembled construct, so the sequence it adds to the
        plus strand is its reverse complement. Getting this backwards is how a
        junction ends up reverse complemented and silently fails to join.
        """
        return thermo.reverse_complement(self.tail) if self.tail else ""

    # --- rendering -------------------------------------------------------
    def display(self) -> str:
        """
        Lowercase 5' tail, uppercase annealing region (pydna's convention).

        `from_display` is its exact inverse, so this is a lossless rendering of
        the tail/anneal split rather than a decoration.
        """
        return self.tail.lower() + self.anneal.upper()

    @classmethod
    def from_display(cls, text: str, **kwargs) -> "TailedPrimer":
        """
        Parse `tttaaCGTAC...` back into tail and annealing region.

        The leading run of lowercase characters is the tail; everything after
        it must be uppercase. A fully uppercase string is a primer with no
        tail, which is the correct reading of an untailed pydna record.
        """
        raw = "".join(str(text).split())
        if not raw:
            raise thermo.SequenceError("empty sequence")
        cut = 0
        while cut < len(raw) and raw[cut].islower():
            cut += 1
        tail, anneal = raw[:cut], raw[cut:]
        if any(ch.islower() for ch in anneal):
            raise thermo.SequenceError(
                "mixed case inside the annealing region: the convention is a "
                "lowercase 5' tail followed by an uppercase annealing region, "
                f"got {raw!r}"
            )
        if not anneal:
            raise thermo.SequenceError(
                "all-lowercase sequence: a primer that is entirely tail would "
                "anneal to nothing"
            )
        return cls(anneal=anneal, tail=tail, **kwargs)

    # --- envelope --------------------------------------------------------
    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "mode": self.mode,
            "sequence": self.sequence,
            "display": self.display(),
            "tail": self.tail,
            "anneal": self.anneal,
            "length": self.length,
            "tail_length": self.tail_length,
            "anneal_length": self.anneal_length,
            "purpose": self.purpose,
            "checks": [c.as_dict() for c in self.checks],
        }


def _length_check(primer: TailedPrimer) -> CheckResult:
    """SOFT, a design choice: synthesis cost, not chemistry. See the docstring."""
    n = primer.length
    return _r(
        "oligo_length", f"total oligo length <= {MAX_OLIGO_LENGTH} nt", SOFT,
        n <= MAX_OLIGO_LENGTH, float(n), f"<= {MAX_OLIGO_LENGTH} nt",
        "design choice: standard oligo synthesis, not a published design rule",
        "" if n <= MAX_OLIGO_LENGTH else
        f"{n} nt ({primer.tail_length} nt tail + {primer.anneal_length} nt "
        "annealing) is long to synthesise",
    )


# --------------------------------------------------------------------------
# Annealing region
# --------------------------------------------------------------------------


def _choose_anneal(
    seq: str,
    constraints: rules.Constraints,
    requested: Optional[int] = None,
) -> str:
    """
    Take an annealing region off the 5' end of `seq`.

    With an explicit length, that length. Otherwise the shortest length inside
    the mode's allowed range whose Tm reaches `constraints.tm_opt`, falling
    back to the longest allowed when nothing does. This is the usual practice
    for assembly and restriction primers, where the annealing region is pinned
    at one end of the fragment and only its length is free. It introduces no
    new threshold: `tm_opt` and the length bounds come from `rules.py`.
    """
    s = thermo.normalise(seq)
    if requested is not None:
        if requested < 1:
            raise ValueError("anneal_length must be at least 1")
        if requested > len(s):
            raise thermo.SequenceError(
                f"annealing region of {requested} nt requested but only "
                f"{len(s)} nt of sequence is available"
            )
        return s[:requested]

    lo = min(constraints.length_min, len(s))
    hi = min(constraints.length_max, len(s))
    if hi < 2:
        return s
    best = s[:hi]
    for n in range(lo, hi + 1):
        candidate = s[:n]
        try:
            tm = thermo.tm_nn(candidate, **constraints.tm_kwargs())
        except (thermo.SequenceError, ValueError):
            # Degenerate or otherwise uncomputable: fall back to the longest
            # allowed rather than guess. The rule checks will say so.
            return best
        if tm >= constraints.tm_opt:
            return candidate
    return best


# --------------------------------------------------------------------------
# Overlap assembly
# --------------------------------------------------------------------------


def recommended_overlap(fragment_count: int) -> tuple[int, int, int]:
    """
    (minimum, maximum, default) overlap length in nt, per NEB's guidance.

    15-20 nt for 2-3 fragments, 20-30 nt for 4-6. The defaults are 20 for the
    small case (NEB says 20 beats 15) and 25 for the large one, which is the
    middle of NEB's published range and our choice rather than their figure.
    Above 6 fragments NEB's table stops; the 4-6 row is carried forward and
    `design_overlap_tails` reports that it is out of published territory.
    """
    if fragment_count < 2:
        raise ValueError("an assembly needs at least 2 fragments")
    if fragment_count <= 3:
        lo, hi = OVERLAP_RANGE_FEW
        return lo, hi, OVERLAP_PREFERRED_NT
    lo, hi = OVERLAP_RANGE_MANY
    return lo, hi, 25


def longest_exact_overlap(
    left: str,
    right: str,
    max_length: int = MAX_OVERLAP_SEARCH,
) -> int:
    """
    Longest k with `left[-k:] == right[:k]`, by exact identity. 0 if none.

    Exact, not IUPAC-compatible: the oligos that make a junction are concrete
    molecules, so treating an N as a match would be reporting an overlap that
    does not exist. That is the error this module was written to make
    impossible.
    """
    l_seq = thermo.normalise(left)
    r_seq = thermo.normalise(right)
    cap = min(len(l_seq), len(r_seq), max(0, int(max_length)))
    for k in range(cap, 0, -1):
        if l_seq[-k:] == r_seq[:k]:
            return k
    return 0


def _first_difference(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def validate_overlap(
    upstream: str,
    downstream: str,
    overlap_length: Optional[int] = None,
    *,
    fragment_count: int = 2,
    constraints: Optional[rules.Constraints] = None,
    max_length: int = MAX_OVERLAP_SEARCH,
) -> list[CheckResult]:
    """
    Does this junction actually join? Checked by exact sequence identity.

    `upstream` and `downstream` are the two pieces AS THEY WILL BE ASSEMBLED,
    tails included: the PCR products, not the untailed template fragments. The
    junction is valid when the last N bases of `upstream` are, character for
    character, the first N bases of `downstream`.

    With `overlap_length` given, exactly that length is tested, so a caller can
    assert the junction it believes it designed. With it left as None the
    longest exact suffix/prefix match is found and then judged, which is the
    right call when inspecting a junction someone else proposed.

    Returns `rules.CheckResult` objects, so a junction drops into the same
    `checks` block as every other rule and `rules.summarise(...)["usable"]`
    answers the only question that matters. The identity check and the 15 nt
    minimum are HARD: both destroy the construct.
    """
    up = thermo.normalise(upstream)
    down = thermo.normalise(downstream)
    con = constraints or rules.OVERLAP_ASSEMBLY
    lo, hi, _ = recommended_overlap(fragment_count)
    checks: list[CheckResult] = []

    declared = overlap_length is not None
    if declared:
        n = int(overlap_length)
        if n < 1:
            raise ValueError("overlap_length must be at least 1")
    else:
        n = longest_exact_overlap(up, down, max_length)

    # --- identity: the whole point ---------------------------------------
    overlap = ""
    if n == 0:
        matched = False
        detail = (
            "no exact overlap of any length between the end of the upstream "
            "fragment and the start of the downstream fragment: these two "
            "pieces do not join"
        )
    elif n > min(len(up), len(down)):
        matched = False
        detail = (
            f"an overlap of {n} nt was claimed but the fragments are only "
            f"{len(up)} and {len(down)} nt long"
        )
    else:
        suffix, prefix = up[-n:], down[:n]
        matched = suffix == prefix
        if matched:
            overlap = suffix
            detail = ""
        else:
            i = _first_difference(suffix, prefix)
            detail = (
                f"the {n} nt junction differs at position {i} "
                f"(upstream {suffix[i]}, downstream {prefix[i]}): "
                f"{suffix} vs {prefix}. These fragments will not join."
            )

    checks.append(_r(
        "overlap_identity",
        "the overlap exists identically at both ends of the junction",
        HARD, matched, float(n) if matched else 0.0, "exact match",
        "NEB assembly guidance; the no-valid-overlap failure mode, "
        "Bioinfoysis re-grade arXiv 2609.03871",
        detail,
    ))

    effective = n if matched else 0

    # --- length ----------------------------------------------------------
    checks.append(_r(
        "overlap_min_length", f"overlap at least {OVERLAP_MIN_NT} nt", HARD,
        effective >= OVERLAP_MIN_NT, float(effective),
        f">= {OVERLAP_MIN_NT} nt", "NEB (plan section 3.2: hard minimum 15)",
        "" if effective >= OVERLAP_MIN_NT else
        f"{effective} nt is below the 15 nt minimum for reliable assembly",
    ))
    checks.append(_r(
        "overlap_preferred_length",
        f"prefer an overlap of at least {OVERLAP_PREFERRED_NT} nt", SOFT,
        effective >= OVERLAP_PREFERRED_NT, float(effective),
        f">= {OVERLAP_PREFERRED_NT} nt", "NEB: 20 beats 15",
        "" if effective >= OVERLAP_PREFERRED_NT else
        f"{effective} nt works but 20 nt assembles more reliably",
    ))
    in_range = lo <= effective <= hi
    checks.append(_r(
        "overlap_recommended_range",
        f"overlap {lo}-{hi} nt for {fragment_count} fragments", SOFT,
        in_range, float(effective), f"{lo}-{hi} nt",
        "NEB: 15-20 nt for 2-3 fragments, 20-30 nt for 4-6",
        "" if in_range else
        f"{effective} nt is outside the {lo}-{hi} nt range NEB gives for "
        f"{fragment_count} fragments",
    ))
    if fragment_count > 6:
        checks.append(_r(
            "fragment_count", "fragment count within published guidance", SOFT,
            False, float(fragment_count), "2-6 fragments",
            "NEB tabulates 2-3 and 4-6 fragments only",
            f"{fragment_count} fragments is past NEB's table; the 4-6 rule is "
            "carried forward, which is an extrapolation",
        ))

    # --- thermodynamics of the overlap, SOFT ------------------------------
    if not overlap or len(overlap) < 2:
        checks.append(_r(
            "overlap_tm", "overlap melting temperature", SOFT, True, None,
            f">= {OVERLAP_MIN_TM:.0f} C",
            "NEB Gibson guidance (50 C assembly incubation); see module "
            "docstring, not re-verified",
            "not computed",
        ))
        checks.append(_r(
            "overlap_gc", "overlap GC content", SOFT, True, None,
            f"{OVERLAP_GC_MIN:.0f}-{OVERLAP_GC_MAX:.0f}%",
            "design choice, not a published NEB figure", "not computed",
        ))
    else:
        try:
            tm = thermo.tm_nn(overlap, **con.tm_kwargs())
        except (thermo.SequenceError, ValueError) as exc:
            checks.append(_r(
                "overlap_tm", "overlap melting temperature", SOFT, True, None,
                f">= {OVERLAP_MIN_TM:.0f} C", "NEB Gibson guidance",
                f"not computed: {exc}",
            ))
        else:
            tm_ok = tm >= OVERLAP_MIN_TM
            checks.append(_r(
                "overlap_tm", "overlap melting temperature", SOFT, tm_ok,
                round(tm, 2), f">= {OVERLAP_MIN_TM:.0f} C",
                "NEB Gibson guidance (50 C assembly incubation); computed in "
                "the rule table's PCR buffer, so not NEB's own number",
                "" if tm_ok else
                f"{tm:.1f} C is low for a 50 C assembly; lengthen the overlap",
            ))
        try:
            gc = 100.0 * thermo.gc_fraction(overlap)
        except thermo.SequenceError as exc:
            checks.append(_r(
                "overlap_gc", "overlap GC content", SOFT, True, None,
                f"{OVERLAP_GC_MIN:.0f}-{OVERLAP_GC_MAX:.0f}%",
                "design choice", f"not computed: {exc}",
            ))
        else:
            gc_ok = OVERLAP_GC_MIN <= gc <= OVERLAP_GC_MAX
            checks.append(_r(
                "overlap_gc", "overlap GC content", SOFT, gc_ok, round(gc, 1),
                f"{OVERLAP_GC_MIN:.0f}-{OVERLAP_GC_MAX:.0f}%",
                "design choice, not a published NEB figure",
                "" if gc_ok else f"{gc:.1f}% GC anneals poorly at the junction",
            ))

    # --- uniqueness, SOFT -------------------------------------------------
    if overlap:
        copies = up.count(overlap) + down.count(overlap)
        unique = copies <= 2   # once at each side of the junction
        checks.append(_r(
            "overlap_unique", "the overlap occurs once per fragment", SOFT,
            unique, float(copies), "<= 1 copy in each fragment",
            "design choice: a repeated overlap invites misassembly",
            "" if unique else
            f"the overlap sequence occurs {copies} times across the two "
            "fragments, so they can join in the wrong place",
        ))

    return checks


@dataclass(frozen=True)
class OverlapDesign:
    """
    The two primers that make one junction, plus the checks on the junction.

    `forward_primer` amplifies the DOWNSTREAM fragment (its annealing region is
    that fragment's 5' end). `reverse_primer` amplifies the UPSTREAM fragment
    (its annealing region is the reverse complement of that fragment's 3' end).
    Whichever one carries the homology tail depends on `placement`; the other
    is returned untailed, because it is still the primer you have to order.
    """

    overlap: str
    placement: str
    fragment_count: int
    forward_primer: TailedPrimer
    reverse_primer: TailedPrimer
    checks: tuple[CheckResult, ...] = ()

    @property
    def overlap_length(self) -> int:
        return len(self.overlap)

    def all_checks(self) -> list[CheckResult]:
        """Junction checks plus both primers'. Rule ids repeat across primers."""
        return (list(self.checks) + list(self.forward_primer.checks)
                + list(self.reverse_primer.checks))

    def amplicons(self, upstream_fragment: str, downstream_fragment: str
                  ) -> tuple[str, str]:
        """
        The two PCR products this design yields, tails included.

        Assembling these two in order must reconstruct
        `upstream_fragment + downstream_fragment`; `assemble` does exactly that
        and the test suite asserts it.
        """
        up = thermo.normalise(upstream_fragment)
        down = thermo.normalise(downstream_fragment)
        return (up + self.reverse_primer.tail_homology(),
                self.forward_primer.tail + down)

    def validate(self, upstream_fragment: str, downstream_fragment: str
                 ) -> list[CheckResult]:
        """Re-run the junction check against the real fragments, from outside."""
        up_amplicon, down_amplicon = self.amplicons(
            upstream_fragment, downstream_fragment)
        return validate_overlap(
            up_amplicon, down_amplicon, self.overlap_length,
            fragment_count=self.fragment_count)

    def as_dict(self) -> dict:
        return {
            "mode": "overlap_assembly",
            "overlap": self.overlap,
            "overlap_length": self.overlap_length,
            "placement": self.placement,
            "fragment_count": self.fragment_count,
            "forward_primer": self.forward_primer.as_dict(),
            "reverse_primer": self.reverse_primer.as_dict(),
            "checks": [c.as_dict() for c in self.checks],
        }


_PLACEMENTS = ("downstream", "upstream", "split")


def _split_lengths(n: int, placement: str) -> tuple[int, int]:
    """
    How much of an n nt junction each primer carries: (forward, reverse).

    `a + b == n` in every case, so the assembled overlap is n nt whichever
    placement is chosen. `split` gives the odd base to the forward primer.
    """
    if placement == "downstream":
        return n, 0
    if placement == "upstream":
        return 0, n
    return n - n // 2, n // 2


def _junction_sequence(up: str, down: str, n: int, placement: str) -> str:
    """
    The n nt junction as it will read on the plus strand of the assembly.

    It is NOT always the upstream fragment's 3' end: with the homology on the
    upstream fragment's reverse primer the junction is the downstream
    fragment's 5' end, and with a split tail it straddles the two. Computing
    the Tm of the wrong one of those would be exactly the sort of quiet
    bookkeeping error this module is written against.
    """
    a, b = _split_lengths(n, placement)
    return (up[-a:] if a else "") + (down[:b] if b else "")


def design_overlap_tails(
    upstream_fragment: str,
    downstream_fragment: str,
    *,
    fragment_count: int = 2,
    overlap_length: Optional[int] = None,
    max_overlap_length: Optional[int] = None,
    placement: str = "downstream",
    anneal_length: Optional[int] = None,
    constraints: Optional[rules.Constraints] = None,
    name_prefix: str = "",
) -> OverlapDesign:
    """
    Tails that make `upstream_fragment` and `downstream_fragment` join.

    The assembled product is `upstream_fragment + downstream_fragment` with the
    overlap appearing once. Where the homology is carried is a real choice:

    * `"downstream"` (default): the downstream fragment's forward primer gets a
      tail equal to the last N bases of the upstream fragment. One long oligo,
      one ordinary one. This is the usual NEBuilder-style arrangement when one
      fragment is a vector you are not re-amplifying.
    * `"upstream"`: the mirror image, the upstream fragment's reverse primer
      carries the homology.
    * `"split"`: each primer carries half, which is the classic arrangement and
      keeps both oligos short. The junction is still N nt; it is simply
      assembled from the two halves.

    With `overlap_length` left as None the length comes from
    `recommended_overlap(fragment_count)` and is then extended, up to
    `max_overlap_length` (default: NEB's maximum for that fragment count),
    while the overlap Tm sits below `OVERLAP_MIN_TM`. That extension is what
    NEB's own designer does for AT-rich junctions; it never pushes past the
    published range on its own.

    The returned design validates its own junction: `checks` holds the output
    of `validate_overlap` run on the amplicons this design actually produces,
    so a design can never report a junction it did not verify.
    """
    if placement not in _PLACEMENTS:
        raise ValueError(
            f"placement must be one of {', '.join(_PLACEMENTS)}, "
            f"not {placement!r}"
        )
    up = thermo.normalise(upstream_fragment)
    down = thermo.normalise(downstream_fragment)
    con = constraints or rules.OVERLAP_ASSEMBLY
    lo, hi, default_n = recommended_overlap(fragment_count)

    # --- how long an overlap ---------------------------------------------
    if overlap_length is not None:
        n = int(overlap_length)
        if n < 1:
            raise ValueError("overlap_length must be at least 1")
    else:
        ceiling = hi if max_overlap_length is None else int(max_overlap_length)
        n = min(default_n, ceiling)
        while n < ceiling and n < min(len(up), len(down)):
            junction = _junction_sequence(up, down, n, placement)
            try:
                if thermo.tm_nn(junction, **con.tm_kwargs()) >= OVERLAP_MIN_TM:
                    break
            except (thermo.SequenceError, ValueError):
                break
            n += 1

    if n > len(up) or n > len(down):
        raise thermo.SequenceError(
            f"an overlap of {n} nt does not fit: the fragments are "
            f"{len(up)} and {len(down)} nt long"
        )

    a, b = _split_lengths(n, placement)
    fwd_tail = up[-a:] if a else ""
    rev_tail = thermo.reverse_complement(down[:b]) if b else ""

    # --- annealing regions ------------------------------------------------
    fwd_anneal = _choose_anneal(down, con, anneal_length)
    rev_anneal = _choose_anneal(
        thermo.reverse_complement(up), con, anneal_length)

    prefix = f"{name_prefix}_" if name_prefix else ""
    forward = TailedPrimer(
        anneal=fwd_anneal, tail=fwd_tail, name=f"{prefix}junction_fwd",
        role="forward", mode="overlap_assembly",
        purpose="amplifies the downstream fragment; the tail is homology to "
                "the upstream fragment",
    )
    reverse = TailedPrimer(
        anneal=rev_anneal, tail=rev_tail, name=f"{prefix}junction_rev",
        role="reverse", mode="overlap_assembly",
        purpose="amplifies the upstream fragment; the tail is homology to the "
                "downstream fragment",
    )

    forward = _with_checks(forward, con)
    reverse = _with_checks(reverse, con)

    # --- self-check: validate the junction this design actually makes ------
    up_amplicon = up + reverse.tail_homology()
    down_amplicon = forward.tail + down
    checks = validate_overlap(
        up_amplicon, down_amplicon, n,
        fragment_count=fragment_count, constraints=con,
    )

    return OverlapDesign(
        # The junction as it appears on the plus strand of the assembly: the
        # last n bases of the upstream amplicon, which placement has already
        # arranged to be identical to the first n of the downstream one.
        overlap=up_amplicon[-n:],
        placement=placement,
        fragment_count=fragment_count,
        forward_primer=forward,
        reverse_primer=reverse,
        checks=tuple(checks),
    )


def _with_checks(primer: TailedPrimer,
                 constraints: rules.Constraints) -> TailedPrimer:
    """Judge the annealing region by the rule table and the oligo by ours."""
    checks = list(rules.evaluate_primer(primer.anneal, constraints))
    checks.append(_length_check(primer))
    return TailedPrimer(
        anneal=primer.anneal, tail=primer.tail, name=primer.name,
        role=primer.role, mode=primer.mode, purpose=primer.purpose,
        checks=tuple(checks),
    )


def assemble(
    fragments: Sequence[str],
    *,
    min_overlap: int = OVERLAP_MIN_NT,
    circular: bool = False,
    max_search: int = MAX_OVERLAP_SEARCH,
) -> str:
    """
    Join fragments on their exact shared ends, in the order given.

    Each consecutive pair must share an exact suffix/prefix of at least
    `min_overlap` nt; the shared sequence appears once in the result. With
    `circular=True` the last fragment must likewise overlap the first, and that
    junction is closed, giving a plasmid in which every overlap appears once.

    Raises `AssemblyError`, naming the junction, when a pair does not join.
    Refusing is the point: this is the simulation that stops a plausible
    junction being reported as a working one.
    """
    seqs = [thermo.normalise(f) for f in fragments]
    if not seqs:
        raise AssemblyError("no fragments to assemble")
    if len(seqs) == 1 and not circular:
        return seqs[0]

    joined = seqs[0]
    for i, nxt in enumerate(seqs[1:], start=1):
        n = longest_exact_overlap(joined, nxt, max_search)
        if n < min_overlap:
            raise AssemblyError(
                f"fragments {i - 1} and {i} share only {n} nt of exact "
                f"sequence, below the {min_overlap} nt minimum: they do not "
                "join"
            )
        joined = joined + nxt[n:]

    if circular:
        cap = min(max_search, len(joined) - 1)
        n = longest_exact_overlap(joined, joined, cap)
        if n < min_overlap:
            raise AssemblyError(
                f"the last fragment shares only {n} nt with the first, below "
                f"the {min_overlap} nt minimum: the circle does not close"
            )
        joined = joined[:-n]
    return joined


# --------------------------------------------------------------------------
# Restriction cloning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Enzyme:
    """
    One restriction enzyme: its recognition site and the spacer it needs.

    `spacer` is how many bases must sit 5' of the recognition site for the
    enzyme to cut near the end of a fragment. `spacer_sourced` says whether
    that figure is a published per-enzyme number or the 6 bp default; see the
    module docstring for why almost all of them are the default and why that is
    the honest state of this table rather than a gap to fill from memory.
    """

    name: str
    site: str
    spacer: int = DEFAULT_SPACER_BP
    spacer_sourced: bool = False
    note: str = ""

    @property
    def length(self) -> int:
        return len(self.site)

    @property
    def palindromic(self) -> bool:
        return self.site == thermo.reverse_complement(self.site)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "site": self.site,
            "spacer": self.spacer,
            "spacer_sourced": self.spacer_sourced,
            "note": self.note,
        }


def _enzyme(name: str, site: str, spacer: int = DEFAULT_SPACER_BP,
            sourced: bool = False, note: str = "") -> Enzyme:
    return Enzyme(name=name, site=thermo.normalise(site), spacer=spacer,
                  spacer_sourced=sourced, note=note)


#: Common cloning enzymes.
#:
#: SPACER PROVENANCE, and this is the part to read before editing:
#:   * AccI 13 is a PUBLISHED PER-ENZYME FIGURE from NEB's "cleavage close to
#:     the end of DNA fragments" guidance, via the plan's rule table.
#:   * every other spacer here is the 6 bp DEFAULT, not a per-enzyme figure.
#:     NEB publishes per-enzyme values; neb.com 403s automated clients (plan
#:     section 4) so they could not be verified, and inventing them would be
#:     worse than defaulting. `spacer_sourced` carries the distinction into the
#:     envelope, and a caller with a figure can pass `spacer=` explicitly.
#: Recognition sites are facts and are given 5'->3' on the top strand.
ENZYMES: dict[str, Enzyme] = {
    e.name.lower(): e
    for e in (
        _enzyme("EcoRI", "GAATTC"),
        _enzyme("BamHI", "GGATCC"),
        _enzyme("HindIII", "AAGCTT"),
        _enzyme("XhoI", "CTCGAG"),
        _enzyme("NdeI", "CATATG"),
        _enzyme("NcoI", "CCATGG"),
        _enzyme("SalI", "GTCGAC"),
        _enzyme("XbaI", "TCTAGA"),
        _enzyme("NotI", "GCGGCCGC"),
        _enzyme("KpnI", "GGTACC"),
        _enzyme("SacI", "GAGCTC"),
        _enzyme("PstI", "CTGCAG"),
        _enzyme("SphI", "GCATGC"),
        _enzyme("SmaI", "CCCGGG"),
        _enzyme(
            "AccI", "GTMKAC", spacer=13, sourced=True,
            note="NEB's cleavage-close-to-the-end guidance gives AccI at least "
                 "13 bases; the site is degenerate, GT(A/C)(G/T)AC",
        ),
    )
}


def lookup_enzyme(enzyme: Union[str, Enzyme]) -> Enzyme:
    """An `Enzyme` by name (case-insensitive) or passed straight through."""
    if isinstance(enzyme, Enzyme):
        return enzyme
    key = str(enzyme).strip().lower()
    if key not in ENZYMES:
        known = ", ".join(sorted(e.name for e in ENZYMES.values()))
        raise KeyError(
            f"unknown enzyme {enzyme!r}; this table holds {known}. Pass an "
            "Enzyme(name=..., site=...) for anything else."
        )
    return ENZYMES[key]


def _site_positions(site: str, sequence: str, *, circular: bool = False
                    ) -> tuple[int, ...]:
    """
    Distinct plus-strand positions where `site` occurs, both strands searched.

    `verify.find_binding_sites` is used so IUPAC codes (AccI's GTMKAC) and the
    coordinate convention are handled exactly as everywhere else in the engine.
    A palindromic site legitimately matches at one position on both strands, so
    positions are deduplicated: a palindrome cut once must count as one cut,
    not two. A non-palindromic site on the minus strand still counts, because
    the enzyme cuts double-stranded DNA regardless of which strand you wrote
    down.
    """
    s = thermo.normalise(site)
    seq = thermo.normalise(sequence)
    if len(s) > len(seq):
        return ()
    hits = verify.find_binding_sites(
        s, seq, circular=circular, max_mismatches=0, three_prime_anchor=0)
    return tuple(sorted({h.start for h in hits}))


def check_internal_sites(
    enzyme: Union[str, Enzyme],
    insert_sequence: str,
    *,
    circular: bool = False,
) -> CheckResult:
    """
    HARD: does this enzyme also cut inside the insert?

    If it does, the digest that is supposed to open the two engineered ends
    cuts the insert into pieces as well and the clone is destroyed. There is no
    soft version of this: it is the single most consequential check in
    restriction cloning, and it is a pure string search that a language model
    reliably gets wrong, which is exactly the division of labour this engine
    is for.

    One caveat worth stating: an insert containing `N` runs will match any
    site, because `verify.bases_compatible` treats IUPAC codes as the sets they
    denote. That direction is the safe one (a warned-about cut that may not
    exist) but it is not silent, so the detail names the positions.
    """
    enz = lookup_enzyme(enzyme)
    positions = _site_positions(enz.site, insert_sequence, circular=circular)
    count = len(positions)
    clean = count == 0
    if len(enz.site) > len(thermo.normalise(insert_sequence)):
        detail = "the insert is shorter than the recognition site"
    elif clean:
        detail = ""
    else:
        shown = ", ".join(str(p) for p in positions[:8])
        more = "" if count <= 8 else f", and {count - 8} more"
        detail = (
            f"{enz.name} ({enz.site}) cuts inside the insert at 0-based "
            f"position(s) {shown}{more}: this digest would destroy the clone. "
            "Choose another enzyme or a different pair of ends."
        )
    return _r(
        f"internal_site_{enz.name.lower()}",
        f"{enz.name} does not cut inside the insert", HARD, clean,
        float(count), "0 internal sites",
        "standard restriction cloning practice: an internal site is cut by the "
        "same digest that opens the ends",
        detail,
    )


#: Filler patterns for an auto-built spacer. Each is run-free and GC-balanced;
#: the first one that does not create a second copy of the recognition site is
#: used, and the primer-level check below asserts that outcome rather than
#: trusting it.
_FILLER_PATTERNS = ("TGCA", "ACTG", "GTCA", "TACG", "ATCG", "CTAG")


def _spacer_sequence(length: int, site: str) -> str:
    """A neutral spacer of `length` bases that does not duplicate `site`."""
    if length <= 0:
        return ""
    for pattern in _FILLER_PATTERNS:
        candidate = (pattern * (length // len(pattern) + 1))[:length]
        if len(_site_positions(site, candidate + site)) == 1:
            return candidate
    # Nothing clean: return the first and let the HARD primer check report it.
    pattern = _FILLER_PATTERNS[0]
    return (pattern * (length // len(pattern) + 1))[:length]


def _resolve_spacer(spacer: Union[None, int, str], enz: Enzyme) -> str:
    if spacer is None:
        return _spacer_sequence(enz.spacer, enz.site)
    if isinstance(spacer, int):
        if spacer < 0:
            raise ValueError("a spacer cannot be shorter than 0 bases")
        return _spacer_sequence(spacer, enz.site)
    return thermo.normalise(spacer) if spacer else ""


def _restriction_primer(
    enz: Enzyme,
    anneal: str,
    spacer: str,
    role: str,
    name: str,
    constraints: rules.Constraints,
) -> TailedPrimer:
    tail = spacer + enz.site
    primer = TailedPrimer(
        anneal=anneal, tail=tail, name=name, role=role, mode="restriction",
        purpose=f"adds a {enz.name} site ({enz.site}) behind a {len(spacer)} bp "
                "spacer",
    )
    checks = list(rules.evaluate_primer(anneal, constraints))

    # Spacer length, per NEB's cleavage-close-to-the-end guidance.
    enough = len(spacer) >= enz.spacer
    checks.append(_r(
        "spacer_length",
        f"at least {enz.spacer} bp 5' of the {enz.name} site", HARD, enough,
        float(len(spacer)), f">= {enz.spacer} bp",
        "NEB cleavage close to the end of DNA fragments"
        + ("" if enz.spacer_sourced else
           f" ({DEFAULT_SPACER_BP} bp default; no per-enzyme figure for "
           f"{enz.name} is encoded here)"),
        "" if enough else
        f"{len(spacer)} bp will not let {enz.name} cut near the fragment end",
    ))

    # The site is really there, found the same way the rest of the engine
    # finds sequence: by simulation, not by assertion.
    positions = _site_positions(enz.site, primer.sequence)
    correct = positions == (len(spacer),)
    checks.append(_r(
        "site_in_primer",
        f"exactly one {enz.name} site, at the expected position", HARD,
        correct, float(len(positions)), f"1 site at position {len(spacer)}",
        "self-check via verify.find_binding_sites",
        "" if correct else
        f"found {len(positions)} site(s) at {list(positions)}; expected one at "
        f"{len(spacer)}. A second copy means the spacer or the annealing "
        "region recreates the site, and the digest would cut the wrong one.",
    ))
    checks.append(_length_check(primer))
    return TailedPrimer(
        anneal=primer.anneal, tail=primer.tail, name=primer.name,
        role=primer.role, mode=primer.mode, purpose=primer.purpose,
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class RestrictionDesign:
    """Two tailed primers that put an insert between two restriction sites."""

    enzyme_forward: Enzyme
    enzyme_reverse: Enzyme
    forward_primer: TailedPrimer
    reverse_primer: TailedPrimer
    checks: tuple[CheckResult, ...] = ()

    def all_checks(self) -> list[CheckResult]:
        return (list(self.checks) + list(self.forward_primer.checks)
                + list(self.reverse_primer.checks))

    def as_dict(self) -> dict:
        return {
            "mode": "restriction",
            "enzyme_forward": self.enzyme_forward.as_dict(),
            "enzyme_reverse": self.enzyme_reverse.as_dict(),
            "forward_primer": self.forward_primer.as_dict(),
            "reverse_primer": self.reverse_primer.as_dict(),
            "checks": [c.as_dict() for c in self.checks],
        }


def design_restriction_tails(
    enzyme_forward: Union[str, Enzyme],
    enzyme_reverse: Union[str, Enzyme],
    insert: str,
    *,
    anneal_length: Optional[int] = None,
    spacer_forward: Union[None, int, str] = None,
    spacer_reverse: Union[None, int, str] = None,
    constraints: Optional[rules.Constraints] = None,
    circular_insert: bool = False,
    name_prefix: str = "",
) -> RestrictionDesign:
    """
    Primers that amplify `insert` with a restriction site added at each end.

    Each tail is `spacer + recognition site`, 5'->3' as the oligo is ordered.
    The reverse primer's site is likewise written 5'->3' ON THE PRIMER, which
    is how cloning protocols state it; for the palindromic sites in this table
    that reads the same either way, and for a non-palindromic one it is the
    reverse complement that ends up on the top strand of the product.

    Spacer length defaults to the enzyme's entry in `ENZYMES` (6 bp for all but
    AccI, see the module docstring). Pass an int for a different length with an
    auto-built filler, or a string to pin the exact spacer sequence.

    The insert is searched for internal sites for BOTH enzymes, as a HARD
    check, because an enzyme that cuts inside the insert destroys the clone.
    Reading frame is NOT considered; see the module docstring.
    """
    fwd_enz = lookup_enzyme(enzyme_forward)
    rev_enz = lookup_enzyme(enzyme_reverse)
    con = constraints or rules.RESTRICTION
    seq = thermo.normalise(insert)

    fwd_anneal = _choose_anneal(seq, con, anneal_length)
    rev_anneal = _choose_anneal(
        thermo.reverse_complement(seq), con, anneal_length)

    prefix = f"{name_prefix}_" if name_prefix else ""
    forward = _restriction_primer(
        fwd_enz, fwd_anneal, _resolve_spacer(spacer_forward, fwd_enz),
        "forward", f"{prefix}{fwd_enz.name}_fwd", con)
    reverse = _restriction_primer(
        rev_enz, rev_anneal, _resolve_spacer(spacer_reverse, rev_enz),
        "reverse", f"{prefix}{rev_enz.name}_rev", con)

    checks = [
        check_internal_sites(fwd_enz, seq, circular=circular_insert),
        check_internal_sites(rev_enz, seq, circular=circular_insert),
    ]
    distinct = fwd_enz.name != rev_enz.name
    checks.append(_r(
        "distinct_enzymes", "a different enzyme at each end", SOFT, distinct,
        None, "2 distinct enzymes",
        "directional cloning practice: one enzyme at both ends lets the insert "
        "ligate in either orientation",
        "" if distinct else
        f"{fwd_enz.name} at both ends gives a non-directional clone, so half "
        "the colonies carry the insert backwards",
    ))
    return RestrictionDesign(
        enzyme_forward=fwd_enz,
        enzyme_reverse=rev_enz,
        forward_primer=forward,
        reverse_primer=reverse,
        checks=tuple(checks),
    )


# --------------------------------------------------------------------------
# Sequencing / colony-check primers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SequencingDesign:
    """
    One sequencing primer, with where it sits and which way it reads.

    Coordinates follow `verify.py`: 0-based, half-open, always plus-strand.
    `strand` is `"+"` when the primer reads toward higher coordinates and
    `"-"` when it reads toward lower ones, which is the same convention
    `verify.BindingSite` uses.
    """

    primer: TailedPrimer
    start: int
    end: int
    strand: str
    read_offset: int
    feature_position: int
    checks: tuple[CheckResult, ...] = ()

    @property
    def sequence(self) -> str:
        return self.primer.sequence

    def all_checks(self) -> list[CheckResult]:
        return list(self.checks) + list(self.primer.checks)

    def as_dict(self) -> dict:
        return {
            "mode": "sequencing",
            "primer": self.primer.as_dict(),
            "start": self.start,
            "end": self.end,
            "strand": self.strand,
            "read_offset": self.read_offset,
            "feature_position": self.feature_position,
            "checks": [c.as_dict() for c in self.checks],
        }


def _extract(template: str, start: int, length: int, circular: bool
             ) -> tuple[str, int]:
    """(sequence, normalised start). Wraps the origin only when circular."""
    n = len(template)
    if circular:
        start %= n
        return "".join(template[(start + i) % n] for i in range(length)), start
    if start < 0 or start + length > n:
        raise thermo.SequenceError(
            f"a {length} nt primer at {start} falls outside a {n} nt linear "
            "template; move the feature, shorten the offset, or pass "
            "circular=True if this is a plasmid"
        )
    return template[start:start + length], start


def design_sequencing_primer(
    template: str,
    feature_position: int,
    *,
    direction: str = "forward",
    offset: int = READ_OFFSET_DEFAULT,
    length: Optional[int] = None,
    circular: bool = False,
    constraints: Optional[rules.Constraints] = None,
    name: str = "seq_primer",
) -> SequencingDesign:
    """
    A primer that reads toward `feature_position` from `offset` bases away.

    Sequencing and colony-check primers take NO TAIL: nothing is being joined,
    so the full oligo is the annealing region. `TailedPrimer.tail` is empty and
    `display()` is therefore entirely uppercase, which is the correct pydna
    rendering of an untailed primer.

    `feature_position` is a 0-based plus-strand coordinate: for a forward
    primer, the first base of the region of interest; for a reverse primer, the
    half-open end of it, so that the read arrives from the other side. The
    primer is placed so that `offset` template bases are read before the
    feature is reached, which is the point of the whole exercise: the opening
    tens of bases of a Sanger read are low quality, so the junk falls before
    the region you care about rather than on top of it. 50-150 nt is the usual
    window and is checked SOFT; it is a convention, and providers differ.

    A HARD check confirms the primer really does occur exactly once in the
    template, at the coordinates reported, via `verify.find_binding_sites`.
    """
    if direction not in ("forward", "reverse"):
        raise ValueError(
            f"direction must be 'forward' or 'reverse', not {direction!r}")
    if offset < 0:
        raise ValueError("offset cannot be negative")
    seq = thermo.normalise(template)
    con = constraints or rules.SEQUENCING
    n_template = len(seq)
    if feature_position < 0 or feature_position > n_template:
        raise ValueError(
            f"feature_position {feature_position} is outside the "
            f"{n_template} nt template"
        )

    size = int(length) if length is not None else con.length_opt
    if size < 1:
        raise ValueError("length must be at least 1")

    if direction == "forward":
        # The primer stops `offset` bases short of the feature and extends
        # toward it, so its footprint ends at feature_position - offset.
        end = feature_position - offset
        footprint, start = _extract(seq, end - size, size, circular)
        primer_seq = footprint
        strand = verify.PLUS
    else:
        # Mirror image: the primer starts `offset` bases past the feature and
        # extends back toward it, so it is the reverse complement.
        start_pos = feature_position + offset
        footprint, start = _extract(seq, start_pos, size, circular)
        primer_seq = thermo.reverse_complement(footprint)
        strand = verify.MINUS

    end = start + size
    primer = TailedPrimer(
        anneal=primer_seq, tail="", name=name, role="sequencing",
        mode="sequencing",
        purpose=f"reads {direction} toward position {feature_position}, "
                f"{offset} nt away",
        checks=tuple(rules.evaluate_primer(primer_seq, con)),
    )

    checks: list[CheckResult] = []

    in_window = READ_OFFSET_MIN <= offset <= READ_OFFSET_MAX
    checks.append(_r(
        "read_offset",
        f"primer sits {READ_OFFSET_MIN}-{READ_OFFSET_MAX} nt before the feature",
        SOFT, in_window, float(offset),
        f"{READ_OFFSET_MIN}-{READ_OFFSET_MAX} nt",
        "convention: the opening bases of a Sanger read are low quality; the "
        "exact window varies by provider, so this is not a published figure",
        "" if in_window else (
            f"{offset} nt is close enough that the low-quality start of the "
            "read may land on the feature"
            if offset < READ_OFFSET_MIN else
            f"{offset} nt wastes read length before the feature"
        ),
    ))

    # Direction: the read must actually travel toward the feature.
    if direction == "forward":
        toward = (end <= feature_position) if not circular else True
        gap = feature_position - end
    else:
        toward = (start >= feature_position) if not circular else True
        gap = start - feature_position
    checks.append(_r(
        "reads_toward_feature", "the primer extends toward the feature", HARD,
        toward, float(gap), "feature ahead of the 3' end",
        "self-check: direction and coordinates",
        "" if toward else
        f"a {direction} primer at {start}..{end} extends away from "
        f"{feature_position}, so the feature is never read",
    ))

    sites = verify.find_binding_sites(
        primer_seq, seq, circular=circular, max_mismatches=0)
    here = [s for s in sites if s.strand == strand and s.start == start]
    found = len(here) == 1
    checks.append(_r(
        "binds_expected_site",
        "the primer is found in the template where it is reported", HARD,
        found, float(len(here)), "1 site at the reported coordinates",
        "self-check via verify.find_binding_sites",
        "" if found else
        f"the primer was not found on strand {strand} at {start}; the "
        "coordinate bookkeeping is wrong",
    ))
    unique = len(sites) <= (2 if primer_seq == thermo.reverse_complement(primer_seq)
                            else 1)
    checks.append(_r(
        "unique_in_template", "the primer binds the template only once", HARD,
        unique, float(len(sites)), "1 site",
        "a sequencing primer with two sites gives an unreadable mixed trace",
        "" if unique else
        f"{len(sites)} exact sites on this template: "
        + ", ".join(s.describe() for s in sites[:4]),
    ))

    return SequencingDesign(
        primer=primer,
        start=start,
        end=end,
        strand=strand,
        read_offset=offset,
        feature_position=feature_position,
        checks=tuple(checks),
    )
