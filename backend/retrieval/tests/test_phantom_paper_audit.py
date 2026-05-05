"""
Standalone unit tests for the phantom-paper-URL audit
(`audit_paper_urls_in_content` in chat_service.py).

Regression guard for the failure pattern observed in chat
a42384f0 on 2026-05-05: on a vLLM/SLURM infra question, the model
fabricated `[HPCS 2005](https://search.muninai.org/paper/10.1109%2F
hpcs.2005.55/pdf)` to support a wrong technical claim, with no
paper_search / paper_lookup / deep_research call on the turn. The
audit is the post-turn backstop that catches this, prepends a
[backend warning] marker, and logs the phantom URL.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_phantom_paper_audit.py

Exit 0 = pass, non-zero = fail.

Pure-function tests, no I/O, no model calls. Behavioural coverage
of the same failure shape lives in
scripts/flakiness-suite.py paper_citation_grounding.
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")

from chat_service import audit_paper_urls_in_content  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# No URLs in content -> no-op
# ---------------------------------------------------------------------------

def test_empty_content_returns_unchanged() -> bool:
    out, phantoms = audit_paper_urls_in_content("", [])
    return _check(
        "empty content -> unchanged, no phantoms",
        out == "" and phantoms == [],
    )


def test_content_without_paper_urls_returns_unchanged() -> bool:
    text = (
        "See the DOI [10.1234/example](https://doi.org/10.1234/example) "
        "and the arXiv link [2305.00001](https://arxiv.org/abs/2305.00001)."
    )
    out, phantoms = audit_paper_urls_in_content(text, [])
    return _check(
        "DOI/arXiv URLs without search.muninai.org -> unchanged",
        out == text and phantoms == [],
    )


def test_artifact_url_is_not_a_paper_url() -> bool:
    """`/api/artifacts/...` URLs are policed by the artifact audit;
    the paper audit must not flag them."""
    content = "Compiled: [PDF](/api/artifacts/abc-123/compiled.pdf)"
    out, phantoms = audit_paper_urls_in_content(content, [])
    return _check(
        "artifact URL ignored by paper audit",
        out == content and phantoms == [],
    )


# ---------------------------------------------------------------------------
# Legitimate URLs that match a tool result -> unchanged
# ---------------------------------------------------------------------------

def test_legitimate_paper_url_matched_by_tool_result_passes() -> bool:
    """Most common case: paper_search returned a `download_url`,
    the model linked to it. Audit must NOT flag this."""
    url = "https://search.muninai.org/paper/10.1234%2Fexample/pdf"
    content = f"This is well established in [the foundational paper]({url})."
    tool_calls = [
        {
            "id": "tc-1",
            "name": "paper_search",
            "result": {
                "results": [
                    {
                        "doi": "10.1234/example",
                        "title": "An example",
                        "download_url": url,
                    }
                ],
            },
        }
    ]
    out, phantoms = audit_paper_urls_in_content(content, tool_calls)
    return _check(
        "legitimate paper URL -> unchanged, no phantoms",
        out == content and phantoms == [],
        f"phantoms={phantoms!r}",
    )


def test_paper_url_nested_deep_in_tool_result_passes() -> bool:
    """The recursive collector must find URLs even when they are
    buried inside lists/dicts/strings inside the tool result."""
    url = "https://search.muninai.org/paper/10.5555%2Fdeep.nest/pdf"
    content = f"As shown in [Smith et al. 2024]({url})."
    tool_calls = [
        {
            "id": "tc-1",
            "name": "deep_research",
            "result": {
                "summary": "...",
                "sources": {
                    "papers": [
                        {
                            "doi": "10.5555/deep.nest",
                            "links": {"download_url": url},
                        },
                    ],
                },
            },
        }
    ]
    out, phantoms = audit_paper_urls_in_content(content, tool_calls)
    return _check(
        "deeply-nested paper URL in tool result -> no phantoms",
        out == content and phantoms == [],
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# THE REGRESSION CASE - phantom URL is detected
# ---------------------------------------------------------------------------

def test_a42384f0_hpcs_2005_fabrication() -> bool:
    """Exact reported-chat failure shape (a42384f0, 2026-05-05).
    Model wrote a confident infra answer, fabricated an HPCS 2005
    citation to support it, with no paper-tool call on the turn."""
    url = "https://search.muninai.org/paper/10.1109%2Fhpcs.2005.55/pdf"
    content = (
        "SLURM has no built-in mechanism to split GPU resources across "
        f"concurrent jobs [HPCS 2005]({url})."
    )
    out, phantoms = audit_paper_urls_in_content(content, [])
    if phantoms != [url]:
        return _check(
            "a42384f0 HPCS 2005 fabrication -> flagged",
            False,
            f"expected [{url!r}], got {phantoms!r}",
        )
    if "[backend warning]" not in out:
        return _check(
            "a42384f0 HPCS 2005 fabrication -> flagged",
            False,
            "expected '[backend warning]' marker prepended to content",
        )
    if url not in out:
        return _check(
            "a42384f0 HPCS 2005 fabrication -> flagged",
            False,
            "phantom URL should still appear in marker for audit trail",
        )
    if not out.endswith(content):
        return _check(
            "a42384f0 HPCS 2005 fabrication -> flagged",
            False,
            "original content should be preserved after the marker",
        )
    return _check(
        "a42384f0 HPCS 2005 fabrication -> flagged + marker added",
        True,
    )


def test_phantom_url_when_other_tools_were_called() -> bool:
    """Model called web_search but invented a paper URL. The
    web_search result has its own URLs (non-paper), so legit paper
    set is empty and the invented paper URL must be flagged."""
    phantom = "https://search.muninai.org/paper/10.9999%2Ffake/pdf"
    content = (
        f"Background: [overview blog post](https://example.com/post). "
        f"And the canonical reference: [Doe 2020]({phantom})."
    )
    tool_calls = [
        {
            "id": "tc-1",
            "name": "web_search",
            "result": {
                "results": [
                    {"url": "https://example.com/post", "title": "blog"},
                ],
            },
        }
    ]
    out, phantoms = audit_paper_urls_in_content(content, tool_calls)
    if phantoms != [phantom]:
        return _check(
            "phantom paper URL flagged when only non-paper tools fired",
            False,
            f"expected [{phantom!r}], got {phantoms!r}",
        )
    return _check(
        "phantom paper URL flagged when only non-paper tools fired",
        True,
    )


def test_multiple_phantoms_all_listed() -> bool:
    p1 = "https://search.muninai.org/paper/10.1/a/pdf"
    p2 = "https://search.muninai.org/paper/10.2/b/pdf"
    content = f"See [first]({p1}) and [second]({p2})."
    out, phantoms = audit_paper_urls_in_content(content, [])
    return _check(
        "multiple phantoms -> both flagged + both in marker",
        sorted(phantoms) == sorted([p1, p2])
        and p1 in out
        and p2 in out
        and "[backend warning]" in out,
        f"phantoms={phantoms!r}",
    )


def test_mixed_legit_and_phantom_only_phantom_flagged() -> bool:
    legit = "https://search.muninai.org/paper/10.1234%2Freal/pdf"
    phantom = "https://search.muninai.org/paper/10.9999%2Ffake/pdf"
    content = (
        f"Real source: [Real et al.]({legit}). "
        f"Imagined source: [Fake et al.]({phantom})."
    )
    tool_calls = [
        {
            "id": "tc-1",
            "name": "paper_lookup",
            "result": {"download_url": legit, "doi": "10.1234/real"},
        }
    ]
    out, phantoms = audit_paper_urls_in_content(content, tool_calls)
    return _check(
        "mixed legit + phantom -> only phantom flagged",
        phantoms == [phantom] and "[backend warning]" in out,
        f"phantoms={phantoms!r}",
    )


def test_url_with_www_subdomain_is_matched() -> bool:
    """Defensive: model occasionally types `www.search.muninai.org`.
    Audit must catch it as a paper URL too."""
    phantom = "https://www.search.muninai.org/paper/10.1/x/pdf"
    content = f"Reference: [Doe]({phantom})."
    out, phantoms = audit_paper_urls_in_content(content, [])
    return _check(
        "www.search.muninai.org variant -> still flagged",
        phantoms == [phantom],
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# Defensive: malformed / weird inputs do not crash
# ---------------------------------------------------------------------------

def test_none_content_does_not_crash() -> bool:
    out, phantoms = audit_paper_urls_in_content(None, [])  # type: ignore[arg-type]
    return _check(
        "None content -> returned unchanged, empty phantoms",
        out is None and phantoms == [],
    )


def test_none_tool_calls_treated_as_empty_list() -> bool:
    url = "https://search.muninai.org/paper/10.1%2Fx/pdf"
    content = f"link [x]({url})"
    out, phantoms = audit_paper_urls_in_content(content, None)  # type: ignore[arg-type]
    return _check(
        "None tool_calls -> treated as empty, URL is phantom",
        phantoms == [url],
    )


def test_malformed_tool_call_entries_dont_crash() -> bool:
    url = "https://search.muninai.org/paper/10.1%2Fx/pdf"
    content = f"see [link]({url})"
    tool_calls = [None, "garbage", {"result": {"download_url": url}}]
    out, phantoms = audit_paper_urls_in_content(content, tool_calls)  # type: ignore[arg-type]
    return _check(
        "malformed tool_calls entries -> ignored, audit still works",
        phantoms == [],
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_empty_content_returns_unchanged,
    test_content_without_paper_urls_returns_unchanged,
    test_artifact_url_is_not_a_paper_url,
    test_legitimate_paper_url_matched_by_tool_result_passes,
    test_paper_url_nested_deep_in_tool_result_passes,
    test_a42384f0_hpcs_2005_fabrication,
    test_phantom_url_when_other_tools_were_called,
    test_multiple_phantoms_all_listed,
    test_mixed_legit_and_phantom_only_phantom_flagged,
    test_url_with_www_subdomain_is_matched,
    test_none_content_does_not_crash,
    test_none_tool_calls_treated_as_empty_list,
    test_malformed_tool_call_entries_dont_crash,
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
