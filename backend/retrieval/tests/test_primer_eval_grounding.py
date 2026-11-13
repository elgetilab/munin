"""
Tests for the sequence-grounding detector in evals/run_eval.py.

Layer 4 of docs/agent-track/PRIMER-DESIGN-PLAN.md rests on one function:
`_ungrounded_sequences`, which decides whether a DNA sequence the model stated
actually exists in the template it was given. That is how the primer scenarios
detect the failure mode every cited study observed, namely confidently emitted
oligos that do not occur anywhere in the target.

Why this file exists: the detector FAILS OPEN. If its regex stopped matching,
`_ungrounded_sequences` would return an empty list for everything and all
three primer scenarios would report PASS while measuring nothing at all. A
silently vacuous eval is worse than no eval, so the detector gets its own
tests, including a canary that it still flags a known-bad sequence.

The eval harness itself is not importable as a package (it is a script that
talks to a live service), so it is loaded by path here. Nothing in this file
touches the network or the service.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_eval_grounding.py
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ROOT = Path(__file__).resolve().parent.parent


def _load_run_eval():
    """Load evals/run_eval.py by path; it is a script, not a module."""
    path = _ROOT / "evals" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("_run_eval_under_test", path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["run_eval.py"]
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    finally:
        sys.argv = saved
    return module


RE = _load_run_eval()
TEMPLATE = RE.PUC19_MCS_REGION


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def test_template_is_sane() -> bool:
    ok = len(TEMPLATE) == 600
    ok &= set(TEMPLATE) <= set("ACGT")
    # It must really contain the landmarks the prompts talk about.
    ok &= "GAATTC" in TEMPLATE            # EcoRI, start of the pUC19 MCS
    ok &= "AAGCTT" in TEMPLATE            # HindIII, end of it
    ok &= "TGTAAAACGACGGCCAGT" in TEMPLATE  # M13 Forward(-21)
    return _check("the inlined pUC19 region is 600 bp and has its landmarks",
                  ok, f"{len(TEMPLATE)} bp")


def test_real_forward_is_grounded() -> bool:
    seq = TEMPLATE[200:220]
    return _check("a real plus-strand oligo is grounded",
                  RE._ungrounded_sequences(f"use {seq}", TEMPLATE) == [])


def test_real_reverse_is_grounded() -> bool:
    """A reverse primer is quoted as the reverse complement of its footprint."""
    seq = RE._revcomp(TEMPLATE[400:420])
    return _check("a real minus-strand oligo is grounded",
                  RE._ungrounded_sequences(f"use {seq}", TEMPLATE) == [])


def test_fabricated_sequence_is_caught() -> bool:
    """THE canary. If this ever stops failing, the detector has gone blind."""
    fake = "ACGTACGTACGTACGTACGT"
    got = RE._ungrounded_sequences(f"use {fake}", TEMPLATE)
    return _check("a fabricated oligo is flagged", got == [fake], f"got {got}")


def test_plausible_but_absent_is_caught() -> bool:
    """
    The realistic failure: not random letters, a sensible-looking primer.

    GC content and length are unremarkable; it simply is not in the template.
    """
    plausible = "ATGGCGCGATTACGGCATCA"
    got = RE._ungrounded_sequences(plausible, TEMPLATE)
    return _check("a plausible but absent oligo is flagged",
                  got == [plausible], f"got {got}")


def test_tailed_primer_handling() -> bool:
    """
    A cloning primer's 5' tail is SUPPOSED to be absent from the template.

    So grounding a tailed primer means grounding its annealing 3' end. Without
    the allowance it must be flagged; with it, accepted.
    """
    tailed = "GGGGAATTCGGGG" + TEMPLATE[200:220]
    strict = RE._ungrounded_sequences(tailed, TEMPLATE, False)
    lenient = RE._ungrounded_sequences(tailed, TEMPLATE, True)
    ok = strict != [] and lenient == []
    return _check("a tailed primer is flagged strictly, accepted leniently", ok)


def test_tail_allowance_does_not_become_a_loophole() -> bool:
    """
    The dangerous case: `allow_tails` must not wave through a wholly invented
    oligo just because it has something tail-shaped on the front.
    """
    bogus = "GGGGAATTC" + "ACGTACGTACGTACGTACGT"
    got = RE._ungrounded_sequences(bogus, TEMPLATE, True)
    return _check("allow_tails still flags an invented annealing end",
                  got != [], f"got {got}")


def test_prose_without_dna_is_ignored() -> bool:
    ok = RE._ungrounded_sequences(
        "The Tm is 60 C and the GC content is 50%.", TEMPLATE) == []
    ok &= RE._ungrounded_sequences("A CAT GAGA TAG ATTACA", TEMPLATE) == []
    # A short run must not trip it; primers are not 8-mers.
    ok &= RE._ungrounded_sequences("ACGTACGT", TEMPLATE) == []
    return _check("prose and short words are not mistaken for oligos", ok)


def test_scenarios_are_registered() -> bool:
    names = ("primer_pcr", "primer_cloning", "primer_nosequence")
    ok = all(n in RE.SCENARIOS for n in names)
    for n in names:
        entry = RE.SCENARIOS.get(n, {})
        ok &= bool(entry.get("prompt")) and callable(entry.get("checks"))
    return _check("the three primer scenarios are registered and well-formed",
                  ok, ", ".join(names))


def test_checks_are_callable_and_named() -> bool:
    """Every scenario's checks must build a list of (name, predicate)."""
    ok = True
    for name in ("primer_pcr", "primer_cloning", "primer_nosequence"):
        built = RE.SCENARIOS[name]["checks"]()
        ok &= bool(built)
        ok &= all(isinstance(label, str) and callable(fn)
                  for label, fn in built)
    # The grounding check must actually be among them for the two that
    # supply a template, or those scenarios measure nothing.
    for name in ("primer_pcr", "primer_cloning"):
        labels = [l for l, _ in RE.SCENARIOS[name]["checks"]()]
        ok &= any("DNA sequence" in l for l in labels)
        ok &= any("design_primers" in l for l in labels)
    return _check("scenario checks are well-formed and include the key two", ok)


def main() -> int:
    tests = [
        test_template_is_sane,
        test_real_forward_is_grounded,
        test_real_reverse_is_grounded,
        test_fabricated_sequence_is_caught,
        test_plausible_but_absent_is_caught,
        test_tailed_primer_handling,
        test_tail_allowance_does_not_become_a_loophole,
        test_prose_without_dna_is_ignored,
        test_scenarios_are_registered,
        test_checks_are_callable_and_named,
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
