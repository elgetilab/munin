"""audit_doi_filenames.classify with a fake Crossref (offline, pipeline venv).

Run:
    /opt/munin/services/pipeline/venv/bin/python3 backend/scripts/pipeline/tests/test_audit_doi_filenames.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import audit_doi_filenames as A  # noqa: E402

FAKE = {"10.1093/molehr/3.5.431": {"title": ["Human ovarian follicle growth in vitro"]},
        "10.1007/s00123_020_1234": {"title": ["Springer paper with underscores"]},
        "10.1000/elsewhere_x": {"title": ["Something else entirely"]}}
A.crossref = lambda doi: FAKE.get(doi.lower())

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")


def row(doi, title):
    return A.classify({"doi": doi, "title": title, "_point_id": 1}, {"10.1093/molehr/3.5.431"})


r = row("10.1093/molehr_3.5.431", "Human ovarian follicle growth in vitro")
check("mangled DOI is mis-keyed with the real one proposed",
      r["status"] == "mis-keyed" and r["proposed_doi"] == "10.1093/molehr/3.5.431" and r["proposed_in_corpus"], str(r))
check("real underscores are ok", row("10.1007/s00123_020_1234", "Springer paper with underscores")["status"] == "ok")
check("resolves to another title -> mismatch", row("10.1000/elsewhere_x", "Raven cognition")["status"] == "mismatch")
FAKE["10.1000/real/x"] = {"title": ["Spreading phenomena with lifetimes"]}
r = row("10.1000/real_x", "Externally excited oscillating laser bullet")
check("candidate resolves to another title -> candidate-title-mismatch",
      r["status"] == "candidate-title-mismatch" and r["proposed_doi"] == "10.1000/real/x", str(r))
check("nothing resolves -> unresolved", row("10.1000/nothing_here", "x y z")["status"] == "unresolved")
check("only DOIs with _ after the prefix are suspects",
      [p["doi"] for p in A.suspects([{"doi": "10.1/a_b"}, {"doi": "10.1/ab"}, {"doi": "x_y"}, {}])] == ["10.1/a_b"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
