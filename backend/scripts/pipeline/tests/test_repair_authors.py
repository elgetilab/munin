"""
Unit tests for the repair decision logic (scripts/pipeline/repair_authors.py).

`decide()` is the whole safety story of the backfill: it is what stops a
mis-keyed record from being handed someone else's authors. Every case below
is modelled on a real record from the 2026-08-03 audit.

Pure function. No network, no Qdrant.

Run:
    python scripts/pipeline/tests/test_repair_authors.py

Exit 0 = pass, non-zero = number of failed cases.
"""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from repair_authors import decide  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _crossref(title: str, authors: list[str]) -> dict:
    return {"title": title, "authors": authors, "source": "crossref"}


# --- the happy paths -------------------------------------------------------
def test_empty_authors_get_filled() -> bool:
    """corpus 10.1126/science.3465038 stored [] for a three-author paper."""
    record = {"id": 1, "doi": "10.1126/science.3465038", "authors": [],
              "title": "Chemotactic peptide receptor coupling"}
    ext = [_crossref("Chemotactic peptide receptor coupling",
                     ["John J. Letterio", "Shaun R. Coughlin"])]
    action, authors, source = decide(record, ext)
    return _check("empty list filled from Crossref",
                  action == "write" and authors[0] == "John J. Letterio"
                  and source == "crossref", f"{action} {authors} {source}")


def test_ocr_wreckage_replaced() -> bool:
    """corpus 10.1523/jneurosci.09-04-01452.1989."""
    record = {"id": 2, "doi": "10.1523/x", "title": "Rapid axonal transport",
              "authors": ["Freda Mil", "! Ergl~a", "Walfram Tet~laff~~"]}
    ext = [_crossref("Rapid axonal transport", ["FD Miller", "W Tetzlaff"])]
    action, authors, _ = decide(record, ext)
    return _check("OCR wreckage replaced by Crossref",
                  action == "write" and authors == ["FD Miller", "W Tetzlaff"],
                  f"{action} {authors}")


def test_fuller_stored_name_wins() -> bool:
    """The agreed policy: 'Dale Leitman' beats Crossref's 'D C Leitman'.
    The record is still rewritten, because the stored copy carries a '$'."""
    record = {"id": 3, "doi": "10.1016/x", "title": "Regulation of guanylate cyclase",
              "authors": ["Dale Leitman", "Jeffrey Andresen$"]}
    ext = [_crossref("Regulation of guanylate cyclase",
                     ["D C Leitman", "J W Andresen"])]
    action, authors, source = decide(record, ext)
    return _check("fuller stored names kept, artefact stripped",
                  action == "write"
                  and authors == ["Dale Leitman", "Jeffrey Andresen"]
                  and source == "sanitized", f"{action} {authors} {source}")


def test_sanitising_alone_is_a_repair() -> bool:
    """corpus 10.1016/S0005-2728(05)80144-6: no external record available,
    but 'Herv6 Bottin' still sanitises to something better."""
    record = {"id": 4, "doi": "10.1016/y", "title": "Fast kinetics",
              "authors": ["Hervé Bottin ‡", "Pierre Sétif1"]}
    action, authors, source = decide(record, [])
    return _check("sanitising with no external source still writes",
                  action == "write" and authors == ["Hervé Bottin", "Pierre Sétif"]
                  and source == "sanitized", f"{action} {authors} {source}")


# --- the guards ------------------------------------------------------------
def test_quarantined_record_is_never_touched() -> bool:
    record = {"id": 5, "doi": "10.1016/z", "title": "LIGPLOT",
              "authors": [], "_crossref_title_rejected": "A JSTOR article"}
    action, authors, _ = decide(record, [_crossref("A JSTOR article", ["X Y"])])
    return _check("quarantined record skipped outright",
                  action == "skip_quarantine" and authors == [], f"{action}")


def test_title_mismatch_blocks_the_write() -> bool:
    """The failure this guard exists for: the DOI resolves to a different
    paper, so its authors must not be written onto this record."""
    record = {"id": 6, "doi": "10.1016/wrong",
              "title": "Pyrene excimer formation in lipid bilayers",
              "authors": []}
    ext = [_crossref("Crystal structure of bovine rhodopsin",
                     ["K Palczewski", "T Kumasaka"])]
    action, authors, _ = decide(record, ext)
    return _check("author list from a different paper is refused",
                  action == "skip_title" and authors == [], f"{action} {authors}")


def test_mismatched_source_does_not_poison_a_good_one() -> bool:
    """Each source is title-checked independently: a wrong Crossref record
    must not block a right S2 one."""
    record = {"id": 7, "doi": "10.1016/mixed",
              "title": "Sensing hydration and behavior of pyrene", "authors": []}
    ext = [_crossref("Something else entirely", ["Wrong Person"]),
           {"title": "Sensing hydration and behavior of pyrene",
            "authors": ["Luís M.S. Loura", "Jorge Martins"],
            "source": "semantic_scholar"}]
    action, authors, source = decide(record, ext)
    return _check("per-source title check",
                  action == "write" and authors[0] == "Luís M.S. Loura"
                  and source == "semantic_scholar", f"{action} {authors} {source}")


def test_missing_stored_title_allows_repair() -> bool:
    """No stored title means nothing to contradict; the DOI is the identity."""
    record = {"id": 8, "doi": "10.1016/notitle", "title": "", "authors": []}
    ext = [_crossref("Some paper", ["A Author", "B Author"])]
    action, authors, _ = decide(record, ext)
    return _check("empty stored title does not block the repair",
                  action == "write" and len(authors) == 2, f"{action} {authors}")


def test_unresolvable_record_is_left_alone() -> bool:
    record = {"id": 9, "doi": "10.1016/gone", "title": "A paper",
              "authors": ["Medical Center"]}
    action, authors, _ = decide(record, [])
    return _check("nothing usable -> no write",
                  action == "skip_unresolved" and authors == [], f"{action}")


def test_already_correct_is_not_rewritten() -> bool:
    """Idempotence: a second run must not churn records it already fixed."""
    record = {"id": 10, "doi": "10.1016/ok", "title": "A paper",
              "authors": ["Jane Roe", "John Doe"]}
    ext = [_crossref("A paper", ["Jane Roe", "John Doe"])]
    action, _, _ = decide(record, ext)
    return _check("no-op record reported as unchanged",
                  action == "skip_unchanged", f"{action}")


def main() -> int:
    tests = [
        test_empty_authors_get_filled,
        test_ocr_wreckage_replaced,
        test_fuller_stored_name_wins,
        test_sanitising_alone_is_a_repair,
        test_quarantined_record_is_never_touched,
        test_title_mismatch_blocks_the_write,
        test_mismatched_source_does_not_poison_a_good_one,
        test_missing_stored_title_allows_repair,
        test_unresolvable_record_is_left_alone,
        test_already_correct_is_not_rewritten,
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
    return failed


if __name__ == "__main__":
    sys.exit(main())
