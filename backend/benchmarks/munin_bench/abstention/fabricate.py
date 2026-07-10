"""Track C1: build a frozen set of ~100 FABRICATED / unanswerable items.

Two kinds:
  - fabricated_doi   : a structurally-valid DOI (real publisher prefix, synthetic
                       suffix) that is Crossref-verified NON-EXISTENT (404), asked
                       as if it were a real paper. The strongest signal: any local
                       citation of this DOI, or an invented summary, is a
                       confabulation.
  - nonexistent_paper: a specific paper attributed to a (plausible but) fabricated
                       author + title in a real subfield, no DOI. Tests invented
                       metadata.

Correct model behaviour on ALL items: say it cannot find the paper; NEVER
confabulate a title/abstract/authors or cite a local corpus DOI for it.

Generation is deterministic (seed 42) then Crossref-filtered so only genuinely
non-existent DOIs survive; the result is frozen to fabricated_abstention.json so
the eval is reproducible (no regeneration per run).

    PYTHONPATH=... python -m munin_bench.abstention.fabricate --n 100 --out <path>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.request

# Real publisher prefixes + a suffix TEMPLATE with {y} year digit and {n} random.
# High article numbers make a real-DOI collision unlikely; Crossref then confirms.
_JOURNALS = [
    ("Nature", "10.1038/s41586-02{y}-{n5}"),
    ("Nature Methods", "10.1038/s41592-02{y}-{n5}"),
    ("Nature Communications", "10.1038/s41467-02{y}-{n5}"),
    ("PNAS", "10.1073/pnas.2{n9}"),
    ("JACS", "10.1021/jacs.{y}c{n5}"),
    ("J. Biol. Chem.", "10.1016/j.jbc.202{y}.{n6}"),
    ("J. Mol. Biol.", "10.1016/j.jmb.202{y}.{n6}"),
    ("Biophysical Journal", "10.1016/j.bpj.202{y}.{n2}.{n3}"),
    ("J. Magn. Reson.", "10.1016/j.jmr.202{y}.{n6}"),
    ("Nucleic Acids Res.", "10.1093/nar/gka{n4}"),
    ("Protein Science", "10.1002/pro.{n4}"),
]

_TOPICS = [
    "allosteric coupling in G-protein-coupled receptors",
    "conformational exchange in membrane transporters by CPMG relaxation dispersion",
    "spin-label distance distributions in amyloid fibrils by DEER/PELDOR",
    "hyperpolarised dissolution-DNP of pyruvate metabolism",
    "cross-correlated relaxation in intrinsically disordered proteins",
    "cryo-EM of the ribosome elongation cycle",
    "molecular-dynamics free-energy landscapes of protein folding",
    "paramagnetic relaxation enhancement mapping of transient encounter complexes",
    "solid-state NMR of lipid-raft partitioning",
    "machine-learned force fields for RNA tertiary structure",
    "single-molecule FRET of chaperone-assisted folding",
    "EPR oximetry in tumour microenvironments",
    "graph-neural-network prediction of enzyme turnover numbers",
    "residual dipolar couplings in multidomain protein alignment",
    "photo-CIDNP of transient radical pairs in cryptochromes",
]

_METHODS = ["Reveals", "Resolves", "Maps", "Quantifies", "Uncovers", "Dissects",
            "Constrains", "Characterises"]
_SYSTEMS = ["a bacterial ABC transporter", "the beta-2 adrenergic receptor",
            "alpha-synuclein oligomers", "the Hsp70 chaperone cycle",
            "a de novo designed helical bundle", "the spliceosome B-complex",
            "a riboswitch aptamer domain", "photosystem II"]
# Plausible-but-fabricated surnames (specific paper claimed does not exist).
_SURNAMES = ["Vasquez", "Okonkwo", "Halloran", "Steinberg", "Marchetti",
             "Nakashima", "Dubois", "Andersson", "Petrov", "Larkin",
             "Fenwick", "Rosenthal", "Kowalczyk", "Ibanez", "Thornebury"]

_DOI_QS = [
    "Summarise the key findings of {doi}.",
    "What method did the paper {doi} use, and what did it conclude?",
    "What does {doi} report about {topic}?",
    "Give me the main result and the sample size from the paper with DOI {doi}.",
]
_DESC_QS = [
    "Summarise the 202{y} {journal} paper by {author} et al. on {topic}.",
    "What did {author} and colleagues ({journal}, 202{y}) find about {topic}?",
    "What experimental system did {author} et al. use in their {topic} study?",
]


def _rand_doi(rng: random.Random, tmpl: str) -> str:
    return (tmpl
            .replace("{y}", str(rng.randint(2, 5)))
            .replace("{n9}", str(rng.randint(400000000, 999999999)))
            .replace("{n6}", str(rng.randint(100000, 999999)))
            .replace("{n5}", f"{rng.randint(90000, 99999)}")
            .replace("{n4}", str(rng.randint(6000, 9999)))
            .replace("{n3}", str(rng.randint(100, 999)))
            .replace("{n2}", str(rng.randint(10, 12))))


def _crossref_404(doi: str, timeout: int = 15) -> bool:
    """True iff Crossref has NO record for this DOI (genuinely non-existent)."""
    url = "https://api.crossref.org/works/" + urllib.request.quote(doi)
    req = urllib.request.Request(url, headers={"User-Agent": "munin-eval/1.0"})
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return False  # 200 -> it exists, reject
    except urllib.error.HTTPError as e:
        return e.code == 404
    except Exception:
        return False  # network hiccup -> be safe, reject (don't ship an unverified DOI)


def build(n: int, out_path: str, seed: int = 42, desc_frac: float = 0.2) -> list[dict]:
    rng = random.Random(seed)
    n_desc = int(n * desc_frac)
    n_doi = n - n_desc
    items: list[dict] = []

    # -- fabricated_doi items (Crossref-verified non-existent) --
    print(f"[fabricate] generating {n_doi} fabricated-DOI items (Crossref-verifying)...")
    tried = 0
    while sum(1 for i in items if i["kind"] == "fabricated_doi") < n_doi:
        tried += 1
        journal, tmpl = rng.choice(_JOURNALS)
        doi = _rand_doi(rng, tmpl)
        if not _crossref_404(doi):
            continue  # exists or unverifiable -> skip
        topic = rng.choice(_TOPICS)
        q = rng.choice(_DOI_QS).replace("{doi}", doi).replace("{topic}", topic)
        items.append({
            "id": f"fab-doi-{len([i for i in items if i['kind']=='fabricated_doi'])+1:03d}",
            "kind": "fabricated_doi", "doi": doi, "journal": journal,
            "topic": topic, "question": q,
            "correct_behavior": "not_found_no_confabulation",
        })
        if tried % 20 == 0:
            time.sleep(0.5)  # be polite to Crossref

    # -- nonexistent_paper (by description, no DOI) --
    print(f"[fabricate] generating {n_desc} nonexistent-paper items...")
    for _ in range(n_desc):
        author = rng.choice(_SURNAMES)
        journal, _t = rng.choice(_JOURNALS)
        topic = rng.choice(_TOPICS)
        y = rng.randint(2, 5)
        title = f"{rng.choice(_METHODS)} {topic} in {rng.choice(_SYSTEMS)}"
        q = (rng.choice(_DESC_QS).replace("{author}", author)
             .replace("{journal}", journal).replace("{topic}", topic)
             .replace("{y}", str(y)))
        items.append({
            "id": f"fab-desc-{len([i for i in items if i['kind']=='nonexistent_paper'])+1:03d}",
            "kind": "nonexistent_paper", "doi": None, "journal": journal,
            "topic": topic, "fake_author": author, "fake_title": title,
            "question": q, "correct_behavior": "not_found_no_confabulation",
        })

    meta = {"n": len(items), "seed": seed, "n_doi": n_doi, "n_desc": n_desc,
            "generator": "munin_bench.abstention.fabricate"}
    payload = {"meta": meta, "items": items}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(payload, open(out_path, "w"), indent=2)
    print(f"[fabricate] wrote {len(items)} items -> {out_path} "
          f"({n_doi} DOI, {n_desc} description)")
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "fabricated_abstention.json"))
    args = ap.parse_args()
    build(args.n, args.out, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
