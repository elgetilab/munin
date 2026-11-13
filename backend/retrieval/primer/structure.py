"""
Secondary structure: hairpins, self-dimers, cross-dimers, 3' end stability.

Built on `thermo.py`, which owns the alphabet, the IUPAC handling and the
SantaLucia 1998 nearest-neighbour table. Nothing here is ported from Primer3's
`thal.c`, primer3-py, UNAFold or any other GPL source. The equations and the
parameter tables are facts reported in papers and are implemented from the
papers; the code is ours, because this service is Apache-2.0 (plan section 4).

What a caller gets
------------------
Every function returns a `Structure`: dH, dS, dG at a stated temperature, the
melting temperature OF THE STRUCTURE in Celsius, the base pairs it found and an
ASCII rendering of the alignment. "No structure" is a real `Structure` with
`found=False`, never None, so a caller never has to branch before reading a
threshold.

THE THRESHOLD CONVENTION, which is easy to get wrong
----------------------------------------------------
Two different communities quote two different numbers and both are in common
use. This module returns both and does not choose for you:

* Primer3's `PRIMER_MAX_SELF_ANY_TH`, `PRIMER_MAX_SELF_END_TH` and
  `PRIMER_MAX_HAIRPIN_TH` (all default 47) are **structure melting
  temperatures in degrees Celsius**. They compare against `Structure.tm`.
  A structure passes when its Tm is BELOW the threshold.
* IDT's widely quoted "keep dimer dG at or above -9 kcal/mol" is a **free
  energy**. It compares against `Structure.dg`. A structure passes when its dG
  is ABOVE (less negative than) the threshold.

Mixing them up silently turns a hard rule into a no-op, so `rules.py` must name
which field each threshold reads.

The model
---------
Both strands are written 5'->3'. In an antiparallel duplex, if base i of
strand 1 pairs with base j of strand 2, then base i+1 pairs with base j-1. So
the problem becomes a co-linear alignment of strand 1 against the REVERSE of
strand 2, which is solved here by dynamic programming over a |s1| x |s2| table:

    H[i][j]  best helix whose innermost pair is (i, j), still open
    C[i][j]  best completed structure contained within [0..i] x [0..j]

    H[i][j] = min( init(i,j) + C[i-1][j-1],            start a new helix
                   H[i-1][j-1] + NN(s1[i-1:i+1]) )     stack onto the helix
    C[i][j] = min( C[i-1][j], C[i][j-1], H[i][j] + init(i,j), 0 )

Scoring is by dG at the requested temperature, dG = dH - T*dS/1000, which is
the standard objective for structure prediction. Only Watson-Crick pairs are
allowed, because `thermo.NN_PARAMS` holds only Watson-Crick stacks; a mismatch
or a bulge therefore ENDS one helix and the next one pays its own initiation
terms. That costs about +2 kcal/mol at 37 C for the two reopened ends, which is
the right order of magnitude for a small internal loop, and it uses only
published initiation parameters rather than a loop table we cannot verify here.
It is an approximation, and it is named as one again under "Known limits".

Because that cost does not grow with loop size, a bulge or internal loop is
capped at `MAX_INTERNAL_LOOP` unpaired bases per side. Published loop tables
stop around there anyway, and without the cap the search happily chains distant
helices across loops it is charging far too little for. Past the cap the two
helices are simply separate structures and the better one is reported.

A hairpin is the same DP run on the sequence against its own reverse, with two
changes: cells are restricted to i + j <= len - 2 - min_loop so that the two
arms cannot overlap, and the innermost pair pays a loop penalty.

Melting temperature of a structure
----------------------------------
A hairpin is unimolecular, so there is no concentration term:
    Tm = 1000*dH / dS
A dimer is bimolecular, so there is:
    Tm = 1000*dH / (dS + R*ln(CT))
with CT = dnac1 (self-dimer: both partners are the same species, as in the
self-complementary branch of `thermo.tm_nn`) or CT = dnac1 - dnac2/2
(cross-dimer between two different primers). Both in mol/L.

Salt
----
`SaltCorrection.SANTALUCIA_1998` adds 0.368 * ln[Na+] to dS once per stacked
pair. For one contiguous helix that is exactly the len-1 phosphate count
`thermo.tm_nn` uses, so a full-length duplex agrees with `thermo` to the last
digit (see `test_palindrome_homodimer_matches_thermo`). For a structure made of
several helices the conventions differ slightly, and this one is stated rather
than inherited. `SaltCorrection.OWCZARZY_2004` is rejected here: it corrects
1/Tm rather than dS, so it yields no corrected dS to build a dG from.

Degenerate primers
------------------
Same contract `thermo.py` set. The point-valued functions refuse an ambiguous
sequence with a clear error naming the `*_range` variant; the `*_range`
variants answer over `thermo.expand_ambiguous()` and report the span plus the
single most stable member, which is the one a designer has to live with. For
dimers the range runs over the CROSS PRODUCT of the two pools, because a
degenerate primer is a population and any member can meet any other.

Known limits, stated rather than buried
---------------------------------------
1. **The hairpin loop penalty is approximate.** `HAIRPIN_LOOP_DG37_REF` below
   is the one number in this module that was not checked against a primary
   source in this environment. The functional form is the Jacobson-Stockmayer
   polymer extrapolation, dG(n) = dG(n_ref) + 1.75*R*T*ln(n/n_ref), which is
   how SantaLucia & Hicks (2004) Annu Rev Biophys Biomol Struct 33:415 extend
   loops past their tabulated sizes. The tabulated short-loop values themselves
   are NOT reproduced here, and the anchor is held as a module constant
   precisely so it can be replaced wholesale once the table is vendored. Until
   then, absolute hairpin dG carries that uncertainty; relative comparisons
   between hairpins of the same loop size do not.
2. No mismatch, dangling-end, terminal-mismatch or coaxial-stacking
   parameters. Only Watson-Crick stacks.
3. No bulge-size or internal-loop-size penalty beyond the helix reinitiation
   described above, which is why loops are capped at `MAX_INTERNAL_LOOP`
   rather than charged properly.
4. The symmetry entropy term is applied to a self-dimer only when the whole
   base-pair set maps onto itself, and it is applied after the search rather
   than inside it.
5. Absolute dG and Tm have NOT been validated against an independent
   implementation. `ntthal` is not available in this environment. Plan layer 1b
   (the CI image carrying `ntthal` and `primer3_core`) is what settles them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# _INIT_AT, _INIT_GC and _SYMMETRY_DS are underscore-private in thermo.py but
# are imported rather than restated here: they are the same SantaLucia 1998
# initiation and symmetry constants, and a second copy would be a second thing
# to get wrong. Promote them in thermo.py if that module ever gets a tidy-up.
from .thermo import (
    NN_PARAMS,
    R_CAL,
    SaltCorrection,
    SequenceError,
    _INIT_AT,
    _INIT_GC,
    _SYMMETRY_DS,
    expand_ambiguous,
    monovalent_equivalent,
    normalise,
    reverse_complement,
    UNAMBIGUOUS,
)

__all__ = [
    "Structure",
    "StructureRange",
    "NO_STRUCTURE_TM",
    "MIN_LOOP",
    "MAX_INTERNAL_LOOP",
    "hairpin",
    "hairpin_range",
    "homodimer",
    "homodimer_range",
    "heterodimer",
    "heterodimer_range",
    "end_stability",
    "end_stability_range",
]

# A structure that does not exist cannot melt. Reported as absolute zero in
# Celsius rather than -inf or NaN so that the value stays JSON-serialisable and
# compares correctly against any "Tm must be below X" rule.
NO_STRUCTURE_TM = -273.15

#: Steric floor on a hairpin loop. A chain cannot turn back on itself in fewer
#: than three unpaired bases.
MIN_LOOP = 3

#: Largest bulge or internal loop, in unpaired bases per side, that two helices
#: may be separated by and still count as one structure. See the module
#: docstring: this module charges a loop only the helix reinitiation terms, so
#: it has no business extrapolating that flat cost over a large loop.
MAX_INTERNAL_LOOP = 4

# --- hairpin loop penalty -------------------------------------------------
# See "Known limits" item 1 in the module docstring. APPROXIMATE.
HAIRPIN_LOOP_DG37_REF = 3.5     # kcal/mol for a loop of HAIRPIN_LOOP_REF_SIZE
HAIRPIN_LOOP_REF_SIZE = 3
LOOP_EXPONENT = 1.75            # Jacobson-Stockmayer chain-closure exponent

_T37_K = 310.15
_KELVIN = 273.15
_R_KCAL = R_CAL / 1000.0

# Dimer `*_range` guard: a 64-fold degenerate primer against itself is 2080
# unordered pairs, which is the realistic worst case in the Dryad benchmark.
MAX_RANGE_PAIRS = 4096

# Traceback markers.
_EMPTY, _UP, _LEFT, _CLOSE = 0, 1, 2, 3
_START, _EXTEND = 0, 1

_WC = {"A": "T", "T": "A", "G": "C", "C": "G"}


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Structure:
    """
    One predicted secondary structure, or a well-formed "nothing found".

    Attributes
    ----------
    kind
        ``hairpin``, ``homodimer``, ``heterodimer`` or ``end_stability``.
    found
        False means no structure was predicted. dg is then 0.0 and tm is
        `NO_STRUCTURE_TM`, so both threshold conventions pass naturally.
    dg
        Free energy in kcal/mol at `temperature`. MORE NEGATIVE IS MORE
        STABLE. Compare against IDT-style dG thresholds (-9 kcal/mol).
    dh, ds
        kcal/mol and cal/(mol*K).
    tm
        Melting temperature of THE STRUCTURE, in Celsius. Compare against
        Primer3's `PRIMER_MAX_*_TH` family (default 47). This is NOT the
        primer's own Tm against its template; `thermo.tm_nn` gives that.
    pairs
        ``(index in seq1, index in seq2)`` for every base pair, 0-based, both
        indices into the 5'->3' sequence as given. For a hairpin both indices
        refer to the same sequence and i < j always.
    loop_size
        Unpaired bases in the hairpin loop; 0 for everything else.
    ascii_art
        A human-readable rendering of the alignment.
    """

    kind: str
    found: bool
    dg: float
    dh: float
    ds: float
    tm: float
    temperature: float
    seq1: str
    seq2: str
    pairs: tuple[tuple[int, int], ...] = ()
    loop_size: int = 0
    ascii_art: str = ""

    def __bool__(self) -> bool:
        return self.found

    @property
    def n_pairs(self) -> int:
        return len(self.pairs)


@dataclass(frozen=True)
class StructureRange:
    """
    The span of a structure prediction over a degenerate primer population.

    `most_stable` is the single worst case a designer has to live with: the
    member with the lowest (most negative) dG. `max_tm` is the worst case under
    the Primer3 convention, and it may come from a different member, which is
    why both are reported rather than one summary number.
    """

    kind: str
    n_variants: int
    n_found: int
    min_dg: float
    max_dg: float
    min_tm: float
    max_tm: float
    most_stable: Structure = field(repr=False)

    @property
    def found(self) -> bool:
        return self.n_found > 0

    def __bool__(self) -> bool:
        return self.n_found > 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _require_unambiguous(seq: str, func: str, suggestion: str) -> str:
    s = normalise(seq)
    if set(s) - UNAMBIGUOUS:
        raise SequenceError(
            f"{func}() needs an unambiguous sequence; "
            f"use {suggestion}() for a degenerate primer"
        )
    return s


def _dg(dh: float, ds: float, temp_k: float) -> float:
    """Gibbs free energy in kcal/mol. dh is kcal/mol, ds is cal/(mol*K)."""
    return dh - temp_k * ds / 1000.0


def _init_terms(base: str) -> tuple[float, float]:
    """Helix initiation for a terminal Watson-Crick pair (SantaLucia 1998)."""
    return _INIT_AT if base in "AT" else _INIT_GC


def _salt_ds_per_stack(
    salt_correction: str,
    na: float,
    k: float,
    tris: float,
    mg: float,
    dntp: float,
) -> float:
    """
    dS added per stacked pair, SantaLucia 1998: 0.368 * ln[Na+ in mol/L].

    Applied per stack so that the DP objective stays additive and so that one
    contiguous helix of length L picks up exactly L-1 terms, matching the
    phosphate count `thermo.tm_nn` uses.
    """
    if salt_correction == SaltCorrection.NONE:
        return 0.0
    if salt_correction != SaltCorrection.SANTALUCIA_1998:
        raise ValueError(
            f"structure prediction supports {SaltCorrection.NONE!r} and "
            f"{SaltCorrection.SANTALUCIA_1998!r}; {salt_correction!r} is not "
            "usable here because it corrects 1/Tm rather than dS, so it "
            "yields no corrected entropy to build a dG from"
        )
    mon = monovalent_equivalent(na=na, k=k, tris=tris, mg=mg, dntp=dntp)
    if mon <= 0:
        raise ValueError("monovalent cation concentration must be positive")
    return 0.368 * math.log(mon / 1000.0)


def _hairpin_loop_terms(size: int) -> tuple[float, float]:
    """
    (dH, dS) of a hairpin loop of `size` unpaired bases.

    Treated as purely entropic (dH = 0), which is the usual simplification
    where no loop enthalpy is tabulated, with the Jacobson-Stockmayer
    extrapolation in loop length. See "Known limits" item 1: the anchor value
    is approximate.
    """
    dg37 = HAIRPIN_LOOP_DG37_REF + (
        LOOP_EXPONENT * _R_KCAL * _T37_K * math.log(size / HAIRPIN_LOOP_REF_SIZE)
    )
    # dH = 0 => dG(T) = -T*dS/1000, so fix dS from the 37 C value.
    return 0.0, -1000.0 * dg37 / _T37_K


def _tm_unimolecular(dh: float, ds: float) -> float:
    if ds >= 0 or dh >= 0:
        return NO_STRUCTURE_TM
    return 1000.0 * dh / ds - _KELVIN


def _tm_bimolecular(dh: float, ds: float, ct_molar: float) -> float:
    if dh >= 0 or ct_molar <= 0:
        return NO_STRUCTURE_TM
    denom = ds + R_CAL * math.log(ct_molar)
    if denom >= 0:
        return NO_STRUCTURE_TM
    tm = 1000.0 * dh / denom - _KELVIN
    return tm if tm > NO_STRUCTURE_TM else NO_STRUCTURE_TM


def _no_structure(
    kind: str, seq1: str, seq2: str, temperature: float
) -> Structure:
    return Structure(
        kind=kind,
        found=False,
        dg=0.0,
        dh=0.0,
        ds=0.0,
        tm=NO_STRUCTURE_TM,
        temperature=temperature,
        seq1=seq1,
        seq2=seq2,
        pairs=(),
        loop_size=0,
        ascii_art="no structure found",
    )


# --------------------------------------------------------------------------
# The alignment DP
# --------------------------------------------------------------------------


class _Fold:
    """
    Nearest-neighbour alignment of `s1` (5'->3') against `s2rev`, which is
    strand 2 written 3'->5' so that the two run co-linearly.

    Three tables, all indexed by the cell (i, j) that is a candidate base pair:

    ``h``   best helix whose innermost pair is (i, j), still open
    ``e``   that helix closed off, so a complete structure ending at (i, j)
    ``c``   running minimum of ``e`` over [0..i] x [0..j], the best structure
            found anywhere so far

    A new helix may start from any closed structure that ended within
    `max_internal_loop` unpaired bases on both strands, which is the bulge and
    internal-loop allowance.

    `cell_limit`, when given, forbids every cell with i + j > cell_limit. That
    is how the hairpin keeps its two arms from overlapping.
    """

    def __init__(
        self,
        s1: str,
        s2rev: str,
        temp_k: float,
        salt_ds: float,
        cell_limit: Optional[int] = None,
        max_internal_loop: int = MAX_INTERNAL_LOOP,
    ) -> None:
        self.s1 = s1
        self.s2rev = s2rev
        self.temp_k = temp_k
        n, m = len(s1), len(s2rev)
        self.n, self.m = n, m

        inf = float("inf")
        self.hh = [[0.0] * m for _ in range(n)]
        self.hs = [[0.0] * m for _ in range(n)]
        self.hback = [[_START] * m for _ in range(n)]
        self.hprev: list[list[Optional[tuple[int, int]]]] = [
            [None] * m for _ in range(n)
        ]
        self.hvalid = [[False] * m for _ in range(n)]

        self.edh = [[0.0] * m for _ in range(n)]
        self.eds = [[0.0] * m for _ in range(n)]
        self.eg = [[inf] * m for _ in range(n)]

        self.cg = [[0.0] * m for _ in range(n)]
        self.cback = [[_EMPTY] * m for _ in range(n)]

        wc = _WC
        params = NN_PARAMS
        scale = temp_k / 1000.0
        for i in range(n):
            a = s1[i]
            partner = wc[a]
            ih, is_ = _init_terms(a)
            init_dg = ih - scale * is_
            hvi = self.hvalid[i]
            jmax = m - 1 if cell_limit is None else min(m - 1, cell_limit - i)
            lo_i = max(0, i - 1 - max_internal_loop)
            for j in range(m):
                if j <= jmax and s2rev[j] == partner:
                    # Start a fresh helix, optionally after an earlier one.
                    best_h, best_s = ih, is_
                    best_dg = init_dg
                    prev: Optional[tuple[int, int]] = None
                    lo_j = max(0, j - 1 - max_internal_loop)
                    for pi in range(lo_i, i):
                        erow = self.eg[pi]
                        for pj in range(lo_j, j):
                            cand = erow[pj]
                            if cand < 0.0 and cand + init_dg < best_dg:
                                best_dg = cand + init_dg
                                best_h = self.edh[pi][pj] + ih
                                best_s = self.eds[pi][pj] + is_
                                prev = (pi, pj)
                    back = _START

                    # Or stack onto the helix that ends one cell back.
                    if i > 0 and j > 0 and self.hvalid[i - 1][j - 1]:
                        sh, ss = params[s1[i - 1:i + 1]]
                        eh = self.hh[i - 1][j - 1] + sh
                        es = self.hs[i - 1][j - 1] + ss + salt_ds
                        if eh - scale * es < best_dg:
                            best_h, best_s, back = eh, es, _EXTEND
                            prev = None

                    self.hh[i][j] = best_h
                    self.hs[i][j] = best_s
                    self.hback[i][j] = back
                    self.hprev[i][j] = prev
                    hvi[j] = True

                    closed_h = best_h + ih
                    closed_s = best_s + is_
                    self.edh[i][j] = closed_h
                    self.eds[i][j] = closed_s
                    self.eg[i][j] = closed_h - scale * closed_s

                # c: best complete structure anywhere inside [0..i] x [0..j].
                best, back = 0.0, _EMPTY
                if i > 0 and self.cg[i - 1][j] < best:
                    best, back = self.cg[i - 1][j], _UP
                if j > 0 and self.cg[i][j - 1] < best:
                    best, back = self.cg[i][j - 1], _LEFT
                if hvi[j] and self.eg[i][j] < best:
                    best, back = self.eg[i][j], _CLOSE
                self.cg[i][j] = best
                self.cback[i][j] = back

    # -- results ----------------------------------------------------------

    def best_closed(self) -> tuple[float, float, list[tuple[int, int]]]:
        """(dH, dS, pairs) of the best complete structure. Empty if none."""
        if self.n == 0 or self.m == 0:
            return 0.0, 0.0, []
        i, j = self.n - 1, self.m - 1
        while True:
            back = self.cback[i][j]
            if back == _EMPTY:
                return 0.0, 0.0, []
            if back == _UP:
                i -= 1
            elif back == _LEFT:
                j -= 1
            else:
                return self.edh[i][j], self.eds[i][j], self._chain(i, j)

    def best_hairpin(
        self, seq_len: int, min_loop: int
    ) -> tuple[float, float, list[tuple[int, int]], int]:
        """
        Best hairpin: minimise over every possible innermost pair.

        The innermost pair closes the loop, so it pays the closing initiation
        terms plus the loop penalty; everything outward of it is whatever the
        helix tables found.
        """
        best = 0.0
        out: Optional[tuple[float, float, int, int, int]] = None
        scale = self.temp_k / 1000.0
        for i in range(self.n):
            hvi = self.hvalid[i]
            for j in range(self.m):
                if not hvi[j]:
                    continue
                loop = seq_len - 2 - i - j
                if loop < min_loop:
                    continue
                lh, ls = _hairpin_loop_terms(loop)
                dh = self.edh[i][j] + lh
                ds = self.eds[i][j] + ls
                cand = dh - scale * ds
                if cand < best:
                    best = cand
                    out = (dh, ds, i, j, loop)
        if out is None:
            return 0.0, 0.0, [], 0
        dh, ds, i, j, loop = out
        return dh, ds, self._chain(i, j), loop

    # -- traceback --------------------------------------------------------

    def _chain(self, i: int, j: int) -> list[tuple[int, int]]:
        """Every base pair in the structure whose last pair is (i, j)."""
        pairs: list[tuple[int, int]] = []
        cell: Optional[tuple[int, int]] = (i, j)
        while cell is not None:
            ci, cj = cell
            while self.hback[ci][cj] == _EXTEND:
                pairs.append((ci, cj))
                ci -= 1
                cj -= 1
            pairs.append((ci, cj))
            cell = self.hprev[ci][cj]
        pairs.sort()
        return pairs


# --------------------------------------------------------------------------
# ASCII rendering
# --------------------------------------------------------------------------


def _render_duplex(s1: str, s2: str, pairs: list[tuple[int, int]]) -> str:
    """
    Three-line rendering. Top strand 5'->3', bottom strand 3'->5' beneath it,
    so a paired column really is a base pair.

    Unpaired bases between two helices are shown in place, with the shorter
    side padded by '-' so the columns stay honest about the gap.
    """
    bottom_src = s2[::-1]
    cols = [(i, len(s2) - 1 - j) for i, j in pairs]  # (i, index into bottom_src)
    top, mid, bot = [], [], []

    first_i, first_p = cols[0]
    lead_top = s1[:first_i]
    lead_bot = bottom_src[:first_p]
    width = max(len(lead_top), len(lead_bot))
    top.append(lead_top.rjust(width))
    bot.append(lead_bot.rjust(width))
    mid.append(" " * width)

    for idx, (i, p) in enumerate(cols):
        top.append(s1[i])
        bot.append(bottom_src[p])
        mid.append("|")
        if idx + 1 < len(cols):
            ni, np_ = cols[idx + 1]
            gap_top = s1[i + 1:ni]
            gap_bot = bottom_src[p + 1:np_]
            width = max(len(gap_top), len(gap_bot))
            if width:
                top.append(gap_top.ljust(width, "-"))
                bot.append(gap_bot.ljust(width, "-"))
                mid.append(" " * width)

    last_i, last_p = cols[-1]
    tail_top = s1[last_i + 1:]
    tail_bot = bottom_src[last_p + 1:]
    width = max(len(tail_top), len(tail_bot))
    top.append(tail_top.ljust(width))
    bot.append(tail_bot.ljust(width))
    mid.append(" " * width)

    return (
        f"5' {''.join(top)} 3'\n"
        f"   {''.join(mid)}\n"
        f"3' {''.join(bot)} 5'"
    )


def _render_hairpin(seq: str, pairs: list[tuple[int, int]], loop: int) -> str:
    """
    The 5' arm on top, the 3' arm beneath it written 3'->5', loop to the right.

    Example shape:
        5' CAGTGCCA--\\
           ||||||||   TTTT
        3' GTCACGGT--/
    """
    inner_i = max(i for i, _ in pairs)
    inner_j = min(j for _, j in pairs)
    arm5 = seq[:inner_i + 1]
    arm3 = seq[inner_j:]
    loop_seq = seq[inner_i + 1:inner_j]
    rel = [(i, len(arm3) - 1 - (j - inner_j)) for i, j in pairs]

    bottom_src = arm3[::-1]
    top, mid, bot = [], [], []
    for idx, (i, p) in enumerate(rel):
        top.append(arm5[i])
        bot.append(bottom_src[p])
        mid.append("|")
        if idx + 1 < len(rel):
            ni, np_ = rel[idx + 1]
            gap_top = arm5[i + 1:ni]
            gap_bot = bottom_src[p + 1:np_]
            width = max(len(gap_top), len(gap_bot))
            if width:
                top.append(gap_top.ljust(width, "-"))
                bot.append(gap_bot.ljust(width, "-"))
                mid.append(" " * width)

    lead_top = arm5[:rel[0][0]]
    lead_bot = bottom_src[:rel[0][1]]
    width = max(len(lead_top), len(lead_bot))
    head = (lead_top.rjust(width), " " * width, lead_bot.rjust(width))

    body_top = head[0] + "".join(top)
    body_mid = head[1] + "".join(mid)
    body_bot = head[2] + "".join(bot)
    return (
        f"5' {body_top}--\\\n"
        f"   {body_mid}   {loop_seq}\n"
        f"3' {body_bot}--/   loop {loop} nt"
    )


# --------------------------------------------------------------------------
# Public API: hairpin
# --------------------------------------------------------------------------


def hairpin(
    seq: str,
    *,
    temperature: float = 37.0,
    min_loop: int = MIN_LOOP,
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
    salt_correction: str = SaltCorrection.SANTALUCIA_1998,
    max_dg: float = 0.0,
    max_internal_loop: int = MAX_INTERNAL_LOOP,
) -> Structure:
    """
    The most stable intramolecular fold of a single oligo.

    `tm` is the melting temperature of the hairpin, which is what Primer3's
    `PRIMER_MAX_HAIRPIN_TH` (default 47 C) compares against. `dg` is the free
    energy at `temperature`, which is what an IDT-style dG rule compares
    against. A hairpin is unimolecular, so its Tm does not depend on primer
    concentration.

    `min_loop` defaults to 3 because a chain cannot turn back on itself in
    fewer unpaired bases. `max_dg` is the stability a fold must beat to be
    reported at all; the default 0.0 reports only folds that form
    spontaneously at `temperature`.

    Raises `SequenceError` on a degenerate sequence: use `hairpin_range`.
    """
    s = _require_unambiguous(seq, "hairpin", "hairpin_range")
    temp_k = temperature + _KELVIN
    salt_ds = _salt_ds_per_stack(salt_correction, na, k, tris, mg, dntp)
    n = len(s)

    # Two base pairs plus the loop is the smallest hairpin worth reporting.
    limit = n - 2 - min_loop
    if n < 4 + min_loop or limit < 0:
        return _no_structure("hairpin", s, s, temperature)

    fold = _Fold(
        s, s[::-1], temp_k, salt_ds,
        cell_limit=limit, max_internal_loop=max_internal_loop,
    )
    dh, ds, cols, loop = fold.best_hairpin(n, min_loop)
    if not cols:
        return _no_structure("hairpin", s, s, temperature)

    pairs = sorted((i, n - 1 - j) for i, j in cols)
    dg = _dg(dh, ds, temp_k)
    if dg > max_dg:
        return _no_structure("hairpin", s, s, temperature)
    return Structure(
        kind="hairpin",
        found=True,
        dg=dg,
        dh=dh,
        ds=ds,
        tm=_tm_unimolecular(dh, ds),
        temperature=temperature,
        seq1=s,
        seq2=s,
        pairs=tuple(pairs),
        loop_size=loop,
        ascii_art=_render_hairpin(s, pairs, loop),
    )


# --------------------------------------------------------------------------
# Public API: dimers
# --------------------------------------------------------------------------


def _dimer(
    kind: str,
    s1: str,
    s2: str,
    temperature: float,
    ct_molar: float,
    salt_ds: float,
    max_dg: float,
    apply_symmetry: bool,
    max_internal_loop: int,
) -> Structure:
    temp_k = temperature + _KELVIN
    fold = _Fold(
        s1, s2[::-1], temp_k, salt_ds, max_internal_loop=max_internal_loop
    )
    dh, ds, cols = fold.best_closed()
    if not cols:
        return _no_structure(kind, s1, s2, temperature)

    pairs = sorted((i, len(s2) - 1 - j) for i, j in cols)
    if apply_symmetry:
        pairset = set(pairs)
        if all((j, i) in pairset for i, j in pairs):
            ds += _SYMMETRY_DS

    dg = _dg(dh, ds, temp_k)
    if dg > max_dg:
        return _no_structure(kind, s1, s2, temperature)
    return Structure(
        kind=kind,
        found=True,
        dg=dg,
        dh=dh,
        ds=ds,
        tm=_tm_bimolecular(dh, ds, ct_molar),
        temperature=temperature,
        seq1=s1,
        seq2=s2,
        pairs=tuple(pairs),
        loop_size=0,
        ascii_art=_render_duplex(s1, s2, pairs),
    )


def heterodimer(
    seq1: str,
    seq2: str,
    *,
    temperature: float = 37.0,
    dnac1: float = 25.0,
    dnac2: float = 25.0,
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
    salt_correction: str = SaltCorrection.SANTALUCIA_1998,
    max_dg: float = 0.0,
    max_internal_loop: int = MAX_INTERNAL_LOOP,
) -> Structure:
    """
    The most stable duplex two different primers can form with each other.

    `tm` compares against Primer3's `PRIMER_MAX_COMPL_ANY_TH` (default 47 C);
    `dg` compares against an IDT-style dG rule. The structure is bimolecular,
    so its Tm depends on primer concentration through CT = dnac1 - dnac2/2 with
    `dnac1` the strand in excess, both in nM.

    Symmetric in its arguments: `heterodimer(a, b)` and `heterodimer(b, a)`
    describe the same duplex and report the same energies.

    Raises `SequenceError` on a degenerate sequence: use `heterodimer_range`.
    """
    a = _require_unambiguous(seq1, "heterodimer", "heterodimer_range")
    b = _require_unambiguous(seq2, "heterodimer", "heterodimer_range")
    ct = (dnac1 - dnac2 / 2.0) * 1e-9
    if ct <= 0:
        raise ValueError(
            "effective strand concentration must be positive "
            "(dnac1 has to exceed dnac2/2)"
        )
    salt_ds = _salt_ds_per_stack(salt_correction, na, k, tris, mg, dntp)
    return _dimer(
        "heterodimer", a, b, temperature, ct, salt_ds, max_dg,
        apply_symmetry=False, max_internal_loop=max_internal_loop,
    )


def homodimer(
    seq: str,
    *,
    temperature: float = 37.0,
    dnac1: float = 25.0,
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
    salt_correction: str = SaltCorrection.SANTALUCIA_1998,
    max_dg: float = 0.0,
    max_internal_loop: int = MAX_INTERNAL_LOOP,
) -> Structure:
    """
    The most stable duplex a primer can form with another copy of itself.

    Both partners are the same species, so the concentration term is CT =
    dnac1 (nM), matching the self-complementary branch of `thermo.tm_nn`. When
    the predicted base-pair set maps onto itself the SantaLucia symmetry
    entropy term is applied, so a fully self-complementary oligo reproduces
    `thermo.nn_thermodynamics` exactly.

    `tm` compares against Primer3's `PRIMER_MAX_SELF_ANY_TH` (default 47 C);
    `dg` compares against an IDT-style dG rule.

    Raises `SequenceError` on a degenerate sequence: use `homodimer_range`.
    """
    s = _require_unambiguous(seq, "homodimer", "homodimer_range")
    ct = dnac1 * 1e-9
    if ct <= 0:
        raise ValueError("strand concentration must be positive")
    salt_ds = _salt_ds_per_stack(salt_correction, na, k, tris, mg, dntp)
    return _dimer(
        "homodimer", s, s, temperature, ct, salt_ds, max_dg,
        apply_symmetry=True, max_internal_loop=max_internal_loop,
    )


# --------------------------------------------------------------------------
# Public API: 3' end stability
# --------------------------------------------------------------------------


def end_stability(
    seq: str,
    n: int = 5,
    *,
    temperature: float = 37.0,
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
    salt_correction: str = SaltCorrection.SANTALUCIA_1998,
    dnac1: float = 25.0,
    dnac2: float = 25.0,
    include_initiation: bool = False,
) -> Structure:
    """
    Free energy of the last `n` bases at the 3' end, paired to their complement.

    The Rychlik (1990, in "PCR Strategies"/Methods guidance, and the basis of
    Primer3's `PRIMER_MAX_END_STABILITY`) concept: a 3' end that is too stable
    stays annealed at a mismatched site long enough for the polymerase to
    extend it, so an over-stable 3' terminus promotes mispriming. The usual
    window is the terminal pentamer, hence `n = 5`.

    Sign convention: `dg` is negative for a stable end, so MORE NEGATIVE MEANS
    MORE MISPRIMING RISK, and a rule reads "dg must be above X". Some tools
    quote the same quantity with the sign flipped; this one does not.

    `include_initiation` controls whether the SantaLucia helix initiation terms
    are added to the stacking sum. It defaults to False because "3' end
    stability" is conventionally the sum of the nearest-neighbour stacks in the
    terminal window, but which convention a given tool uses could not be
    verified here, so it is a flag rather than a silent choice. `tm` is
    computed from whichever dH/dS the flag selects; note that the melting
    temperature of a pentamer is not physically meaningful and is reported only
    so the dataclass has one shape.

    Raises `SequenceError` on a degenerate sequence: use `end_stability_range`.
    """
    s = _require_unambiguous(seq, "end_stability", "end_stability_range")
    if n < 2:
        raise SequenceError("end_stability needs a window of at least 2 bases")
    if len(s) < n:
        raise SequenceError(
            f"sequence of {len(s)} bases is shorter than the {n}-base 3' window"
        )
    window = s[-n:]
    comp = reverse_complement(window)
    salt_ds = _salt_ds_per_stack(salt_correction, na, k, tris, mg, dntp)
    temp_k = temperature + _KELVIN

    dh = 0.0
    ds = 0.0
    for i in range(n - 1):
        d_h, d_s = NN_PARAMS[window[i:i + 2]]
        dh += d_h
        ds += d_s + salt_ds
    if include_initiation:
        for end in (window[0], window[-1]):
            i_h, i_s = _init_terms(end)
            dh += i_h
            ds += i_s

    ct = (dnac1 - dnac2 / 2.0) * 1e-9
    pairs = tuple((i, n - 1 - i) for i in range(n))
    art = (
        f"5' {window} 3'   3' end, last {n} nt\n"
        f"   {'|' * n}\n"
        f"3' {comp[::-1]} 5'"
    )
    return Structure(
        kind="end_stability",
        found=True,
        dg=_dg(dh, ds, temp_k),
        dh=dh,
        ds=ds,
        tm=_tm_bimolecular(dh, ds, ct),
        temperature=temperature,
        seq1=window,
        seq2=comp,
        pairs=pairs,
        loop_size=0,
        ascii_art=art,
    )


# --------------------------------------------------------------------------
# Degenerate variants
# --------------------------------------------------------------------------


def _summarise(
    kind: str, results: list[Structure], n_variants: int
) -> StructureRange:
    found = [r for r in results if r.found]
    if not found:
        empty = results[0] if results else None
        return StructureRange(
            kind=kind,
            n_variants=n_variants,
            n_found=0,
            min_dg=0.0,
            max_dg=0.0,
            min_tm=NO_STRUCTURE_TM,
            max_tm=NO_STRUCTURE_TM,
            most_stable=empty,
        )
    dgs = [r.dg for r in results]
    tms = [r.tm for r in results]
    return StructureRange(
        kind=kind,
        n_variants=n_variants,
        n_found=len(found),
        min_dg=min(dgs),
        max_dg=max(dgs),
        min_tm=min(tms),
        max_tm=max(tms),
        most_stable=min(results, key=lambda r: r.dg),
    )


def hairpin_range(seq: str, **kwargs) -> StructureRange:
    """
    `hairpin` over every oligo a degenerate sequence denotes.

    For an unambiguous sequence the range collapses to a point, so a caller can
    use this uniformly. `most_stable` is the member a designer has to live
    with; `max_tm` is the worst case under the Primer3 Tm convention and may
    come from a different member.
    """
    variants = expand_ambiguous(seq)
    results = [hairpin(v, **kwargs) for v in variants]
    return _summarise("hairpin", results, len(variants))


def homodimer_range(seq: str, **kwargs) -> StructureRange:
    """
    Self-dimers across a degenerate primer POOL.

    A degenerate primer is a population, so member X can meet member Y as well
    as another copy of itself. This runs every unordered pair (X, Y), which is
    the honest reading and is why it is quadratic in the degeneracy. It refuses
    past `MAX_RANGE_PAIRS` rather than silently sampling.
    """
    variants = expand_ambiguous(seq)
    n = len(variants)
    total = n * (n + 1) // 2
    if total > MAX_RANGE_PAIRS:
        raise SequenceError(
            f"{n} variants give {total} self-dimer combinations, over the "
            f"limit of {MAX_RANGE_PAIRS}; too degenerate to enumerate"
        )
    dnac1 = kwargs.pop("dnac1", 25.0)
    results: list[Structure] = []
    for a in range(n):
        for b in range(a, n):
            if a == b:
                results.append(homodimer(variants[a], dnac1=dnac1, **kwargs))
            else:
                # Two different members of the pool meeting: a cross-dimer,
                # scored with the same total-strand concentration.
                results.append(
                    heterodimer(
                        variants[a], variants[b],
                        dnac1=dnac1, dnac2=dnac1 / 2.0, **kwargs,
                    )
                )
    return _summarise("homodimer", results, n)


def heterodimer_range(seq1: str, seq2: str, **kwargs) -> StructureRange:
    """
    Cross-dimers over the cross product of two degenerate primer pools.

    Refuses past `MAX_RANGE_PAIRS` combinations rather than silently sampling,
    which would make the reported worst case quietly wrong.
    """
    v1 = expand_ambiguous(seq1)
    v2 = expand_ambiguous(seq2)
    total = len(v1) * len(v2)
    if total > MAX_RANGE_PAIRS:
        raise SequenceError(
            f"{len(v1)} x {len(v2)} = {total} cross-dimer combinations, over "
            f"the limit of {MAX_RANGE_PAIRS}; too degenerate to enumerate"
        )
    results = [heterodimer(a, b, **kwargs) for a in v1 for b in v2]
    return _summarise("heterodimer", results, total)


def end_stability_range(seq: str, n: int = 5, **kwargs) -> StructureRange:
    """
    `end_stability` over every oligo a degenerate sequence denotes.

    Only the terminal window matters, so the expansion is taken over the whole
    sequence and duplicates collapse naturally in the reported span.
    """
    variants = expand_ambiguous(seq)
    results = [end_stability(v, n, **kwargs) for v in variants]
    return _summarise("end_stability", results, len(variants))
