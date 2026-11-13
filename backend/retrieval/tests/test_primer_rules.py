"""
Tests for primer/rules.py - the design rule table and its evaluation.

Layer 1 of the primer eval plan (docs/agent-track/PRIMER-DESIGN-PLAN.md).
Pure functions, no network, no services.

What is being tested, and what is not
-------------------------------------
The thresholds themselves are editorial: published sources disagree about
nearly all of them, which is why `rules.py` records a citation and a severity
beside each one. These tests therefore do NOT assert that "40-60% GC is the
right rule". They assert the things that can actually be wrong in code:

  - a rule fires on the correct side of its boundary, checked AT the boundary
  - a HARD failure makes a primer unusable and a SOFT one does not
  - a rule whose input was never computed is reported as not-computed rather
    than silently passing, which would be the dangerous failure mode
  - degenerate primers are handled as a population, never collapsed to a point
  - the `gibson` alias normalises to `overlap_assembly`

Real primers are used where possible: M13 Forward(-21) is a decades-old
working primer and should pass cleanly, which is a useful sanity check that
the defaults are not accidentally impossible to satisfy.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_rules.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import rules  # noqa: E402
from primer.rules import HARD, SOFT, Constraints  # noqa: E402

M13F21 = "TGTAAAACGACGGCCAGT"          # real, works at the bench
M13R = "AGCGGATAACAATTTCACACAGG"       # real
DEGENERATE = "RARRGGRATTTTGTCSGKGT"    # SB-CYTB-3 reverse, Dryad benchmark


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _by_rule(checks, rule):
    for c in checks:
        if c.rule == rule:
            return c
    raise AssertionError(f"no check named {rule!r}")


# --- modes ----------------------------------------------------------------
def test_mode_alias_normalises() -> bool:
    """`gibson` is an accepted alias; `overlap_assembly` is canonical."""
    ok = rules.normalise_mode("gibson") == "overlap_assembly"
    ok &= rules.normalise_mode("GIBSON") == "overlap_assembly"
    ok &= rules.normalise_mode(" Gibson ") == "overlap_assembly"
    ok &= rules.normalise_mode("overlap_assembly") == "overlap_assembly"
    ok &= rules.normalise_mode("PCR") == "pcr"
    return _check("gibson normalises to overlap_assembly", ok)


def test_unknown_mode_rejected() -> bool:
    ok = False
    try:
        rules.normalise_mode("qpcr")
    except ValueError:
        ok = True
    return _check("an unknown mode is rejected", ok)


def test_every_mode_has_constraints() -> bool:
    ok = all(isinstance(rules.constraints_for(m), Constraints)
             for m in list(rules.MODES) + list(rules.MODE_ALIASES))
    return _check("every mode and alias resolves to constraints", ok)


# --- measurements ---------------------------------------------------------
def test_longest_run() -> bool:
    ok = rules.longest_run("AAAA")[0] == 4
    ok &= rules.longest_run("ACGT")[0] == 1
    ok &= rules.longest_run("ACGGGGGT") == (5, "G")
    ok &= rules.longest_run("A")[0] == 1
    ok &= rules.longest_run("TTTACGTTTT") == (4, "T")   # last run wins on ties
    return _check("longest_run finds the longest homopolymer", ok)


def test_end_gc_count() -> bool:
    ok = rules.end_gc_count("AAAAAGGGGG", 5) == 5
    ok &= rules.end_gc_count("GGGGGAAAAA", 5) == 0
    ok &= rules.end_gc_count("AAAAAAAAGC", 5) == 2
    ok &= rules.end_gc_count("AAAAAAAAAS", 5) == 1   # S is unambiguously G/C
    return _check("end_gc_count counts the 3' window only", ok)


# --- a real primer should pass -------------------------------------------
def test_real_primer_passes_cleanly() -> bool:
    """If M13F(-21) cannot pass, the defaults are wrong, not the primer."""
    checks = rules.evaluate_primer(M13F21, rules.PCR)
    summary = rules.summarise(checks)
    ok = summary["usable"] and summary["hard_failures"] == 0
    ok &= summary["soft_failures"] == 0
    return _check("M13 Forward(-21) passes every default rule", ok,
                  f"{summary['passed']}/{summary['total']}, "
                  f"{summary['soft_failures']} soft failures")


# --- boundaries -----------------------------------------------------------
def test_length_boundary() -> bool:
    """Checked AT the boundary, where an off-by-one actually lives."""
    c = rules.PCR
    at_min = "AC" * 9                      # 18 nt
    below = "AC" * 8 + "A"                 # 17 nt
    at_max = ("ACGT" * 7)[:25]             # 25 nt
    above = ("ACGT" * 7)[:26]              # 26 nt
    ok = _by_rule(rules.evaluate_primer(at_min, c), "primer_length").passed
    ok &= not _by_rule(rules.evaluate_primer(below, c), "primer_length").passed
    ok &= _by_rule(rules.evaluate_primer(at_max, c), "primer_length").passed
    ok &= not _by_rule(rules.evaluate_primer(above, c), "primer_length").passed
    return _check("length rule fires exactly at 18 and 25", ok)


def test_poly_run_boundary_and_severity() -> bool:
    """A run of 5 is tolerated (hard limit), 4 preferred (soft), 6 rejected."""
    c = rules.PCR
    run5 = "GCATGCAAAAAGCATGCTA"
    run6 = "GCATGCAAAAAAGCATGCT"
    hard5 = _by_rule(rules.evaluate_primer(run5, c), "poly_run")
    soft5 = _by_rule(rules.evaluate_primer(run5, c), "poly_run_preferred")
    hard6 = _by_rule(rules.evaluate_primer(run6, c), "poly_run")
    ok = hard5.passed and hard5.severity == HARD
    ok &= not soft5.passed and soft5.severity == SOFT
    ok &= not hard6.passed
    ok &= rules.summarise(rules.evaluate_primer(run6, c))["usable"] is False
    return _check("poly-run: 5 allowed, 4 preferred, 6 unusable", ok)


def test_hard_failure_makes_unusable_soft_does_not() -> bool:
    """The core contract of the severity split."""
    c = rules.PCR
    # GC-poor: soft failures only, so still usable.
    at_rich = "ATATTTAATTATATTAATAT"
    soft_summary = rules.summarise(rules.evaluate_primer(at_rich, c))
    # A long homopolymer is a hard failure.
    hard_summary = rules.summarise(
        rules.evaluate_primer("GCATGCAAAAAAAGCATGCT", c))
    ok = soft_summary["soft_failures"] > 0 and soft_summary["usable"] is True
    ok &= hard_summary["hard_failures"] > 0 and hard_summary["usable"] is False
    return _check("hard failures disqualify, soft failures only penalise", ok,
                  f"soft usable={soft_summary['usable']} "
                  f"hard usable={hard_summary['usable']}")


# --- the dangerous case: uncomputed inputs -------------------------------
def test_uncomputed_structure_is_reported_not_hidden() -> bool:
    """
    A structure rule with no value must say "not computed".

    Silently passing would let the envelope claim a primer was checked for
    hairpins when nothing of the sort happened. That is the failure mode this
    whole design exists to prevent.
    """
    checks = rules.evaluate_primer(M13F21, rules.PCR)
    hairpin = _by_rule(checks, "hairpin_tm")
    ok = hairpin.value is None and hairpin.detail == "not computed"
    summary = rules.summarise(checks)
    ok &= summary["not_computed"] >= 1
    return _check("uncomputed structure checks are flagged, not hidden", ok,
                  f"not_computed={summary['not_computed']}")


def test_supplied_structure_values_are_applied() -> bool:
    c = rules.PCR
    good = rules.evaluate_primer(M13F21, c, hairpin_tm=20.0, self_dimer_tm=10.0)
    bad = rules.evaluate_primer(M13F21, c, hairpin_tm=60.0, self_dimer_tm=10.0)
    ok = _by_rule(good, "hairpin_tm").passed
    ok &= not _by_rule(bad, "hairpin_tm").passed
    ok &= rules.summarise(bad)["usable"] is False   # structure rules are HARD
    return _check("supplied hairpin/dimer values are evaluated", ok)


def test_end_stability_uses_magnitude() -> bool:
    """dG is negative; the rule is about how large it is, not its sign."""
    c = rules.PCR
    ok = _by_rule(rules.evaluate_primer(M13F21, c, end_stability_dg=-5.0),
                  "end_stability").passed
    ok &= not _by_rule(rules.evaluate_primer(M13F21, c, end_stability_dg=-12.0),
                       "end_stability").passed
    return _check("3' end stability compares magnitude, not sign", ok)


# --- degenerate primers ---------------------------------------------------
def test_degenerate_primer_reported_as_population() -> bool:
    """Tm must be judged over the whole set, never collapsed to one number."""
    checks = rules.evaluate_primer(DEGENERATE, rules.PCR)
    deg = _by_rule(checks, "degeneracy")
    tm = _by_rule(checks, "tm")
    ok = deg.value == 64 and not deg.passed
    ok &= "64" in tm.detail or "64" in deg.detail
    return _check("a degenerate primer is evaluated as a population", ok,
                  f"degeneracy={deg.value}, tm detail={tm.detail[:50]!r}")


def test_degenerate_pair_skips_tm_diff_honestly() -> bool:
    checks = rules.evaluate_pair(DEGENERATE, M13R, rules.PCR)
    diff = _by_rule(checks, "pair_tm_diff")
    ok = diff.value is None and "not computed" in diff.detail
    return _check("pair Tm difference is skipped, with a reason, when degenerate",
                  ok, diff.detail[:60])


# --- pairs ----------------------------------------------------------------
def test_pair_tm_difference() -> bool:
    c = rules.PCR
    matched = rules.evaluate_pair(M13F21, M13R, c)
    ok = _by_rule(matched, "pair_tm_diff").passed
    # A deliberately mismatched partner: short and AT-rich against GC-rich.
    mismatched = rules.evaluate_pair("GCGCGCGCGCGCGCGCGC", "ATATATATATATATATAT", c)
    ok &= not _by_rule(mismatched, "pair_tm_diff").passed
    return _check("pair Tm difference passes a real pair, fails a mismatched one",
                  ok)


def test_product_size_rule() -> bool:
    c = rules.PCR
    ok = _by_rule(rules.evaluate_pair(M13F21, M13R, c, product_size=500),
                  "product_size").passed
    ok &= not _by_rule(rules.evaluate_pair(M13F21, M13R, c, product_size=5000),
                       "product_size").passed
    # Sequencing mode has no product at all, so the rule should not appear.
    seq_checks = rules.evaluate_pair(M13F21, M13R, rules.SEQUENCING)
    ok &= not any(ch.rule == "product_size" for ch in seq_checks)
    return _check("product size enforced for PCR, absent for sequencing", ok)


# --- envelope shape -------------------------------------------------------
def test_checks_serialise() -> bool:
    """Every check must survive the trip into a JSON envelope."""
    import json
    checks = rules.evaluate_primer(M13F21, rules.PCR)
    payload = [c.as_dict() for c in checks]
    text = json.dumps(payload)
    back = json.loads(text)
    required = {"rule", "label", "severity", "passed", "value", "threshold",
                "source"}
    ok = all(required <= set(entry) for entry in back)
    ok &= all(entry["severity"] in (HARD, SOFT) for entry in back)
    ok &= all(entry["source"] for entry in back)   # every rule cites something
    return _check("checks serialise to JSON with a source on each", ok,
                  f"{len(back)} checks, {len(text)} bytes")


def test_rule_ids_unique_and_stable() -> bool:
    checks = rules.evaluate_primer(M13F21, rules.PCR)
    ids = [c.rule for c in checks]
    ok = len(ids) == len(set(ids))
    # The order must be stable so a diff between two runs is readable.
    again = [c.rule for c in rules.evaluate_primer(M13F21, rules.PCR)]
    ok &= ids == again
    return _check("rule ids are unique and ordering is stable", ok)


def main() -> int:
    tests = [
        test_mode_alias_normalises,
        test_unknown_mode_rejected,
        test_every_mode_has_constraints,
        test_longest_run,
        test_end_gc_count,
        test_real_primer_passes_cleanly,
        test_length_boundary,
        test_poly_run_boundary_and_severity,
        test_hard_failure_makes_unusable_soft_does_not,
        test_uncomputed_structure_is_reported_not_hidden,
        test_supplied_structure_values_are_applied,
        test_end_stability_uses_magnitude,
        test_degenerate_primer_reported_as_population,
        test_degenerate_pair_skips_tm_diff_honestly,
        test_pair_tm_difference,
        test_product_size_rule,
        test_checks_serialise,
        test_rule_ids_unique_and_stable,
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
