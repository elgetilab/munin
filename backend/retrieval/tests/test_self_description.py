"""
Tests for what Munin says it can do: capabilities._STATIC_FEATURES and the
admin-curated FAQ in config/faq.yml.

WHY. These two lists are not documentation, they are ANSWERS. A model asked
"what can I do here" reads `_STATIC_FEATURES` back almost verbatim, so an
omission is not a missing detail, it is a feature the user is told does not
exist. On 2026-08-20 a user who had attached a research-group scope asked to
browse it and got the six-item feature list plus a suggestion to "check your
interface directly" - while the Knowledge page was doing exactly what he asked,
one click away, and the tag scope he was already using appeared nowhere in the
list he was shown.

So the property under test is COVERAGE of the user-visible surface, not
wording. Each assertion below names a real feature a user has asked about and
been told, wrongly, was unavailable.

    docker exec munin-retrieval python /app/tests/test_self_description.py

The FAQ half reads config/faq.yml from the REPO, not the container copy, so it
runs on the host too:

    python backend/retrieval/tests/test_self_description.py
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml  # noqa: E402


def _check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


def _faq_path() -> str:
    """The deployed path first (container), else the repo copy (host)."""
    for candidate in (
        os.environ.get("FAQ_PATH", "/app/config/faq.yml"),
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "faq.yml"),
    ):
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""


def _topics() -> dict:
    path = _faq_path()
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("topics") or {}


# --- capabilities -----------------------------------------------------------

def _features() -> tuple:
    import capabilities
    return capabilities._STATIC_FEATURES


def test_features_mention_tag_scoping():
    blob = " ".join(_features()).lower()
    return _check("feature list mentions #tag knowledge scoping",
                  "#tag" in blob or "attach knowledge" in blob,
                  f"features: {_features()}")


def test_features_mention_the_knowledge_page():
    blob = " ".join(_features()).lower()
    return _check("feature list mentions the Knowledge browse page",
                  "/knowledge" in blob and "brows" in blob,
                  f"features: {_features()}")


def test_features_say_tags_scope_corpus_only():
    """The list is read out as-is, so the one thing users get wrong about tags
    (that they also scope personal uploads) has to be qualified in place."""
    blob = " ".join(_features()).lower()
    return _check("the tag entry says it scopes corpus search only",
                  "corpus search only" in blob, f"features: {_features()}")


def test_capabilities_block_renders_the_new_features():
    import capabilities
    block = capabilities.build_capabilities_block() or ""
    return _check("capabilities block carries the knowledge features",
                  "/knowledge" in block and "Attach knowledge" in block,
                  f"block was {len(block)} chars")


# --- faq.yml ----------------------------------------------------------------

def test_faq_file_is_present_and_parses():
    path = _faq_path()
    return _check("faq.yml is readable and parses", bool(path) and bool(_topics()),
                  f"path={path!r}")


def test_faq_has_a_knowledge_scope_topic():
    return _check("faq covers knowledge scoping", "knowledge_scope" in _topics(),
                  f"topics: {sorted(_topics())}")


def test_knowledge_scope_answer_covers_both_halves():
    """What the tags DO scope and what they do NOT, plus where to browse. The
    transcript failed on all three."""
    answer = (_topics().get("knowledge_scope") or {}).get("answer", "").lower()
    haves = {
        "corpus search tools": "paper_search" in answer,
        "the negative": "do not scope" in answer and "uploaded" in answer,
        "the browse route": "/knowledge" in answer and "browse_tag_papers" in answer,
    }
    return _check("knowledge_scope answer covers scope, non-scope and browse",
                  all(haves.values()), f"{haves}")


def test_upload_topic_points_at_the_inventory_tool():
    """"How many papers did I upload" is a listing question, and answering it
    with a semantic search is what produced four false empties."""
    answer = (_topics().get("upload_documents") or {}).get("answer", "")
    return _check("upload topic points at list_documents for inventories",
                  "list_documents" in answer and "never by #tag" in answer,
                  f"answer={answer[:120]!r}")


def test_every_faq_topic_is_well_formed():
    bad = [
        k for k, v in _topics().items()
        if not isinstance(v, dict) or not (v.get("question") or "").strip()
        or not (v.get("answer") or "").strip()
    ]
    return _check("every faq topic has a question and an answer", not bad, f"bad: {bad}")


def test_faq_answers_stay_within_budget():
    """The whole file is not injected into the prompt, but a long answer still
    costs a tool result. The existing corpus sits at 500-660 chars; this catches
    a new entry that runs away, not a 30-char overshoot."""
    over = {k: len(v.get("answer", "")) for k, v in _topics().items()
            if len(v.get("answer", "")) > 800}
    return _check("no faq answer exceeds the 800-char budget", not over, f"{over}")


TESTS = [
    test_features_mention_tag_scoping,
    test_features_mention_the_knowledge_page,
    test_features_say_tags_scope_corpus_only,
    test_capabilities_block_renders_the_new_features,
    test_faq_file_is_present_and_parses,
    test_faq_has_a_knowledge_scope_topic,
    test_knowledge_scope_answer_covers_both_halves,
    test_upload_topic_points_at_the_inventory_tool,
    test_every_faq_topic_is_well_formed,
    test_faq_answers_stay_within_budget,
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
