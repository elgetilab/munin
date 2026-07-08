"""B1 smoke test for the MiniCheck faithfulness judge.

Loads the real model and asserts the ordering property that makes it useful:
a claim entailed by the context scores HIGH, a fabricated/contradicted claim
scores LOW, on a toy document. Not a unit test of exact probabilities (those
are model-dependent) — a behavioural sanity gate.

    /opt/munin/services/pipeline/venv/bin/python \
        -m munin_bench.faithfulness.smoke_minicheck
"""

from __future__ import annotations

import sys

from .minicheck import MiniCheck, split_sentences

_CONTEXT = (
    "The James Webb Space Telescope was launched on 25 December 2021 from "
    "Kourou, French Guiana, aboard an Ariane 5 rocket. It observes primarily "
    "in the infrared and orbits the Sun near the second Lagrange point (L2), "
    "about 1.5 million kilometres from Earth."
)

# Hard gate: clearly entailed claims (SUP) and clearly off-context fabrications
# (UNS). These MUST separate — it's the property that makes the judge useful.
_CASES = [
    ("The James Webb Space Telescope launched in December 2021.", True),
    ("JWST observes mainly in the infrared.", True),
    ("The telescope orbits near the L2 point, 1.5 million km from Earth.", True),
    ("JWST is an ultraviolet telescope in low Earth orbit.", False),
    ("The telescope was launched from Cape Canaveral, Florida.", False),
]

# Known-hard: a single wrong DETAIL (year) on an otherwise-grounded claim.
# MiniCheck scores *support*, and the model card notes it does NOT target
# injected-error claims like date swaps — so this often scores mid/high. Reported
# for insight, NOT gated; quantifying this class is exactly Track B's B2/RAGTruth
# validation job, not the smoke's.
_KNOWN_HARD = [
    ("The James Webb Space Telescope was launched in 2018.", False),
]


def main() -> int:
    mc = MiniCheck()
    print(f"model on {mc.device}\n")
    ok = True
    scored = []
    for claim, exp in _CASES:
        p = mc.score_claim(_CONTEXT, claim)
        scored.append((claim, exp, p))
        flag = "PASS" if (p >= 0.5) == exp else "FAIL"
        if flag == "FAIL":
            ok = False
        print(f"[{flag}] support={p:.3f} expect={'SUP' if exp else 'UNS'}  {claim}")

    for claim, _ in _KNOWN_HARD:
        p = mc.score_claim(_CONTEXT, claim)
        print(f"[hard] support={p:.3f} (known weak spot: date swap)  {claim}")

    sup = [p for _, e, p in scored if e]
    uns = [p for _, e, p in scored if not e]
    margin = min(sup) - max(uns)
    print(f"\nmin(supported)={min(sup):.3f}  max(unsupported)={max(uns):.3f}  "
          f"separation margin={margin:+.3f}")

    # answer-level aggregation smoke
    ans = ("JWST launched in December 2021. It observes in the infrared. "
           "It was launched from Cape Canaveral.")
    res = mc.score_answer(ans, [_CONTEXT])
    print(f"\nscore_answer: n_claims={res['n_claims']} "
          f"mean={res['mean_support']:.3f} frac_supported={res['frac_supported']:.2f} "
          f"any_unsupported={res['any_unsupported']}")
    assert res["n_claims"] == 3, f"expected 3 claims, got {res['n_claims']}"
    assert res["any_unsupported"] is True, "the Cape Canaveral claim should be unsupported"

    print("\n" + ("SMOKE PASS" if ok and margin > 0 else "SMOKE FAIL"))
    return 0 if (ok and margin > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
