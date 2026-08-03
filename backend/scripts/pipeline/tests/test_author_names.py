"""
Unit tests for author-name sanitising and candidate selection
(scripts/pipeline/author_names.py, Phase C of the 2026-08 citation work).

Every damaged input below was taken from a real record in the production
papers_bge corpus, and every "expected" author list from the Crossref record
for the same DOI. That matters: the point of this module is to survive real
PDF text-layer damage, not synthetic examples.

Pure functions. No network, no Qdrant.

Run:
    python scripts/pipeline/tests/test_author_names.py

Exit 0 = pass, non-zero = number of failed cases.
"""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from author_names import (  # noqa: E402
    best_author_list, is_damaged, sanitize_author, sanitize_authors,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# --- single-name repair ----------------------------------------------------
def test_strips_affiliation_superscript() -> bool:
    # corpus: 10.1016/0140-6736(93)91464-w
    got = (sanitize_author("R Tait1"), sanitize_author("Pam Taylor1"))
    return _check("trailing affiliation digits removed",
                  got == ("R Tait", "Pam Taylor"), str(got))


def test_strips_footnote_daggers() -> bool:
    got = (sanitize_author("Malcolm Whiteway{"), sanitize_author("‡ Hooper"))
    return _check("brace / dagger artefacts removed",
                  got == ("Malcolm Whiteway", "Hooper"), str(got))


def test_repairs_split_diacritics() -> bool:
    # corpus: 'Gu ¨nther Schu ¨tz ‡'
    got = sanitize_author("Gu ¨nther Schu ¨tz ‡")
    return _check("floating combining marks recomposed",
                  got == "Günther Schütz", repr(got))


def test_rejects_affiliation_as_person() -> bool:
    got = [sanitize_author(x) for x in
           ("Medical Center", "Theodor-Kocher Institute", "Resonance Center")]
    return _check("affiliations are not people", got == [None, None, None],
                  str(got))


def test_rejects_email_and_conjunction() -> bool:
    got = [sanitize_author("user096@example.org"),
           sanitize_author("Tohru Ueda,' And")]
    return _check("emails and joined names rejected", got == [None, None],
                  str(got))


def test_rejects_exploded_ocr() -> bool:
    # corpus: 'L A R L S O N', '4 H D O ', 'He Le Á Ne Dutartre'
    got = [sanitize_author("L A R L S O N"), sanitize_author("4 H D O '")]
    return _check("OCR-exploded strings rejected", got == [None, None], str(got))


def test_keeps_legitimate_names_untouched() -> bool:
    keep = ["Hélène Dutartre", "Le Guyader", "Jarmila Repáková",
            "António M.T. Martins do Canto", "J. Holopainen", "Xu Li"]
    got = [sanitize_author(n) for n in keep]
    return _check("clean names pass through unchanged", got == keep, str(got))


def test_particle_surnames_survive() -> bool:
    """'do Canto' and 'van der Waals' must not be mistaken for damage."""
    got = sanitize_authors(["António M.T. Martins do Canto",
                            "Johannes van der Waals"])
    return _check("multi-particle surnames kept", len(got) == 2, str(got))


# --- list handling ---------------------------------------------------------
def test_sanitize_list_drops_and_dedups() -> bool:
    got = sanitize_authors(["R Tait1", "Medical Center", "R Tait",
                            {"name": "Pam Taylor1"}, ""])
    return _check("list sanitised, deduped, dicts accepted",
                  got == ["R Tait", "Pam Taylor"], str(got))


def test_is_damaged_reports_reasons() -> bool:
    damaged, reasons = is_damaged(["Freda Mil", "! Ergl~a", "Walfram Tet~laff~~"])
    ok = damaged and "artefact_char" in reasons
    return _check("is_damaged explains itself", ok, f"{damaged} {reasons}")


def test_is_damaged_false_for_clean_list() -> bool:
    damaged, reasons = is_damaged(["Hélène Dutartre", "Mark Harris"])
    return _check("clean list is not damaged", not damaged, str(reasons))


# --- candidate selection ---------------------------------------------------
def test_prefers_fuller_given_names() -> bool:
    """Crossref initials must not displace real given names.
    corpus 10.1016/s0021-9258(18)68984-7: stored 'Dale Leitman',
    Crossref 'D C Leitman'."""
    got = best_author_list(["Dale Leitman", "Rosanne Catalano"],
                           ["D C Leitman", "R M Catalano"])
    return _check("fuller given names win",
                  got == ["Dale Leitman", "Rosanne Catalano"], str(got))


def test_crossref_wins_when_stored_is_damaged() -> bool:
    """corpus 10.1523/jneurosci.09-04-01452.1989: the stored list is OCR
    wreckage, so Crossref's initials are still the better record."""
    stored = ["Freda Mil", "! Ergl~a", "Walfram Tet~laff~~", "Mark Blsby"]
    crossref = ["FD Miller", "W Tetzlaff", "MA Bisby", "JW Fawcett", "RJ Milner"]
    got = best_author_list(stored, crossref)
    return _check("damaged stored list loses to Crossref", got == crossref,
                  str(got))


def test_crossref_wins_when_stored_lost_authors() -> bool:
    """corpus 10.1016/s0021-9258(18)96607-x: stored ['T Wilson$'] is one
    mangled name where the paper has two authors."""
    got = best_author_list(["T Wilson$"],
                           ["Herbert H. Winkler", "T. Hastings Wilson"])
    return _check("more complete list wins",
                  got == ["Herbert H. Winkler", "T. Hastings Wilson"], str(got))


def test_empty_candidates_yield_empty() -> bool:
    got = best_author_list([], None, ["Medical Center"])
    return _check("no usable candidate -> empty list", got == [], str(got))


def test_first_candidate_wins_ties() -> bool:
    got = best_author_list(["Jane Roe"], ["Jane Roe"])
    return _check("tie goes to the preferred source", got == ["Jane Roe"],
                  str(got))


def main() -> int:
    tests = [
        test_strips_affiliation_superscript,
        test_strips_footnote_daggers,
        test_repairs_split_diacritics,
        test_rejects_affiliation_as_person,
        test_rejects_email_and_conjunction,
        test_rejects_exploded_ocr,
        test_keeps_legitimate_names_untouched,
        test_particle_surnames_survive,
        test_sanitize_list_drops_and_dedups,
        test_is_damaged_reports_reasons,
        test_is_damaged_false_for_clean_list,
        test_prefers_fuller_given_names,
        test_crossref_wins_when_stored_is_damaged,
        test_crossref_wins_when_stored_lost_authors,
        test_empty_candidates_yield_empty,
        test_first_candidate_wins_ties,
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
