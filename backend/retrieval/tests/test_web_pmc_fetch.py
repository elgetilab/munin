"""
Unit tests for the PMC full-text path and the blocked-publisher error in
mcp/tools/web.py.

WHY THIS EXISTS. T11 put web_fetch at a 0.678 error rate on the 2026-08-26
Track D run. Reproducing against 24 URLs taken from real web_search results
gave: 12 ok, 7 HTTP 403, 5 anti-bot interstitials. Every one of the 5
interstitials was pmc.ncbi.nlm.nih.gov, which matters out of proportion to the
count because PMC is the primary open-access source for the biomedical
literature this system is aimed at.

The fix is not a way around the block. NCBI serves the same article through
efetch and asks programmatic clients to use it: the PMCID that returned a
131-character interstitial returns ~37k characters through the API. After the
change the same measurement gave 17 ok / 6 blocked-publisher / 1 interstitial,
i.e. an ok rate of 0.50 -> 0.71.

These tests are pure-function and mocked; they make no network calls.

    docker exec munin-retrieval python /app/tests/test_web_pmc_fetch.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from mcp.tools import web  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


# --- pmcid_from_url: the host gate is a correctness property, not a nicety --

def test_pmcid_extracted_from_both_pmc_url_shapes() -> bool:
    a = web.pmcid_from_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC10034092/")
    b = web.pmcid_from_url("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/")
    return _check("both NCBI PMC URL shapes yield the PMCID",
                  a == "PMC10034092" and b == "PMC123456", f"got {a!r}, {b!r}")


def test_pmcid_is_host_gated() -> bool:
    """A PMCxxxx substring on someone else's domain must NOT send us to efetch
    with an id that host never meant. Without the host check, any site could
    steer our API calls by putting /PMC123/ in a path."""
    out = web.pmcid_from_url("https://evil.example.com/articles/PMC10034092/")
    return _check("non-NCBI host with a PMC-looking path -> None", out is None, f"got {out!r}")


def test_non_pmc_urls_return_none() -> bool:
    vals = [web.pmcid_from_url("https://arxiv.org/abs/2401.00001"),
            web.pmcid_from_url("https://www.ncbi.nlm.nih.gov/pubmed/12345"),
            web.pmcid_from_url(""), web.pmcid_from_url(None)]
    return _check("non-PMC / empty inputs -> None", all(v is None for v in vals), f"got {vals!r}")


def test_pmcid_is_case_normalised() -> bool:
    out = web.pmcid_from_url("https://pmc.ncbi.nlm.nih.gov/articles/pmc999/")
    return _check("lowercase pmc999 -> PMC999", out == "PMC999", f"got {out!r}")


# --- JATS body extraction ---------------------------------------------------

_JATS = """<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta><abstract><p>Abstract text here.</p></abstract></article-meta></front>
  <body>
    <sec><title>Introduction</title><p>Genome editing tools <xref>1</xref> hold promise.</p></sec>
    <sec><title>Results</title><p>We observed a 42% reduction.</p></sec>
  </body>
</article>"""


def test_jats_body_text_extracts_body() -> bool:
    txt = web._jats_body_text(_JATS)
    ok = "Genome editing tools" in txt and "42% reduction" in txt
    return _check("JATS <body> text extracted", ok, f"got {txt[:80]!r}")


def test_jats_body_excludes_front_matter() -> bool:
    """Abstract lives in <front>, not <body>. Returning it as full text would
    pass an abstract off as the article."""
    txt = web._jats_body_text(_JATS)
    return _check("abstract (in <front>) not included", "Abstract text here" not in txt)


def test_jats_no_body_returns_empty() -> bool:
    """Abstract-only record: return "" so the caller reports an honest failure
    and falls through, rather than summarising nothing."""
    txt = web._jats_body_text("<article><front><abstract><p>Only an abstract.</p></abstract></front></article>")
    return _check("record with no <body> -> empty string", txt == "", f"got {txt!r}")


def test_jats_malformed_xml_returns_empty() -> bool:
    return _check("malformed XML -> empty string, no raise",
                  web._jats_body_text("<article><body><p>unclosed") == "")


# --- blocked publisher: fail fast with something the model can act on -------

def test_blocked_publisher_error_is_actionable() -> bool:
    """A bare 'HTTP 403' tells the model nothing, so it retries or gives up.
    The replacement names the open-access route and flags the response."""
    import httpx

    def _Client(*a, **k):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(403)), **k)

    async def run():
        with patch.object(web, "guarded_client", _Client):
            with patch.object(web, "pmcid_from_url", lambda u: None):
                return await web.web_fetch_content("https://www.sciencedirect.com/science/article/pii/S1")

    res = asyncio.run(run())
    ok = (res.get("blocked_by_publisher") is True
          and "source" in res.get("error", "")
          and "Do not retry" in res.get("error", ""))
    return _check("403 -> actionable error + blocked_by_publisher flag", ok,
                  f"got {res!r}")


def test_other_http_errors_keep_plain_message() -> bool:
    """A 500 is transient and retryable; it must NOT be labelled as a
    publisher block or the model would wrongly abandon the URL."""
    import httpx

    def _Client(*a, **k):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(500)), **k)

    async def run():
        with patch.object(web, "guarded_client", _Client):
            with patch.object(web, "pmcid_from_url", lambda u: None):
                return await web.web_fetch_content("https://example.com/x")

    res = asyncio.run(run())
    ok = "blocked_by_publisher" not in res and "HTTP 500" in res.get("error", "")
    return _check("500 not mislabelled as a publisher block", ok, f"got {res!r}")


TESTS = [
    test_pmcid_extracted_from_both_pmc_url_shapes,
    test_pmcid_is_host_gated,
    test_non_pmc_urls_return_none,
    test_pmcid_is_case_normalised,
    test_jats_body_text_extracts_body,
    test_jats_body_excludes_front_matter,
    test_jats_no_body_returns_empty,
    test_jats_malformed_xml_returns_empty,
    test_blocked_publisher_error_is_actionable,
    test_other_http_errors_keep_plain_message,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
