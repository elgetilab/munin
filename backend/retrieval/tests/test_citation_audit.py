"""
Tests for the ungrounded-author audit (chat_service.audit_citation_claims_in_content).

Regression source: chat 61443530 (2026-07-29). The model wrote "Bazzi et al.
(PMC2098716)" and "Gonzalez-Rodriguez et al. ... PubMed 23274277" with neither
name present in any tool result on those turns, plus "Mun et al." for
10.1021/jp061300r. The user caught two of the three.

Annotate-only by design: the audit prefixes a [backend warning] block, exactly
like the two phantom-URL audits it sits beside. It never rewrites the answer.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_citation_audit.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat_service import audit_citation_claims_in_content  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _tool_calls(*results) -> list:
    return [{"id": f"c{i}", "name": "search", "arguments": {},
             "result": r} for i, r in enumerate(results)]


def test_no_attribution_is_a_noop() -> bool:
    content = "Pyrene forms excimers in POPC bilayers."
    out, ungrounded, grounded = audit_citation_claims_in_content(content, [])
    return _check("no 'et al.' -> untouched",
                  out == content and ungrounded == [] and grounded == 0)


def test_grounded_attribution_passes() -> bool:
    content = "**Loura et al. (2013)** ran MD simulations (PMID 23274277)."
    calls = _tool_calls({"ranked": [{"title": "Sensing hydration...",
                                     "authors": ["Loura LM", "do Canto AM"]}]})
    out, ungrounded, grounded = audit_citation_claims_in_content(content, calls)
    return _check("author present in tool result -> grounded, no warning",
                  out == content and ungrounded == [] and grounded == 1,
                  f"{ungrounded} {grounded}")


def test_the_reported_bug_bazzi() -> bool:
    """The exact shape Holger reported: right identifier, invented author."""
    content = "Diese Klasse wurde von **Bazzi et al. (PMC2098716)** eingeführt."
    calls = _tool_calls({"results": [{
        "title": "Changes of the Membrane Lipid Organization Characterized by "
                 "Means of a New Cholesterol-Pyrene Probe - PMC",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC2098716/",
        "snippet": "We synthesized 3-beta-hydroxy-pregn-5-ene..."}]})
    out, ungrounded, grounded = audit_citation_claims_in_content(content, calls)
    return _check("Bazzi flagged as ungrounded",
                  ungrounded == ["Bazzi"] and out.startswith("**[backend warning]**")
                  and "Bazzi et al." in out and content in out,
                  f"{ungrounded}")


def test_the_reported_bug_hyphenated_name() -> bool:
    content = ("**Gonzalez-Rodriguez et al. (2012)** (*Biochimica et Biophysica "
               "Acta*; PubMed 23274277): Molekulardynamik-Simulationen ...")
    calls = _tool_calls({"results": [{
        "title": "Sensing hydration and behavior of pyrene ... - PubMed",
        "url": "https://pubmed.ncbi.nlm.nih.gov/23274277/",
        "snippet": "In agreement with previous studies ..."}]})
    out, ungrounded, _ = audit_citation_claims_in_content(content, calls)
    return _check("hyphenated surname flagged whole",
                  ungrounded == ["Gonzalez-Rodriguez"], f"{ungrounded}")


def test_mun_is_not_grounded_by_munin() -> bool:
    """The third fabrication escaped a hand audit because 'Mun' is inside
    'Munin', which appears in every payload. Word-boundary matching only."""
    content = "**Mun et al. (2006)** (*J. Phys. Chem. B*) found that ..."
    calls = _tool_calls({"agent": "Munin search agent",
                         "results": [{"title": "Influence of Pyrene-Labeling",
                                      "url": "https://pubs.acs.org/doi/abs/10.1021/jp061300r"}]})
    out, ungrounded, _ = audit_citation_claims_in_content(content, calls)
    return _check("'Mun' not satisfied by 'Munin'", ungrounded == ["Mun"],
                  f"{ungrounded}")


def test_vancouver_initials_do_not_become_the_surname() -> bool:
    """"Knight MJ et al." must be audited as "Knight". Auditing the initials
    reported a fabrication for a correctly cited paper (conv 2547e764)."""
    content = '**Knight MJ et al.** "Magic Angle Spinning NMR of Paramagnetic Proteins."'
    calls = _tool_calls({"authors": ["Michael J. Knight", "Andrea Pell"]})
    _, ungrounded, grounded = audit_citation_claims_in_content(content, calls)
    return _check("surname taken before trailing initials",
                  ungrounded == [] and grounded == 1, f"{ungrounded}")


def test_multiword_surname_grounded() -> bool:
    content = "As **Le Guyader et al.** showed, the probe reports on order."
    calls = _tool_calls({"authors": ["Le Guyader L", "Le Roux C", "Lopez A"]})
    _, ungrounded, grounded = audit_citation_claims_in_content(content, calls)
    return _check("two-token surname matches across the space",
                  ungrounded == [] and grounded == 1, f"{ungrounded}")


def test_diacritics_match_as_written() -> bool:
    content = "Repáková et al. simulated pyrene-labelled lipids."
    calls = _tool_calls({"authors": ["Jarmila Repáková", "J. Holopainen"]})
    _, ungrounded, _ = audit_citation_claims_in_content(content, calls)
    return _check("accented surname matches the tool spelling",
                  ungrounded == [], f"{ungrounded}")


def test_nested_tool_payload_is_searched() -> bool:
    """Authors usually sit several levels down in a tool result."""
    content = "Lagane et al. measured the association constant."
    calls = _tool_calls({"ranked": [{"meta": {"authors": [{"name": "B Lagane"}]}}]})
    _, ungrounded, _ = audit_citation_claims_in_content(content, calls)
    return _check("deeply nested author name counts as grounded",
                  ungrounded == [], f"{ungrounded}")


def test_stopwords_are_not_treated_as_authors() -> bool:
    content = ("The et al. convention is common. Munin et al. is not a paper. "
               "Table et al. neither.")
    _, ungrounded, grounded = audit_citation_claims_in_content(content, [])
    return _check("product and generic words never flagged",
                  ungrounded == [] and grounded == 0, f"{ungrounded}")


def test_repeated_attribution_counted_once() -> bool:
    content = "Bazzi et al. said X. Later, Bazzi et al. said Y."
    _, ungrounded, _ = audit_citation_claims_in_content(content, [])
    return _check("same surname reported once", ungrounded == ["Bazzi"],
                  f"{ungrounded}")


def test_no_tool_calls_flags_every_attribution() -> bool:
    """A turn with no tools at all cannot ground any attribution."""
    content = "Smith et al. (2020) reported a 40% increase."
    out, ungrounded, grounded = audit_citation_claims_in_content(content, None)
    return _check("attribution with zero tool calls is ungrounded",
                  ungrounded == ["Smith"] and grounded == 0
                  and out.startswith("**[backend warning]**"), f"{ungrounded}")


def test_grounded_by_an_earlier_turn() -> bool:
    """The model does not see prior tool results (history is replayed as
    role/content only), so on a later turn it cites a paper it read earlier
    from its own text. That is a correct citation, not a fabrication.
    Measured over July 2026: 87 of 105 otherwise-unexplained attributions."""
    from chat_service import conversation_grounding_text
    conversation = {"messages": [
        {"role": "user", "content": "what about GPCR activation?"},
        {"role": "assistant", "content": "Lotta et al. showed ...",
         "tool_calls": _tool_calls({"authors": ["V. Lotta", "A. Wingler"]})},
        {"role": "user", "content": "and the thermodynamics?"},
    ]}
    prior = conversation_grounding_text(conversation)
    _, ungrounded, grounded = audit_citation_claims_in_content(
        "As Lotta et al. reported, the shift is entropic.", [], prior)
    return _check("author from an earlier turn's tools is grounded",
                  ungrounded == [] and grounded == 1, f"{ungrounded}")


def test_grounded_by_the_user() -> bool:
    from chat_service import conversation_grounding_text
    conversation = {"messages": [
        {"role": "user", "content": "have you read the Wingler et al. paper?"},
    ]}
    prior = conversation_grounding_text(conversation)
    _, ungrounded, _ = audit_citation_claims_in_content(
        "Wingler et al. is about biased agonism.", [], prior)
    return _check("name supplied by the user is grounded", ungrounded == [],
                  f"{ungrounded}")


def test_prior_assistant_prose_does_not_ground() -> bool:
    """A fabrication must not launder itself by being repeated."""
    from chat_service import conversation_grounding_text
    conversation = {"messages": [
        {"role": "assistant", "content": "Bazzi et al. introduced Py-met-chol."},
    ]}
    prior = conversation_grounding_text(conversation)
    _, ungrounded, _ = audit_citation_claims_in_content(
        "As noted, Bazzi et al. introduced it.", [], prior)
    return _check("the model's own earlier prose is not grounding",
                  ungrounded == ["Bazzi"], f"{ungrounded}")


def test_content_is_preserved_verbatim() -> bool:
    content = "Bazzi et al. (PMC2098716) introduced Py-met-chol."
    out, _, _ = audit_citation_claims_in_content(content, [])
    return _check("original answer text kept intact below the warning",
                  out.endswith(content))


def main() -> int:
    tests = [
        test_no_attribution_is_a_noop,
        test_grounded_attribution_passes,
        test_the_reported_bug_bazzi,
        test_the_reported_bug_hyphenated_name,
        test_mun_is_not_grounded_by_munin,
        test_vancouver_initials_do_not_become_the_surname,
        test_multiword_surname_grounded,
        test_diacritics_match_as_written,
        test_nested_tool_payload_is_searched,
        test_stopwords_are_not_treated_as_authors,
        test_repeated_attribution_counted_once,
        test_no_tool_calls_flags_every_attribution,
        test_grounded_by_an_earlier_turn,
        test_grounded_by_the_user,
        test_prior_assistant_prose_does_not_ground,
        test_content_is_preserved_verbatim,
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
