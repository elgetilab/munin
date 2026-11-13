"""The layer-6c primer specificity dataset: 905 bench-labelled PCR reactions
from So et al. 2020 (Ecosphere 11(7):e03193) joined to their primer sequences.

These are gold-value tests over a LOCALLY BUILT dataset. They run offline,
touch no network and import nothing from `backend/retrieval`: the point is
that every headline number in `data/primer/README.md` is re-derivable from the
files the build script produces. If one of these fails, the data changed, and
the README is now wrong.

The data is NOT committed, because it is not ours to redistribute: the labels
come from a CC0 Dryad deposit and the primer sequences from a CC BY 4.0 paper,
and neither can be re-fetched automatically (Dryad gates downloads behind
OAuth, and the publisher refuses automated clients). So the inputs are
downloaded by hand and the dataset is rebuilt locally:

    python backend/benchmarks/scripts/build_primer_edna_dataset.py \
        --dryad <dir with Table S1/S2/S3 xlsx> \
        --paper-txt <pdftotext -layout output of the paper>

Until that has been run, every test here SKIPS rather than fails, so a clone
without the data has a green suite. Point `MUNIN_PRIMER_EDNA_DIR` at the
output directory if you built it somewhere other than `data/primer/`.

Three things get pinned beyond the counts, because all three were places where
the extraction could have gone silently wrong:

* the Primer-BLAST confusion matrices, which are the baseline the design tool
  has to beat and which the plan quotes;
* the GA-CYTB-3 / MA-CYTB-1 split, re-derived here from the published feature
  columns rather than asserted, because the PDF layout interleaved those two
  rows and the sequences could have been assigned the wrong way round;
* the IUPAC ambiguity codes, which must survive verbatim. Seven pairs carry
  them, and any pass that "cleaned" a sequence would break this file.
"""

from __future__ import annotations

import csv
import os

import pytest

DATA = os.environ.get("MUNIN_PRIMER_EDNA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "primer")

#: Produced by scripts/build_primer_edna_dataset.py; see the module docstring.
_REQUIRED = ("edna_specificity_cpcr.csv", "edna_specificity_qpcr.csv",
             "edna_primers.csv")

_MISSING = [n for n in _REQUIRED if not os.path.exists(os.path.join(DATA, n))]

# Skip the whole module rather than fail when the dataset has not been built.
# The inputs are hand-downloaded and not redistributable, so an unbuilt
# dataset is the normal state of a fresh clone, not a broken one.
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        "eDNA specificity dataset not built (missing: "
        + ", ".join(_MISSING)
        + f" under {DATA}). Build it with "
        "scripts/build_primer_edna_dataset.py --dryad <dir> --paper-txt <file>, "
        "or set MUNIN_PRIMER_EDNA_DIR."
    ),
)

# Expected GC fraction per base, the convention the paper's own "Min. GC (%)"
# column uses: a degenerate base contributes the fraction of the bases it
# stands for that are G or C. Deliberately re-stated here rather than imported
# from the build script, so the test checks the data instead of agreeing with
# the code that wrote it.
GC_WEIGHT = {"A": 0.0, "T": 0.0, "W": 0.0, "C": 1.0, "G": 1.0, "S": 1.0,
             "R": 0.5, "Y": 0.5, "M": 0.5, "K": 0.5, "N": 0.5,
             "B": 2 / 3, "V": 2 / 3, "D": 1 / 3, "H": 1 / 3}
IUPAC = set(GC_WEIGHT)


def _load(name):
    with open(os.path.join(DATA, name), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def cpcr():
    return _load("edna_specificity_cpcr.csv")


@pytest.fixture(scope="module")
def qpcr():
    return _load("edna_specificity_qpcr.csv")


@pytest.fixture(scope="module")
def primers():
    return _load("edna_primers.csv")


def gc_percent(seq):
    return 100.0 * sum(GC_WEIGHT[b] for b in seq) / len(seq)


def confusion(rows, scenario):
    """(accuracy, TP, TN, FP, FN) of one Primer-BLAST stringency setting."""
    tp = tn = fp = fn = 0
    for r in rows:
        y, p = r["amplified"], r[f"primer_blast_scenario{scenario}"]
        tp += y == "1" and p == "1"
        tn += y == "0" and p == "0"
        fp += y == "0" and p == "1"
        fn += y == "1" and p == "0"
    return (tp + tn) / len(rows), tp, tn, fp, fn


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------
def test_row_counts(cpcr, qpcr, primers):
    assert len(cpcr) == 679
    assert len(qpcr) == 226
    assert len(primers) == 38


def test_the_label_and_prediction_columns_are_never_blank(cpcr, qpcr):
    """Appendix S3 ships complete; a blank here means a parse dropped a cell."""
    for rows in (cpcr, qpcr):
        for r in rows:
            for col in ("amplified", "primer_blast_scenario1",
                        "primer_blast_scenario2"):
                assert r[col] in ("0", "1"), (r["pair_id"], col, r[col])


def test_distinct_pairs_per_assay(cpcr, qpcr):
    assert len({r["pair_id"] for r in cpcr}) == 38
    assert len({r["pair_id"] for r in qpcr}) == 12


def test_the_twelve_qpcr_pairs_split_five_sybr_seven_taqman(qpcr, primers):
    """Table 1 marks the qPCR subsets with two footnote symbols; Appendix S3
    names the chemistry per reaction. The two must agree, pair for pair."""
    chem = {p["pair_id"]: p["qpcr_chemistry"] for p in primers}
    sybr = {p for p, c in chem.items() if c == "SYBR Green"}
    taq = {p for p, c in chem.items() if c == "TaqMan"}
    assert len(sybr) == 5 and len(taq) == 7
    assert sybr | taq == {r["pair_id"] for r in qpcr}
    for r in qpcr:
        assert r["method"] == chem[r["pair_id"]]
    assert sum(1 for r in qpcr if r["method"] == "SYBR Green") == 70
    assert sum(1 for r in qpcr if r["method"] == "TaqMan") == 156


def test_every_reaction_joins_to_a_primer_pair(cpcr, qpcr, primers):
    """The join is the whole reason this dataset exists: Appendix S3 carries
    identifiers and features but no ACGT, and Table 1 carries the sequences."""
    known = {p["pair_id"] for p in primers}
    for rows in (cpcr, qpcr):
        for r in rows:
            assert r["pair_id"] in known, f"{r['pair_id']} has no primer row"
            assert r["forward_primer"] and r["reverse_primer"]


def test_probes_accompany_taqman_rows_only(qpcr):
    for r in qpcr:
        if r["method"] == "TaqMan":
            assert r["probe"], r["pair_id"]
            assert r["probe_mismatches"] != ""
        else:
            assert r["probe"] == ""
            assert r["probe_mismatches"] == ""   # "NA" in the source


def test_denormalised_sequences_match_the_primer_table(cpcr, qpcr, primers):
    by_id = {p["pair_id"]: p for p in primers}
    for rows in (cpcr, qpcr):
        for r in rows:
            p = by_id[r["pair_id"]]
            assert r["forward_primer"] == p["forward_primer"]
            assert r["reverse_primer"] == p["reverse_primer"]


# --------------------------------------------------------------------------
# labels and the Primer-BLAST baseline
# --------------------------------------------------------------------------
def test_outcome_distributions(cpcr, qpcr):
    assert sum(1 for r in cpcr if r["amplified"] == "0") == 404
    assert sum(1 for r in cpcr if r["amplified"] == "1") == 275
    assert sum(1 for r in qpcr if r["amplified"] == "0") == 160
    assert sum(1 for r in qpcr if r["amplified"] == "1") == 66


def test_primer_blast_baseline_cpcr(cpcr):
    acc, tp, tn, fp, fn = confusion(cpcr, 1)
    assert (tp, tn, fp, fn) == (146, 344, 60, 129)
    assert acc == pytest.approx(0.722, abs=0.0005)
    acc, tp, tn, fp, fn = confusion(cpcr, 2)
    assert (tp, tn, fp, fn) == (171, 285, 119, 104)
    assert acc == pytest.approx(0.672, abs=0.0005)


def test_primer_blast_baseline_qpcr(qpcr):
    acc, tp, tn, fp, fn = confusion(qpcr, 1)
    assert (tp, tn, fp, fn) == (42, 127, 33, 24)
    assert acc == pytest.approx(0.748, abs=0.0005)
    acc, tp, tn, fp, fn = confusion(qpcr, 2)
    assert (tp, tn, fp, fn) == (48, 111, 49, 18)
    assert acc == pytest.approx(0.704, abs=0.0005)


def test_the_second_stringency_trades_error_types_rather_than_improving(cpcr):
    """Scenario 2 recovers 25 of scenario 1's false negatives and pays 59 new
    false positives for them. This is the paper's argument, and it is the
    reason the dataset is worth having: there is no stringency that works."""
    _, _, _, fp1, fn1 = confusion(cpcr, 1)
    _, _, _, fp2, fn2 = confusion(cpcr, 2)
    assert fn2 < fn1 and fp2 > fp1


def test_all_the_signal_lives_in_the_off_target_rows(cpcr, qpcr):
    """Every on-target reaction amplified, in both assays, so the on-target
    rows are constant and carry no information. A model scored over the whole
    file gets those for free; the discriminative task is cross-amplification."""
    on = [r for r in cpcr if r["on_target"] == "1"]
    assert len(on) == 93 and all(r["amplified"] == "1" for r in on)
    off = [r for r in cpcr if r["on_target"] == "0"]
    assert len(off) == 586
    assert sum(1 for r in off if r["amplified"] == "1") == 182

    on = [r for r in qpcr if r["on_target"] == "1"]
    assert len(on) == 24 and all(r["amplified"] == "1" for r in on)
    off = [r for r in qpcr if r["on_target"] == "0"]
    assert len(off) == 202
    assert sum(1 for r in off if r["amplified"] == "1") == 42


def test_on_target_means_tested_against_the_target_species(cpcr, qpcr):
    for rows in (cpcr, qpcr):
        for r in rows:
            assert r["on_target"] == str(
                int(r["target_species"] == r["tested_species"]))


# --------------------------------------------------------------------------
# the sequences
# --------------------------------------------------------------------------
def test_sequences_are_iupac_and_nothing_else(primers):
    for p in primers:
        for col in ("forward_primer", "reverse_primer", "probe"):
            assert set(p[col]) <= IUPAC, (p["pair_id"], col, p[col])


def test_seven_pairs_carry_ambiguity_codes_and_keep_them(primers):
    """Degenerate primers are normal in eDNA work. Expanding or "cleaning"
    these would silently change seven of the 38 assays, so the exact strings
    are pinned. They are also what forced IUPAC support into thermo.py and
    verify.py."""
    degenerate = sorted(p["pair_id"] for p in primers
                        if p["has_iupac_ambiguity"] == "1")
    assert degenerate == ["CT-ND4-2", "CT-ND4-3", "PeS-16S-1", "PeS-COI-2",
                          "SB-CYTB-3", "TSE-12S-1", "TSE-ND4-3"]
    by_id = {p["pair_id"]: p for p in primers}
    assert by_id["SB-CYTB-3"]["reverse_primer"] == "RARRGGRATTTTGTCSGKGT"
    assert by_id["SB-CYTB-3"]["forward_primer"] == "YTTTACCTTCCACTTTCTACTGC"
    assert by_id["TSE-12S-1"]["forward_primer"] == "TCGCCAGCYTACCCYGT"
    for p in primers:
        flag = any(b not in "ACGT"
                   for b in p["forward_primer"] + p["reverse_primer"]
                   + p["probe"])
        assert p["has_iupac_ambiguity"] == str(int(flag))


def test_sequences_reproduce_the_published_features(primers):
    """Length and GC content are published per pair and computable from the
    sequences, which makes them a checksum on the PDF extraction. 37 of 38
    agree exactly; TSE-COI-2 is a published inconsistency, flagged in the
    `caveat` column and explained in the README, not repaired here."""
    flagged = []
    for p in primers:
        seqs = [p["forward_primer"], p["reverse_primer"]]
        ok = (max(len(s) for s in seqs) == int(p["max_primer_length"])
              and abs(min(gc_percent(s) for s in seqs)
                      - float(p["min_gc_percent"])) < 0.06)
        if not ok:
            flagged.append(p["pair_id"])
            assert p["caveat"] == "length_gc_inconsistent", p["pair_id"]
    assert flagged == ["TSE-COI-2"]


def test_the_interleaved_pdf_rows_resolve_only_one_way(primers):
    """GA-CYTB-3 and MA-CYTB-1 print as two-line rows that pdftotext merged,
    leaving GA-CYTB-3 with one sequence and MA-CYTB-1 with three. The repair is
    not a judgement call: of the three ways to cut the four sequences in
    reading order, exactly one satisfies both pairs' published max length and
    min GC. That is re-derived here rather than asserted, so the file cannot
    quietly drift to the wrong assignment.
    """
    by_id = {p["pair_id"]: p for p in primers}
    ga, ma = by_id["GA-CYTB-3"], by_id["MA-CYTB-1"]
    # the four sequences in the order the page prints them
    pool = [ga["forward_primer"], ga["reverse_primer"],
            ma["forward_primer"], ma["reverse_primer"]]

    def fits(seqs, pair):
        return (max(len(s) for s in seqs) == int(pair["max_primer_length"])
                and abs(min(gc_percent(s) for s in seqs)
                        - float(pair["min_gc_percent"])) < 0.06)

    viable = [cut for cut in (1, 2, 3)
              if fits(pool[:cut], ga) and fits(pool[cut:], ma)]
    assert viable == [2], viable

    assert ga["forward_primer"] == "GACTATACTACGGCTCCTACCTATT"
    assert ga["reverse_primer"] == "GGCGGTTATTATGACTAGAAGA"
    assert ma["forward_primer"] == "GCAACATTAACACGATTCTTC"
    assert ma["reverse_primer"] == "TGAAATGATACTTTATCTGCGTCT"
    # and the independent confirmation: both amplify their own template
    assert (ga["verified_on_accession"], ga["insilico_amplicon_bp"]) == \
        ("AP004422.1", "80")
    assert (ma["verified_on_accession"], ma["insilico_amplicon_bp"]) == \
        ("HM856629.1", "146")


def test_every_pair_amplifies_its_own_target_template(primers):
    """Recorded offline from the in-silico PCR described in the README. A pair
    whose two sequences had been mis-split would not amplify at all, so this
    column is the strongest single check on the extraction."""
    for p in primers:
        assert p["verified_on_accession"], p["pair_id"]
        assert int(p["insilico_amplicon_bp"]) > 0


def test_six_published_amplicon_sizes_disagree_with_the_in_silico_product(
        primers):
    """Five by 1 bp and PM-ND4-3 by 23 bp. Published quirks, carried through;
    pinned so that a future regeneration cannot grow the list unnoticed."""
    off = {p["pair_id"]: int(p["insilico_amplicon_bp"])
           - int(p["amplicon_size_bp"])
           for p in primers
           if int(p["insilico_amplicon_bp"]) != int(p["amplicon_size_bp"])}
    assert off == {"CT-COI-1": 1, "PM-ND4-3": 23, "SB-16S-1": -1,
                   "SB-COI-2": 1, "SB-CYTB-3": 1, "TSE-12S-1": -1}
