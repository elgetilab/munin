"""
Differential oracle for primer/ against independent implementations.

Layer 1b of the primer eval plan (docs/agent-track/PRIMER-DESIGN-PLAN.md).
Where test_primer_thermo.py pins our arithmetic against goldens this
implementation produced, this file pins it against software nobody here
wrote: primer3's `oligotm` and `ntthal`, and Biopython's `MeltingTemp`. A
golden can only catch a regression. An oracle can catch an equation that was
wrong from the first commit.

Where the reference tools come from
-----------------------------------
They are baked into the CI test image (backend/scripts/ci/retrieval-tests
.Dockerfile) and nowhere else. The hermetic tier runs with --network none, so
nothing can be fetched at test time: it is in the image already or the test
SKIPs. On a developer machine without them every oracle test prints SKIP and
the file still exits 0.

LICENSE, and why these tools are not in the production image
------------------------------------------------------------
Primer3 is GPL-2.0-or-later and this repo is Apache-2.0. Its binaries live
only in the CI test image, which is built, used and discarded, never
distributed. Primer3's web help section 4, "FAIR USE OF PRIMER3", blesses
this directly: "If you only call Primer3 from your software and interpret its
output, you can use any license you want for your software." That is exactly
what happens below: subprocess, then a regex over stdout. Nothing is linked,
no source is vendored, and no expected value is transcribed from primer3's own
GPL test corpus. Every number compared against here is produced by running the
binary. Biopython (Biopython License) and BLAST+ (US-government public domain)
carry no such constraint.

The two conventions this file exists to pin
--------------------------------------------
Both are silent when wrong, which is why they get dedicated tests rather than
a tolerance wide enough to hide them.

1. **Strand concentration, ordinary case.** `tm_nn(dnac1=C, dnac2=C)` forms
   an effective CT = (C - C/2) = C/2. primer3's `-d` is the TOTAL strand
   concentration and is quartered internally, so `-d 2C` is the same
   experiment. A factor of two here moves Tm by about 0.7 C, which looks like
   nothing in particular.

2. **Strand concentration, self-complementary case.** A palindrome anneals to
   itself, so SantaLucia 1998 uses CT rather than CT/4 plus a -1.4 cal/mol/K
   symmetry entropy. BOTH implementations do this. What differs is which
   number they call CT: primer3 keeps using the total (`-d`), while we and
   Biopython use `dnac1`. So on a palindrome the matching call is
   `tm_nn(dnac1=2C)` against `oligotm -d 2C`, not `dnac1=C`. Line that up and
   agreement is exact to float precision; leave it crossed and every
   palindrome reads 0.6 to 1.7 C low. `test_oligotm_self_complementary_
   convention` asserts the aligned form and prints the crossed gap, so the
   trap stays documented instead of being rediscovered.

Salt: `oligotm -tp 1 -sc 1` is SantaLucia 1998 and `-sc 2` is Owczarzy 2004,
one-for-one with our two SaltCorrection values. Biopython's numbering is
settled by `test_biopython_saltcorr_mapping` below.

Tolerances, all stated, all with the observed worst case beside them
---------------------------------------------------------------------
    Biopython Tm_NN            0.01 C          observed 0.0000 (exact)
    primer3 oligotm            0.05 C          observed 0.005
    primer3 ntthal duplex Tm   0.25 C          observed 0.140
    primer3 ntthal duplex dH   50 cal/mol      observed 0
    structure.py dG vs ntthal  4.5 kcal/mol    observed 3.95

The last one is not an agreement claim and should not be read as one. See
`test_structure_dg_vs_ntthal`.

Run inside the CI test image:
    docker build -t munin-retrieval-tests -f backend/scripts/ci/retrieval-tests.Dockerfile backend/retrieval
    backend/scripts/ci/run_retrieval_tests.sh --only test_primer_thermo_oracle
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import thermo  # noqa: E402
from primer.thermo import SaltCorrection  # noqa: E402

# structure.py is built alongside this file. Guarded so the oracle can land
# first, or on its own, without turning a missing module into a red suite.
try:
    from primer import structure  # type: ignore  # noqa: E402
except ImportError:
    structure = None  # type: ignore

try:
    from Bio.SeqUtils import MeltingTemp as mt  # type: ignore  # noqa: E402
except ImportError:
    mt = None  # type: ignore

OLIGOTM = shutil.which("oligotm")
NTTHAL = shutil.which("ntthal")
BLASTN = shutil.which("blastn")
MAKEBLASTDB = shutil.which("makeblastdb")

# --- the panel ------------------------------------------------------------
# Our own selection, not primer3's test corpus, which is GPL and whose
# arrangement the plan says explicitly not to reuse. Real universal primers
# whose sequences are verifiable in the vector and phage records the plan
# cites, plus synthetic extremes that break things: homopolymer, all-GC,
# GC-block-then-AT-block, a 9-mer, a 34-mer, and six palindromes.
OLIGOS = [
    "CGTTCCAAAGATGTGGGCATGAGCTTAC",        # the reference 28-mer, 50% GC
    "GTAAAACGACGGCCAGT",                   # M13 forward (-21)
    "CAGGAAACAGCTATGAC",                   # M13 reverse
    "CGCCAGGGTTTTCCCAGTCACGAC",            # M13/pUC forward, 24-mer variant
    "TAATACGACTCACTATAGGG",                # T7 promoter
    "ATTAACCCTCACTAAAGGGA",                # T3, the variant matching NC_003298
    "ATTTAGGTGACACTATAG",                  # SP6
    "ATGCGTAGCTAGCTTA",                    # unremarkable 16-mer
    "TTTTTTTTTTTTTTTTTTTT",                # homopolymer, 0% GC
    "GGGGGGCCCCCCAAATTT",                  # GC block then AT block
    "ACGTGGTCA",                           # short, 9-mer
    "GCGCATGCTTTTGCATGCGC",                # strong hairpin, not a palindrome
    "GCCGCCATGGCGGCTTTTTTGCCGCCATGGCGGC",  # 34-mer, repeated arms
    "CGTACGTACGTACGTA",                    # near-palindrome, odd phase
    "GGGGCCCCGGGGCCCC",                    # palindrome, all GC
    "ACGCGCGCGT",                          # palindrome, 10-mer
    "ACGTACGTACGTACGTACGT",                # palindrome, 20-mer
    "GCGCGCGCGCGCGCGCGCGC",                # palindrome, 100% GC
    "TGCATGCATGCATGCATGCATGCATGCATGCA",    # palindrome, 32-mer
    "AAGGCCTTAAGGCCTT",                    # palindrome, mixed
]

# Derived, never hand-maintained: a palindrome that drifted into the wrong
# list would silently weaken the convention tests below.
SELF_COMPLEMENTARY = [s for s in OLIGOS if thermo.reverse_complement(s) == s]
PANEL = [s for s in OLIGOS if s not in SELF_COMPLEMENTARY]

REF = "CGTTCCAAAGATGTGGGCATGAGCTTAC"

# Our dnac1 = dnac2 = 25 nM is CT = 12.5 nM; primer3 quarters its -d.
DNAC_NM = 25.0
PRIMER3_DNA_CONC = 2.0 * DNAC_NM
NA_MM = 50.0

TOL_BIOPYTHON = 0.01      # C
TOL_OLIGOTM = 0.05        # C
TOL_NTTHAL_TM = 0.25      # C
TOL_NTTHAL_DH = 50.0      # cal/mol
TOL_STRUCTURE_DG = 4.5    # kcal/mol, and read its test before trusting it


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _skip(name: str, why: str) -> bool:
    print(f"[SKIP] {name} ({why})")
    return True


# --- reference tool wrappers ----------------------------------------------
def _oligotm(seq: str, sc: int, dna_conc: float = PRIMER3_DNA_CONC,
             na: float = NA_MM) -> float:
    """primer3 oligotm. -tp 1 selects the SantaLucia 1998 NN table."""
    out = subprocess.run(
        [OLIGOTM, "-mv", str(na), "-dv", "0", "-n", "0",
         "-d", str(dna_conc), "-tp", "1", "-sc", str(sc), seq],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return float(out.stdout.strip())


_THERMO_LINE = re.compile(
    r"dS\s*=\s*(?P<ds>-?[\d.eE+]+)\s+dH\s*=\s*(?P<dh>-?[\d.eE+]+)\s+"
    r"dG\s*=\s*(?P<dg>-?[\d.eE+]+)\s+t\s*=\s*(?P<t>-?[\d.eE+]+)"
)


def _ntthal(mode: str, s1: str, s2: str = "", temp: float = 37.0,
            dna_conc: float = PRIMER3_DNA_CONC, na: float = NA_MM) -> dict:
    """
    primer3 ntthal. dS and dH in cal/mol, dG in cal/mol, t in Celsius.

    ntthal prints "No secondary structure could be calculated" when the
    alignment finds nothing. That is a real answer, not a failure, and it maps
    onto the same thing structure.py reports as `found=False`: zeros.
    """
    cmd = [NTTHAL, "-mv", str(na), "-dv", "0", "-n", "0",
           "-d", str(dna_conc), "-t", str(temp), "-a", mode, "-s1", s1]
    if s2:
        cmd += ["-s2", s2]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         check=True, timeout=30)
    m = _THERMO_LINE.search(out.stdout)
    if not m:
        return {"ds": 0.0, "dh": 0.0, "dg": 0.0, "t": 0.0, "found": False}
    found = {k: float(v) for k, v in m.groupdict().items()}
    found["found"] = True
    return found


def _ntthal_duplex(seq: str) -> dict:
    """The perfect duplex: an oligo against its own reverse complement."""
    return _ntthal("ANY", seq, thermo.reverse_complement(seq))


# --- inventory ------------------------------------------------------------
def test_oracles_available() -> bool:
    """
    Always passes; reports which oracles this run actually had.

    Read it as the header for everything below. A run where every line says
    "absent" proved nothing about our thermodynamics and must not be mistaken
    for a green differential test.
    """
    found = {
        "primer3 oligotm": OLIGOTM,
        "primer3 ntthal": NTTHAL,
        "ncbi blastn": BLASTN,
        "ncbi makeblastdb": MAKEBLASTDB,
        "biopython": getattr(mt, "__name__", None) if mt else None,
        "primer.structure": "built" if structure is not None else None,
    }
    for name, where in found.items():
        print(f"       {name:18s} {where or 'absent'}")
    live = sum(1 for v in found.values() if v)
    print(f"       panel: {len(PANEL)} oligos, "
          f"{len(SELF_COMPLEMENTARY)} self-complementary")
    return _check(f"oracle inventory ({live}/{len(found)} available)", True)


# --- Biopython ------------------------------------------------------------
def test_biopython_saltcorr_mapping() -> bool:
    """
    Which Biopython saltcorr NUMBER is which published formula.

    test_primer_thermo.py's docstring records this as the one thing its
    goldens leave open: our 60.32 and 59.78 both appear in Biopython's
    published seven-value series for the reference oligo, but which slot is
    which was never checked against Biopython itself. This checks it and
    prints the mapping, so the answer lives in the CI log.

    Biopython's own salt_correction docstring says:
        1 Schildkraut & Lifson 1965     2 Wetmur 1991
        3 SantaLucia 1996               4 SantaLucia 1998 (11.7 log[Na+])
        5 SantaLucia 1998, on entropy   6 Owczarzy 2004, on 1/Tm
        7 Owczarzy 2008
    We implement 5 and 6, so those are what this asserts.
    """
    if mt is None:
        return _skip("biopython saltcorr mapping", "Bio not installed")

    series = [mt.Tm_NN(REF, dnac1=DNAC_NM, dnac2=DNAC_NM, Na=NA_MM, saltcorr=m)
              for m in range(1, 8)]
    ours = {
        SaltCorrection.SANTALUCIA_1998: thermo.tm_nn(
            REF, salt_correction=SaltCorrection.SANTALUCIA_1998),
        SaltCorrection.OWCZARZY_2004: thermo.tm_nn(
            REF, salt_correction=SaltCorrection.OWCZARZY_2004),
    }
    uncorrected = mt.Tm_NN(REF, dnac1=DNAC_NM, dnac2=DNAC_NM, Na=NA_MM,
                           saltcorr=0)

    print("       biopython saltcorr 1..7: "
          + ", ".join(f"{t:.4f}" for t in series))
    mapping = {}
    for name, value in ours.items():
        hits = [i + 1 for i, t in enumerate(series)
                if abs(t - value) <= TOL_BIOPYTHON]
        mapping[name] = hits
        print(f"       ours {name:15s} = {value:.4f} -> saltcorr {hits}")
    ours_none = thermo.tm_nn(REF, salt_correction=SaltCorrection.NONE)
    print(f"       ours {SaltCorrection.NONE:15s} = {ours_none:.4f} -> "
          f"saltcorr 0, no correction = {uncorrected:.4f}")

    ok = mapping[SaltCorrection.SANTALUCIA_1998] == [5]
    # 7 (Owczarzy 2008) collapses onto 6 when only Na+ is present, so our
    # Owczarzy 2004 legitimately lands in two slots here. 6 must be one.
    ok &= 6 in mapping[SaltCorrection.OWCZARZY_2004]
    ok &= abs(ours_none - uncorrected) <= TOL_BIOPYTHON
    return _check(
        "saltcorr 5 = SantaLucia 1998 entropy, 6 = Owczarzy 2004, 0 = none",
        ok, f"got {mapping}")


def test_biopython_panel() -> bool:
    """Every panel oligo, both salt corrections, against Bio.SeqUtils."""
    if mt is None:
        return _skip("biopython panel", "Bio not installed")

    worst = 0.0
    worst_at = ""
    ok = True
    for seq in OLIGOS:
        # Biopython takes self-complementarity as an explicit flag that
        # defaults off; we detect it. Passing it is what makes the two
        # comparable at all.
        selfcomp = seq in SELF_COMPLEMENTARY
        for method, saltcorr in ((SaltCorrection.SANTALUCIA_1998, 5),
                                 (SaltCorrection.OWCZARZY_2004, 6)):
            ours = thermo.tm_nn(seq, dnac1=DNAC_NM, dnac2=DNAC_NM, na=NA_MM,
                                salt_correction=method)
            theirs = mt.Tm_NN(seq, dnac1=DNAC_NM, dnac2=DNAC_NM, Na=NA_MM,
                              saltcorr=saltcorr, selfcomp=selfcomp)
            diff = abs(ours - theirs)
            if diff > worst:
                worst, worst_at = diff, f"{seq} {method}"
            if diff > TOL_BIOPYTHON:
                ok = False
    print(f"       worst disagreement {worst:.4f} C "
          f"(tolerance {TOL_BIOPYTHON})")
    return _check(
        f"tm_nn matches Biopython on {len(OLIGOS)} oligos x 2 corrections",
        ok, f"worst {worst:.4f} C at {worst_at}")


def test_biopython_ion_mixtures() -> bool:
    """
    K+, Tris, Mg2+ and dNTPs, not only Na+.

    The von Ahsen 2001 monovalent-equivalent conversion is a separate chance
    to be wrong from the NN table, and it only shows up once a realistic PCR
    buffer is used instead of plain sodium.
    """
    if mt is None:
        return _skip("biopython ion mixtures", "Bio not installed")

    buffers = [
        ({"na": 50.0, "tris": 10.0}, {"Na": 50, "Tris": 10}),
        ({"na": 50.0, "tris": 10.0, "mg": 1.5},
         {"Na": 50, "Tris": 10, "Mg": 1.5}),
        ({"na": 50.0, "tris": 10.0, "mg": 1.5, "dntp": 0.6},
         {"Na": 50, "Tris": 10, "Mg": 1.5, "dNTPs": 0.6}),
        ({"na": 50.0, "k": 20.0}, {"Na": 50, "K": 20}),
        ({"na": 0.0, "k": 50.0, "tris": 20.0}, {"Na": 0, "K": 50, "Tris": 20}),
        # dNTPs in excess of Mg: the chelation branch, where the term vanishes
        ({"na": 50.0, "mg": 1.0, "dntp": 2.0}, {"Na": 50, "Mg": 1.0, "dNTPs": 2.0}),
    ]
    ok = True
    detail = []
    for ours_kw, theirs_kw in buffers:
        for method, saltcorr in ((SaltCorrection.SANTALUCIA_1998, 5),
                                 (SaltCorrection.OWCZARZY_2004, 6)):
            ours = thermo.tm_nn(REF, dnac1=DNAC_NM, dnac2=DNAC_NM,
                                salt_correction=method, **ours_kw)
            theirs = mt.Tm_NN(REF, dnac1=DNAC_NM, dnac2=DNAC_NM,
                              saltcorr=saltcorr, **theirs_kw)
            if abs(ours - theirs) > TOL_BIOPYTHON:
                ok = False
                detail.append(
                    f"{theirs_kw} {method}: {ours:.4f} vs {theirs:.4f}")
    return _check("monovalent-equivalent conversion matches Biopython across "
                  f"{len(buffers)} buffers", ok, "; ".join(detail))


def test_biopython_wallace() -> bool:
    """The rule of thumb, as a check that the sequence survived at all."""
    if mt is None:
        return _skip("biopython Wallace", "Bio not installed")
    ok = True
    for seq in OLIGOS:
        if abs(thermo.tm_wallace(seq) - mt.Tm_Wallace(seq)) > 1e-9:
            ok = False
    return _check(f"tm_wallace agrees with Biopython on {len(OLIGOS)} oligos",
                  ok)


# --- primer3 oligotm ------------------------------------------------------
def test_concentration_convention() -> bool:
    """
    primer3's -d is the total and is quartered; ours is (dnac1 - dnac2/2).

    So `-d 2C` must equal `dnac1 = dnac2 = C`. Pinned because a factor of two
    here shifts Tm by well under a degree, which is exactly the size of error
    that survives review.
    """
    if not OLIGOTM:
        return _skip("concentration convention", "oligotm not on PATH")
    ok = True
    detail = []
    for conc in (5.0, 25.0, 200.0, 1000.0):
        ours = thermo.tm_nn(REF, dnac1=conc, dnac2=conc, na=NA_MM,
                            salt_correction=SaltCorrection.SANTALUCIA_1998)
        theirs = _oligotm(REF, sc=1, dna_conc=2.0 * conc)
        detail.append(f"{conc:g}nM {ours:.4f}/{theirs:.4f}")
        if abs(ours - theirs) > TOL_OLIGOTM:
            ok = False
    print("       ours/oligotm: " + ", ".join(detail))
    return _check("dnac1=dnac2=C equals primer3 -d 2C from 5 to 1000 nM", ok,
                  "; ".join(detail))


def test_oligotm_panel() -> bool:
    """
    Both salt corrections against primer3's own oligotm, panel-wide.

    Palindromes are handled by the next test, which has to cross the
    concentration convention first.
    """
    if not OLIGOTM:
        return _skip("oligotm panel", "oligotm not on PATH")

    worst = 0.0
    worst_at = ""
    ok = True
    for seq in PANEL:
        for method, sc in ((SaltCorrection.SANTALUCIA_1998, 1),
                           (SaltCorrection.OWCZARZY_2004, 2)):
            ours = thermo.tm_nn(seq, dnac1=DNAC_NM, dnac2=DNAC_NM, na=NA_MM,
                                salt_correction=method)
            theirs = _oligotm(seq, sc=sc)
            diff = abs(ours - theirs)
            if diff > worst:
                worst, worst_at = diff, f"{seq} {method}"
            if diff > TOL_OLIGOTM:
                ok = False
    print(f"       worst disagreement {worst:.4f} C "
          f"(tolerance {TOL_OLIGOTM})")
    return _check(
        f"tm_nn matches primer3 oligotm on {len(PANEL)} oligos x 2 corrections",
        ok, f"worst {worst:.4f} C at {worst_at}")


def test_oligotm_self_complementary_convention() -> bool:
    """
    Palindromes, where the two tools mean different things by "CT".

    Both apply the SantaLucia self-complementary treatment (CT, not CT/4,
    plus the -1.4 cal/mol/K symmetry entropy). primer3 keeps reading `-d` as
    the total strand concentration; we read `dnac1`, as Biopython does. So the
    matching call against `oligotm -d 2C` is `tm_nn(dnac1=2C)`, and with that
    the agreement is exact.

    The crossed mapping, `dnac1=C` against `-d 2C`, is the trap: it runs
    clean, reports every palindrome 0.6 to 1.7 C low, and looks like a
    thermodynamic disagreement rather than an argument mismatch. It is printed
    on every run so the number stays on record, and asserted to stay inside
    that band so that a change in our palindrome handling cannot pass
    unnoticed.
    """
    if not OLIGOTM:
        return _skip("oligotm self-complementary convention",
                     "oligotm not on PATH")

    ok = True
    worst = 0.0
    crossed = []
    for seq in SELF_COMPLEMENTARY:
        for conc in (DNAC_NM, 4 * DNAC_NM):
            total = 2.0 * conc
            for method, sc in ((SaltCorrection.SANTALUCIA_1998, 1),
                               (SaltCorrection.OWCZARZY_2004, 2)):
                ours = thermo.tm_nn(seq, dnac1=total, dnac2=total, na=NA_MM,
                                    salt_correction=method)
                theirs = _oligotm(seq, sc=sc, dna_conc=total)
                worst = max(worst, abs(ours - theirs))
                if abs(ours - theirs) > TOL_OLIGOTM:
                    ok = False
        naive = thermo.tm_nn(seq, dnac1=DNAC_NM, dnac2=DNAC_NM, na=NA_MM,
                             salt_correction=SaltCorrection.SANTALUCIA_1998)
        gap = _oligotm(seq, sc=1) - naive
        crossed.append(f"{seq[:10]}..{len(seq)}mer {gap:+.2f}")
        if not 0.4 <= gap <= 2.0:
            ok = False
    print(f"       aligned CT: worst {worst:.4f} C "
          f"(tolerance {TOL_OLIGOTM})")
    print("       crossed CT would read low by: " + ", ".join(crossed))
    return _check(
        f"palindromes match oligotm once CT is aligned "
        f"({len(SELF_COMPLEMENTARY)} oligos)", ok,
        f"worst {worst:.4f} C; crossed gaps {crossed}")


# --- primer3 ntthal -------------------------------------------------------
def test_ntthal_duplex_enthalpy() -> bool:
    """
    Our NN enthalpy sum against primer3's, on the perfect duplex.

    dH carries no salt and no concentration term, so this isolates the
    parameter table and the initiation terms from every other moving part. A
    single mistyped stack shows up here and nowhere else as cleanly.
    """
    if not NTTHAL:
        return _skip("ntthal enthalpy", "ntthal not on PATH")

    worst = 0.0
    worst_at = ""
    ok = True
    for seq in OLIGOS:
        ours_kcal, _ = thermo.nn_thermodynamics(seq)
        theirs_cal = _ntthal_duplex(seq)["dh"]
        diff = abs(ours_kcal * 1000.0 - theirs_cal)
        if diff > worst:
            worst, worst_at = diff, seq
        if diff > TOL_NTTHAL_DH:
            ok = False
    print(f"       worst disagreement {worst:.1f} cal/mol "
          f"(tolerance {TOL_NTTHAL_DH})")
    return _check(
        f"nn_thermodynamics dH matches ntthal on {len(OLIGOS)} duplexes",
        ok, f"worst {worst:.1f} cal/mol at {worst_at}")


def test_ntthal_duplex_tm() -> bool:
    """
    Duplex Tm against ntthal's thermodynamic alignment.

    Looser than oligotm, 0.25 C, because ntthal is solving a different
    problem: it aligns the two strands and is free to leave a terminal base
    unpaired, where tm_nn assumes the perfect duplex throughout. Palindromes
    are excluded, for the concentration reason above.
    """
    if not NTTHAL:
        return _skip("ntthal duplex Tm", "ntthal not on PATH")

    worst = 0.0
    worst_at = ""
    ok = True
    for seq in PANEL:
        ours = thermo.tm_nn(seq, dnac1=DNAC_NM, dnac2=DNAC_NM, na=NA_MM,
                            salt_correction=SaltCorrection.SANTALUCIA_1998)
        theirs = _ntthal_duplex(seq)["t"]
        diff = abs(ours - theirs)
        if diff > worst:
            worst, worst_at = diff, seq
        if diff > TOL_NTTHAL_TM:
            ok = False
    print(f"       worst disagreement {worst:.4f} C "
          f"(tolerance {TOL_NTTHAL_TM})")
    return _check(f"tm_nn matches ntthal on {len(PANEL)} duplexes",
                  ok, f"worst {worst:.4f} C at {worst_at}")


# --- structure.py ---------------------------------------------------------
def _dg_kcal(value) -> float:
    """
    Normalise a dG to kcal/mol.

    ntthal prints cal/mol; structure.py returns kcal/mol, the convention
    thermo.nn_thermodynamics already uses. Anything above 200 in magnitude
    cannot be kcal/mol for an oligo, so it is read as cal/mol. Accepts a bare
    float or a Structure.
    """
    for attr in ("dg", "dG", "delta_g"):
        if hasattr(value, attr):
            value = getattr(value, attr)
            break
    value = float(value)
    return value / 1000.0 if abs(value) > 200.0 else value


#: Cases where structure.py and ntthal are known to disagree by more than
#: TOL_STRUCTURE_DG, each with the reason. This is a pin, not an exemption:
#: a listed case that comes back inside tolerance prints a loud line telling
#: whoever fixed it to delete the entry, so the registry cannot silently rot
#: into a list of excuses. Entries are keyed (kind, sequence label).
KNOWN_STRUCTURE_GAPS = {
    ("homodimer", "GCCGCCATGGCGGCTTTTTTGCCGCCATGGCGGC"): (
        "two identical 14-bp GC arms separated by T6. ntthal pairs BOTH arms "
        "across a 6-nt internal loop (-34.0 kcal/mol); structure.py reports "
        "one arm and stops (-18.0). Its own docstring says a mismatch ends a "
        "helix and the next one pays its initiation terms, so the second "
        "helix should be found at a cost of about +2, not +16 kcal/mol. "
        "Reported to the module owner, not worked around here."
    ),
}


def test_structure_dg_vs_ntthal() -> bool:
    """
    Hairpin, homodimer and heterodimer dG against ntthal.

    READ THE TOLERANCE AS A BOUND ON KNOWN-MISSING PHYSICS, NOT AS AGREEMENT.
    structure.py says in its own "Known limits" that it has no mismatch,
    dangling-end, terminal-mismatch or coaxial-stacking parameters, no bulge
    or internal-loop size table, and an approximate hairpin loop anchor.
    ntthal has all of them, so ntthal finds systematically more stable
    structures. 4.5 kcal/mol is the size of that gap plus headroom, not a
    claim that the two agree. What this test does establish:

      * outside the registry above, the two never diverge by more than that,
        so our DP is solving the same problem rather than a different one;
      * wherever WE report a fold, ntthal reports one too, so we are not
        producing structures out of thin air;
      * the signed offset is printed per kind every run, so when structure.py
        gains loop and mismatch tables the gap closing is visible rather than
        inferred. As of writing, hairpins carry most of it: on sequences where
        both tools pick the SAME base pairs, ours is about 3.6 to 4.0 kcal/mol
        less stable, which is the approximate loop anchor the module flags as
        its one unverified constant.

    SKIPs if primer/structure.py is not built yet.
    """
    if structure is None:
        return _skip("structure dG vs ntthal", "primer.structure not built")
    if not NTTHAL:
        return _skip("structure dG vs ntthal", "ntthal not on PATH")

    cases = []
    for seq in OLIGOS:
        if hasattr(structure, "hairpin"):
            cases.append(("hairpin", seq, structure.hairpin(seq),
                          _ntthal("HAIRPIN", seq)))
        if hasattr(structure, "homodimer"):
            cases.append(("homodimer", seq, structure.homodimer(seq),
                          _ntthal("ANY", seq, seq)))
    if hasattr(structure, "heterodimer"):
        for a, b in zip(PANEL, PANEL[1:]):
            cases.append(("heterodimer", f"{a}/{b}",
                          structure.heterodimer(a, b), _ntthal("ANY", a, b)))
    if not cases:
        return _skip("structure dG vs ntthal", "no entry points found")

    ok = True
    worst = 0.0
    worst_at = ""
    by_kind: dict[str, list[float]] = {}
    detail = []
    for kind, label, ours_obj, ref in cases:
        try:
            ours = _dg_kcal(ours_obj)
        except Exception as exc:                            # noqa: BLE001
            ok = False
            detail.append(f"{kind}({label}) unreadable: {exc!r}")
            continue
        theirs = ref["dg"] / 1000.0
        offset = ours - theirs
        by_kind.setdefault(kind, []).append(offset)
        known = KNOWN_STRUCTURE_GAPS.get((kind, label))
        if known is not None:
            if abs(offset) <= TOL_STRUCTURE_DG:
                print(f"       [GAP CLOSED] {kind}({label[:28]}) is now "
                      f"{offset:+.2f} kcal/mol: delete its entry from "
                      f"KNOWN_STRUCTURE_GAPS")
            else:
                print(f"       [KNOWN GAP] {kind}({label[:28]}) "
                      f"{ours:.2f} vs ntthal {theirs:.2f} kcal/mol: {known}")
            continue
        if abs(offset) > worst:
            worst, worst_at = abs(offset), f"{kind}({label})"
        if abs(offset) > TOL_STRUCTURE_DG:
            ok = False
            detail.append(f"{kind}({label}): {ours:.2f} vs ntthal "
                          f"{theirs:.2f} kcal/mol")
        # No fold invented where ntthal sees none.
        if getattr(ours_obj, "found", ours != 0.0) and not ref["found"]:
            ok = False
            detail.append(f"{kind}({label}): we fold, ntthal finds nothing")

    for kind, offsets in sorted(by_kind.items()):
        mean = sum(offsets) / len(offsets)
        print(f"       {kind:12s} n={len(offsets):3d} mean offset "
              f"{mean:+.2f} kcal/mol (positive = we call it less stable)")
    print(f"       worst |offset| outside the registry {worst:.2f} kcal/mol "
          f"at {worst_at} (tolerance {TOL_STRUCTURE_DG})")
    return _check("structure dG tracks ntthal within the known-limits bound",
                  ok, "; ".join(detail))


# --- BLAST+ ---------------------------------------------------------------
def test_blast_round_trip_offline() -> bool:
    """
    makeblastdb and blastn work with no network, which specificity.py needs
    (plan build order step 7).

    Not a differential test of our code: there is no BLAST caller yet. It is a
    check that the toolchain is really baked into the image rather than
    reachable only because some run happened to have a network. CI runs
    --network none, so a BLAST step that wants to fetch anything fails here
    first instead of in whatever branch adds specificity.
    """
    if not (BLASTN and MAKEBLASTDB):
        return _skip("BLAST+ round trip", "blastn/makeblastdb not on PATH")

    primer = "TAATACGACTCACTATAGGG"
    template = ("ACGTACGTTTGACGTAACGT" + primer
                + "GGGCCCAAATTTGGGCCCAAATTTACGT")
    with tempfile.TemporaryDirectory() as tmp:
        subject = Path(tmp) / "subject.fasta"
        query = Path(tmp) / "query.fasta"
        subject.write_text(f">template\n{template}\n", encoding="utf-8")
        query.write_text(f">T7\n{primer}\n", encoding="utf-8")
        db = str(Path(tmp) / "db")
        env = dict(os.environ, BLASTDB=tmp)
        subprocess.run(
            [MAKEBLASTDB, "-in", str(subject), "-dbtype", "nucl", "-out", db],
            capture_output=True, text=True, check=True, timeout=60, env=env,
        )
        out = subprocess.run(
            [BLASTN, "-task", "blastn-short", "-query", str(query),
             "-db", db, "-evalue", "10", "-outfmt",
             "6 qseqid pident length sstart send"],
            capture_output=True, text=True, check=True, timeout=60, env=env,
        )
    rows = [r.split("\t") for r in out.stdout.strip().splitlines() if r]
    detail = out.stdout.strip().replace("\n", " | ") or out.stderr.strip()
    ok = bool(rows)
    if ok:
        pident, length = float(rows[0][1]), int(rows[0][2])
        start, end = int(rows[0][3]), int(rows[0][4])
        # The site is planted at 1-based 21..40 of the template.
        ok = pident == 100.0 and length == len(primer) and (start, end) == (21, 40)
    return _check("blastn finds a planted exact primer site offline",
                  ok, detail)


def main() -> int:
    tests = [
        test_oracles_available,
        test_biopython_saltcorr_mapping,
        test_biopython_panel,
        test_biopython_ion_mixtures,
        test_biopython_wallace,
        test_concentration_convention,
        test_oligotm_panel,
        test_oligotm_self_complementary_convention,
        test_ntthal_duplex_enthalpy,
        test_ntthal_duplex_tm,
        test_structure_dg_vs_ntthal,
        test_blast_round_trip_offline,
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
