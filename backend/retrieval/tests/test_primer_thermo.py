"""
Tests for primer/thermo.py - nearest-neighbour DNA thermodynamics.

Layer 1 of the primer eval plan (docs/agent-track/PRIMER-DESIGN-PLAN.md).
Pure functions, no network, no services.

Where the expected values come from
-----------------------------------
`tm_wallace` is exact arithmetic (2*(A+T) + 4*(G+C)) and is verifiable by
hand, which makes it the check that a sequence was read correctly at all.

The Tm goldens below were produced by this implementation, which was written
from the published equations (SantaLucia 1998 PNAS 95:1460 for the parameters
and the entropy salt correction; Owczarzy 2004 Biochemistry 43:3537 eq. 22 for
the 1/Tm correction). They are corroborated, NOT defined, by an independent
reference: two of them, 60.32 and 59.78, appear verbatim in the published
series of melting temperatures for this same 28-mer under the seven salt
corrections offered by Biopython's `MeltingTemp` module
(54.27, 54.02, 59.60, 60.64, 60.32, 59.78, 59.78). An implementation written
from papers alone reproducing two listed values to two decimals is good
evidence that the parameter table, the initiation terms, the effective
concentration convention and both salt formulas are right.

Honest limitation: which of Biopython's method NUMBERS corresponds to which
formula is not asserted here, because it was not verified against Biopython
itself (the package is not installed in this environment). The constants were
not tuned to chase a remembered label. `test_biopython_crosscheck` below runs
a real comparison and SKIPs when Biopython is absent; installing it in the CI
image settles the mapping, and the differential oracle against primer3
(plan layer 1b) settles the thermodynamics independently.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_thermo.py
"""

from __future__ import annotations

import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import thermo  # noqa: E402
from primer.thermo import SaltCorrection, SequenceError  # noqa: E402

# The 28-mer used as the reference oligo throughout.
REF = "CGTTCCAAAGATGTGGGCATGAGCTTAC"

# A real degenerate primer: the reverse of SB-CYTB-3 from the Dryad eDNA
# specificity benchmark (So et al. 2020, Ecosphere 11(7):e03193, Table 1).
DEGENERATE = "RARRGGRATTTTGTCSGKGT"

TOL = 0.005


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol


# --- alphabet and sequence handling ---------------------------------------
def test_normalise_and_validate() -> bool:
    ok = thermo.normalise(" cgt tcc \n") == "CGTTCC"
    ok &= thermo.normalise("ACGU") == "ACGT"          # RNA input mapped to DNA
    for bad in ("ACGZ", "ACG1", "hello"):
        try:
            thermo.normalise(bad)
            ok = False
        except SequenceError:
            pass
    try:
        thermo.normalise("")
        ok = False
    except SequenceError:
        pass
    return _check("normalise upper-cases, maps U->T, rejects non-DNA", ok)


def test_reverse_complement_involution() -> bool:
    """rc(rc(x)) == x, for ambiguity codes too. The classic off-by-strand bug."""
    ok = True
    for seq in (REF, DEGENERATE, "A", "ACGT", "NNNN", "RYSWKM", "BDHVN"):
        if thermo.reverse_complement(thermo.reverse_complement(seq)) != seq:
            ok = False
    ok &= thermo.reverse_complement("ACGT") == "ACGT"
    ok &= thermo.reverse_complement("AAAA") == "TTTT"
    ok &= thermo.reverse_complement("RY") == "RY"     # R<->Y, then reversed
    return _check("reverse_complement is an involution (IUPAC included)", ok)


def test_is_ambiguous() -> bool:
    ok = not thermo.is_ambiguous(REF)
    ok &= thermo.is_ambiguous(DEGENERATE)
    ok &= thermo.is_ambiguous("ACGN")
    ok &= not thermo.is_ambiguous("acgt")
    return _check("is_ambiguous distinguishes ACGT from IUPAC codes", ok)


def test_gc_fraction() -> bool:
    ok = _close(thermo.gc_fraction(REF), 0.5)
    ok &= _close(thermo.gc_fraction("GGCC"), 1.0)
    ok &= _close(thermo.gc_fraction("AATT"), 0.0)
    # S is unambiguously strong, W unambiguously weak; both count.
    ok &= _close(thermo.gc_fraction("SSWW"), 0.5)
    # N spans both classes, so it is excluded from numerator and denominator.
    ok &= _close(thermo.gc_fraction("GCNN"), 1.0)
    return _check("gc_fraction counts S/W, excludes spanning codes", ok)


# --- Wallace: exact, hand-verifiable --------------------------------------
def test_wallace_exact() -> bool:
    """
    REF has 14 A/T and 14 G/C, so 2*14 + 4*14 = 84 exactly.

    This also proves the sequence survived transcription intact.
    """
    got = thermo.tm_wallace(REF)
    ok = _close(got, 84.00, 1e-9)
    ok &= _close(thermo.tm_wallace("AAAA"), 8.0, 1e-9)
    ok &= _close(thermo.tm_wallace("GGGG"), 16.0, 1e-9)
    return _check("tm_wallace = 84.00 for the reference 28-mer",
                  ok, f"got {got}")


# --- nearest-neighbour thermodynamics -------------------------------------
def test_nn_table_complete_and_symmetric() -> bool:
    """All 16 dinucleotides present, and each equals its reverse complement."""
    ok = len(thermo.NN_PARAMS) == 16
    for dimer, value in thermo.NN_PARAMS.items():
        rc = thermo.reverse_complement(dimer)
        if thermo.NN_PARAMS[rc] != value:
            ok = False
    return _check("NN table covers 16 dimers, symmetric under rev-comp",
                  ok, f"{len(thermo.NN_PARAMS)} entries")


def test_nn_thermodynamics_golden() -> bool:
    dh, ds = thermo.nn_thermodynamics(REF)
    ok = _close(dh, -222.9, 0.05) and _close(ds, -602.5, 0.05)
    return _check("nn_thermodynamics(REF) = (-222.9 kcal/mol, -602.5 cal/molK)",
                  ok, f"got ({dh:.1f}, {ds:.1f})")


def test_nn_rejects_ambiguous() -> bool:
    ok = False
    try:
        thermo.nn_thermodynamics(DEGENERATE)
    except SequenceError:
        ok = True
    return _check("nn_thermodynamics refuses a degenerate sequence", ok)


# --- melting temperature --------------------------------------------------
def test_tm_salt_corrections() -> bool:
    """
    Goldens for the reference 28-mer at 50 mM Na+, 25 nM each strand.

    60.32 and 59.78 both appear in the published Biopython series for this
    oligo; see the module docstring on what that does and does not establish.
    """
    cases = [
        (SaltCorrection.NONE, 75.86),
        (SaltCorrection.SANTALUCIA_1998, 60.32),
        (SaltCorrection.OWCZARZY_2004, 59.78),
    ]
    ok = True
    detail = []
    for method, expected in cases:
        got = thermo.tm_nn(REF, salt_correction=method)
        if not _close(got, expected):
            ok = False
        detail.append(f"{method}={got:.2f} (want {expected})")
    return _check("tm_nn matches goldens for all three salt corrections",
                  ok, "; ".join(detail))


def test_salt_correction_phosphate_count() -> bool:
    """
    The SantaLucia entropy correction uses N = len - 1 (phosphates), not len.

    Pinning this because it is a silent off-by-one: len would give 59.77 and
    len-2 would give 60.87 for the reference oligo, both plausible-looking.
    """
    dh, ds = thermo.nn_thermodynamics(REF)
    ct = (25.0 - 12.5) * 1e-9
    got = thermo.tm_nn(REF, salt_correction=SaltCorrection.SANTALUCIA_1998)

    def with_n(n: int) -> float:
        d = ds + 0.368 * n * math.log(0.05)
        return (1000.0 * dh) / (d + thermo.R_CAL * math.log(ct)) - 273.15

    ok = _close(got, with_n(len(REF) - 1))
    ok &= not _close(got, with_n(len(REF)))
    return _check("salt correction uses N = len-1 phosphates", ok)


def test_unknown_salt_correction_rejected() -> bool:
    ok = False
    try:
        thermo.tm_nn(REF, salt_correction="owczarzy2099")
    except ValueError:
        ok = True
    return _check("tm_nn rejects an unknown salt correction name", ok)


def test_tm_monotonic_in_gc() -> bool:
    """Higher GC means higher Tm at fixed length. Sanity, not precision."""
    length = 20
    temps = []
    for gc in range(0, length + 1, 4):
        seq = "G" * gc + "A" * (length - gc)
        temps.append(thermo.tm_nn(seq))
    ok = all(b > a for a, b in zip(temps, temps[1:]))
    return _check("Tm increases with GC content",
                  ok, ", ".join(f"{t:.1f}" for t in temps))


def test_tm_monotonic_in_length() -> bool:
    ok = True
    prev = None
    for n in range(10, 30, 4):
        tm = thermo.tm_nn(("ACGT" * 10)[:n])
        if prev is not None and tm <= prev:
            ok = False
        prev = tm
    return _check("Tm increases with oligo length", ok)


def test_magnesium_raises_tm() -> bool:
    """
    Mg2+ stabilises the duplex, and free dNTPs chelate it away again.

    von Ahsen 2001: 120*sqrt(Mg - dNTP). So adding Mg must raise Tm, and
    adding dNTPs in excess of Mg must cancel the effect entirely.
    """
    base = thermo.tm_nn(REF, na=50.0, mg=0.0)
    with_mg = thermo.tm_nn(REF, na=50.0, mg=1.5)
    chelated = thermo.tm_nn(REF, na=50.0, mg=1.5, dntp=1.5)
    ok = with_mg > base and _close(chelated, base)
    return _check("Mg raises Tm; equimolar dNTPs cancel it",
                  ok, f"base={base:.2f} +Mg={with_mg:.2f} chelated={chelated:.2f}")


def test_self_complementary_handling() -> bool:
    """A palindrome gets the symmetry entropy penalty and c, not c/4."""
    palindrome = "ACGCGCGCGT"
    ok = thermo.reverse_complement(palindrome) == palindrome

    # The -1.4 cal/molK symmetry penalty really is in there: rebuild dS from
    # the NN sum plus initiation and check the difference is exactly that.
    _, ds = thermo.nn_thermodynamics(palindrome)
    manual = sum(thermo.NN_PARAMS[palindrome[i:i + 2]][1]
                 for i in range(len(palindrome) - 1))
    for end in (palindrome[0], palindrome[-1]):
        manual += (4.1 if end in "AT" else -2.8)
    ok &= _close(ds - manual, -1.4, 1e-9)

    # And forcing the flag changes the concentration term, so the Tm moves.
    forced_on = thermo.tm_nn(palindrome, self_complementary=True)
    forced_off = thermo.tm_nn(palindrome, self_complementary=False)
    ok &= not _close(forced_on, forced_off)
    auto = thermo.tm_nn(palindrome)
    ok &= _close(auto, forced_on)
    return _check("self-complementary oligo detected and handled", ok)


# --- degenerate primers ---------------------------------------------------
def test_degeneracy_counts() -> bool:
    ok = thermo.degeneracy(REF) == 1
    ok &= thermo.degeneracy("N") == 4
    ok &= thermo.degeneracy("NN") == 16
    ok &= thermo.degeneracy("RY") == 4
    ok &= thermo.degeneracy("BDHV") == 81
    ok &= thermo.degeneracy(DEGENERATE) == 64
    return _check("degeneracy counts the oligo population",
                  ok, f"DEGENERATE -> {thermo.degeneracy(DEGENERATE)}")


def test_expansion_matches_degeneracy() -> bool:
    variants = thermo.expand_ambiguous(DEGENERATE)
    ok = len(variants) == 64 == len(set(variants))
    ok &= all(not thermo.is_ambiguous(v) for v in variants)
    ok &= all(len(v) == len(DEGENERATE) for v in variants)
    return _check("expand_ambiguous yields exactly the distinct oligos", ok)


def test_expansion_cap_refuses() -> bool:
    """An over-degenerate primer must refuse, not silently truncate."""
    ok = False
    try:
        thermo.expand_ambiguous("N" * 12)   # 16.7M
    except SequenceError:
        ok = True
    return _check("expand_ambiguous refuses past the cap", ok)


def test_tm_range_spread() -> bool:
    """
    The reason degenerate primers get a range and not a number.

    This real benchmark primer spans ~11 C across its 64 oligos, so quoting a
    single Tm for it would be actively misleading.
    """
    lo, hi = thermo.tm_range(DEGENERATE)
    ok = _close(lo, 46.18, 0.05) and _close(hi, 57.12, 0.05)
    ok &= hi - lo > 10.0
    return _check("tm_range spans the degenerate population",
                  ok, f"{lo:.2f}..{hi:.2f} (spread {hi - lo:.2f} C)")


def test_tm_range_degenerates_to_point() -> bool:
    lo, hi = thermo.tm_range(REF)
    ok = _close(lo, hi) and _close(lo, thermo.tm_nn(REF))
    return _check("tm_range on an unambiguous oligo is a single point", ok)


def test_tm_nn_refuses_degenerate() -> bool:
    ok = False
    try:
        thermo.tm_nn(DEGENERATE)
    except SequenceError:
        ok = True
    return _check("tm_nn refuses a degenerate sequence (use tm_range)", ok)


# --- optional independent cross-check -------------------------------------
def test_biopython_crosscheck() -> bool:
    """
    Compare against Biopython when it is importable; SKIP when it is not.

    This is what settles the method-number mapping discussed in the module
    docstring. It is deliberately not a hard dependency: the goldens above
    stand on the published equations.
    """
    try:
        from Bio.SeqUtils import MeltingTemp as mt  # type: ignore
    except ImportError:
        print("[SKIP] biopython cross-check (Bio not installed)")
        return True

    series = [
        round(mt.Tm_NN(REF, dnac1=25, dnac2=25, Na=50, saltcorr=m), 2)
        for m in range(1, 8)
    ]
    ours = {
        "santalucia1998": round(
            thermo.tm_nn(REF, salt_correction=SaltCorrection.SANTALUCIA_1998), 2),
        "owczarzy2004": round(
            thermo.tm_nn(REF, salt_correction=SaltCorrection.OWCZARZY_2004), 2),
    }
    print(f"       biopython saltcorr 1..7: {series}")
    print(f"       ours: {ours}")
    ok = all(v in series for v in ours.values())
    return _check("our Tm values appear in the Biopython series", ok)


def main() -> int:
    tests = [
        test_normalise_and_validate,
        test_reverse_complement_involution,
        test_is_ambiguous,
        test_gc_fraction,
        test_wallace_exact,
        test_nn_table_complete_and_symmetric,
        test_nn_thermodynamics_golden,
        test_nn_rejects_ambiguous,
        test_tm_salt_corrections,
        test_salt_correction_phosphate_count,
        test_unknown_salt_correction_rejected,
        test_tm_monotonic_in_gc,
        test_tm_monotonic_in_length,
        test_magnesium_raises_tm,
        test_self_complementary_handling,
        test_degeneracy_counts,
        test_expansion_matches_degeneracy,
        test_expansion_cap_refuses,
        test_tm_range_spread,
        test_tm_range_degenerates_to_point,
        test_tm_nn_refuses_degenerate,
        test_biopython_crosscheck,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
