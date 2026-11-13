"""
Tests for primer/structure.py - hairpins, dimers, 3' end stability.

Layer 1 of the primer eval plan (docs/agent-track/PRIMER-DESIGN-PLAN.md).
Pure functions, no network, no services.

Where the expected values come from, and where they do NOT
----------------------------------------------------------
Read this before trusting a number below.

There is **no independent reference implementation available in this
environment**. `ntthal` is not on PATH, primer3-py is not installed, and
Biopython has no secondary-structure predictor to borrow. So the absolute dG
and Tm of a hairpin or a dimer are NOT validated here, and no test below
pretends otherwise. `test_primer3_crosscheck` shells out to `ntthal` and SKIPs
when it is absent, mirroring how `test_primer_thermo.py` handles the missing
Biopython; the CI image of plan layer 1b is what actually settles these.

What IS validated, in descending order of strength:

1. **Agreement with `thermo.py` on the cases where the two must coincide.**
   A perfect duplex is the whole nearest-neighbour model and nothing else, so
   `heterodimer(x, reverse_complement(x))` has to reproduce
   `thermo.nn_thermodynamics(x)` and `thermo.tm_nn(x)` exactly, and a fully
   self-complementary oligo's homodimer has to reproduce them including the
   symmetry entropy term. This reaches a published number: the Tm that falls
   out of `test_perfect_duplex_matches_thermo` is 60.32, which appears
   verbatim in the published Biopython `MeltingTemp` series for that 28-mer
   (see the `test_primer_thermo.py` docstring for what that does and does not
   establish). That chain is the strongest evidence in this file.
2. **Exact arithmetic over `thermo.NN_PARAMS`.** `end_stability` is by
   definition the sum of the nearest-neighbour stacks in the terminal window,
   so `test_end_stability_is_the_stack_sum` recomputes it from the table
   rather than hard-coding a number. It would catch a window taken from the
   wrong end, an off-by-one in the window, or a dropped stack.
3. **Structural and property assertions.** Designed structures must be found
   where they were designed (a stem-loop's stem located base by base), a
   poly-A 20-mer must fold into nothing, `heterodimer(a, b)` must equal
   `heterodimer(b, a)`, every reported base pair must actually be
   Watson-Crick, every '|' in the ASCII rendering must sit over a real pair,
   adding GC to a stem must make it more stable, dG must rise with
   temperature, and a dimer's Tm must depend on strand concentration while a
   hairpin's must not. These hold for any correct implementation and do not
   depend on the parameter values being right.
4. **Regression goldens.** Clearly labelled as such in
   `test_regression_goldens`. They are this implementation's own output, kept
   so that a refactor cannot move a number silently. They are NOT validated
   truth, and they carry the known approximations listed in the
   `structure.py` docstring, chiefly the hairpin loop penalty anchor.

One known approximation worth repeating here, because it bounds how much the
hairpin goldens can be trusted: the loop penalty uses the Jacobson-Stockmayer
functional form with a single anchor constant, `HAIRPIN_LOOP_DG37_REF`, that
was not checked against a primary source. Hairpin dG therefore carries that
offset. Dimer dG does not, since it uses only the SantaLucia stacking and
initiation parameters that `thermo.py` already pins.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_structure.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import structure as st  # noqa: E402
from primer import thermo  # noqa: E402
from primer.structure import NO_STRUCTURE_TM, Structure  # noqa: E402
from primer.thermo import SaltCorrection, SequenceError  # noqa: E402

# The 28-mer used as the reference oligo in test_primer_thermo.py. Reused so
# the two files can be compared against each other.
REF = "CGTTCCAAAGATGTGGGCATGAGCTTAC"

# A designed stem-loop: an 8 bp stem, a 4 nt loop, nothing else. Built as
# stem + TTTT + reverse_complement(stem), so the stem's position is known by
# construction rather than discovered.
STEM = "CAGTGCCA"
LOOP = "TTTT"
HAIRPIN_SEQ = STEM + LOOP + thermo.reverse_complement(STEM)

# A perfect self-complementary palindrome. Its homodimer is the full duplex.
PALINDROME = "ACGCGCGCGT"

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


def _pairs_are_watson_crick(s: Structure) -> bool:
    for i, j in s.pairs:
        if not (0 <= i < len(s.seq1) and 0 <= j < len(s.seq2)):
            return False
        if thermo.reverse_complement(s.seq1[i]) != s.seq2[j]:
            return False
    return True


def _ascii_columns_agree(s: Structure) -> bool:
    """
    Every '|' in the rendering must sit over two complementary bases.

    All renderings put a three-character strand label in front of the body, so
    the columns line up across the three lines regardless of what is appended
    after the body.
    """
    lines = s.ascii_art.split("\n")
    if len(lines) < 3:
        return False
    top, mid, bot = lines[0], lines[1], lines[2]
    bars = [c for c, ch in enumerate(mid) if ch == "|"]
    if len(bars) != len(s.pairs):
        return False
    for c in bars:
        if c >= len(top) or c >= len(bot):
            return False
        if thermo.reverse_complement(top[c]) != bot[c]:
            return False
    return True


# --- the "no structure" contract ------------------------------------------
def test_no_structure_is_a_structure_not_none() -> bool:
    """
    A fold that finds nothing returns a well-formed Structure, never None.

    dg 0.0 and tm NO_STRUCTURE_TM are chosen so that BOTH threshold
    conventions pass without the caller branching: a Primer3 "Tm below 47"
    rule and an IDT "dG at or above -9" rule are each satisfied.
    """
    poly_a = "A" * 20
    results = [st.hairpin(poly_a), st.homodimer(poly_a)]
    ok = all(r is not None for r in results)
    ok &= all(isinstance(r, Structure) for r in results)
    ok &= all(not r.found and not bool(r) for r in results)
    ok &= all(_close(r.dg, 0.0, 1e-12) for r in results)
    ok &= all(r.tm == NO_STRUCTURE_TM for r in results)
    ok &= all(r.pairs == () and r.n_pairs == 0 for r in results)
    ok &= all(r.ascii_art for r in results)
    # Both conventions pass for a non-structure.
    ok &= all(r.tm <= 47.0 and r.dg >= -9.0 for r in results)
    return _check("no structure found returns a Structure, not None", ok)


def test_polya_has_no_hairpin_and_no_self_dimer() -> bool:
    """A 20-mer of A cannot pair with anything, including itself."""
    poly_a = "A" * 20
    hp = st.hairpin(poly_a)
    hd = st.homodimer(poly_a)
    ok = not hp.found and not hd.found
    # And the same for poly-T, which is the other half of the same argument.
    ok &= not st.hairpin("T" * 20).found
    ok &= not st.homodimer("T" * 20).found
    # But poly-A against poly-T is a perfect duplex.
    ok &= st.heterodimer("A" * 20, "T" * 20).found
    return _check("poly-A 20-mer: no hairpin, no self-dimer", ok,
                  f"hairpin={hp.found} homodimer={hd.found}")


# --- agreement with thermo.py, the strongest evidence here ----------------
def test_perfect_duplex_matches_thermo() -> bool:
    """
    A primer against its own reverse complement IS the nearest-neighbour model.

    So the structure search must recover exactly `thermo.nn_thermodynamics`
    plus the salt correction, and its structure Tm must equal `thermo.tm_nn`.
    That lands on 60.32, a value that appears in the published Biopython
    MeltingTemp series for this 28-mer, which makes this the one number in
    this file with outside corroboration.
    """
    rc = thermo.reverse_complement(REF)
    s = st.heterodimer(REF, rc, salt_correction=SaltCorrection.SANTALUCIA_1998)
    expected_tm = thermo.tm_nn(REF, salt_correction=SaltCorrection.SANTALUCIA_1998)

    ok = s.found
    ok &= s.n_pairs == len(REF)                       # every base paired
    ok &= s.pairs == tuple((i, len(REF) - 1 - i) for i in range(len(REF)))
    ok &= _close(s.tm, expected_tm, 1e-9)
    ok &= _close(s.tm, 60.32, 0.005)

    # dH carries no salt term, so it must equal thermo exactly.
    dh, _ = thermo.nn_thermodynamics(REF)
    ok &= _close(s.dh, dh, 1e-9)
    return _check("heterodimer(x, revcomp x) reproduces thermo.tm_nn = 60.32",
                  ok, f"tm={s.tm:.4f} want {expected_tm:.4f}, {s.n_pairs} pairs")


def test_palindrome_homodimer_matches_thermo() -> bool:
    """
    A self-complementary oligo's homodimer is its own full duplex.

    This also pins the symmetry entropy term (-1.4 cal/molK) and the
    self-complementary concentration convention (CT = dnac1, not dnac1/4),
    both of which must match `thermo.tm_nn(self_complementary=True)`.
    """
    s = st.homodimer(PALINDROME, salt_correction=SaltCorrection.NONE)
    dh, ds = thermo.nn_thermodynamics(PALINDROME)
    ok = s.found
    ok &= _close(s.dh, dh, 1e-9)
    ok &= _close(s.ds, ds, 1e-9)          # includes the -1.4 symmetry term
    ok &= s.n_pairs == len(PALINDROME)

    salted = st.homodimer(PALINDROME,
                          salt_correction=SaltCorrection.SANTALUCIA_1998)
    expected = thermo.tm_nn(PALINDROME,
                            salt_correction=SaltCorrection.SANTALUCIA_1998,
                            self_complementary=True)
    ok &= _close(salted.tm, expected, 1e-9)
    return _check("palindrome homodimer reproduces thermo dH, dS and Tm",
                  ok, f"dH={s.dh:.3f}/{dh:.3f} dS={s.ds:.3f}/{ds:.3f}")


def test_palindrome_is_a_very_stable_homodimer() -> bool:
    """
    The headline property: a perfect palindrome self-dimerises hard.

    Both threshold conventions must FAIL it, which is the point: this is the
    primer a designer must never order.
    """
    s = st.homodimer(PALINDROME)
    ok = s.found
    ok &= s.dg < -9.0            # fails the IDT dG rule
    ok &= s.n_pairs == len(PALINDROME)
    ok &= _pairs_are_watson_crick(s)
    # A longer palindrome also breaks the Primer3 Tm rule outright.
    long_pal = "GGCCGGCCTTAAGGCCGGCC"
    ok &= thermo.reverse_complement(long_pal) == long_pal
    big = st.homodimer(long_pal)
    ok &= big.found and big.tm > 47.0 and big.dg < -9.0
    return _check("a self-complementary palindrome forms a very stable homodimer",
                  ok, f"dg={s.dg:.2f} tm={s.tm:.2f} pairs={s.n_pairs}")


# --- hairpins -------------------------------------------------------------
def test_designed_hairpin_is_found_with_the_stem_located() -> bool:
    """
    An 8 bp stem with a 4 nt loop, found base by base where it was built.

    The stem is positions 0-7 pairing 19-12, the loop is 8-11. Asserting the
    exact pair list is what catches an off-by-one in the loop constraint or a
    reversed arm, which is the bug class this whole module exists to avoid.
    """
    s = st.hairpin(HAIRPIN_SEQ)
    n = len(HAIRPIN_SEQ)
    expected = tuple((i, n - 1 - i) for i in range(len(STEM)))
    ok = s.found
    ok &= s.pairs == expected
    ok &= s.loop_size == len(LOOP)
    ok &= s.kind == "hairpin"
    ok &= _pairs_are_watson_crick(s)
    ok &= _ascii_columns_agree(s)
    # The loop really is the designed loop.
    ok &= HAIRPIN_SEQ[len(STEM):len(STEM) + len(LOOP)] == LOOP
    return _check("designed 8 bp stem / 4 nt loop found, stem located exactly",
                  ok, f"pairs={s.pairs} loop={s.loop_size}")


def test_hairpin_minimum_loop_is_enforced() -> bool:
    """
    Steric reality: a chain cannot turn back on itself in fewer than 3 bases.

    Built so that the MOST STABLE fold is the forbidden one: an 8 bp stem over
    a 2 nt loop. The default must refuse it and settle for a weaker 7 bp stem
    over a 4 nt loop, while min_loop=2 takes the tight fold. The constraint
    therefore costs real stability, which is what proves it is binding rather
    than decorative.
    """
    tight = STEM + "TT" + thermo.reverse_complement(STEM)
    default = st.hairpin(tight)
    relaxed = st.hairpin(tight, min_loop=2)
    ok = default.found and default.loop_size == 4 and default.n_pairs == 7
    ok &= relaxed.found and relaxed.loop_size == 2 and relaxed.n_pairs == 8
    ok &= relaxed.dg < default.dg          # the forbidden fold is the better one
    ok &= not st.hairpin(tight, min_loop=5).found
    # And across a spread of sequences, no reported loop is ever under 3.
    for seq in (HAIRPIN_SEQ, REF, PALINDROME, "GCGCGCGCAAAAGCGCGCGC"):
        h = st.hairpin(seq)
        if h.found and h.loop_size < st.MIN_LOOP:
            ok = False
    return _check("hairpin enforces a minimum loop of 3 nt",
                  ok, f"default loop={default.loop_size} pairs={default.n_pairs} "
                      f"dg={default.dg:.3f}; min_loop=2 loop={relaxed.loop_size} "
                      f"pairs={relaxed.n_pairs} dg={relaxed.dg:.3f}")


def test_gc_in_the_stem_makes_it_more_stable() -> bool:
    """
    Same stem length, same loop, more G/C: lower dG and higher Tm, monotonically.

    Each stem below is the previous one with one A/T pair swapped for a G/C
    pair, so nothing varies except GC content.
    """
    stems = ["CAATACCA", "CAGTGCCA", "CGGTGCCG", "CGGCGCCG"]
    dgs, tms = [], []
    for stem in stems:
        seq = stem + LOOP + thermo.reverse_complement(stem)
        h = st.hairpin(seq)
        if not h.found or h.n_pairs != len(stem):
            return _check("GC in the stem makes the hairpin more stable",
                          False, f"{stem}: found={h.found} pairs={h.n_pairs}")
        dgs.append(h.dg)
        tms.append(h.tm)
    ok = all(b < a for a, b in zip(dgs, dgs[1:]))
    ok &= all(b > a for a, b in zip(tms, tms[1:]))
    return _check("GC in the stem makes the hairpin more stable", ok,
                  "dg " + ", ".join(f"{d:.2f}" for d in dgs))


def test_hairpin_tm_is_unimolecular() -> bool:
    """
    A hairpin is intramolecular, so its Tm cannot depend on concentration.

    Checked structurally: Tm = 1000*dH/dS exactly, with no R*ln(CT) term, and
    `hairpin` takes no strand-concentration argument at all.
    """
    h = st.hairpin(HAIRPIN_SEQ)
    ok = h.found
    ok &= _close(h.tm, 1000.0 * h.dh / h.ds - 273.15, 1e-9)
    try:
        st.hairpin(HAIRPIN_SEQ, dnac1=1000.0)   # type: ignore[call-arg]
        ok = False
    except TypeError:
        pass
    return _check("hairpin Tm is unimolecular (no concentration term)", ok,
                  f"tm={h.tm:.4f}")


# --- dimers ---------------------------------------------------------------
def test_heterodimer_is_symmetric_in_its_arguments() -> bool:
    """
    heterodimer(a, b) and heterodimer(b, a) are the same physical duplex.

    They must agree on every energy. The pair lists are mirror images, so they
    are compared as such rather than for equality.
    """
    pairs_to_try = [
        (REF, HAIRPIN_SEQ),
        (REF, "GCTTACGGGATTTCCCATGAAC"),
        (PALINDROME, REF),
        ("GGGGCCCCAAAA", "TTTTGGGGCCCC"),
    ]
    ok = True
    worst = 0.0
    for a, b in pairs_to_try:
        x = st.heterodimer(a, b)
        y = st.heterodimer(b, a)
        if x.found != y.found:
            ok = False
            continue
        if not x.found:
            continue
        worst = max(worst, abs(x.dg - y.dg), abs(x.tm - y.tm))
        if not (_close(x.dg, y.dg, 1e-9) and _close(x.dh, y.dh, 1e-9)
                and _close(x.ds, y.ds, 1e-9) and _close(x.tm, y.tm, 1e-9)):
            ok = False
        if tuple(sorted((j, i) for i, j in x.pairs)) != y.pairs:
            ok = False
    return _check("heterodimer(a, b) == heterodimer(b, a)", ok,
                  f"worst disagreement {worst:.2e}")


def test_heterodimer_with_own_revcomp_is_maximal() -> bool:
    """
    Nothing a primer can meet is more stable than its own reverse complement.

    Checked against every other partner in this file, and against every
    single-base mutant of the reverse complement, each of which breaks at
    least one pair.
    """
    rc = thermo.reverse_complement(REF)
    best = st.heterodimer(REF, rc)
    ok = best.found and best.n_pairs == len(REF)

    others = [HAIRPIN_SEQ, PALINDROME, "A" * 20, "GCTTACGGGATTTCCCATGAAC"]
    for other in others:
        s = st.heterodimer(REF, other)
        if s.dg <= best.dg:
            ok = False

    for pos in range(0, len(rc), 3):
        for base in "ACGT":
            if base == rc[pos]:
                continue
            mutant = rc[:pos] + base + rc[pos + 1:]
            if st.heterodimer(REF, mutant).dg <= best.dg:
                ok = False
    return _check("heterodimer with the exact reverse complement is maximal",
                  ok, f"dg={best.dg:.3f} over {len(REF)} pairs")


def test_dimer_tm_depends_on_concentration() -> bool:
    """
    A dimer is bimolecular, so diluting the primers lowers its Tm.

    dG must NOT move, because free energy of the structure does not carry the
    concentration term. Both halves matter: getting this backwards is how a
    concentration term ends up in the wrong equation.
    """
    rc = thermo.reverse_complement(REF)
    lo = st.heterodimer(REF, rc, dnac1=5.0, dnac2=5.0)
    hi = st.heterodimer(REF, rc, dnac1=1000.0, dnac2=1000.0)
    ok = hi.tm > lo.tm
    ok &= _close(hi.dg, lo.dg, 1e-9)
    ok &= _close(hi.dh, lo.dh, 1e-9)
    return _check("dimer Tm rises with concentration, dG does not move",
                  ok, f"5 nM -> {lo.tm:.2f} C, 1000 nM -> {hi.tm:.2f} C")


def test_dg_rises_with_temperature() -> bool:
    """
    dG = dH - T*dS/1000 with dH and dS both negative, so dG climbs with T.

    Holds for the minimum over structures too, since every candidate's dG is
    increasing in T. Checked on a hairpin and on a dimer.
    """
    temps = [10.0, 25.0, 37.0, 50.0]
    hp = [st.hairpin(HAIRPIN_SEQ, temperature=t).dg for t in temps]
    rc = thermo.reverse_complement(REF)
    dim = [st.heterodimer(REF, rc, temperature=t).dg for t in temps]
    ok = all(b > a for a, b in zip(hp, hp[1:]))
    ok &= all(b > a for a, b in zip(dim, dim[1:]))
    # And far enough above the hairpin's Tm it stops forming at all.
    ok &= not st.hairpin(HAIRPIN_SEQ, temperature=75.0).found
    return _check("dG becomes less negative as temperature rises", ok,
                  "hairpin " + ", ".join(f"{d:.2f}" for d in hp))


def test_reported_pairs_are_real_base_pairs() -> bool:
    """
    Every (i, j) must index a genuine Watson-Crick pair, in both coordinates.

    The coordinates are into the sequences AS GIVEN, 5'->3', not into some
    internal reversed copy. Getting that wrong is invisible in the energies
    and fatal to anything downstream that uses the positions.
    """
    cases = [
        st.hairpin(HAIRPIN_SEQ),
        st.hairpin("GCGCGCGCAAAAGCGCGCGC"),
        st.homodimer(PALINDROME),
        st.homodimer(REF),
        st.heterodimer(REF, thermo.reverse_complement(REF)),
        st.heterodimer(REF, HAIRPIN_SEQ),
        st.heterodimer("GGCATTCCAA", "TTGGAATTGCC"),
        st.end_stability(REF),
    ]
    ok = True
    for s in cases:
        if not s.found:
            continue
        if not _pairs_are_watson_crick(s):
            ok = False
        # Both index sequences must advance monotonically: strand 1 forward,
        # strand 2 backward, which is what antiparallel means.
        i_idx = [i for i, _ in s.pairs]
        j_idx = [j for _, j in s.pairs]
        if i_idx != sorted(i_idx) or j_idx != sorted(j_idx, reverse=True):
            ok = False
        if len(set(i_idx)) != len(i_idx) or len(set(j_idx)) != len(j_idx):
            ok = False
    return _check("every reported pair is Watson-Crick and antiparallel", ok)


def test_ascii_rendering_is_honest() -> bool:
    """
    Each '|' in the rendering sits over two bases that really do pair.

    A rendering nobody checks drifts out of step with the pair list, and then
    a human reading it is being misled about what the tool found.
    """
    cases = [
        st.hairpin(HAIRPIN_SEQ),
        st.homodimer(PALINDROME),
        st.heterodimer(REF, thermo.reverse_complement(REF)),
        st.heterodimer(REF, HAIRPIN_SEQ),
        st.heterodimer("GGCATTCCAA", "TTGGAATTGCC"),
        st.end_stability(REF),
    ]
    ok = True
    for s in cases:
        if s.found and not _ascii_columns_agree(s):
            ok = False
            print(f"       mismatched rendering for {s.kind}:\n{s.ascii_art}")
    return _check("every '|' in the ASCII rendering is over a real pair", ok)


def test_bulge_is_rendered_and_skips_one_base() -> bool:
    """
    A duplex with one extra base on one strand: a single-base bulge.

    `TTGGAATTGCC` is the reverse complement of `GGCATTCCAA` with one T
    inserted, so the correct answer pairs everything except that T.
    """
    s1, s2 = "GGCATTCCAA", "TTGGAATTGCC"
    s = st.heterodimer(s1, s2)
    ok = s.found and s.n_pairs == len(s1)          # every strand-1 base paired
    paired_j = {j for _, j in s.pairs}
    ok &= len(set(range(len(s2))) - paired_j) == 1  # exactly one base bulged
    ok &= "-" in s.ascii_art.split("\n")[0]         # and it shows in the render
    ok &= _ascii_columns_agree(s)
    return _check("a single-base bulge is found and rendered", ok,
                  f"pairs={s.n_pairs} unpaired on s2={len(s2) - len(paired_j)}")


def test_internal_loop_cap_is_respected() -> bool:
    """
    With max_internal_loop=0 the fold must be one contiguous helix.

    This is the knob that stops the search chaining distant helices across
    loops it charges nothing for; see "Known limits" in structure.py.
    """
    s = st.heterodimer(REF, HAIRPIN_SEQ, max_internal_loop=0)
    ok = True
    if s.found:
        i_idx = [i for i, _ in s.pairs]
        j_idx = [j for _, j in s.pairs]
        ok &= i_idx == list(range(i_idx[0], i_idx[0] + len(i_idx)))
        ok &= j_idx == list(range(j_idx[0], j_idx[0] - len(j_idx), -1))
    # Allowing loops can only find something at least as stable.
    loose = st.heterodimer(REF, HAIRPIN_SEQ)
    ok &= loose.dg <= s.dg + 1e-9
    return _check("max_internal_loop=0 yields one contiguous helix", ok,
                  f"tight dg={s.dg:.3f} loose dg={loose.dg:.3f}")


# --- 3' end stability -----------------------------------------------------
def test_end_stability_is_the_stack_sum() -> bool:
    """
    Exact arithmetic, recomputed from thermo.NN_PARAMS rather than hard-coded.

    3' end stability is by definition the nearest-neighbour stacking sum over
    the terminal window. Recomputing it here catches a window taken from the
    5' end, an off-by-one in the window, or a dropped stack, without asserting
    any value this implementation cannot already justify.
    """
    n = 5
    window = REF[-n:]
    s = st.end_stability(REF, n, salt_correction=SaltCorrection.NONE)

    dh = sum(thermo.NN_PARAMS[window[i:i + 2]][0] for i in range(n - 1))
    ds = sum(thermo.NN_PARAMS[window[i:i + 2]][1] for i in range(n - 1))
    ok = _close(s.dh, dh, 1e-9) and _close(s.ds, ds, 1e-9)
    ok &= _close(s.dg, dh - 310.15 * ds / 1000.0, 1e-9)
    ok &= s.seq1 == window                       # the 3' end, not the 5' end
    ok &= s.seq2 == thermo.reverse_complement(window)
    ok &= s.n_pairs == n and s.found

    # The window length is honoured.
    for k in (2, 3, 5, 8):
        ok &= st.end_stability(REF, k).seq1 == REF[-k:]
    return _check("end_stability is the NN stack sum over the 3' window", ok,
                  f"dh={s.dh:.3f} want {dh:.3f}")


def test_end_stability_ranks_gc_ends_as_riskier() -> bool:
    """
    Rychlik 1990: an over-stable 3' end stays annealed at a mismatch and primes.

    So a GC-rich 3' end must score MORE NEGATIVE than an AT-rich one on an
    otherwise identical primer. The sign convention matters: this module keeps
    dG negative for stable, so "more negative = more mispriming risk".
    """
    body = "CGTTCCAAAGATGTGGGCAT"
    gc_end = st.end_stability(body + "GGCCG")
    at_end = st.end_stability(body + "AATTA")
    ok = gc_end.dg < at_end.dg
    # Only the 3' end matters, not the body.
    ok &= _close(st.end_stability("TTTTTTTTTTTTTTT" + "GGCCG").dg,
                 gc_end.dg, 1e-9)
    return _check("a GC-rich 3' end scores more stable (riskier) than AT-rich",
                  ok, f"GC end {gc_end.dg:.3f} vs AT end {at_end.dg:.3f}")


def test_end_stability_initiation_flag() -> bool:
    """
    The include_initiation flag is a real switch, and defaults to stacking only.

    Which convention a given tool uses could not be verified here, so the flag
    exists rather than a silent choice (see the structure.py docstring).
    """
    plain = st.end_stability(REF, salt_correction=SaltCorrection.NONE)
    with_init = st.end_stability(REF, include_initiation=True,
                                 salt_correction=SaltCorrection.NONE)
    window = REF[-5:]
    i_dh = sum((2.3 if b in "AT" else 0.1) for b in (window[0], window[-1]))
    i_ds = sum((4.1 if b in "AT" else -2.8) for b in (window[0], window[-1]))
    ok = _close(with_init.dh - plain.dh, i_dh, 1e-9)
    ok &= _close(with_init.ds - plain.ds, i_ds, 1e-9)
    ok &= with_init.dg > plain.dg          # initiation is destabilising
    return _check("end_stability include_initiation adds exactly the init terms",
                  ok, f"delta dh={with_init.dh - plain.dh:.3f}")


def test_end_stability_rejects_a_short_window() -> bool:
    ok = False
    try:
        st.end_stability("ACG", 5)
    except SequenceError:
        ok = True
    ok2 = False
    try:
        st.end_stability(REF, 1)
    except SequenceError:
        ok2 = True
    return _check("end_stability refuses an impossible window", ok and ok2)


# --- the threshold convention ---------------------------------------------
def test_both_threshold_conventions_are_available() -> bool:
    """
    The convention trap, pinned.

    Primer3's PRIMER_MAX_SELF_ANY_TH / SELF_END_TH / HAIRPIN_TH (all default
    47) are structure MELTING TEMPERATURES IN CELSIUS. IDT's "dimer dG at or
    above -9 kcal/mol" is a FREE ENERGY. They are different numbers on
    different scales, and this module returns both so a caller can apply
    either. A structure that passes one can fail the other, which is exactly
    why conflating them is dangerous.
    """
    s = st.homodimer(PALINDROME)
    ok = s.found
    ok &= s.tm != s.dg                       # not the same quantity
    ok &= -60.0 < s.tm < 120.0               # a plausible Celsius temperature
    ok &= s.dg < 0.0                         # a kcal/mol free energy
    ok &= _close(s.temperature, 37.0)        # dg is reported at 37 C by default

    # A real case where the two conventions disagree: this dimer is well under
    # the Primer3 Tm ceiling yet nowhere near the IDT dG floor, so a tool that
    # used 47 as a dG threshold would reject everything.
    disagreeing = [st.homodimer(REF), st.heterodimer(REF, HAIRPIN_SEQ)]
    for d in disagreeing:
        if d.found:
            ok &= d.tm < 47.0 and d.dg > -9.0
    return _check("Tm (Primer3, 47 C) and dG (IDT, -9 kcal/mol) both returned",
                  ok, f"palindrome tm={s.tm:.2f} C dg={s.dg:.2f} kcal/mol")


def test_owczarzy_rejected_for_structures() -> bool:
    """
    The Owczarzy 2004 correction acts on 1/Tm, so it yields no corrected dS.

    Silently ignoring it would make dG quietly uncorrected, so it raises.
    """
    ok = False
    try:
        st.hairpin(HAIRPIN_SEQ, salt_correction=SaltCorrection.OWCZARZY_2004)
    except ValueError:
        ok = True
    ok2 = False
    try:
        st.homodimer(REF, salt_correction="not-a-method")
    except ValueError:
        ok2 = True
    # The two supported methods do differ, so the correction is live.
    a = st.hairpin(HAIRPIN_SEQ, salt_correction=SaltCorrection.NONE)
    b = st.hairpin(HAIRPIN_SEQ, salt_correction=SaltCorrection.SANTALUCIA_1998)
    ok3 = not _close(a.tm, b.tm) and a.tm > b.tm   # less salt, lower Tm
    return _check("structures reject Owczarzy 2004, accept the other two",
                  ok and ok2 and ok3)


# --- degenerate primers ---------------------------------------------------
def test_point_functions_refuse_degenerate_sequences() -> bool:
    """Same contract thermo.py set: refuse, and name the _range variant."""
    cases = [
        (lambda: st.hairpin(DEGENERATE), "hairpin_range"),
        (lambda: st.homodimer(DEGENERATE), "homodimer_range"),
        (lambda: st.heterodimer(REF, DEGENERATE), "heterodimer_range"),
        (lambda: st.heterodimer(DEGENERATE, REF), "heterodimer_range"),
        (lambda: st.end_stability(DEGENERATE), "end_stability_range"),
        (lambda: st.hairpin("ACGN" * 5), "hairpin_range"),
    ]
    ok = True
    for call, wanted in cases:
        try:
            call()
            ok = False
        except SequenceError as exc:
            if wanted not in str(exc):
                ok = False
    return _check("point functions refuse IUPAC codes and name the _range call",
                  ok)


def test_ranges_collapse_to_a_point_when_unambiguous() -> bool:
    """A non-degenerate sequence has one variant, so the span is zero wide."""
    ok = True
    hp = st.hairpin_range(HAIRPIN_SEQ)
    point = st.hairpin(HAIRPIN_SEQ)
    ok &= hp.n_variants == 1 and hp.n_found == 1
    ok &= _close(hp.min_dg, hp.max_dg, 1e-12) and _close(hp.min_dg, point.dg, 1e-12)
    ok &= _close(hp.min_tm, point.tm, 1e-12)
    ok &= hp.most_stable.pairs == point.pairs

    hd = st.homodimer_range(PALINDROME)
    ok &= hd.n_variants == 1 and _close(hd.min_dg, st.homodimer(PALINDROME).dg, 1e-12)

    xd = st.heterodimer_range(REF, thermo.reverse_complement(REF))
    ok &= xd.n_variants == 1
    ok &= _close(xd.min_dg, st.heterodimer(REF, thermo.reverse_complement(REF)).dg,
                 1e-12)

    es = st.end_stability_range(REF)
    ok &= es.n_variants == 1 and _close(es.min_dg, st.end_stability(REF).dg, 1e-12)
    return _check("_range variants collapse to a point on an exact sequence", ok)


def test_degenerate_ranges_span_the_population() -> bool:
    """
    The real benchmark primer, 64 oligos, three different IUPAC codes.

    `RARRGGRATTTTGTCSGKGT` is the reverse primer of SB-CYTB-3 from the Dryad
    eDNA set (So et al. 2020), one of the 7 degenerate pairs of 38 that forced
    IUPAC support into the engine at all. The span matters: a single number
    for a 64-member pool would be a fiction.
    """
    es = st.end_stability_range(DEGENERATE)
    ok = es.n_variants == 64 and es.n_found == 64
    ok &= es.max_dg > es.min_dg                   # a real spread

    hd = st.homodimer_range(DEGENERATE)
    ok &= hd.n_variants == 64
    ok &= hd.n_found > 0
    ok &= hd.most_stable.found
    ok &= _close(hd.most_stable.dg, hd.min_dg, 1e-12)
    ok &= _pairs_are_watson_crick(hd.most_stable)

    hp = st.hairpin_range(DEGENERATE)
    ok &= hp.n_variants == 64
    # No member of this pool folds on itself, and that is reported cleanly.
    ok &= (not hp.found) and hp.min_tm == NO_STRUCTURE_TM
    return _check("degenerate ranges span the 64-oligo population", ok,
                  f"end dG {es.min_dg:.2f}..{es.max_dg:.2f}; "
                  f"homodimer found in {hd.n_found} combinations")


def test_degenerate_range_refuses_past_the_cap() -> bool:
    """
    Over-degenerate input must refuse, not silently sample.

    A sampled worst case is a quietly wrong worst case, which is worse than no
    answer at all. Same stance `thermo.expand_ambiguous` takes.
    """
    ok = False
    try:
        st.heterodimer_range("N" * 8, "N" * 8)
    except SequenceError:
        ok = True
    ok2 = False
    try:
        st.homodimer_range("N" * 8)
    except SequenceError:
        ok2 = True
    ok3 = False
    try:
        st.hairpin_range("N" * 12)        # caught by thermo's own cap
    except SequenceError:
        ok3 = True
    return _check("_range variants refuse past the degeneracy cap",
                  ok and ok2 and ok3)


def test_homodimer_range_covers_cross_members() -> bool:
    """
    A degenerate primer is a pool, so member X can meet member Y, not only X.

    64 variants give 64*65/2 = 2080 unordered combinations, and the reported
    worst case must be the worst over all of them, not over the 64 self-pairs.
    """
    hd = st.homodimer_range(DEGENERATE)
    self_only = min(st.homodimer(v).dg for v in thermo.expand_ambiguous(DEGENERATE))
    ok = hd.min_dg <= self_only + 1e-12
    ok &= hd.n_found > 64          # more combinations than there are variants
    return _check("homodimer_range covers cross-member dimers in the pool", ok,
                  f"pool worst {hd.min_dg:.3f} vs self-pairs only {self_only:.3f}")


# --- regression goldens ---------------------------------------------------
def test_regression_goldens() -> bool:
    """
    REGRESSION GOLDENS. These are this implementation's own output.

    They are NOT validated truth. No independent reference was available when
    they were recorded, so they pin behaviour against silent drift during a
    refactor and nothing more. Each was produced by the method stated in the
    structure.py docstring: dG = dH - T*dS/1000 over the SantaLucia 1998
    unified nearest-neighbour parameters (PNAS 95:1460) with per-helix
    initiation terms, the SantaLucia entropy salt correction at 50 mM Na+, and
    for hairpins a Jacobson-Stockmayer loop penalty whose anchor constant is
    itself approximate.

    Replace them, do not defend them, once `ntthal` runs in CI (plan layer 1b).
    """
    cases = [
        ("hairpin(stem-loop)", st.hairpin(HAIRPIN_SEQ), -2.9781, 54.0665),
        ("homodimer(REF)", st.homodimer(REF), -1.2893, -60.2283),
        ("homodimer(palindrome)", st.homodimer(PALINDROME), -12.6897, 44.1867),
        ("heterodimer(REF, rc)", st.heterodimer(REF, thermo.reverse_complement(REF)),
         -26.8028, 60.3209),
        ("heterodimer(REF, hairpin)", st.heterodimer(REF, HAIRPIN_SEQ),
         -2.9241, -2.7475),
        ("end_stability(REF)", st.end_stability(REF), -2.9803, -27.5986),
    ]
    ok = True
    detail = []
    for name, s, want_dg, want_tm in cases:
        if not (_close(s.dg, want_dg, 0.001) and _close(s.tm, want_tm, 0.001)):
            ok = False
        detail.append(f"{name} dg={s.dg:.4f}/{want_dg} tm={s.tm:.4f}/{want_tm}")
    return _check("regression goldens (NOT validated truth, see docstring)",
                  ok, "; ".join(detail))


# --- optional independent cross-check -------------------------------------
def test_primer3_crosscheck() -> bool:
    """
    Compare against Primer3's `ntthal` when it is on PATH; SKIP when it is not.

    This is the oracle that settles the absolute values, per plan layer 1b: the
    CI image vendors the binary, the production image does not, and calling a
    binary and reading its output is explicitly blessed by Primer3's own "FAIR
    USE OF PRIMER3" note. Nothing is copied from its source or its test data.

    Only dG is asserted, because dG carries no strand-concentration term and so
    cannot be thrown off by a mismatch between our nM convention and ntthal's
    `-d` flag. Tm is printed side by side for inspection instead. `-d 50` is
    passed on the understanding that primer3 treats `-d` as the TOTAL strand
    concentration and quarters it internally, so it corresponds to our
    dnac1 = dnac2 = 25 nM; that convention is pinned properly in
    `test_primer_thermo_oracle.py`, not here.

    If this file and the thermo oracle both end up shelling out to primer3,
    the owner may prefer to move this test over there and keep the oracle
    calls in one place. It lives here for now so that `structure.py` ships
    with its own cross-check rather than none.

    THE TOLERANCE BELOW IS PROVISIONAL. It was never exercised, because
    `ntthal` is absent here. ntthal has mismatch, dangling-end and loop tables
    that this module deliberately does not, so exact agreement is not expected
    even in principle; the first CI run that executes this is what turns the
    bound into a real number.
    """
    exe = shutil.which("ntthal")
    if exe is None:
        print("[SKIP] ntthal cross-check (ntthal not on PATH)")
        return True

    def run(args: list[str]) -> tuple[float, float] | None:
        try:
            out = subprocess.run(
                [exe, "-mv", "50", "-dv", "0", "-n", "0", "-d", "50",
                 "-t", "37", *args],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        dg = re.search(r"dG\s*=\s*(-?[\d.eE+]+)", out)
        tm = re.search(r"\bt\s*=\s*(-?[\d.eE+]+)", out)
        if not dg or not tm:
            return None
        return float(dg.group(1)) / 1000.0, float(tm.group(1))

    rc = thermo.reverse_complement(REF)
    cases = [
        ("hairpin", ["-a", "HAIRPIN", "-s1", HAIRPIN_SEQ],
         st.hairpin(HAIRPIN_SEQ)),
        ("perfect duplex", ["-a", "ANY", "-s1", REF, "-s2", rc],
         st.heterodimer(REF, rc)),
        ("self-dimer", ["-a", "ANY", "-s1", PALINDROME, "-s2", PALINDROME],
         st.homodimer(PALINDROME)),
    ]
    dg_tol = 1.5          # kcal/mol, PROVISIONAL, see the docstring
    ok = True
    for name, args, ours in cases:
        got = run(args)
        if got is None:
            print(f"[SKIP] ntthal cross-check for {name} (no parseable output)")
            continue
        their_dg, their_tm = got
        print(f"       {name}: ours dG={ours.dg:.3f} Tm={ours.tm:.2f} | "
              f"ntthal dG={their_dg:.3f} Tm={their_tm:.2f}")
        if abs(ours.dg - their_dg) > dg_tol:
            ok = False
    return _check(f"ntthal agrees on dG within {dg_tol} kcal/mol "
                  "(provisional tolerance)", ok)


def main() -> int:
    tests = [
        test_no_structure_is_a_structure_not_none,
        test_polya_has_no_hairpin_and_no_self_dimer,
        test_perfect_duplex_matches_thermo,
        test_palindrome_homodimer_matches_thermo,
        test_palindrome_is_a_very_stable_homodimer,
        test_designed_hairpin_is_found_with_the_stem_located,
        test_hairpin_minimum_loop_is_enforced,
        test_gc_in_the_stem_makes_it_more_stable,
        test_hairpin_tm_is_unimolecular,
        test_heterodimer_is_symmetric_in_its_arguments,
        test_heterodimer_with_own_revcomp_is_maximal,
        test_dimer_tm_depends_on_concentration,
        test_dg_rises_with_temperature,
        test_reported_pairs_are_real_base_pairs,
        test_ascii_rendering_is_honest,
        test_bulge_is_rendered_and_skips_one_base,
        test_internal_loop_cap_is_respected,
        test_end_stability_is_the_stack_sum,
        test_end_stability_ranks_gc_ends_as_riskier,
        test_end_stability_initiation_flag,
        test_end_stability_rejects_a_short_window,
        test_both_threshold_conventions_are_available,
        test_owczarzy_rejected_for_structures,
        test_point_functions_refuse_degenerate_sequences,
        test_ranges_collapse_to_a_point_when_unambiguous,
        test_degenerate_ranges_span_the_population,
        test_degenerate_range_refuses_past_the_cap,
        test_homodimer_range_covers_cross_members,
        test_regression_goldens,
        test_primer3_crosscheck,
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
