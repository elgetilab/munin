"""
Tests for primer/verify.py - the in-silico PCR oracle.

Layers 2 and 3 of the primer eval plan (docs/agent-track/PRIMER-DESIGN-PLAN.md).
Pure functions, no network, no services. This module is both the tool's
self-check and the eval suite's grader, so a bug here is a bug in every number
the suite reports; the tests are correspondingly blunt.

Where the expected values come from
-----------------------------------
Two real GenBank records, vendored under `data/primer/` and read from disk.
**Nothing here fetches at test time**, because the hermetic CI tier runs with
`--network none`. `data/primer/README.md` records the accessions, the exact
retrieval commands, the retrieval date and the sha256 of each file.

The three golden amplicon sizes come from the plan's verified fixture table
(section 4, layer 3), where they were established by exact string match
against the fetched records, circular-aware and on both strands, independently
of this implementation. The test re-asserts the record lengths first, so a
re-fetch that silently returned a different version fails here rather than
quietly moving a golden.

| Template | Pair | Amplicon |
|---|---|---|
| pUC19 L09137 | M13 Fwd(-21) x M13/pUC Rev | 123 bp |
| pUC19 L09137 | M13 Fwd(-40) x M13/pUC Rev | 142 bp |
| pBluescript X52328 | T7 x T3 (21-mer variant) | 168 bp |
| pBluescript X52328 | T7 x T3 (20-mer variant) | 165 bp |

One correction to the plan, recorded rather than papered over: its table lists
168 bp for "T7 x T3" while its sequence caveat recommends the 20-mer T3
`ATTAACCCTCACTAAAGGGA` (the only variant that also matches the T3 phage
genome NC_003298). Those two statements do not describe the same reaction.
The 21-mer `GCAATTAACCCTCACTAAAGG` gives 168 bp and the recommended 20-mer
gives 165 bp, both by exact match against X52328. Both are pinned below so
neither number can drift, and `data/primer/README.md` carries the detail.

The pUC19 restriction-site positions check against the plan's 1-based MCS
table (EcoRI 396, SacI 402, KpnI 408, BamHI 417, XbaI 423, SalI 429, PstI 435,
SphI 441, HindIII 447), which doubles as an explicit check of the 0-based
half-open convention this module reports in.

Everything else is constructed in the test and self-evident by inspection:
synthetic templates built by concatenation, so the right answer is visible in
the construction rather than asserted from outside.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_verify.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import thermo, verify  # noqa: E402
from primer.thermo import SequenceError, reverse_complement  # noqa: E402
from primer.verify import (  # noqa: E402
    MINUS,
    MULTIPLE_PRODUCTS,
    NO_PRODUCT,
    PLUS,
    SINGLE_PRODUCT,
)

DATA = Path(__file__).resolve().parent / "data" / "primer"

# --- the vendored records -------------------------------------------------
PUC19_FILE = DATA / "pUC19_L09137.fasta"
PBS_FILE = DATA / "pBluescriptIISKplus_X52328.fasta"
PUC19_LENGTH = 2686
PBS_LENGTH = 2961

# --- primers, each variant pinned exactly (plan section 4, layer 3) -------
M13_FWD_21 = "TGTAAAACGACGGCCAGT"        # M13 Forward (-21)
M13_FWD_40 = "GTTTTCCCAGTCACGAC"         # M13 Forward (-40)
M13_REV = "AGCGGATAACAATTTCACACAGG"      # M13/pUC Reverse
T7 = "TAATACGACTCACTATAGGG"              # T7 promoter
T3_20 = "ATTAACCCTCACTAAAGGGA"           # T3, the variant the plan prefers
T3_21 = "GCAATTAACCCTCACTAAAGG"          # T3, the widely circulated variant

# pUC19 MCS, plan section 4: all unique, and the two that are absent.
MCS_UNIQUE = [
    ("EcoRI", "GAATTC", 396),
    ("SacI", "GAGCTC", 402),
    ("KpnI", "GGTACC", 408),
    ("BamHI", "GGATCC", 417),
    ("XbaI", "TCTAGA", 423),
    ("SalI", "GTCGAC", 429),
    ("PstI", "CTGCAG", 435),
    ("SphI", "GCATGC", 441),
    ("HindIII", "AAGCTT", 447),
]
MCS_ABSENT = [("NcoI", "CCATGG"), ("NotI", "GCGGCCGC")]


def _load_fasta(path: Path) -> str:
    """Single-record FASTA to an uppercase sequence string. Offline only."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "".join(line.strip() for line in lines if not line.startswith(">")).upper()


PUC19 = _load_fasta(PUC19_FILE)
PBS = _load_fasta(PBS_FILE)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# --- the fixtures are what we think they are ------------------------------
def test_vendored_records_intact() -> bool:
    """
    Lengths first, before anything is asserted against these sequences.

    If a re-fetch ever returns a different version of either record, this is
    the test that says so, instead of three amplicon goldens shifting by a few
    bases and someone 'fixing' the expected values.
    """
    ok = len(PUC19) == PUC19_LENGTH
    ok &= len(PBS) == PBS_LENGTH
    ok &= set(PUC19) <= set("ACGT") and set(PBS) <= set("ACGT")
    return _check(
        "vendored pUC19 (2686 bp) and pBluescript (2961 bp) intact",
        ok, f"got {len(PUC19)} and {len(PBS)}",
    )


# --- base compatibility, the single comparison function -------------------
def test_bases_compatible_iupac() -> bool:
    """R matches A and G but not C or T; N matches everything, both ways."""
    ok = verify.bases_compatible("R", "A") and verify.bases_compatible("R", "G")
    ok &= not verify.bases_compatible("R", "C")
    ok &= not verify.bases_compatible("R", "T")
    ok &= verify.bases_compatible("A", "A") and not verify.bases_compatible("A", "C")
    # Template N accepts any primer base, primer N accepts any template base.
    ok &= all(verify.bases_compatible(b, "N") for b in "ACGTRYSWKMBDHVN")
    ok &= all(verify.bases_compatible("N", b) for b in "ACGTRYSWKMBDHVN")
    # Codes whose sets intersect match; codes whose sets are disjoint do not.
    ok &= verify.bases_compatible("R", "Y") is False      # {A,G} vs {C,T}
    ok &= verify.bases_compatible("R", "S") is True       # {A,G} vs {C,G}
    ok &= verify.bases_compatible("B", "A") is False      # not-A vs A
    ok &= verify.bases_compatible("V", "A") is True       # not-T vs A
    # Symmetric, and case-insensitive.
    ok &= verify.bases_compatible("r", "a")
    for bad in ("Z", "1", "-"):
        try:
            verify.bases_compatible(bad, "A")
            ok = False
        except SequenceError:
            pass
    return _check("bases_compatible implements IUPAC set intersection", ok)


# --- coordinates: 0-based half-open, plus strand --------------------------
def test_coordinate_convention() -> bool:
    """
    The convention the whole module hangs on, asserted directly.

    `end - start == len(primer)` always, `template[start:end]` is the primer
    on `"+"` and its reverse complement on `"-"`. If this test passes, no
    off-by-one is possible in the amplicon arithmetic downstream.
    """
    fwd = verify.find_binding_sites(M13_FWD_21, PUC19, strands=PLUS)
    rev = verify.find_binding_sites(M13_REV, PUC19, strands=MINUS)
    ok = len(fwd) == 1 and len(rev) == 1
    if not ok:
        return _check("coordinates are 0-based half-open on the plus strand",
                      False, f"{len(fwd)} fwd, {len(rev)} rev sites")

    f, r = fwd[0], rev[0]
    ok &= f.end - f.start == len(M13_FWD_21)
    ok &= r.end - r.start == len(M13_REV)
    ok &= PUC19[f.start:f.end] == M13_FWD_21
    ok &= PUC19[r.start:r.end] == reverse_complement(M13_REV)
    ok &= f.length == len(M13_FWD_21) and r.length == len(M13_REV)
    # Known coordinates for this record, 0-based.
    ok &= (f.start, f.end) == (377, 395)
    ok &= (r.start, r.end) == (477, 500)
    # 3' ends: forward's is the right edge, reverse's is the left edge.
    ok &= f.three_prime_position == 394
    ok &= r.three_prime_position == 477
    return _check(
        "coordinates are 0-based half-open on the plus strand",
        ok, f"fwd {f.start}..{f.end}, rev {r.start}..{r.end}",
    )


# --- orientation: the classic error ---------------------------------------
def test_reverse_primer_is_not_in_the_template() -> bool:
    """
    The reverse primer does NOT occur in the template; its rev-comp does.

    This is the single most common bookkeeping failure the plan cites, so it
    gets an explicit test rather than being implied by an amplicon size.
    """
    ok = M13_REV not in PUC19
    ok &= reverse_complement(M13_REV) in PUC19
    ok &= M13_FWD_21 in PUC19
    # And the module agrees: M13_REV has no plus-strand site, one minus site.
    ok &= verify.find_binding_sites(M13_REV, PUC19, strands=PLUS) == []
    ok &= len(verify.find_binding_sites(M13_REV, PUC19, strands=MINUS)) == 1
    ok &= len(verify.find_binding_sites(M13_FWD_21, PUC19, strands=PLUS)) == 1
    ok &= verify.find_binding_sites(M13_FWD_21, PUC19, strands=MINUS) == []
    # Both strands at once finds exactly these two, one each.
    both = verify.find_binding_sites(M13_REV, PUC19, strands="both")
    ok &= len(both) == 1 and both[0].strand == MINUS
    return _check("reverse primer appears only as its reverse complement", ok)


def test_swapped_pair_yields_nothing() -> bool:
    """
    Hand the pair over backwards and get zero products, not a wrong number.

    `simulate_pcr` looks for the forward primer in `"+"` orientation and the
    reverse in `"-"`, so a swapped pair is not found at all: zero sites, zero
    products. That is the strongest possible report of the error, and a
    simulator that shrugged and returned a plausible size here would be worse
    than useless as a grader.
    """
    right = verify.simulate_pcr(M13_FWD_21, M13_REV, PUC19, circular=True)
    wrong = verify.simulate_pcr(M13_REV, M13_FWD_21, PUC19, circular=True)
    ok = right.outcome == SINGLE_PRODUCT and right.sizes == (123,)
    ok &= wrong.outcome == NO_PRODUCT and wrong.count == 0
    ok &= len(wrong.forward_sites) == 0 and len(wrong.reverse_sites) == 0
    # Both primers are in the template, just not in the orientations asked for.
    ok &= len(verify.find_binding_sites(M13_REV, PUC19, circular=True)) == 1
    ok &= len(verify.find_binding_sites(M13_FWD_21, PUC19, circular=True)) == 1
    return _check(
        "swapped forward/reverse gives no product, not a wrong one",
        ok, f"right={right.sizes} wrong={wrong.sizes}",
    )


def test_outward_facing_pair() -> bool:
    """
    Both primers bind correctly but point away from each other.

    Linearly there is nothing between them to copy, so no product. On a
    circle they still meet, the long way round, and that product is real:
    this is how a plasmid gets linearised by inverse PCR. The two answers
    differ by the whole plasmid, so an implementation that confuses them is
    not subtly wrong.
    """
    fwd = PUC19[200:220]
    rev = reverse_complement(PUC19[100:120])
    flat = verify.simulate_pcr(fwd, rev, PUC19, circular=False)
    circ = verify.simulate_pcr(fwd, rev, PUC19, circular=True)
    ok = len(flat.forward_sites) == 1 and len(flat.reverse_sites) == 1
    ok &= flat.outcome == NO_PRODUCT
    ok &= circ.outcome == SINGLE_PRODUCT
    ok &= circ.sizes == (PUC19_LENGTH - 200 + 120,)       # 2606
    p = circ.product
    ok &= p is not None and p.wraps and p.starts_with_forward and p.ends_with_reverse
    return _check("outward-facing pair: nothing linear, the long way round circular",
                  ok, f"linear={flat.sizes} circular={circ.sizes}")


# --- the golden amplicons -------------------------------------------------
def test_golden_amplicon_m13_21() -> bool:
    """pUC19 x M13 Forward(-21) x M13/pUC Reverse = 123 bp. The canonical case."""
    res = verify.simulate_pcr(M13_FWD_21, M13_REV, PUC19, circular=True)
    ok = res.outcome == SINGLE_PRODUCT and res.sizes == (123,)
    p = res.product
    ok &= p is not None and p.length == len(p.sequence) == 123
    ok &= p is not None and not p.wraps
    return _check("pUC19 M13 Fwd(-21) x M13/pUC Rev = 123 bp",
                  ok, f"got {res.sizes} ({res.outcome})")


def test_golden_amplicon_m13_40() -> bool:
    """pUC19 x M13 Forward(-40) x the same reverse = 142 bp."""
    res = verify.simulate_pcr(M13_FWD_40, M13_REV, PUC19, circular=True)
    ok = res.outcome == SINGLE_PRODUCT and res.sizes == (142,)
    return _check("pUC19 M13 Fwd(-40) x M13/pUC Rev = 142 bp",
                  ok, f"got {res.sizes} ({res.outcome})")


def test_golden_amplicon_t7_t3() -> bool:
    """
    pBluescript T7 x T3, both circulated T3 variants pinned.

    168 bp with the 21-mer `GCAATTAACCCTCACTAAAGG` (the plan's table), 165 bp
    with the 20-mer `ATTAACCCTCACTAAAGGGA` (the plan's sequence caveat). The
    3 bp is the 21-mer's two extra 5' bases plus a one-base register shift;
    see this module's docstring. Pinning both is what stops either number
    being quietly adjusted later.
    """
    res21 = verify.simulate_pcr(T7, T3_21, PBS, circular=True)
    res20 = verify.simulate_pcr(T7, T3_20, PBS, circular=True)
    ok = res21.outcome == SINGLE_PRODUCT and res21.sizes == (168,)
    ok &= res20.outcome == SINGLE_PRODUCT and res20.sizes == (165,)
    # Same forward site in both, so the whole difference is the T3 variant.
    ok &= res21.forward_sites[0].start == res20.forward_sites[0].start == 625
    return _check(
        "pBluescript T7 x T3 = 168 bp (21-mer) and 165 bp (20-mer)",
        ok, f"21-mer {res21.sizes}, 20-mer {res20.sizes}",
    )


def test_amplicon_ends_are_the_primers() -> bool:
    """
    The product's own ends must be the two primers. The anti-fabrication check.

    5' end equals the forward primer verbatim; 3' end equals the reverse
    complement of the reverse primer. Checked on the extracted string, not on
    the coordinates it came from, so a coordinate error cannot hide here.
    """
    ok = True
    for fwd, rev, template, size in (
        (M13_FWD_21, M13_REV, PUC19, 123),
        (M13_FWD_40, M13_REV, PUC19, 142),
        (T7, T3_20, PBS, 165),
    ):
        p = verify.simulate_pcr(fwd, rev, template, circular=True).product
        if p is None:
            ok = False
            continue
        ok &= p.sequence[:len(fwd)] == fwd
        ok &= p.sequence[len(p.sequence) - len(rev):] == reverse_complement(rev)
        ok &= reverse_complement(p.sequence)[:len(rev)] == rev
        ok &= p.starts_with_forward and p.ends_with_reverse
        ok &= len(p.sequence) == size
    return _check("amplicon's own ends equal the two primers", ok)


# --- circular templates ---------------------------------------------------
def test_origin_wrapping_amplicon() -> bool:
    """
    An amplicon that wraps the origin, and the same pair giving nothing linear.

    The primers are lifted straight out of pUC19 itself: forward from
    2650..2670 near the end, reverse from 40..60 just past the start. On the
    circle they face each other across the origin and give 96 bp
    ((60 + 2686) - 2650). Flattened to a string the reverse site sits
    *upstream* of the forward site and there is no product at all. Any
    implementation that treats a plasmid as a flat string reports the second
    answer for a real plasmid, which is the bug this fixture exists to catch.
    """
    fwd = PUC19[2650:2670]
    rev = reverse_complement(PUC19[40:60])
    expected = (60 + PUC19_LENGTH) - 2650           # 96

    circ = verify.simulate_pcr(fwd, rev, PUC19, circular=True)
    flat = verify.simulate_pcr(fwd, rev, PUC19, circular=False)

    ok = circ.outcome == SINGLE_PRODUCT and circ.sizes == (expected,) == (96,)
    p = circ.product
    ok &= p is not None and p.wraps
    ok &= p is not None and p.sequence == PUC19[2650:] + PUC19[:60]
    ok &= p is not None and p.starts_with_forward and p.ends_with_reverse
    # Same pair, linear reading: both primers still bind, nothing amplifies.
    ok &= flat.outcome == NO_PRODUCT
    ok &= len(flat.forward_sites) == 1 and len(flat.reverse_sites) == 1
    return _check(
        "origin-wrapping amplicon found when circular, absent when linear",
        ok, f"circular={circ.sizes} linear={flat.sizes}",
    )


def test_binding_site_wraps_the_origin() -> bool:
    """
    A primer site that itself straddles position 0.

    Reported as start < length <= end, so `end - start` stays the primer
    length and `end_in_template` gives the position. A linear search must not
    find it at all.
    """
    primer = PUC19[-8:] + PUC19[:12]                # 20 nt across the origin
    circ = verify.find_binding_sites(primer, PUC19, circular=True)
    flat = verify.find_binding_sites(primer, PUC19, circular=False)
    ok = len(circ) == 1 and len(flat) == 0
    if ok:
        s = circ[0]
        ok &= s.strand == PLUS
        ok &= s.start == PUC19_LENGTH - 8
        ok &= s.end == PUC19_LENGTH + 12
        ok &= s.wraps and s.end_in_template == 12
        ok &= s.end - s.start == len(primer)
        ok &= s.template_footprint == primer
    return _check("a binding site may wrap the origin on a circular template",
                  ok, f"circular={len(circ)} linear={len(flat)}")


def test_whole_plasmid_amplification() -> bool:
    """
    Back-to-back primers on a circle amplify the whole plasmid, not zero bases.

    The degenerate case where the two 5' ends coincide: the span modulo the
    length is 0, which must mean one full turn rather than nothing. This is a
    real cloning operation (inverse PCR / site-directed mutagenesis), so it
    has to come out as 2686 bp.
    """
    fwd = PUC19[100:120]
    rev = reverse_complement(PUC19[80:100])
    res = verify.simulate_pcr(fwd, rev, PUC19, circular=True)
    ok = res.outcome == SINGLE_PRODUCT and res.sizes == (PUC19_LENGTH,)
    p = res.product
    ok &= p is not None and p.sequence == PUC19[100:] + PUC19[:100]
    return _check("back-to-back primers amplify the whole circle",
                  ok, f"got {res.sizes}, want ({PUC19_LENGTH},)")


# --- zero and several products --------------------------------------------
def test_absent_primer_gives_no_product() -> bool:
    """
    A primer that is simply not in the template. The CRISPR-GPT failure mode.

    NotI's site does not occur in pUC19 at all, so a design built on it must
    report zero, with zero binding sites, and must not raise.
    """
    absent = "GCGGCCGCGCGGCCGCGCGG"
    ok = len(verify.find_binding_sites(absent, PUC19, circular=True)) == 0
    res = verify.simulate_pcr(M13_FWD_21, absent, PUC19, circular=True)
    ok &= res.outcome == NO_PRODUCT and res.count == 0 and res.sizes == ()
    ok &= len(res.forward_sites) == 1 and len(res.reverse_sites) == 0
    ok &= res.product is None and not res.is_single
    return _check("a primer absent from the template gives zero products", ok)


def test_repeated_primer_gives_several_products() -> bool:
    """
    A forward primer binding twice gives two bands, and both are reported.

    Built by concatenation so the two expected sizes are readable off the
    construction: the forward site occurs at 20 and again at 20+16+40=76, the
    reverse footprint ends at 76+16+30+16 = 138.
    """
    fwd = "ACGTACGTACGTAAGG"                       # 16 nt
    rev = "TTGACCTTGGCAAGTC"                       # 16 nt
    head = "TTTTTTTTTTCCCCCCCCCC"                  # 20
    gap1 = "AAAAAAAAAACCCCCCCCCCAAAAAAAAAACCCCCCCCCC"   # 40
    gap2 = "AAAAAAAAAATTTTTTTTTTAAAAAAAAAA"         # 30
    tail = "CCCCCCCCCCTTTTTTTTTT"                  # 20
    template = head + fwd + gap1 + fwd + gap2 + reverse_complement(rev) + tail

    res = verify.simulate_pcr(fwd, rev, template, circular=False)
    ok = len(res.forward_sites) == 2 and len(res.reverse_sites) == 1
    ok &= res.outcome == MULTIPLE_PRODUCTS and res.count == 2
    ok &= res.sizes == (62, 118)                   # 138-76 and 138-20
    ok &= not res.is_single and res.product is None
    # Both products really are the sequence between their own primers.
    ok &= all(p.starts_with_forward and p.ends_with_reverse for p in res.products)
    # And the two share the one reverse site.
    ok &= len({p.reverse_site.start for p in res.products}) == 1
    return _check("a primer occurring twice yields two products",
                  ok, f"{res.count} products, sizes {res.sizes}")


def test_max_product_size_filters() -> bool:
    """An extension-time cap drops the long band and leaves the short one."""
    fwd = "ACGTACGTACGTAAGG"
    rev = "TTGACCTTGGCAAGTC"
    template = ("TTTTTTTTTTCCCCCCCCCC" + fwd
                + "AAAAAAAAAACCCCCCCCCCAAAAAAAAAACCCCCCCCCC" + fwd
                + "AAAAAAAAAATTTTTTTTTTAAAAAAAAAA"
                + reverse_complement(rev) + "CCCCCCCCCCTTTTTTTTTT")
    res = verify.simulate_pcr(fwd, rev, template, max_product_size=100)
    ok = res.outcome == SINGLE_PRODUCT and res.sizes == (62,)
    return _check("max_product_size drops oversized bands",
                  ok, f"got {res.sizes}")


def test_overlapping_primers_give_no_product() -> bool:
    """
    Primers whose footprints run past each other cannot make a product.

    The span between the two 5' ends is shorter than a primer, so there is
    nothing for a polymerase to copy. Reported as zero, not as a tiny band.
    """
    fwd = "ACGTACGTACGTAAGG"
    rev = "TTGACCTTGGCAAGTC"
    # Reverse footprint ends only 5 bases past where the forward site starts.
    template = "TTTTTTTTTT" + fwd + "CCCCCCCCCC"
    ok = len(verify.find_binding_sites(fwd, template, strands=PLUS)) == 1
    nested = "TTTTTTTTTT" + reverse_complement(rev)[:5] + fwd + "CCCCC"
    res = verify.simulate_pcr(fwd, rev, nested, circular=False)
    ok &= res.outcome == NO_PRODUCT
    return _check("primers overlapping past each other give no product", ok)


# --- IUPAC ambiguity ------------------------------------------------------
def test_iupac_in_primer() -> bool:
    """
    A degenerate primer matches every template variant it denotes.

    `R` at one position must find both the A and the G template, and must
    still refuse C and T. Built as three otherwise-identical templates so the
    only variable is the base under the ambiguity code.
    """
    degenerate = "ACGTRCGTACGTAACG"
    flank_l, flank_r = "TTTTTTTTTT", "CCCCCCCCCC"
    found = {}
    for base in "ACGT":
        concrete = degenerate.replace("R", base)
        template = flank_l + concrete + flank_r
        found[base] = len(verify.find_binding_sites(degenerate, template))
    ok = found["A"] == 1 and found["G"] == 1
    ok &= found["C"] == 0 and found["T"] == 0

    # And a real degenerate benchmark primer: it finds each of its own oligos.
    real = "RARRGGRATTTTGTCSGKGT"
    variants = thermo.expand_ambiguous(real)
    hits = 0
    for variant in variants[:8]:
        template = flank_l + variant + flank_r
        sites = verify.find_binding_sites(real, template)
        if len(sites) == 1 and sites[0].mismatches == 0:
            hits += 1
    ok &= hits == 8
    return _check("IUPAC codes in the primer match every base they denote",
                  ok, f"R -> {found}, real primer {hits}/8")


def test_iupac_in_template() -> bool:
    """
    Ambiguity codes in the TEMPLATE, which is the half people forget.

    A template `N` matches any primer base, and a template `R` accepts a
    primer A or G but not C or T. Mismatch counting has to agree.
    """
    primer = "ACGTACGTACGTAACG"
    with_n = "TTTTTTTTTT" + "ACGTNCGTACGTAACG" + "CCCCCCCCCC"
    sites = verify.find_binding_sites(primer, with_n)
    ok = len(sites) == 1 and sites[0].mismatches == 0

    with_r = "TTTTTTTTTT" + "RCGTACGTACGTAACG" + "CCCCCCCCCC"
    ok &= len(verify.find_binding_sites("ACGTACGTACGTAACG", with_r)) == 1
    ok &= len(verify.find_binding_sites("CCGTACGTACGTAACG", with_r)) == 0

    # It works end to end too: a template with an N inside the amplicon.
    fwd, rev = "ACGTACGTACGTAACG", "TTGACCTTGGCAAGTC"
    template = ("TTTTTTTTTT" + fwd + "AAAAANNNNNAAAAA"
                + reverse_complement(rev) + "CCCCCCCCCC")
    res = verify.simulate_pcr(fwd, rev, template)
    ok &= res.outcome == SINGLE_PRODUCT and res.sizes == (47,)
    return _check("IUPAC codes in the template are honoured", ok)


def test_degenerate_primer_amplifies() -> bool:
    """A degenerate pair still simulates a PCR, on both primers at once."""
    fwd = "ACGTRCGTACGTAACG"
    rev = "TTGAYCTTGGCAAGTC"
    template = ("TTTTTTTTTT" + "ACGTGCGTACGTAACG" + "AAAAACCCCCAAAAA"
                + reverse_complement("TTGACCTTGGCAAGTC") + "CCCCCCCCCC")
    res = verify.simulate_pcr(fwd, rev, template)
    ok = res.outcome == SINGLE_PRODUCT and res.sizes == (47,)
    p = res.product
    ok &= p is not None and p.forward_site.mismatches == 0
    ok &= p is not None and p.reverse_site.mismatches == 0
    # The reported footprint is the concrete template, not the degenerate code.
    ok &= p is not None and p.forward_site.template_footprint == "ACGTGCGTACGTAACG"
    return _check("a degenerate primer pair amplifies", ok,
                  f"got {res.sizes}")


# --- mismatches and 3' anchoring ------------------------------------------
def test_mismatch_tolerance_and_positions() -> bool:
    """
    Mismatches are counted, and reported as indices into the primer 5'->3'.

    On the minus strand the primer reads the other way along the template, so
    the index mapping has to be reversed. Checking both strands for the same
    internal mismatch is the only way to catch that being skipped.
    """
    primer = "ACGTACGTACGTAACG"
    mutated = primer[:5] + ("T" if primer[5] != "T" else "A") + primer[6:]

    plus_template = "TTTTTTTTTT" + mutated + "CCCCCCCCCC"
    exact = verify.find_binding_sites(primer, plus_template, max_mismatches=0)
    tolerant = verify.find_binding_sites(primer, plus_template, max_mismatches=1)
    ok = exact == [] and len(tolerant) == 1
    ok &= tolerant[0].mismatches == 1
    ok &= tolerant[0].mismatch_positions == (5,)
    ok &= tolerant[0].three_prime_match

    minus_template = "TTTTTTTTTT" + reverse_complement(mutated) + "CCCCCCCCCC"
    rev_sites = verify.find_binding_sites(primer, minus_template, max_mismatches=1)
    ok &= len(rev_sites) == 1 and rev_sites[0].strand == MINUS
    # Same primer index on either strand: position 5 from the 5' end.
    ok &= rev_sites[0].mismatch_positions == (5,)
    ok &= rev_sites[0].template_footprint == mutated
    return _check("mismatch positions are primer indices on both strands",
                  ok, f"plus {tolerant[0].mismatch_positions if tolerant else None}, "
                      f"minus {rev_sites[0].mismatch_positions if rev_sites else None}")


def test_three_prime_anchor_rejects_terminal_mismatch() -> bool:
    """
    A mismatched 3' terminal base blocks extension, and that is a parameter.

    Same template, same primer, same mismatch budget: with the default anchor
    of 1 there is no site, with the anchor disabled there is one and it is
    flagged `three_prime_match=False`. An internal mismatch is unaffected
    either way, which is what distinguishes anchoring from plain counting.
    """
    primer = "ACGTACGTACGTAACG"
    terminal = primer[:-1] + ("A" if primer[-1] != "A" else "C")
    template = "TTTTTTTTTT" + terminal + "CCCCCCCCCC"

    anchored = verify.find_binding_sites(primer, template, max_mismatches=1)
    unanchored = verify.find_binding_sites(
        primer, template, max_mismatches=1, three_prime_anchor=0)
    ok = anchored == []
    ok &= len(unanchored) == 1
    ok &= unanchored[0].mismatches == 1
    ok &= unanchored[0].mismatch_positions == (len(primer) - 1,)
    ok &= unanchored[0].three_prime_match is False

    # A wider anchor rejects a mismatch further in from the 3' end.
    near = primer[:-3] + ("A" if primer[-3] != "A" else "C") + primer[-2:]
    near_template = "TTTTTTTTTT" + near + "CCCCCCCCCC"
    ok &= len(verify.find_binding_sites(
        primer, near_template, max_mismatches=1, three_prime_anchor=1)) == 1
    ok &= verify.find_binding_sites(
        primer, near_template, max_mismatches=1, three_prime_anchor=5) == []

    # Anchoring applies on the minus strand too, at the same primer end.
    minus_template = "TTTTTTTTTT" + reverse_complement(terminal) + "CCCCCCCCCC"
    ok &= verify.find_binding_sites(primer, minus_template, max_mismatches=1) == []
    ok &= len(verify.find_binding_sites(
        primer, minus_template, max_mismatches=1, three_prime_anchor=0)) == 1

    # And it propagates into the simulation: no extension, no product.
    rev = "TTGACCTTGGCAAGTC"
    full = ("TTTTTTTTTT" + terminal + "AAAAACCCCCAAAAA"
            + reverse_complement(rev) + "CCCCCCCCCC")
    ok &= verify.simulate_pcr(primer, rev, full, max_mismatches=1).count == 0
    ok &= verify.simulate_pcr(
        primer, rev, full, max_mismatches=1, three_prime_anchor=0).count == 1
    return _check("3'-terminal mismatch blocks extension unless anchoring is off", ok)


# --- pUC19 restriction sites ----------------------------------------------
def test_puc19_mcs_sites_unique() -> bool:
    """
    The nine unique pUC19 MCS sites, each found exactly once, at its position.

    The plan lists these 1-based; this module reports 0-based half-open, so
    `start == plan_position - 1`. Asserting that mapping here is the clearest
    possible statement of the convention, and the cheapest possible guard
    against it drifting. Each site is palindromic, so it matches on both
    strands at the same place, which is why the plus-strand count is what
    "exactly once" means.
    """
    ok = True
    detail = []
    for name, site, pos_1based in MCS_UNIQUE:
        plus = verify.find_binding_sites(site, PUC19, circular=True, strands=PLUS)
        both = verify.find_binding_sites(site, PUC19, circular=True)
        if len(plus) != 1 or plus[0].start != pos_1based - 1:
            ok = False
            detail.append(f"{name}: {[s.start for s in plus]} want {pos_1based - 1}")
            continue
        # Palindrome: the same position also reads as a minus-strand site.
        if len(both) != 2 or {s.start for s in both} != {pos_1based - 1}:
            ok = False
            detail.append(f"{name}: {len(both)} sites over both strands")
        if PUC19[plus[0].start:plus[0].end] != site:
            ok = False
            detail.append(f"{name}: slice mismatch")
    return _check("pUC19 MCS: nine unique sites at their documented positions",
                  ok, "; ".join(detail))


def test_puc19_absent_sites() -> bool:
    """NcoI and NotI do not cut pUC19. The negative controls."""
    ok = True
    detail = []
    for name, site in MCS_ABSENT:
        hits = verify.find_binding_sites(site, PUC19, circular=True)
        if hits:
            ok = False
            detail.append(f"{name} found at {[s.start for s in hits]}")
    # A search that finds nothing must still be a search, so prove the same
    # call does find these sites when they are present.
    for name, site in MCS_ABSENT:
        planted = PUC19[:100] + site + PUC19[100:]
        if len(verify.find_binding_sites(site, planted, strands=PLUS)) != 1:
            ok = False
            detail.append(f"{name} not found even when planted")
    return _check("pUC19 lacks NcoI and NotI sites", ok, "; ".join(detail))


# --- envelope -------------------------------------------------------------
def test_verify_pair_envelope() -> bool:
    """
    The compact verdict a tool envelope carries. Flat and JSON-safe.

    The point of this block is that the model reports what the simulation
    found rather than what it believes, so the fields have to say both the
    count and the size, and `size_matches` has to be false when a claimed size
    is wrong.
    """
    good = verify.verify_pair(M13_FWD_21, M13_REV, PUC19,
                              circular=True, expected_size=123)
    ok = good["outcome"] == SINGLE_PRODUCT
    ok &= good["product_count"] == 1
    ok &= good["product_sizes"] == [123]
    ok &= good["product_size"] == 123
    ok &= good["single_clean_product"] is True
    ok &= good["size_matches"] is True
    ok &= good["wraps_origin"] is False
    ok &= good["template_length"] == PUC19_LENGTH
    ok &= good["forward_start"] == 377 and good["reverse_end"] == 500

    wrong = verify.verify_pair(M13_FWD_21, M13_REV, PUC19,
                               circular=True, expected_size=150)
    ok &= wrong["size_matches"] is False

    none = verify.verify_pair(M13_REV, M13_FWD_21, PUC19, circular=False)
    ok &= none["outcome"] == NO_PRODUCT
    ok &= none["product_count"] == 0 and none["product_size"] is None
    ok &= none["single_clean_product"] is False

    wrapping = verify.verify_pair(
        PUC19[2650:2670], reverse_complement(PUC19[40:60]), PUC19, circular=True)
    ok &= wrapping["wraps_origin"] is True and wrapping["product_size"] == 96

    # JSON-serialisable: no dataclasses, no tuples-as-keys, no sets.
    import json
    try:
        json.dumps(good)
        json.dumps(wrapping)
    except TypeError:
        ok = False
    return _check("verify_pair returns a flat, JSON-safe verdict", ok)


# --- input handling -------------------------------------------------------
def test_input_validation() -> bool:
    """Bad input raises SequenceError rather than returning a quiet wrong answer."""
    ok = True
    for bad in ("ACGZ", "hello", ""):
        try:
            verify.find_binding_sites(bad, PUC19)
            ok = False
        except SequenceError:
            pass
    # A primer longer than the template is an error, not an empty result.
    try:
        verify.find_binding_sites("A" * 50, "ACGTACGTAC")
        ok = False
    except SequenceError:
        pass
    # Whitespace and lower case are normalised, and the site is the same one.
    spaced = verify.find_binding_sites(" tgtaaaacg acggccagt \n", PUC19)
    ok &= len(spaced) == 1 and spaced[0].start == 377
    ok &= spaced[0].primer == M13_FWD_21
    for kwargs in ({"max_mismatches": -1}, {"three_prime_anchor": -1},
                   {"strands": "plus"}):
        try:
            verify.find_binding_sites(M13_FWD_21, PUC19, **kwargs)
            ok = False
        except ValueError:
            pass
    return _check("bad input raises rather than returning a wrong answer", ok)


def main() -> int:
    tests = [
        test_vendored_records_intact,
        test_bases_compatible_iupac,
        test_coordinate_convention,
        test_reverse_primer_is_not_in_the_template,
        test_swapped_pair_yields_nothing,
        test_outward_facing_pair,
        test_golden_amplicon_m13_21,
        test_golden_amplicon_m13_40,
        test_golden_amplicon_t7_t3,
        test_amplicon_ends_are_the_primers,
        test_origin_wrapping_amplicon,
        test_binding_site_wraps_the_origin,
        test_whole_plasmid_amplification,
        test_absent_primer_gives_no_product,
        test_repeated_primer_gives_several_products,
        test_max_product_size_filters,
        test_overlapping_primers_give_no_product,
        test_iupac_in_primer,
        test_iupac_in_template,
        test_degenerate_primer_amplifies,
        test_mismatch_tolerance_and_positions,
        test_three_prime_anchor_rejects_terminal_mismatch,
        test_puc19_mcs_sites_unique,
        test_puc19_absent_sites,
        test_verify_pair_envelope,
        test_input_validation,
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
