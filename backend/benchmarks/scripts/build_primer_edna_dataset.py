#!/usr/bin/env python3
"""Build the layer-6c primer specificity dataset from its two upstream sources.

    scripts/build_primer_edna_dataset.py --dryad DIR --paper-txt FILE [--out DIR]
                                         [--templates-fasta FILE]

NOTHING HERE IS COMMITTED, by decision: the inputs are not ours to
redistribute and the outputs are derived from them. The dataset is rebuilt
locally from files you download by hand, which is also why both input paths
are required arguments with no defaults. Neither source can be fetched
automatically: Dryad now gates every download route behind an OAuth bearer
token, and the publisher returns 403 to automated clients.

  --dryad      the unzipped Dryad package doi:10.5061/dryad.41ns1rnb7
               (CC0-1.0, public domain dedication), holding
               "Table S1-20191106.xlsx", "Table S2-20191106.xlsx" and
               "Table S3-20191106.xlsx". Download from
               https://datadryad.org/dataset/doi:10.5061/dryad.41ns1rnb7
               using the browser button, then unzip twice: the outer archive
               contains ECS19-0803_Additional_supporting_information.zip
               (119,687 bytes, sha256 b205efb7b36b642e6f77c437a203908573ea
               70fe4fa2822551f87648aeabd7bd), which holds the three xlsx files.

  --paper-txt  `pdftotext -layout` output of So KN, Fong JJ, Lam IPY, Dudgeon D,
               "Pitfalls during in silico prediction of primer specificity for
               eDNA surveillance", Ecosphere 2020;11(7):e03193, CC BY 4.0.
               Download the PDF from https://doi.org/10.1002/ecs2.3193 in a
               browser, then `pdftotext -layout paper.pdf paper.txt`. Table 1
               carries the 38 primer pairs and Table 2 the 7 TaqMan probes.

ATTRIBUTION: the labels are CC0 and carry no obligation. The primer sequences
come from the CC BY 4.0 paper and must be credited to So et al. 2020 wherever
they are reported or published.
  --templates-fasta
               optional multi-FASTA of the Table S1 accessions. Build it by
               taking the `accession` column of the edna_templates.csv this
               script writes and fetching them from NCBI, which places no
               restrictions on redistributing its records:
                 efetch.fcgi?db=nuccore&id=<comma-separated>&rettype=fasta
                 &retmode=text
               When present, every pair is
               amplified in silico against its own target species and the
               product length is written into edna_primers.csv. This is the
               only step that touches the network or `backend/retrieval`, it is
               off by default, and nothing downstream depends on it.

Outputs, under backend/benchmarks/data/primer/, which the blanket `data/` rule
in backend/benchmarks/.gitignore keeps out of the repository. That is the
intended end state, not an obstacle to work around:

  edna_primers.csv            38 pairs, sequences plus published features
  edna_specificity_cpcr.csv   679 labelled cPCR reactions
  edna_specificity_qpcr.csv   226 labelled qPCR reactions
  edna_templates.csv          105 GenBank accessions, one per source sequence

The script is deliberately assertive: every published feature column is
recomputed from the parsed sequences and checked, and the one ambiguous block
in the PDF layout is resolved by constraint rather than by hand. If an
assertion fires, the extraction is wrong and nothing is written.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from itertools import product

import openpyxl

# --- Table 1 / Table 2 parsing ----------------------------------------------

IUPAC = "ACGTRYMSKWBDHVN"

# Expected GC fraction per base: an unambiguous base contributes 0 or 1, a
# degenerate base contributes the fraction of the bases it stands for that are
# G or C. This is the convention the paper's own "Min. GC (%)" column uses, as
# CT-ND4-2 (11 G/C plus one R over 21 nt -> 11.5/21 = 54.76%) proves.
GC_WEIGHT = {
    "A": 0.0, "T": 0.0, "W": 0.0,
    "C": 1.0, "G": 1.0, "S": 1.0,
    "R": 0.5, "Y": 0.5, "M": 0.5, "K": 0.5, "N": 0.5,
    "B": 2 / 3, "V": 2 / 3,
    "D": 1 / 3, "H": 1 / 3,
}

_PAIR_ROW = re.compile(
    r"^([A-Za-z]{2,3}-(?:CYTB|COI|ND4|16S|12S)-\d)([‡§]?)\s+"
    r"(\S+)\s+(\S+)\s+"
    r"([" + IUPAC + r"]{13,30})\s+"
    r"([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s*$"
)
_T2_ROW = re.compile(
    r"^([A-Za-z]{2,3}-(?:CYTB|COI|ND4|16S|12S)-\d)\s+(\S+)\s+(\S+)\s+"
    r"([" + IUPAC + r"]{13,30})\s+([\d.]+)\s*$"
)
_LONE_SEQ = re.compile(r"^\s+([" + IUPAC + r"]{13,30})\s*$")
_BLOCK_END = re.compile(r"^\s*(Notes?:|†|‡|§|Table \d)")

SYBR_MARK = "‡"   # double dagger, Table 1 footnote: SYBR Green qPCR
TAQMAN_MARK = "§"  # section sign, Table 1 footnote: TaqMan qPCR


def gc_percent(seq: str) -> float:
    return 100.0 * sum(GC_WEIGHT[b] for b in seq) / len(seq)


def _block_spans(lines, starts):
    """Line spans of the tables whose caption starts with one of `starts`.

    A span ends at the table's footnotes, the next table, or the start of the
    next span, whichever comes first. Without the last clause the first half of
    a two-page table swallows its own continuation.
    """
    heads = [i for i, l in enumerate(lines)
             if any(l.strip().startswith(s) for s in starts)]
    spans = []
    for n, i in enumerate(heads):
        limit = heads[n + 1] if n + 1 < len(heads) else len(lines)
        j = i + 1
        while j < limit and not _BLOCK_END.match(lines[j]):
            j += 1
        spans.append((i + 1, j))
    return spans


def parse_table1(lines):
    """Rows of Table 1, each with however many sequences the layout gave it."""
    rows = []
    for a, b in _block_spans(
            lines, ["Table 1. Information on the 38 primer pairs",
                    "(Table 1. Continued.)"]):
        for line in lines[a:b]:
            m = _PAIR_ROW.match(line)
            if m:
                rows.append({
                    "pair_id": m.group(1),
                    "mark": m.group(2),
                    "target_abbrev": m.group(3),
                    "taxa_group": m.group(4),
                    "seqs": [m.group(5)],
                    "min_3prime_stability_kcal_mol": float(m.group(6)),
                    "min_gc_percent": float(m.group(7)),
                    "max_primer_length": int(m.group(8)),
                    "amplicon_size_bp": int(m.group(9)),
                    "cpcr_annealing_temp_c": float(m.group(10)),
                })
                continue
            m = _LONE_SEQ.match(line)
            if m and rows:
                rows[-1]["seqs"].append(m.group(1))
    seen, uniq = set(), []
    for r in rows:                       # the continuation header repeats none,
        if r["pair_id"] in seen:         # but be explicit rather than lucky
            continue
        seen.add(r["pair_id"])
        uniq.append(r)
    return uniq


def parse_table2(lines):
    """{pair_id: (forward, reverse, probe, qpcr_annealing_temp)} for the 7
    TaqMan assays. An independent witness to Table 1's forward/reverse split."""
    out = {}
    cur = None
    for a, b in _block_spans(
            lines, ["Table 2. Information on the seven qPCR assays"]):
        for line in lines[a:b]:
            m = _T2_ROW.match(line)
            if m:
                cur = {"seqs": [m.group(4)], "ta": float(m.group(5))}
                out[m.group(1)] = cur
                continue
            m = _LONE_SEQ.match(line)
            if m and cur is not None:
                cur["seqs"].append(m.group(1))
    return out


def _fits(seqs, row):
    """Does this candidate assignment reproduce the pair's published features?"""
    if len(seqs) != 2:
        return False
    if max(len(s) for s in seqs) != row["max_primer_length"]:
        return False
    return abs(min(gc_percent(s) for s in seqs) - row["min_gc_percent"]) < 0.06


def resolve_interleaved(rows):
    """Repair rows that the PDF's two-line-per-pair layout interleaved.

    pdftotext puts each text line where the page puts it, so a reverse primer
    printed level with the next pair's first line lands on that next pair's
    row. The sequences stay in reading order, so the repair is a question of
    where to cut a run of consecutive rows, and the published max-length and
    min-GC columns decide it. Every candidate cut is enumerated; exactly one
    must satisfy both pairs, or the data is not recoverable and we stop.
    """
    notes = []
    i = 0
    while i < len(rows):
        if len(rows[i]["seqs"]) == 2:
            i += 1
            continue
        j = i
        while j + 1 < len(rows) and sum(len(rows[k]["seqs"])
                                        for k in range(i, j + 1)) != 2 * (j - i + 1):
            j += 1
        run = rows[i:j + 1]
        pool = [s for r in run for s in r["seqs"]]
        if len(pool) != 2 * len(run):
            raise SystemExit(
                f"cannot rebalance {[r['pair_id'] for r in run]}: "
                f"{len(pool)} sequences for {len(run)} pairs")
        # every ordered cut of `pool` into len(run) non-empty consecutive parts
        n, k = len(pool), len(run)
        good = []
        for cuts in product(range(1, n), repeat=k - 1):
            if list(cuts) != sorted(cuts) or len(set(cuts)) != k - 1:
                continue
            bounds = [0, *cuts, n]
            parts = [pool[bounds[t]:bounds[t + 1]] for t in range(k)]
            if any(not p for p in parts):
                continue
            if all(_fits(p, r) for p, r in zip(parts, run)):
                good.append(parts)
        if len(good) != 1:
            raise SystemExit(
                f"{len(good)} feature-consistent splits for "
                f"{[r['pair_id'] for r in run]}; refusing to guess")
        for r, part in zip(run, good[0]):
            r["seqs"] = part
        notes.append((tuple(r["pair_id"] for r in run), tuple(good[0])))
        i = j + 1
    return notes


# --- Table S3 ----------------------------------------------------------------

# Source header (rows 3-4 of each sheet) -> our column name, in source order.
CPCR_COLUMNS = [
    "pair_id", "taxa_group", "locus", "target_species", "tested_species",
    "sample_id", "min_3prime_stability_kcal_mol", "max_primer_length",
    "min_gc_percent", "max_total_cm", "max_term_cm", "max_3prime_cm",
    "max_total_ncm", "max_term_ncm", "max_3prime_ncm",
    "template_ng_per_ul", "annealing_temp_c", "amplified",
    "primer_blast_scenario1", "primer_blast_scenario2",
]
QPCR_COLUMNS = CPCR_COLUMNS[:17] + [
    "method", "probe_mismatches", "amplified",
    "primer_blast_scenario1", "primer_blast_scenario2",
]


def read_sheet(path, sheet, columns):
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    assert ws.max_column == len(columns), (sheet, ws.max_column, len(columns))
    rows = []
    for r in range(5, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        if all(v is None for v in vals):
            continue
        row = dict(zip(columns, vals))
        assert not any(v is None for v in vals), f"{sheet} row {r} has a blank"
        rows.append(row)
    return rows


def num(v):
    """Render a cell the way the spreadsheet meant it.

    Excel stores 2.333333333 as a float that repr()s as 2.3333333329999997;
    %.10g recovers the authored value without inventing precision.
    """
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        return float(f"{v:.10g}")
    return v


# --- assembly ----------------------------------------------------------------

REACTION_COLUMNS = [
    "assay", "pair_id", "taxa_group", "locus", "target_species",
    "tested_species", "on_target", "sample_id", "method", "forward_primer",
    "reverse_primer", "probe", "min_3prime_stability_kcal_mol",
    "max_primer_length", "min_gc_percent", "max_total_cm", "max_term_cm",
    "max_3prime_cm", "max_total_ncm", "max_term_ncm", "max_3prime_ncm",
    "probe_mismatches", "template_ng_per_ul", "annealing_temp_c",
    "amplified", "primer_blast_scenario1", "primer_blast_scenario2",
]

PRIMER_COLUMNS = [
    "pair_id", "target_abbrev", "taxa_group", "locus", "forward_primer",
    "reverse_primer", "probe", "qpcr_chemistry", "forward_length",
    "reverse_length", "min_3prime_stability_kcal_mol", "min_gc_percent",
    "max_primer_length", "amplicon_size_bp", "cpcr_annealing_temp_c",
    "qpcr_annealing_temp_c", "has_iupac_ambiguity", "verified_on_accession",
    "insilico_amplicon_bp", "caveat",
]

# Published feature columns that the published sequences do not reproduce.
# Both are errors in the sources, confirmed against the GenBank templates, and
# both are carried through verbatim rather than silently repaired. The
# generated data/primer/README.md repeats this under "Known data issues"; it
# is restated here because that README is generated output and so is not in
# the repository.
#
# TSE-COI-2: Table 1 and Table S3 both give max length 22 and min GC 40.909%,
# but the printed primers are 21 nt and 20 nt and give min GC 45.0%. 40.909%
# is 9/22, and 9 is exactly the reverse primer's G+C count, so the GC figure
# was computed with the wrong denominator; 22 is the max length of the two
# neighbouring TSE rows, which is where it looks copied from. The printed
# sequences are right: they match KM216748.1 with zero mismatches and give
# exactly the published 178 bp product.
FEATURE_EXCEPTIONS = {
    "TSE-COI-2": "length_gc_inconsistent",
}
# Table S3's min GC for SB-CYTB-3 is 30%, Table 1's is 41.3%. 41.3% is what the
# sequences give when a degenerate base contributes its expected GC fraction,
# which is the convention every other pair in both tables follows; 30% is what
# they give when every ambiguity code is counted as non-GC. Only this pair is
# affected, so S3 is the outlier. Reaction rows keep S3's value because S3 is
# the labels file; edna_primers.csv keeps Table 1's.
S3_FEATURE_EXCEPTIONS = {("SB-CYTB-3", "min_gc_percent")}


def build_primers(t1_rows, t2_rows):
    out = []
    for r in t1_rows:
        fwd, rev = r["seqs"]
        chem = {SYBR_MARK: "SYBR Green", TAQMAN_MARK: "TaqMan"}.get(r["mark"], "")
        probe, qta = "", ""
        if r["pair_id"] in t2_rows:
            t2 = t2_rows[r["pair_id"]]
            assert len(t2["seqs"]) == 3, (r["pair_id"], t2["seqs"])
            # Table 2 is an independent printing of the same two primers.
            assert t2["seqs"][0] == fwd and t2["seqs"][1] == rev, \
                f"Table 1 and Table 2 disagree on {r['pair_id']}"
            probe = t2["seqs"][2]
            qta = t2["ta"]
            assert chem == "TaqMan", (r["pair_id"], chem)
        locus = r["pair_id"].split("-")[1]
        out.append({
            "pair_id": r["pair_id"],
            "target_abbrev": r["target_abbrev"],
            "taxa_group": r["taxa_group"],
            "locus": locus,
            "forward_primer": fwd,
            "reverse_primer": rev,
            "probe": probe,
            "qpcr_chemistry": chem,
            "forward_length": len(fwd),
            "reverse_length": len(rev),
            "caveat": FEATURE_EXCEPTIONS.get(r["pair_id"], ""),
            "verified_on_accession": "",
            "insilico_amplicon_bp": "",
            "min_3prime_stability_kcal_mol": num(r["min_3prime_stability_kcal_mol"]),
            "min_gc_percent": num(r["min_gc_percent"]),
            "max_primer_length": r["max_primer_length"],
            "amplicon_size_bp": r["amplicon_size_bp"],
            "cpcr_annealing_temp_c": num(r["cpcr_annealing_temp_c"]),
            "qpcr_annealing_temp_c": num(qta) if qta != "" else "",
            "has_iupac_ambiguity": int(any(
                b not in "ACGT" for b in fwd + rev + probe)),
        })
    return out


def check_primers_against_s3(primers, cpcr, qpcr):
    """Table 1's published features must equal Table S3's, pair by pair, and
    both must equal what the parsed sequences actually compute to."""
    by_id = {p["pair_id"]: p for p in primers}
    problems = []
    for p in primers:
        if p["pair_id"] in FEATURE_EXCEPTIONS:
            continue
        seqs = [p["forward_primer"], p["reverse_primer"]]
        if max(len(s) for s in seqs) != p["max_primer_length"]:
            problems.append(f"{p['pair_id']}: length "
                            f"{max(len(s) for s in seqs)} != "
                            f"{p['max_primer_length']}")
        got = min(gc_percent(s) for s in seqs)
        if abs(got - p["min_gc_percent"]) >= 0.06:
            problems.append(f"{p['pair_id']}: GC {got:.2f} != "
                            f"{p['min_gc_percent']}")
    seen = set()
    for rows, label in ((cpcr, "cPCR"), (qpcr, "qPCR")):
        for r in rows:
            p = by_id.get(r["pair_id"])
            if p is None:
                problems.append(f"{label}: {r['pair_id']} has no Table 1 row")
                continue
            for col in ("min_3prime_stability_kcal_mol", "max_primer_length",
                        "min_gc_percent"):
                if (r["pair_id"], col) in S3_FEATURE_EXCEPTIONS:
                    continue
                a, b = num(r[col]), p[col]
                if abs(float(a) - float(b)) >= 0.06 and \
                        (label, r["pair_id"], col) not in seen:
                    seen.add((label, r["pair_id"], col))
                    problems.append(
                        f"{label} {r['pair_id']}: {col} {a} (S3) != {b} (Table 1)")
    return problems


def read_fasta(path):
    seqs, acc, buf = {}, None, []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(">"):
                if acc:
                    seqs[acc] = "".join(buf)
                acc, buf = line[1:].split()[0], []
            elif line:
                buf.append(line)
    if acc:
        seqs[acc] = "".join(buf)
    return seqs


def align_to_targets(primers, templates, accessions):
    """In-silico PCR of each pair against its own target species' GenBank
    records. The definitive check on the Table 1 extraction: a pair whose two
    sequences were mis-split would not amplify its own template at all."""
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..")))
    from retrieval.primer.verify import simulate_pcr  # noqa: E402

    by_ab = {}
    for row in accessions:
        by_ab.setdefault((row["abbreviation"] or "").strip(), []).append(row)
    failures = []
    for p in primers:
        hit = None
        for row in by_ab.get(p["target_abbrev"], []):
            acc = row["genbank_accession"].strip()
            tpl = templates.get(acc) or templates.get(acc + ".1")
            if not tpl:
                continue
            res = simulate_pcr(p["forward_primer"], p["reverse_primer"], tpl,
                               circular=True, max_mismatches=0,
                               three_prime_anchor=1, max_product_size=3000)
            if len(res.products) == 1:
                hit = (acc, len(res.products[0].sequence))
                break
            if res.products:
                failures.append(f"{p['pair_id']}: {len(res.products)} products "
                                f"on {acc}")
        if hit is None:
            failures.append(f"{p['pair_id']}: no product on any {p['target_abbrev']} record")
            continue
        p["verified_on_accession"], p["insilico_amplicon_bp"] = hit
    return failures


def build_reactions(rows, primers, assay):
    by_id = {p["pair_id"]: p for p in primers}
    out = []
    for r in rows:
        p = by_id[r["pair_id"]]
        method = r.get("method", "cPCR")
        if method == "Taqman":           # the sheet's spelling; Table 1 says TaqMan
            method = "TaqMan"
        pm = r.get("probe_mismatches", "")
        if pm == "NA" or pm is None:
            pm = ""
        out.append({
            "assay": assay,
            "pair_id": r["pair_id"],
            "taxa_group": r["taxa_group"],
            "locus": r["locus"],
            "target_species": r["target_species"],
            "tested_species": r["tested_species"],
            "on_target": int(r["target_species"] == r["tested_species"]),
            "sample_id": r["sample_id"],
            "method": method,
            "forward_primer": p["forward_primer"],
            "reverse_primer": p["reverse_primer"],
            "probe": p["probe"] if method == "TaqMan" else "",
            "probe_mismatches": num(pm) if pm != "" else "",
            **{c: num(r[c]) for c in (
                "min_3prime_stability_kcal_mol", "max_primer_length",
                "min_gc_percent", "max_total_cm", "max_term_cm",
                "max_3prime_cm", "max_total_ncm", "max_term_ncm",
                "max_3prime_ncm", "template_ng_per_ul", "annealing_temp_c",
                "amplified", "primer_blast_scenario1",
                "primer_blast_scenario2")},
        })
    return out


def build_templates(path):
    ws = openpyxl.load_workbook(path, data_only=True)["GenBank Accession numbers"]
    out = []
    for r in range(3, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, 6)]
        if all(v is None for v in vals):
            continue
        out.append(dict(zip(
            ["species", "abbreviation", "taxa_group", "genbank_accession",
             "gene"], [str(v).strip() for v in vals])))
    return out


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    print(f"wrote {path} ({len(rows)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryad", required=True)
    ap.add_argument("--paper-txt", required=True)
    ap.add_argument("--templates-fasta", default=None)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "primer"))
    args = ap.parse_args()

    lines = open(args.paper_txt, encoding="utf-8").read().splitlines()
    t1 = parse_table1(lines)
    assert len(t1) == 38, f"Table 1 gave {len(t1)} pairs, expected 38"
    repairs = resolve_interleaved(t1)
    for pair_ids, parts in repairs:
        print("resolved interleaved rows:")
        for pid, part in zip(pair_ids, parts):
            print(f"  {pid}: {' / '.join(part)}")
    t2 = parse_table2(lines)
    assert len(t2) == 7, f"Table 2 gave {len(t2)} assays, expected 7"

    primers = build_primers(t1, t2)
    s3 = os.path.join(args.dryad, "Table S3-20191106.xlsx")
    cpcr = read_sheet(s3, "cPCRdata_results", CPCR_COLUMNS)
    qpcr = read_sheet(s3, "qPCRdata_results", QPCR_COLUMNS)
    assert len(cpcr) == 679 and len(qpcr) == 226, (len(cpcr), len(qpcr))

    problems = check_primers_against_s3(primers, cpcr, qpcr)
    if problems:
        for p in problems:
            print("MISMATCH:", p, file=sys.stderr)
        raise SystemExit("feature cross-check failed; nothing written")

    sybr = {p["pair_id"] for p in primers if p["qpcr_chemistry"] == "SYBR Green"}
    taq = {p["pair_id"] for p in primers if p["qpcr_chemistry"] == "TaqMan"}
    assert len(sybr) == 5 and len(taq) == 7, (sorted(sybr), sorted(taq))
    assert sybr | taq == {r["pair_id"] for r in qpcr}
    for r in qpcr:
        want = "SYBR Green" if r["pair_id"] in sybr else "TaqMan"
        assert r["method"].lower() == want.lower(), (r["pair_id"], r["method"])

    templates = build_templates(os.path.join(args.dryad,
                                             "Table S1-20191106.xlsx"))
    if args.templates_fasta:
        failures = align_to_targets(primers, read_fasta(args.templates_fasta),
                                    templates)
        if failures:
            for f in failures:
                print("ALIGNMENT:", f, file=sys.stderr)
            raise SystemExit("in-silico PCR against the target templates failed")
        print(f"in-silico PCR: {len(primers)}/{len(primers)} pairs give a "
              f"single zero-mismatch product on their own target template")

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    write_csv(os.path.join(out, "edna_primers.csv"), PRIMER_COLUMNS, primers)
    write_csv(os.path.join(out, "edna_specificity_cpcr.csv"), REACTION_COLUMNS,
              build_reactions(cpcr, primers, "cPCR"))
    write_csv(os.path.join(out, "edna_specificity_qpcr.csv"), REACTION_COLUMNS,
              build_reactions(qpcr, primers, "qPCR"))
    write_csv(os.path.join(out, "edna_templates.csv"),
              ["species", "abbreviation", "taxa_group", "genbank_accession",
               "gene"], templates)


if __name__ == "__main__":
    main()
