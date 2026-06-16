"""
Standalone unit tests for the /api/tags catalog assembly helpers.

Covers the two correctness fixes (2026-06-16):
  - the contributor mention list dedups by username INDEPENDENTLY of the
    group dedup, so a contributor who shares a research group with another
    is no longer silently dropped;
  - a multi-email person collapses to a single record.

Runs in-process inside the retrieval container (no live service / Qdrant):

    docker exec munin-retrieval python /app/tests/test_tag_catalog.py

Exit code 0 = pass, non-zero = fail.
"""
import sys
import traceback

import main


def test_dedup_collapses_multi_email_person() -> bool:
    entry = {"display_name": "Bob", "username": "bob", "research_group": "lab"}
    known = {"bob@x.org": entry, "bob@y.de": entry}  # one person, two emails
    people = main._dedup_contributor_people(known)
    ok = len(people) == 1 and people[0]["emails"] == {"bob@x.org", "bob@y.de"}
    print(("[PASS]" if ok else "[FAIL]") + " multi-email person collapses to one record")
    return ok


def test_contributors_sharing_a_group_both_listed() -> bool:
    """The regression: two contributors in one group must both appear, and
    the group must appear exactly once."""
    a = {"display_name": "Alice", "username": "alice", "research_group": "lab",
         "research_group_display_name": "The Lab"}
    b = {"display_name": "Bob", "username": "bob", "research_group": "lab",
         "research_group_display_name": "The Lab"}
    known = {"alice@x": a, "bob@x": b}
    people = main._dedup_contributor_people(known)
    groups, contributors = main._build_group_contributor_lists(
        people, {"lab": 5}, {"alice": 3, "bob": 2})
    usernames = {c["username"] for c in contributors}
    ok = usernames == {"alice", "bob"} and len(groups) == 1 and groups[0]["paper_count"] == 5
    print(("[PASS]" if ok else "[FAIL]") + " two contributors sharing a group both listed; group deduped")
    return ok


def test_username_less_contributor_excluded_from_mention_list() -> bool:
    a = {"display_name": "Carol", "research_group": "lab2"}  # no username
    known = {"carol@x": a}
    people = main._dedup_contributor_people(known)
    groups, contributors = main._build_group_contributor_lists(people, {}, {})
    ok = contributors == [] and len(groups) == 1
    print(("[PASS]" if ok else "[FAIL]") + " username-less contributor excluded from mention list, group kept")
    return ok


def test_lists_sorted_by_paper_count_desc() -> bool:
    a = {"display_name": "A", "username": "a", "research_group": "g1"}
    b = {"display_name": "B", "username": "b", "research_group": "g2"}
    known = {"a@x": a, "b@x": b}
    people = main._dedup_contributor_people(known)
    groups, contributors = main._build_group_contributor_lists(
        people, {"g1": 1, "g2": 9}, {"a": 1, "b": 9})
    ok = ([g["slug"] for g in groups] == ["g2", "g1"]
          and [c["username"] for c in contributors] == ["b", "a"])
    print(("[PASS]" if ok else "[FAIL]") + " groups and contributors sorted by paper_count desc")
    return ok


TESTS = [
    test_dedup_collapses_multi_email_person,
    test_contributors_sharing_a_group_both_listed,
    test_username_less_contributor_excluded_from_mention_list,
    test_lists_sorted_by_paper_count_desc,
]


def run() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    total = len(TESTS)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
