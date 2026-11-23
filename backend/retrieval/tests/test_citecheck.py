"""
Tests for citation verification (citecheck.py, CITATION-VERIFY-PLAN.md).

Hermetic: the registry lookup and the correction call are replaced by fakes.
The answers are synthetic, shaped like the failures in the user-failure
baseline (wrong journal for a real DOI, wrong first author, a DOI that does
not exist), plus the false-positive shapes found when calibrating on real
answers (acronyms, a prose range, maths in italics, a name from an earlier
sentence).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_citecheck.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import citecheck  # noqa: E402

REC = {
    "10.1000/jmb.1": {"title": "Folding of a small protein", "authors": ["Andrej Sali", "Mark Bycroft"],
                      "families": ["Sali", "Bycroft"], "year": 1991, "journal": "Journal of Molecular Biology",
                      "journal_short": "J Mol Biol", "journals": ["Journal of Molecular Biology", "J Mol Biol"],
                      "volume": "220", "page": "779-788"},
    "10.1000/pnas.2": {"title": "Late events in rhodopsin activation", "authors": ["B. Knierim"],
                       "families": ["Knierim"], "year": 2007,
                       "journal": "Proceedings of the National Academy of Sciences", "journal_short": "",
                       "journals": ["Proceedings of the National Academy of Sciences"],
                       "volume": "104", "page": "20290-20295"},
    "10.1000/ejpr.3": {"title": "Scandal and coalition terminations", "authors": ["A. Hjermitslev"],
                       "families": ["Hjermitslev"], "year": 2026,
                       "journal": "European Journal of Political Research", "journal_short": "",
                       "journals": ["European Journal of Political Research"], "volume": "", "page": ""},
}


def _fake_lookup(missing=(), unknown=()):
    async def lookup(doi, client):
        if doi in missing:
            return ("missing", None)
        if doi in unknown or doi not in REC:
            return ("unknown", None)
        return ("ok", REC[doi])
    return lookup


def _verify(text, **kw):
    citecheck.lookup = _fake_lookup(**kw)
    return asyncio.run(citecheck.verify(text))


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def test_correct_citation_passes() -> bool:
    text = ("1. Sali A, Bycroft M. Folding of a small protein. *J Mol Biol* 220:779-788 (1991). "
            "https://doi.org/10.1000/jmb.1")
    us = _verify(text)
    return _check("correct reference-list item -> no problems", us[0].problems == [], str(us[0].problems))


def test_wrong_journal() -> bool:
    text = 'The paper is Hjermitslev (2026), "Scandal" (*Journal of Politics*, doi: 10.1000/ejpr.3).'
    us = _verify(text)
    return _check("wrong journal for a real DOI is flagged",
                  any("journal" in p for p in us[0].problems), str(us[0].problems))


def test_wrong_first_author() -> bool:
    text = "Its core packing makes it robust ([Fersht et al., JMB, 1991](https://doi.org/10.1000/jmb.1))."
    us = _verify(text)
    return _check("nearest 'X et al.' not among the authors is flagged",
                  any("Fersht" in p for p in us[0].problems), str(us[0].problems))


def test_earlier_name_is_not_the_attribution() -> bool:
    text = ("As discussed by Smith et al. in their review, the dimer behaves differently. "
            "Packing matters ([Sali et al., 1991](https://doi.org/10.1000/jmb.1)).")
    us = _verify(text)
    return _check("a name from an earlier sentence is not checked", us[0].problems == [], str(us[0].problems))


def test_missing_doi() -> bool:
    us = _verify("See 10.1000/nope.9 for details.", missing={"10.1000/nope.9"})
    return _check("unregistered DOI is flagged", "not registered" in " ".join(us[0].problems))


def test_lookup_failure_flags_nothing() -> bool:
    us = _verify("See 10.1000/jmb.1 (Nobody et al., 1850).", unknown={"10.1000/jmb.1"})
    return _check("a failed lookup verifies nothing and flags nothing", us[0].problems == [])


def test_acronym_and_prefix_journals_pass() -> bool:
    a = _verify('Knierim et al. (2007), "Late events", *PNAS* 104:20290-20295, DOI 10.1000/pnas.2')
    b = _verify("Sali A, Bycroft M (1991). *J. Mol.* 220. 10.1000/jmb.1")
    return _check("PNAS and a shortened journal name are not flagged",
                  a[0].problems == [] and b[0].problems == [], f"{a[0].problems} {b[0].problems}")


def test_prose_range_and_maths_not_pages_or_journal() -> bool:
    text = ("Efficiency peaks for donor/acceptor ratios in the range 1-20, where the line shape *f(H)* "
            "is normalised (Knierim et al., 2007, 10.1000/pnas.2).")
    us = _verify(text)
    return _check("a prose range and italic maths are ignored", us[0].problems == [], str(us[0].problems))


def test_wrong_pages_and_year() -> bool:
    text = "1. Knierim B. Late events. *PNAS* 2003, 104, 20340-20345. doi:10.1000/pnas.2"
    us = _verify(text)
    ps = " ".join(us[0].problems)
    return _check("wrong pages and year are flagged", "pages" in ps and "year 2003" in ps, ps)


def test_old_elsevier_doi_with_parentheses() -> bool:
    us = citecheck.extract_units("J Mol Biol 10.1016/0022-2836(91)90117-O.")
    return _check("DOI with parentheses kept whole", us and us[0].doi == "10.1016/0022-2836(91)90117-O")


def test_warn_marks_inline() -> bool:
    citecheck.lookup = _fake_lookup(missing={"10.1000/nope.9"})
    text = "Claim one (10.1000/nope.9).\nClaim two."
    out, notes = asyncio.run(citecheck.run(text, mode_override="warn"))
    return _check("warn: failing citation marked on its line, footer added",
                  "Claim one (10.1000/nope.9). **[unverified:" in out and "Claim two." in out
                  and notes["unverified"] == 1 and "could not be verified" in out, out)


def test_enforce_applies_correction() -> bool:
    citecheck.lookup = _fake_lookup()
    text = ('Hjermitslev (2026), "Scandal" (*Journal of Politics*, doi: 10.1000/ejpr.3). '
            "More text that stays the same.")
    fixed = text.replace("Journal of Politics", "European Journal of Political Research")

    async def fake_correct(content, bad, ungrounded):
        assert "European Journal of Political Research" in citecheck.correction_prompt(content, bad, ungrounded)
        return fixed
    citecheck._correct = fake_correct
    out, notes = asyncio.run(citecheck.run(text, mode_override="enforce"))
    return _check("enforce: verified correction replaces the answer",
                  out.startswith(fixed) and notes["corrected"] == 1 and notes["unverified"] == 0
                  and "1 corrected" in out, out)


def test_enforce_rejects_truncated_revision() -> bool:
    citecheck.lookup = _fake_lookup()
    text = ('Hjermitslev (2026), "Scandal" (*Journal of Politics*, doi: 10.1000/ejpr.3). '
            "See the plot /api/artifacts/c1/abc123/view and a long tail of text that must survive.")

    async def lossy(content, bad, ungrounded):
        return "Short."
    citecheck._correct = lossy
    out, notes = asyncio.run(citecheck.run(text, mode_override="enforce"))
    return _check("enforce: a lossy revision is rejected, the original is marked",
                  "/api/artifacts/c1/abc123/view" in out and "[unverified:" in out
                  and notes["corrected"] == 0, out)


def test_off_and_clean_are_noops() -> bool:
    citecheck.lookup = _fake_lookup()
    text = "1. Sali A, Bycroft M (1991). *J Mol Biol* 220:779-788. 10.1000/jmb.1"
    a, _ = asyncio.run(citecheck.run(text, mode_override="off"))
    b, notes = asyncio.run(citecheck.run(text, mode_override="enforce"))
    c, _ = asyncio.run(citecheck.run("No citations here.", mode_override="enforce"))
    return _check("off, all-correct and citation-free answers are untouched",
                  a == text and b == text and c == "No citations here." and notes["checked"] == 1)


def test_revision_guard() -> bool:
    orig = "x" * 100 + " /api/artifacts/c/1/view"
    return _check("revision_ok rejects short or link-losing revisions",
                  citecheck.revision_ok(orig, orig) and not citecheck.revision_ok(orig, "x" * 50)
                  and not citecheck.revision_ok(orig, "x" * 130))


def main() -> int:
    tests = [
        test_correct_citation_passes,
        test_wrong_journal,
        test_wrong_first_author,
        test_earlier_name_is_not_the_attribution,
        test_missing_doi,
        test_lookup_failure_flags_nothing,
        test_acronym_and_prefix_journals_pass,
        test_prose_range_and_maths_not_pages_or_journal,
        test_wrong_pages_and_year,
        test_old_elsevier_doi_with_parentheses,
        test_warn_marks_inline,
        test_enforce_applies_correction,
        test_enforce_rejects_truncated_revision,
        test_off_and_clean_are_noops,
        test_revision_guard,
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
