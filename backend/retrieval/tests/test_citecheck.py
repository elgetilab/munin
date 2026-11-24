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


def test_abbreviation_entity_and_wrong_letter() -> bool:
    rec = citecheck._crossref_record({"container-title": ["Crystal Growth &amp; Design"], "title": ["X"]})
    a = citecheck._phrase_matches("Proc R Soc Lond B",
                                  "Proceedings of the Royal Society of London. Series B. Biological Sciences")
    b = citecheck._phrase_matches("J Phys Chem C", "Journal of Physical Chemistry B")
    return _check("ISO abbreviation and an &amp; title match, a wrong section letter does not",
                  rec["journal"] == "Crystal Growth & Design" and a and not b, f"{rec['journal']} {a} {b}")


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


def test_footnote_caret_not_in_doi() -> bool:
    us = citecheck.extract_units("Shown by NMR [^10.1000/jmb.1^].\n\n[^10.1000/jmb.1^]: Sali A (1991).")
    return _check("footnote caret is not part of the DOI", us and all(u.doi == "10.1000/jmb.1" for u in us),
                  str([u.doi for u in us]))


def test_warn_marks_inline() -> bool:
    citecheck.lookup = _fake_lookup(missing={"10.1000/nope.9"})
    text = "Claim one (10.1000/nope.9).\nClaim two."
    out, notes = asyncio.run(citecheck.run(text, mode_override="warn"))
    return _check("warn: failing citation marked on its line, no footer",
                  "Claim one (10.1000/nope.9). **[unverified:" in out and "Claim two." in out
                  and notes["unverified"] == 1 and "DOI registry" not in out, out)


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
                  out == fixed and notes["corrected"] == 1 and notes["unverified"] == 0, out)


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


def test_review_false_positives() -> bool:
    cases = {
        "sentence adverb": "However, Sali et al. showed folding is fast (doi:10.1000/jmb.1).",
        "species in italics": "Sali et al. showed that *Escherichia coli* folds it fast (10.1000/jmb.1).",
        "prose range": "In a 1,200-1,500 patient cohort, Sali et al. found it (10.1000/jmb.1).",
        "wavenumber": "The band at 1950 cm-1 was assigned by Sali et al. (10.1000/jmb.1).",
        "neighbour after": "Sali et al. showed X (10.1000/jmb.1). Later, Jones (2001) disagreed in *Nature*, 344-350.",
        "neighbour before": "Jones (2001, *Nature* 12) proposed this mechanism. Sali et al. confirmed it [10.1000/jmb.1].",
    }
    bad = {k: _verify(t)[0].problems for k, t in cases.items()}
    bad = {k: v for k, v in bad.items() if v}
    return _check("reviewed false positives are not flagged", not bad, str(bad))


def test_url_tails_trimmed() -> bool:
    text = ("https://www.biorxiv.org/content/10.1101/2020.01.01.123456v2.full and "
            "https://link.springer.com/article/10.1007/s00249-019-01234-5#Sec2 and "
            "https://doi.org/10.1016/j.bpj.2019.01.001?via=ihub and 10.1073/pnas.123/-/DCSupplemental "
            "and 10.1002/(SICI)1097-4636(199603)30:3<283::AID-JBM2>3.0.CO;2-M")
    got = [u.doi for u in citecheck.extract_units(text)]
    want = ["10.1101/2020.01.01.123456", "10.1007/s00249-019-01234-5", "10.1016/j.bpj.2019.01.001",
            "10.1073/pnas.123", "10.1002/(SICI)1097-4636(199603)30:3<283::AID-JBM2>3.0.CO;2-M"]
    return _check("publisher URL tails and SICI DOIs", got == want, str(got))


def test_doi_of_another_paper() -> bool:
    text = ('1. Salikhov K, Stehlik D. "Transient EPR spectra of coupled three-spin systems". '
            "*Appl Magn Reson* 1996, 10, 1-20. doi:10.1000/jmb.1")
    us = _verify(text)
    ok = len(us[0].problems) == 1 and "different paper" in us[0].problems[0]
    prompt = citecheck.correction_prompt(text, us, [])
    return _check("a DOI on the wrong paper is one problem, and the fix keeps the reference",
                  ok and "Never change the reference to the DOI's paper" in citecheck.CORRECT_SYSTEM
                  and "different paper" in prompt, str(us[0].problems))


def test_code_and_table_marks() -> bool:
    citecheck.lookup = _fake_lookup(missing={"10.1000/nope.9"})
    code = "```bibtex\n@article{x, doi={10.1000/nope.9}}\n```"
    a, _ = asyncio.run(citecheck.run(code, mode_override="warn"))
    table = "| Paper | DOI |\n|---|---|\n| X | 10.1000/nope.9 |"
    b, _ = asyncio.run(citecheck.run(table, mode_override="warn"))
    last = b.splitlines()[-1]
    return _check("code: existence checked, mark after the block; a table mark stays inside the last cell",
                  a.startswith(code + "\n\n**[unverified: DOI 10.1000/nope.9 is not registered") and last.endswith("|") and last.count("|") == 3 and "[unverified" in last, b)


def main() -> int:
    tests = [
        test_correct_citation_passes,
        test_wrong_journal,
        test_wrong_first_author,
        test_earlier_name_is_not_the_attribution,
        test_missing_doi,
        test_lookup_failure_flags_nothing,
        test_acronym_and_prefix_journals_pass,
        test_abbreviation_entity_and_wrong_letter,
        test_prose_range_and_maths_not_pages_or_journal,
        test_wrong_pages_and_year,
        test_old_elsevier_doi_with_parentheses,
        test_footnote_caret_not_in_doi,
        test_warn_marks_inline,
        test_enforce_applies_correction,
        test_enforce_rejects_truncated_revision,
        test_off_and_clean_are_noops,
        test_revision_guard,
        test_review_false_positives,
        test_url_tails_trimmed,
        test_doi_of_another_paper,
        test_code_and_table_marks,
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
