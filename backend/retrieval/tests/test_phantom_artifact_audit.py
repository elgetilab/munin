"""
Standalone unit tests for the phantom-artifact-URL audit
(`audit_artifact_urls_in_content` in chat_service.py).

Regression guard for the failure pattern observed in chat
e45e3f2b on 2026-04-25: model wrote prose claiming a Beamer deck
had been recompiled and emitted a fabricated
`/api/artifacts/<uuid>/compiled.pdf` URL without calling
`compile_latex`. The audit is the post-turn backstop that catches
this, prepends a [backend warning] marker, and logs the phantom URL.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_phantom_artifact_audit.py

Exit 0 = pass, non-zero = fail.

Pure-function tests — no I/O, no model calls. Behavioural coverage
of the same failure shape lives in
scripts/flakiness-suite.py latex_compile/modify_colour_theme_after_initial_build.
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")

from chat_service import audit_artifact_urls_in_content  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# No URLs in content → no-op
# ---------------------------------------------------------------------------

def test_empty_content_returns_unchanged() -> bool:
    out, phantoms = audit_artifact_urls_in_content("", [])
    return _check(
        "empty content → unchanged, no phantoms",
        out == "" and phantoms == [],
    )


def test_content_without_artifact_urls_returns_unchanged() -> bool:
    text = "Here are three references and a [DOI link](https://doi.org/10.1/x)."
    out, phantoms = audit_artifact_urls_in_content(text, [])
    return _check(
        "content without /api/artifacts URLs → unchanged",
        out == text and phantoms == [],
    )


# ---------------------------------------------------------------------------
# Legitimate URLs that match a tool result → unchanged
# ---------------------------------------------------------------------------

def test_legitimate_url_matched_by_tool_result_passes() -> bool:
    """Most common case: the model called compile_latex, the tool
    returned an external_url, and the model linked to it. Audit
    must NOT flag this."""
    url = "/api/artifacts/abc-123/compiled.pdf"
    content = f"I've compiled the deck. [Download PDF]({url})"
    tool_calls = [
        {
            "id": "tc-1",
            "name": "compile_latex",
            "result": {
                "success": True,
                "external_url": url,
                "tex_url": "/api/artifacts/abc-123/source.tex",
            },
        }
    ]
    out, phantoms = audit_artifact_urls_in_content(content, tool_calls)
    return _check(
        "legitimate URL → unchanged, no phantoms",
        out == content and phantoms == [],
        f"phantoms={phantoms!r}",
    )


def test_url_nested_deep_in_tool_result_passes() -> bool:
    """The recursive collector must find URLs even when they're
    buried inside lists/dicts/strings inside the tool result."""
    url = "/api/artifacts/deep-nested/output.png"
    content = f"See the plot at [link]({url})."
    tool_calls = [
        {
            "id": "tc-1",
            "name": "run_python",
            "result": {
                "stdout": "Computed values",
                "artifacts": [
                    {"id": "art-1", "external_url": url, "kind": "image"},
                ],
                "metadata": {"runtime": 1.2},
            },
        }
    ]
    out, phantoms = audit_artifact_urls_in_content(content, tool_calls)
    return _check(
        "deeply-nested URL in tool result → no phantoms",
        out == content and phantoms == [],
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# THE REGRESSION CASE — phantom URL is detected
# ---------------------------------------------------------------------------

def test_phantom_url_with_no_tool_calls() -> bool:
    """The exact e45e3f2b failure shape: model claims it
    re-compiled but tool_calls is empty (or doesn't include
    compile_latex). The fabricated URL must be flagged."""
    url = "/api/artifacts/a5e4d5b7-7c3f-4e9a-a2e3-b9f8c0d1e2f3/compiled.pdf"
    content = (
        "I've updated the presentation with a red color scheme. "
        f"[Download PDF]({url})"
    )
    out, phantoms = audit_artifact_urls_in_content(content, [])
    if phantoms != [url]:
        return _check(
            "phantom URL with no tool calls → flagged",
            False,
            f"expected [{url!r}], got {phantoms!r}",
        )
    if "[backend warning]" not in out:
        return _check(
            "phantom URL with no tool calls → flagged",
            False,
            "expected '[backend warning]' marker prepended to content",
        )
    if url not in out:
        return _check(
            "phantom URL with no tool calls → flagged",
            False,
            "phantom URL should still appear in marker for audit trail",
        )
    if not out.endswith(content):
        return _check(
            "phantom URL with no tool calls → flagged",
            False,
            "original content should be preserved after the marker",
        )
    return _check("phantom URL with no tool calls → flagged + marker added", True)


def test_phantom_url_when_other_tools_were_called() -> bool:
    """Model called list_artifacts but invented a compile URL.
    The list_artifacts tool result contains its own real URLs,
    so legit-set is non-empty — the invented URL must STILL be
    flagged because it isn't IN the legit set."""
    legit_url = "/api/artifacts/old-uuid/old.pdf"
    phantom = "/api/artifacts/fabricated/compiled.pdf"
    content = (
        f"I checked the artifacts list and found [the previous PDF]({legit_url}). "
        f"I've now recompiled with a red theme: [new PDF]({phantom})"
    )
    tool_calls = [
        {
            "id": "tc-1",
            "name": "list_artifacts",
            "result": {
                "artifacts": [
                    {"id": "old-uuid", "external_url": legit_url},
                ]
            },
        }
    ]
    out, phantoms = audit_artifact_urls_in_content(content, tool_calls)
    if phantoms != [phantom]:
        return _check(
            "phantom flagged even when other tool URLs are legit",
            False,
            f"expected [{phantom!r}], got {phantoms!r}",
        )
    return _check("phantom flagged even when other tool URLs are legit", True)


def test_multiple_phantoms_all_listed() -> bool:
    """Model fabricates two artifact URLs; both must be in the
    phantom list, both must appear in the warning marker."""
    p1 = "/api/artifacts/fake-1/a.pdf"
    p2 = "/api/artifacts/fake-2/b.tex"
    content = f"PDF: [pdf]({p1}). Source: [tex]({p2})."
    out, phantoms = audit_artifact_urls_in_content(content, [])
    return _check(
        "multiple phantoms → both flagged + both in marker",
        sorted(phantoms) == sorted([p1, p2])
        and p1 in out
        and p2 in out
        and "[backend warning]" in out,
        f"phantoms={phantoms!r}",
    )


def test_mixed_legit_and_phantom_only_phantom_flagged() -> bool:
    legit = "/api/artifacts/real/source.tex"
    phantom = "/api/artifacts/imagined/compiled.pdf"
    content = (
        f"Source [here]({legit}); compiled [here]({phantom})."
    )
    tool_calls = [
        {
            "id": "tc-1",
            "name": "create_artifact",
            "result": {"external_url": legit, "id": "real"},
        }
    ]
    out, phantoms = audit_artifact_urls_in_content(content, tool_calls)
    return _check(
        "mixed legit + phantom → only phantom flagged",
        phantoms == [phantom] and "[backend warning]" in out,
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# Defensive: malformed / weird inputs don't crash
# ---------------------------------------------------------------------------

def test_none_content_does_not_crash() -> bool:
    out, phantoms = audit_artifact_urls_in_content(None, [])  # type: ignore[arg-type]
    return _check(
        "None content → returned unchanged, empty phantoms",
        out is None and phantoms == [],
    )


def test_none_tool_calls_treated_as_empty_list() -> bool:
    """tool_calls=None should be handled gracefully — same as []."""
    url = "/api/artifacts/x/y.pdf"
    content = f"link [x]({url})"
    out, phantoms = audit_artifact_urls_in_content(content, None)  # type: ignore[arg-type]
    return _check(
        "None tool_calls → treated as empty, URL is phantom",
        phantoms == [url],
    )


def test_malformed_tool_call_entries_dont_crash() -> bool:
    """tool_calls list contains a non-dict (string, None, etc.).
    Audit must not crash; just skip the bad entry."""
    url = "/api/artifacts/x/y.pdf"
    content = f"see [link]({url})"
    tool_calls = [None, "garbage", {"result": {"external_url": url}}]
    out, phantoms = audit_artifact_urls_in_content(content, tool_calls)  # type: ignore[arg-type]
    return _check(
        "malformed tool_calls entries → ignored, audit still works",
        phantoms == [],
        f"phantoms={phantoms!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_empty_content_returns_unchanged,
    test_content_without_artifact_urls_returns_unchanged,
    test_legitimate_url_matched_by_tool_result_passes,
    test_url_nested_deep_in_tool_result_passes,
    test_phantom_url_with_no_tool_calls,
    test_phantom_url_when_other_tools_were_called,
    test_multiple_phantoms_all_listed,
    test_mixed_legit_and_phantom_only_phantom_flagged,
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
