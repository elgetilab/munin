"""
Tests for primer/select.py - candidate enumeration, scoring and pairing.

Layer 1 + layer 2 of the primer eval plan
(docs/agent-track/PRIMER-DESIGN-PLAN.md). Offline: reads the vendored vector
FASTAs under tests/data/primer/ and never fetches.

What these assert
-----------------
`select.py` composes four modules, so most of what can go wrong here is
integration, not arithmetic. The arithmetic is already pinned by
test_primer_thermo.py (validated against three independent implementations),
test_primer_structure.py and test_primer_verify.py (validated against known
amplicon sizes on real vectors).

So these tests target the seams:

  - every returned pair really does amplify, once, at the size claimed. This
    is the contract the tool envelope rests on, and it is checked here by
    re-running the in-silico PCR independently of the design path.
  - a product that must contain a target actually contains it
  - no returned primer carries an unexplained HARD rule failure
  - results are genuinely different designs, not one primer shifted by a base
  - the impossible cases return nothing instead of inventing something

The design-space caveat is pinned too: the cheap filter bounds the search at
the constraint window, which is STRICTER than the soft rules. That is
deliberate and documented in `_cheap_reject`, and
`test_search_window_is_a_hard_boundary` asserts it rather than leaving it to
be rediscovered.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_primer_select.py
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from primer import rules, select, thermo, verify  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "primer"


def _load(name: str) -> str:
    text = (DATA / name).read_text(encoding="utf-8")
    return "".join(l.strip() for l in text.splitlines()
                   if not l.startswith(">")).upper()


PUC19 = _load("pUC19_L09137.fasta")


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# --- enumeration ----------------------------------------------------------
def test_enumerate_respects_region() -> bool:
    lo, hi = 400, 700
    cands = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(lo, hi), evaluate_structure=False)
    ok = bool(cands)
    ok &= all(lo <= c.start and c.end <= hi for c in cands)
    return _check("enumerate_candidates stays inside its region", ok,
                  f"{len(cands)} candidates")


def test_forward_and_reverse_relate_to_template_correctly() -> bool:
    """
    The orientation trap. A forward primer reads off the plus strand as-is;
    a reverse primer is the reverse complement of its plus-strand footprint.
    """
    fwd = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(400, 700), evaluate_structure=False)
    rev = select.enumerate_candidates(
        PUC19, strand=verify.MINUS, region=(400, 700), evaluate_structure=False)
    ok = all(c.seq == PUC19[c.start:c.end] for c in fwd[:50])
    ok &= all(c.seq == thermo.reverse_complement(PUC19[c.start:c.end])
              for c in rev[:50])
    # And the 3' end is at opposite coordinates for the two strands.
    ok &= all(c.three_prime == c.end - 1 for c in fwd[:20])
    ok &= all(c.three_prime == c.start for c in rev[:20])
    return _check("forward/reverse candidates match the template correctly", ok)


def test_candidates_sorted_by_penalty() -> bool:
    cands = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(400, 900), evaluate_structure=False)
    ok = all(a.penalty <= b.penalty for a, b in zip(cands, cands[1:]))
    return _check("candidates come back best-first", ok, f"{len(cands)} candidates")


def test_no_candidate_has_a_hard_failure() -> bool:
    """A candidate that survives enumeration must be usable, by construction."""
    cands = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(400, 900), evaluate_structure=False)
    bad = [c for c in cands if not c.usable]
    return _check("every enumerated candidate is usable", not bad,
                  f"{len(bad)} of {len(cands)} carry a hard failure")


def test_search_window_is_a_hard_boundary() -> bool:
    """
    The documented asymmetry: the SEARCH is bounded by the constraint window
    even where the RULE is soft.

    GC content is a SOFT rule, yet no enumerated candidate falls outside the
    GC window, because `_cheap_reject` bounds the search there. Pinned so the
    behaviour is a decision on record rather than a surprise.
    """
    c = rules.PCR
    cands = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(0, 1200), constraints=c,
        evaluate_structure=False)
    gcs = [100.0 * thermo.gc_fraction(x.seq) for x in cands]
    ok = all(c.gc_min <= g <= c.gc_max for g in gcs)
    # and the rule itself is soft, which is the half that makes this a caveat
    gc_rule = [ch for ch in rules.evaluate_primer("GGGGCCCCGGGGCCCCGGGG", c)
               if ch.rule == "gc_content"][0]
    ok &= gc_rule.severity == rules.SOFT
    return _check("search is bounded at the GC window although the rule is soft",
                  ok, f"GC range of candidates: {min(gcs):.0f}-{max(gcs):.0f}%")


def test_widening_constraints_widens_the_search() -> bool:
    """The documented escape hatch has to actually work."""
    narrow = rules.PCR
    wide = replace(rules.PCR, gc_min=20.0, gc_max=80.0,
                   tm_min=45.0, tm_max=72.0)
    n_narrow = len(select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(0, 800), constraints=narrow,
        evaluate_structure=False))
    n_wide = len(select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(0, 800), constraints=wide,
        evaluate_structure=False))
    return _check("widening Constraints widens the candidate pool",
                  n_wide > n_narrow, f"{n_narrow} -> {n_wide}")


def test_structure_pool_limits_expensive_checks() -> bool:
    """
    Beyond the pool, structure checks are reported as not-computed rather
    than silently passed. That honesty is the whole reason rules.py has the
    'not computed' state.
    """
    cands = select.enumerate_candidates(
        PUC19, strand=verify.PLUS, region=(400, 900), structure_pool=5)
    if len(cands) < 10:
        return _check("structure pool limits expensive checks", False,
                      "not enough candidates to tell")
    first = [c for c in cands[0].checks if c.rule == "hairpin_tm"][0]
    last = [c for c in cands[-1].checks if c.rule == "hairpin_tm"][0]
    ok = first.value is not None and last.value is None
    ok &= last.detail == "not computed"
    return _check("structure checked for the pool, not-computed beyond it", ok)


# --- pairing: the contract ------------------------------------------------
def test_designed_pairs_actually_amplify() -> bool:
    """
    THE contract. Re-run the PCR independently of the design path and demand
    one product at the promised size.
    """
    pairs = select.design_pairs(PUC19, target=(400, 450),
                                product_range=(100, 400), circular=True, n=3)
    ok = len(pairs) == 3
    detail = []
    for p in pairs:
        result = verify.simulate_pcr(p.forward.seq, p.reverse.seq, PUC19,
                                     circular=True)
        if not result.is_single or result.product.length != p.product_size:
            ok = False
        detail.append(f"{p.product_size}bp")
    return _check("every designed pair amplifies once at the stated size", ok,
                  ", ".join(detail))


def test_product_contains_the_target() -> bool:
    target = (400, 450)
    pairs = select.design_pairs(PUC19, target=target,
                                product_range=(100, 400), circular=True, n=3)
    ok = bool(pairs)
    for p in pairs:
        ok &= p.forward.end <= target[0] and p.reverse.start >= target[1]
        ok &= PUC19[target[0]:target[1]] in p.amplicon
    return _check("the amplicon contains the requested target", ok)


def test_amplicon_is_internally_consistent() -> bool:
    """Length, coordinates and the sequence itself must all agree."""
    pairs = select.design_pairs(PUC19, target=(400, 450),
                                product_range=(100, 400), circular=True, n=3)
    ok = bool(pairs)
    for p in pairs:
        ok &= len(p.amplicon) == p.product_size
        ok &= p.product_size == p.reverse.end - p.forward.start
        ok &= p.amplicon.startswith(p.forward.seq)
        ok &= p.amplicon.endswith(thermo.reverse_complement(p.reverse.seq))
    return _check("amplicon length, coordinates and sequence agree", ok)


def test_pairs_are_usable_and_sorted() -> bool:
    pairs = select.design_pairs(PUC19, target=(400, 450),
                                product_range=(100, 400), circular=True, n=5)
    ok = all(p.usable for p in pairs)
    ok &= all(a.penalty <= b.penalty for a, b in zip(pairs, pairs[1:]))
    return _check("pairs are usable and ordered best-first", ok,
                  f"{len(pairs)} pairs")


def test_pairs_are_genuinely_distinct() -> bool:
    """
    Not the same primer shifted by one base.

    Without the 3'-end separation rule the top three results were one reverse
    primer at offsets n, n+1, n+2, which is a choice in appearance only.
    """
    d = select.DEFAULT_MIN_THREE_PRIME_DISTANCE
    pairs = select.design_pairs(PUC19, target=(400, 450),
                                product_range=(100, 400), circular=True, n=4)
    ok = len(pairs) >= 2
    for i, a in enumerate(pairs):
        for b in pairs[i + 1:]:
            close_f = abs(a.forward.three_prime - b.forward.three_prime) < d
            close_r = abs(a.reverse.three_prime - b.reverse.three_prime) < d
            if close_f and close_r:
                ok = False
    return _check("returned pairs are genuinely different designs", ok,
                  f"{len(pairs)} pairs, min 3' distance {d}")


def test_tm_difference_is_small_for_real_designs() -> bool:
    pairs = select.design_pairs(PUC19, target=(400, 450),
                                product_range=(100, 400), circular=True, n=3)
    ok = bool(pairs) and all(
        p.tm_difference <= rules.PCR.pair_tm_diff_max for p in pairs)
    worst = max((p.tm_difference for p in pairs), default=0.0)
    return _check("designed pairs are Tm-matched", ok, f"worst dTm {worst:.2f} C")


# --- the impossible cases -------------------------------------------------
def test_impossible_product_range_returns_nothing() -> bool:
    """No solution must be an empty list, never a fabricated design."""
    got = select.design_pairs(PUC19, product_range=(10, 20), n=3)
    return _check("an impossible product range returns no pairs", got == [],
                  f"got {len(got)}")


def test_hopeless_template_returns_nothing() -> bool:
    ok = select.best_pair("A" * 500) is None
    ok &= select.design_pairs("A" * 500, n=3) == []
    return _check("a poly-A template yields no pairs and no crash", ok)


def test_invalid_arguments_rejected() -> bool:
    ok = False
    try:
        select.design_pairs(PUC19, target=(10, 5))
    except ValueError:
        ok = True
    try:
        select.design_pairs(PUC19, product_range=(500, 100))
        ok = False
    except ValueError:
        pass
    try:
        select.design_pairs(PUC19, target=(0, 99999))
        ok = False
    except ValueError:
        pass
    try:
        select.enumerate_candidates(PUC19, strand="x")
        ok = False
    except ValueError:
        pass
    return _check("invalid target, range and strand are rejected", ok)


# --- envelope -------------------------------------------------------------
def test_pair_serialises() -> bool:
    import json
    pair = select.best_pair(PUC19, target=(400, 450),
                            product_range=(100, 400), circular=True)
    ok = pair is not None
    if ok:
        payload = json.loads(json.dumps(pair.as_dict()))
        ok &= {"forward", "reverse", "product_size", "checks",
               "verified"} <= set(payload)
        ok &= payload["verified"].get("outcome") == "single_product"
        ok &= all("source" in c for c in payload["checks"])
    return _check("a pair serialises into the envelope shape", ok)


def main() -> int:
    tests = [
        test_enumerate_respects_region,
        test_forward_and_reverse_relate_to_template_correctly,
        test_candidates_sorted_by_penalty,
        test_no_candidate_has_a_hard_failure,
        test_search_window_is_a_hard_boundary,
        test_widening_constraints_widens_the_search,
        test_structure_pool_limits_expensive_checks,
        test_designed_pairs_actually_amplify,
        test_product_contains_the_target,
        test_amplicon_is_internally_consistent,
        test_pairs_are_usable_and_sorted,
        test_pairs_are_genuinely_distinct,
        test_tm_difference_is_small_for_real_designs,
        test_impossible_product_range_returns_nothing,
        test_hopeless_template_returns_nothing,
        test_invalid_arguments_rejected,
        test_pair_serialises,
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
