"""
Nearest-neighbour DNA thermodynamics: Tm, enthalpy, entropy, IUPAC handling.

Everything here is implemented from the published equations and parameter
tables, NOT ported from any existing implementation. The parameters are facts
reported in papers; the code is ours. This matters because Primer3 and
primer3-py are GPL-2.0 and this service is Apache-2.0 (plan section 4).

Sources
-------
SantaLucia J Jr (1998) PNAS 95:1460-1465. The "unified" nearest-neighbour
    parameter set, Table 1, plus the initiation and symmetry terms. The same
    values appear in Allawi & SantaLucia (1997) Biochemistry 36:10581.
SantaLucia J Jr (1998), salt correction: dS(Na+) = dS(1M) + 0.368*N*ln[Na+],
    where N is the number of phosphates (len - 1).
Owczarzy R et al. (2004) Biochemistry 43:3537-3554, eq. 22: a correction
    applied to 1/Tm rather than to dS.
von Ahsen N et al. (2001) Clin Chem 47:1956, the divalent-to-monovalent
    conversion used when a Mg2+ concentration is supplied.

Conventions
-----------
dH is kcal/mol, dS is cal/(mol*K), Tm is degrees Celsius. Concentrations are
nanomolar for strands and millimolar for ions, which is how a bench protocol
states them.

For a duplex of two different strands the effective concentration term is
CT = c_high - c_low/2 (SantaLucia 1998). With equal strand concentrations this
is the familiar C_total/4.

Degenerate primers
------------------
IUPAC ambiguity codes are first-class here, not an afterthought. Real eDNA and
barcoding primers carry them routinely: 7 of the 38 pairs in the Dryad
specificity benchmark do, one of them `RARRGGRATTTTGTCSGKGT` with three
different codes. A degenerate primer is a POPULATION of oligos, so its Tm is a
range, not a number. `tm_nn` therefore refuses an ambiguous sequence and
`tm_range` answers for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product
from typing import Iterable, Iterator, Optional

# Gas constant, cal/(mol*K).
R_CAL = 1.987

# --------------------------------------------------------------------------
# Alphabet
# --------------------------------------------------------------------------

#: IUPAC ambiguity code -> the bases it stands for.
IUPAC_CODES: dict[str, str] = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "AG",    # puRine
    "Y": "CT",    # pYrimidine
    "S": "CG",    # Strong (3 H-bonds)
    "W": "AT",    # Weak (2 H-bonds)
    "K": "GT",    # Keto
    "M": "AC",    # aMino
    "B": "CGT",   # not A
    "D": "AGT",   # not C
    "H": "ACT",   # not G
    "V": "ACG",   # not T
    "N": "ACGT",
}

UNAMBIGUOUS = frozenset("ACGT")

_COMPLEMENT = {
    "A": "T", "C": "G", "G": "C", "T": "A",
    "R": "Y", "Y": "R", "S": "S", "W": "W",
    "K": "M", "M": "K", "B": "V", "V": "B",
    "D": "H", "H": "D", "N": "N",
}

# Guard against a combinatorial blow-up: an all-N 20-mer is 4**20 sequences.
MAX_EXPANSION = 4096


class SequenceError(ValueError):
    """A sequence was empty, too short, or held a character that is not DNA."""


def normalise(seq: str) -> str:
    """Upper-case, strip whitespace, map U to T, and validate the alphabet."""
    cleaned = "".join(seq.split()).upper().replace("U", "T")
    if not cleaned:
        raise SequenceError("empty sequence")
    bad = sorted(set(cleaned) - set(IUPAC_CODES))
    if bad:
        raise SequenceError(
            f"not a DNA sequence: unexpected character(s) {', '.join(bad)}"
        )
    return cleaned


def is_ambiguous(seq: str) -> bool:
    """True if the sequence contains any IUPAC code other than A, C, G, T."""
    return bool(set(normalise(seq)) - UNAMBIGUOUS)


def reverse_complement(seq: str) -> str:
    """Reverse complement, preserving IUPAC ambiguity codes."""
    return "".join(_COMPLEMENT[b] for b in reversed(normalise(seq)))


def degeneracy(seq: str) -> int:
    """How many distinct oligos this (possibly degenerate) sequence denotes."""
    n = 1
    for base in normalise(seq):
        n *= len(IUPAC_CODES[base])
    return n


def iter_ambiguous(seq: str) -> Iterator[str]:
    """Yield every concrete ACGT sequence the given sequence denotes."""
    for combo in product(*(IUPAC_CODES[b] for b in normalise(seq))):
        yield "".join(combo)


def expand_ambiguous(seq: str, limit: int = MAX_EXPANSION) -> list[str]:
    """
    Every concrete ACGT sequence this sequence denotes.

    Raises if the expansion would exceed ``limit``: silently truncating would
    make a Tm range quietly wrong, which is worse than refusing.
    """
    n = degeneracy(seq)
    if n > limit:
        raise SequenceError(
            f"sequence expands to {n} oligos, over the limit of {limit}; "
            "too degenerate to enumerate"
        )
    return list(iter_ambiguous(seq))


def gc_fraction(seq: str) -> float:
    """
    Fraction of G or C, counting only unambiguous bases.

    S is counted as GC and W as AT because they are unambiguous about strength;
    every other ambiguity code spans both classes and is excluded from the
    numerator and the denominator alike.
    """
    s = normalise(seq)
    gc = sum(1 for b in s if b in "GCS")
    at = sum(1 for b in s if b in "ATW")
    total = gc + at
    if not total:
        raise SequenceError("no unambiguous bases to compute GC content from")
    return gc / total


# --------------------------------------------------------------------------
# Nearest-neighbour parameters, SantaLucia 1998 unified set
# --------------------------------------------------------------------------

# 5'-XY-3' paired with 3'-X'Y'-5'. Only ten values are independent; the other
# six dinucleotides are reached through reverse complementarity, which
# _nn_params() applies. (dH kcal/mol, dS cal/(mol*K))
_NN_CORE: dict[str, tuple[float, float]] = {
    "AA": (-7.9, -22.2),
    "AT": (-7.2, -20.4),
    "TA": (-7.2, -21.3),
    "CA": (-8.5, -22.7),
    "GT": (-8.4, -22.4),
    "CT": (-7.8, -21.0),
    "GA": (-8.2, -22.2),
    "CG": (-10.6, -27.2),
    "GC": (-9.8, -24.4),
    "GG": (-8.0, -19.9),
}

# Helix initiation, applied once per end according to that end's base pair.
_INIT_AT = (2.3, 4.1)
_INIT_GC = (0.1, -2.8)

# Entropic penalty for a self-complementary duplex.
_SYMMETRY_DS = -1.4


def _nn_params() -> dict[str, tuple[float, float]]:
    """All 16 dinucleotides, filled in from the ten independent values."""
    table = dict(_NN_CORE)
    for dimer, value in _NN_CORE.items():
        rc = reverse_complement(dimer)
        table.setdefault(rc, value)
    return table


NN_PARAMS: dict[str, tuple[float, float]] = _nn_params()


def nn_thermodynamics(seq: str) -> tuple[float, float]:
    """
    (dH, dS) at 1 M Na+ for an unambiguous sequence, including initiation and
    the symmetry correction. dH in kcal/mol, dS in cal/(mol*K).
    """
    s = normalise(seq)
    if set(s) - UNAMBIGUOUS:
        raise SequenceError(
            "nn_thermodynamics needs an unambiguous sequence; "
            "use tm_range() for a degenerate primer"
        )
    if len(s) < 2:
        raise SequenceError("sequence must be at least 2 bases long")

    dh = 0.0
    ds = 0.0
    for i in range(len(s) - 1):
        d_h, d_s = NN_PARAMS[s[i:i + 2]]
        dh += d_h
        ds += d_s

    for end in (s[0], s[-1]):
        d_h, d_s = _INIT_AT if end in "AT" else _INIT_GC
        dh += d_h
        ds += d_s

    if s == reverse_complement(s):
        ds += _SYMMETRY_DS

    return dh, ds


# --------------------------------------------------------------------------
# Salt
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SaltCorrection:
    """Which published salt correction to apply. See module docstring."""

    NONE = "none"
    SANTALUCIA_1998 = "santalucia1998"
    OWCZARZY_2004 = "owczarzy2004"

    ALL = (NONE, SANTALUCIA_1998, OWCZARZY_2004)


def monovalent_equivalent(
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
) -> float:
    """
    Total monovalent cation concentration in mM.

    Tris contributes about half its concentration as cation. When Mg2+ is
    supplied it is converted to a monovalent equivalent by von Ahsen 2001,
    ``120 * sqrt(Mg - dNTP)``, since free dNTPs chelate Mg2+. If dNTPs exceed
    Mg2+ there is no free Mg2+ and the term vanishes.
    """
    mon = na + k + tris / 2.0
    free_mg = mg - dntp
    if free_mg > 0:
        mon += 120.0 * math.sqrt(free_mg)
    return mon


def _salt_mol_per_litre(mon_mM: float) -> float:
    if mon_mM <= 0:
        raise ValueError("monovalent cation concentration must be positive")
    return mon_mM / 1000.0


# --------------------------------------------------------------------------
# Melting temperature
# --------------------------------------------------------------------------


def tm_wallace(seq: str) -> float:
    """
    The Wallace rule, 2*(A+T) + 4*(G+C).

    A rule of thumb for short oligos, included because it is exactly
    verifiable by hand and so makes a good check that a sequence was read
    correctly. Not for design decisions.
    """
    s = normalise(seq)
    if set(s) - UNAMBIGUOUS:
        raise SequenceError("tm_wallace needs an unambiguous sequence")
    at = sum(1 for b in s if b in "AT")
    gc = sum(1 for b in s if b in "GC")
    return 2.0 * at + 4.0 * gc


def tm_nn(
    seq: str,
    *,
    dnac1: float = 25.0,
    dnac2: float = 25.0,
    na: float = 50.0,
    k: float = 0.0,
    tris: float = 0.0,
    mg: float = 0.0,
    dntp: float = 0.0,
    salt_correction: str = SaltCorrection.OWCZARZY_2004,
    self_complementary: Optional[bool] = None,
) -> float:
    """
    Melting temperature in Celsius by the nearest-neighbour model.

    dnac1/dnac2 are the two strand concentrations in nM (dnac1 the one in
    excess); ion concentrations are mM. Raises on a degenerate sequence: call
    `tm_range` for that.
    """
    if salt_correction not in SaltCorrection.ALL:
        raise ValueError(
            f"unknown salt correction {salt_correction!r}; "
            f"expected one of {', '.join(SaltCorrection.ALL)}"
        )

    s = normalise(seq)
    dh, ds = nn_thermodynamics(s)

    selfcomp = (
        s == reverse_complement(s) if self_complementary is None
        else self_complementary
    )

    if selfcomp:
        ct = dnac1 * 1e-9
    else:
        ct = (dnac1 - dnac2 / 2.0) * 1e-9
    if ct <= 0:
        raise ValueError(
            "effective strand concentration must be positive "
            "(dnac1 has to exceed dnac2/2)"
        )

    mon = monovalent_equivalent(na=na, k=k, tris=tris, mg=mg, dntp=dntp)

    if salt_correction == SaltCorrection.SANTALUCIA_1998:
        # Correction applied to entropy, before the Tm is formed.
        ds = ds + 0.368 * (len(s) - 1) * math.log(_salt_mol_per_litre(mon))

    tm_kelvin = (1000.0 * dh) / (ds + R_CAL * math.log(ct))

    if salt_correction == SaltCorrection.OWCZARZY_2004:
        # Correction applied to 1/Tm, not to entropy.
        ln_na = math.log(_salt_mol_per_litre(mon))
        fgc = gc_fraction(s)
        inv = (
            1.0 / tm_kelvin
            + (4.29 * fgc - 3.95) * 1e-5 * ln_na
            + 9.40e-6 * ln_na ** 2
        )
        tm_kelvin = 1.0 / inv

    return tm_kelvin - 273.15


def tm_range(seq: str, **kwargs) -> tuple[float, float]:
    """
    (min Tm, max Tm) over every oligo a degenerate sequence denotes.

    For an unambiguous sequence both values are the same, so a caller can use
    this uniformly and look at the spread to decide whether the degeneracy
    matters.
    """
    temps = [tm_nn(variant, **kwargs) for variant in expand_ambiguous(seq)]
    return min(temps), max(temps)
