"""
doi_*.pdf filenames: exact names decode, ambiguous ones only verify.

Regression for the 2026-09 review: the decoder restored only the first `_`
of `doi_<doi with / and : as _>.pdf` and its result overrode GROBID's DOI,
so `10.1093/molehr/3.5.431` was stored as `10.1093/molehr_3.5.431`, SICI
DOIs lost their `::`, and `10.21203/rs.3.rs-1484102/v1` lost its `/v1`.
Offline: Crossref is a dict.

Run:
    python backend/scripts/pipeline/tests/test_doi_filename.py
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import doi_filename as D  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")


def _title_sim(a: str, b: str) -> float:
    ta, tb = set(a.lower().split()), set(b.lower().split())
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


# Fake Crossref: only real DOIs resolve.
CROSSREF = {
    "10.1093/molehr/3.5.431": {"title": ["Human ovarian follicle growth in vitro"]},
    "10.21203/rs.3.rs-1484102/v1": {"title": ["A preprint about raven cognition"]},
    "10.1002/(sici)1098-2264(199802)21:2<131::aid-gcc9>3.0.co;2-#":
        {"title": ["Chromosome aberrations in tumours"]},
    "10.1007/s00123_020_1234": {"title": ["Springer paper with underscores"]},
}


def fetch(doi: str):
    return CROSSREF.get(doi.lower())


# exact vs ambiguous
check("single _ after prefix decodes exactly",
      D.decode("doi_10.1038_nature12373.pdf") == ("10.1038/nature12373", []))
check("non doi_ names are ignored", D.decode("arxiv_2101.00001.pdf") == (None, []))
exact, cands = D.decode("doi_10.1093_molehr_3.5.431.pdf")
check("multi _ name is ambiguous, not decoded", exact is None and len(cands) > 1, str(cands))
check("the real DOI is among the candidates", "10.1093/molehr/3.5.431" in cands, str(cands))
check("the old (wrong) decode is among them too", "10.1093/molehr_3.5.431" in cands)
check("/v1 preprint candidate present",
      "10.21203/rs.3.rs-1484102/v1" in D.decode("doi_10.21203_rs.3.rs-1484102_v1.pdf")[1])
sici = "doi_10.1002_(sici)1098-2264(199802)21_2<131__aid-gcc9>3.0.co;2-#.pdf"
check("SICI :: candidate present",
      "10.1002/(sici)1098-2264(199802)21:2<131::aid-gcc9>3.0.co;2-#" in D.decode(sici)[1])

# verify
title = "Human ovarian follicle growth in vitro"
doi, trace = D.verify(D.decode("doi_10.1093_molehr_3.5.431.pdf")[1], title, fetch, _title_sim)
check("verify picks the DOI Crossref resolves with a matching title",
      doi == "10.1093/molehr/3.5.431", str(trace))
doi, _ = D.verify(D.decode("doi_10.1093_molehr_3.5.431.pdf")[1], "Unrelated quantum dots", fetch, _title_sim)
check("a resolving DOI with a different title is not accepted", doi is None)
doi, _ = D.verify(D.decode("doi_10.1093_molehr_3.5.431.pdf")[1], "", fetch, _title_sim)
check("no title, nothing accepted", doi is None)
doi, _ = D.verify(D.decode("doi_10.1007_s00123_020_1234.pdf")[1],
                  "Springer paper with underscores", fetch, _title_sim)
check("a DOI with real underscores still verifies", doi == "10.1007/s00123_020_1234")
doi, _ = D.verify(D.decode(sici)[1], "Chromosome aberrations in tumours", fetch, _title_sim)
check("SICI DOI verifies", doi == "10.1002/(sici)1098-2264(199802)21:2<131::aid-gcc9>3.0.co;2-#")

# The pipeline's own wiring (needs the pipeline venv's deps; skipped without).
try:
    import paper_pipeline as pp
except ImportError as e:
    print(f"[SKIP] paper_pipeline wiring: {e}")
else:
    pipe = pp.PaperPipeline.__new__(pp.PaperPipeline)  # no models, no services
    pipe._fetch_crossref = fetch
    check("pipeline: exact name still decodes without Crossref",
          pipe._extract_doi_from_filename("doi_10.1038_nature12373.pdf") == "10.1038/nature12373")
    check("pipeline: ambiguous name no longer decodes blindly",
          pipe._extract_doi_from_filename("doi_10.1093_molehr_3.5.431.pdf") is None)
    check("pipeline: ambiguous name verified against Crossref + title",
          pipe._verified_filename_doi("doi_10.1093_molehr_3.5.431.pdf",
                                      "Human ovarian follicle growth in vitro") == "10.1093/molehr/3.5.431")

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
