"""
Tests for bibref.py and the web-tier metadata plumbing (chat 61443530).

The bug: a web hit carries a title and a URL and no author list, so the model
supplied author names from memory ("Bazzi et al." for PMC2098716,
"Gonzalez-Rodriguez et al." for PMID 23274277, "Mun et al." for
10.1021/jp061300r). These tests cover the pieces that make the real authors
available at the point the hit is produced.

No network: the eutils parser is exercised against a captured esummary
payload, and identifier extraction / meta parsing are pure.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_bibref.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bibref  # noqa: E402
from mcp.tools import web  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# --- identifier extraction -------------------------------------------------
def test_extract_pubmed_url() -> bool:
    got = bibref.extract_identifier("https://pubmed.ncbi.nlm.nih.gov/23274277/")
    return _check("PubMed URL -> pmid", got == ("pmid", "23274277"), str(got))


def test_extract_pmc_url() -> bool:
    got = bibref.extract_identifier(
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC2098716/")
    return _check("PMC URL -> pmcid", got == ("pmcid", "PMC2098716"), str(got))


def test_extract_doi_from_publisher_url() -> bool:
    got = bibref.extract_identifier(
        "https://pubs.acs.org/doi/abs/10.1021/jp061300r")
    return _check("publisher URL -> doi", got == ("doi", "10.1021/jp061300r"),
                  str(got))


def test_extract_doi_strips_prose_punctuation() -> bool:
    got = bibref.extract_identifier("see doi 10.1016/j.bbamem.2012.12.014.")
    return _check("trailing period not part of the DOI",
                  got == ("doi", "10.1016/j.bbamem.2012.12.014"), str(got))


def test_extract_arxiv() -> bool:
    got = bibref.extract_identifier("https://arxiv.org/abs/2101.00001")
    return _check("arXiv URL -> arxiv", got == ("arxiv", "2101.00001"), str(got))


def test_plain_page_has_no_identifier() -> bool:
    got = bibref.extract_identifier("https://example.org/blog/membranes")
    return _check("ordinary web page yields no identifier", got is None, str(got))


# --- esummary parsing ------------------------------------------------------
# Trimmed from the live response for PMID 23274277, the paper the model
# misattributed to "Gonzalez-Rodriguez et al.".
_ESUMMARY_23274277 = {
    "uid": "23274277",
    "pubdate": "2013 Mar",
    "epubdate": "2012 Dec 26",
    "source": "Biochim Biophys Acta",
    "authors": [
        {"name": "Loura LM", "authtype": "Author"},
        {"name": "do Canto AM", "authtype": "Author"},
        {"name": "Martins J", "authtype": "Author"},
        {"name": "Some Collaboration", "authtype": "CollectiveName"},
    ],
    "title": ("Sensing hydration and behavior of pyrene in POPC and "
              "POPC/cholesterol bilayers: a molecular dynamics study."),
    "fulljournalname": "Biochimica et biophysica acta",
    "articleids": [
        {"idtype": "pubmed", "value": "23274277"},
        {"idtype": "doi", "value": "10.1016/j.bbamem.2012.12.014"},
    ],
}


def test_esummary_parse_authors_and_ids() -> bool:
    got = bibref._parse_esummary(_ESUMMARY_23274277)
    ok = (got is not None
          and got["authors"] == ["Loura LM", "do Canto AM", "Martins J"]
          and got["doi"] == "10.1016/j.bbamem.2012.12.014"
          and got["pmid"] == "23274277"
          and got["year"] == 2013
          and got["title"].endswith("molecular dynamics study"))
    return _check("esummary -> authors, ids, year (collective name dropped)",
                  ok, str(got))


def test_esummary_rejects_error_record() -> bool:
    got = bibref._parse_esummary({"uid": "999", "error": "cannot get document"})
    return _check("esummary error record -> None", got is None, str(got))


# --- egress gating ---------------------------------------------------------
def test_resolve_respects_egress_off() -> bool:
    """An egress=off run (benchmarks, certification) must not reach NCBI."""
    import provenance
    bibref.cache_clear()
    with provenance.controls(egress="off"):
        got = asyncio.run(bibref.resolve("pmid", "23274277"))
    return _check("egress=off blocks the PubMed leg", got is None, str(got))


# --- web-tier plumbing -----------------------------------------------------
_PUBMED_HTML = """
<html><head>
<meta name="citation_title" content="Changes of the membrane lipid organization">
<meta name="citation_author" content="Le Guyader, L.">
<meta name="citation_author" content="Le Roux, C.">
<meta content="Mazeres, S." name="citation_author">
<meta name="citation_journal_title" content="Biophysical Journal">
<meta name="citation_date" content="2007/12/15">
<meta name="citation_doi" content="10.1529/biophysj.107.112821">
<meta name="citation_pmid" content="17766338">
</head><body>...</body></html>
"""


def test_meta_extraction() -> bool:
    got = web.extract_meta_bibliographic(_PUBMED_HTML)
    ok = (got.get("authors") == ["Le Guyader, L.", "Le Roux, C.", "Mazeres, S."]
          and got.get("doi") == "10.1529/biophysj.107.112821"
          and got.get("pmid") == "17766338"
          and got.get("year") == 2007
          and got.get("journal") == "Biophysical Journal")
    return _check("meta tags -> authors/doi/pmid/year (both attribute orders)",
                  ok, str(got))


def test_meta_extraction_empty_on_plain_page() -> bool:
    got = web.extract_meta_bibliographic(
        "<html><head><title>Blog</title></head><body>hi</body></html>")
    return _check("page without citation meta -> {}", got == {}, str(got))


def test_bot_check_detection() -> bool:
    """The PMC interstitial that was summarised as though it were an article."""
    interstitial = ("Checking your browser before accessing "
                    "pmc.ncbi.nlm.nih.gov. This process is automatic.")
    return _check("short interstitial detected",
                  web.looks_like_bot_check(interstitial) is True)


def test_bot_check_ignores_long_article_mentioning_captcha() -> bool:
    article = ("We evaluate CAPTCHA solving as a proxy task. " * 40)
    return _check("long article mentioning captcha is not flagged",
                  web.looks_like_bot_check(article) is False,
                  f"len={len(article)}")


def test_canonical_reference_url_gate() -> bool:
    ok = (web._is_canonical_reference_url("https://pubmed.ncbi.nlm.nih.gov/17766338/")
          and web._is_canonical_reference_url("https://doi.org/10.1021/jp061300r")
          and not web._is_canonical_reference_url("https://pubs.acs.org/doi/abs/10.1021/jp061300r")
          and not web._is_canonical_reference_url("https://pubmed.ncbi.nlm.nih.gov/"))
    return _check("canonical-resolver allowance is narrow", ok)


def test_norm_web_always_declares_metadata_state() -> bool:
    """A missing `authors` key reads as 'not applicable'; the model must see
    an explicit unknown instead."""
    from mcp.tools.search_agent import _norm_web
    rows = [
        {"title": "Enriched", "url": "https://pubmed.ncbi.nlm.nih.gov/23274277/",
         "snippet": "x", "matched_by": 2,
         "authors": ["Loura LM", "do Canto AM"], "year": 2013,
         "doi": "10.1016/j.bbamem.2012.12.014"},
        {"title": "Bare", "url": "https://example.org/post", "snippet": "y",
         "matched_by": 1},
    ]
    out = _norm_web(rows)
    ok = (out[0]["authors"] == ["Loura LM", "do Canto AM"]
          and out[0]["metadata_available"] is True
          and out[0]["doi"] == "10.1016/j.bbamem.2012.12.014"
          and "authors" in out[1] and out[1]["authors"] is None
          and out[1]["metadata_available"] is False)
    return _check("_norm_web carries metadata and declares its absence",
                  ok, str(out))


def main() -> int:
    tests = [
        test_extract_pubmed_url,
        test_extract_pmc_url,
        test_extract_doi_from_publisher_url,
        test_extract_doi_strips_prose_punctuation,
        test_extract_arxiv,
        test_plain_page_has_no_identifier,
        test_esummary_parse_authors_and_ids,
        test_esummary_rejects_error_record,
        test_resolve_respects_egress_off,
        test_meta_extraction,
        test_meta_extraction_empty_on_plain_page,
        test_bot_check_detection,
        test_bot_check_ignores_long_article_mentioning_captcha,
        test_canonical_reference_url_gate,
        test_norm_web_always_declares_metadata_state,
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
