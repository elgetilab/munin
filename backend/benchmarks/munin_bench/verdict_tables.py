"""Per-question verdict tables for the Track C2b, Track D and faithfulness scorecards.

The scorecards for these tracks publish aggregates only (Track D, C2b) or carry
per-question values inside a large JSON (faithfulness). The per-question run
files they were computed from (ablation_runs/, c2_runs/) are gitignored because
they hold model answers and retrieved passages. This module extracts the
minimum needed to regenerate every aggregate and paired test: qid, arm or
condition, verdict, chosen letter, and for faithfulness the per-item score.
No question text, answer text, passages, model output or DOIs are written.

    python -m munin_bench.verdict_tables build    # run files -> verdicts/*.csv
    python -m munin_bench.verdict_tables verify   # verdicts/*.csv -> scorecard numbers

`verify` reads ONLY the CSVs (and the scorecard JSONs it compares against), so
it runs from a release checkout without any run directory.

Row order matters for bit-exact CIs in two places, and the tables preserve it:
C2b (run_c2 iterates the verdict file's dict order, which is completion order)
and faithfulness (the per-arm bootstrap runs over scored rows in capture
order). Point estimates and Track D statistics are order-independent.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

from .metrics.bootstrap import paired_bootstrap, single_bootstrap

_BENCH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SC = os.path.join(_BENCH, "scorecards")
_OUT = os.path.join(_BENCH, "verdicts")
_ABL = os.path.join(_BENCH, "ablation_runs")
_C2 = os.path.join(_BENCH, "c2_runs")

# ---------------------------------------------------------------------------
# Which run files produced which scorecard. Established by recomputing every
# scorecard from these files (see verify); the bare and rag arms of the
# egress-off and recapture runs are copies of the named full run's arms.
# ---------------------------------------------------------------------------

TRACK_D = {
    "2026-08-26_harness-ablation": {
        "bare": "qwen38-27b/bare.json", "rag": "qwen38-27b/rag.json",
        "agentic": "qwen38-27b/agentic.2026-08-26-egressfull.json"},
    "2026-09-15_harness-ablation-agentic-egressoff": {
        "bare": "qwen38-27b/bare.json", "rag": "qwen38-27b/rag.json",
        "agentic": "qwen38-27b/agentic.2026-09-15-egressoff.json"},
    "2026-09-16_harness-ablation-gpt-oss-20b": {
        a: f"gpt-oss-20b/{a}.json" for a in ("bare", "rag", "agentic")},
    "2026-09-16_gpt-oss-20b-prerepair": {
        a: f"gpt-oss-20b-prerepair/{a}.json" for a in ("bare", "rag", "agentic")},
    "2026-09-16_harness-ablation-qwen38-27b-recapture": {
        a: f"qwen38-27b-recapture/{a}.json" for a in ("rag", "agentic")},
    "2026-09-16_harness-ablation-gpt-oss-20b-recapture": {
        a: f"gpt-oss-20b-recapture/{a}.json" for a in ("rag", "agentic")},
    "2026-09-17_harness-ablation-qwen3.6-35b-a3b": {
        a: f"qwen3.6-35b-a3b/{a}.json" for a in ("bare", "rag", "agentic")},
    "2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b": {
        a: f"qwen3.6-35b-a3b-egressoff/{a}.json" for a in ("bare", "rag", "agentic")},
    "2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b": {
        a: f"gpt-oss-20b-egressoff/{a}.json" for a in ("bare", "rag", "agentic")},
}

# scorecard -> (present file, absent file) under c2_runs/
C2B = {
    "2026-07-10_abstention-c2-shadow": (
        "present.verdicts.2026-07-10.json", "absent.verdicts.2026-07-10.json"),
    "2026-07-27_abstention-c2-shadow": (
        "present.verdicts.2026-07-27.json", "absent.verdicts.2026-07-27.json"),
    "2026-07-27_abstention-c2-shadow-egressfull": (
        "present.verdicts.egressfull.json", "absent.verdicts.egressfull.json"),
    "2026-09-15_abstention-c2-shadow": (
        "present.verdicts.json", "absent.verdicts.json"),
    "2026-09-16_abstention-c2-shadow-gpt-oss-20b": (
        "gpt-oss-20b/present.verdicts.json", "gpt-oss-20b/absent.verdicts.json"),
    "2026-09-17_abstention-c2-shadow-qwen3.6-35b-a3b": (
        "qwen3.6-35b-a3b/present.verdicts.json", "qwen3.6-35b-a3b/absent.verdicts.json"),
}
# scorecard key holding the question-paired delta -> the earlier pair's table
C2B_VS = {
    "2026-07-27_abstention-c2-shadow": (
        "correct_abstention_vs_2026-07-10", "2026-07-10_abstention-c2-shadow"),
    "2026-09-15_abstention-c2-shadow": (
        "correct_abstention_vs_2026-07-27", "2026-07-27_abstention-c2-shadow"),
    "2026-09-16_abstention-c2-shadow-gpt-oss-20b": (
        "correct_abstention_vs_c2_runs", "2026-09-15_abstention-c2-shadow"),
    "2026-09-17_abstention-c2-shadow-qwen3.6-35b-a3b": (
        "correct_abstention_vs_c2_runs", "2026-09-15_abstention-c2-shadow"),
}

# Per-arm faithfulness: the per-question values are in the scorecard itself.
FAITH_ARM = [
    "2026-07-27_harness-ablation-faithfulness",
    "2026-08-26_harness-ablation-faithfulness",
    "2026-09-16_harness-ablation-faithfulness-qwen38-recapture",
    "2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture",
    "2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b",
]
# Single-arm live faithfulness (Track B3/B4): per_q list in the scorecard.
FAITH_LIVE = [
    "2026-07-09_faithfulness-agentic-live",
    "2026-07-09_faithfulness-agentic-live-t1a",
    "2026-07-09_faithfulness-agentic-live-cap",
]

# Cited but not reproducible from the files on this machine.
NOT_REPRODUCIBLE = {
    "2026-07-27_harness-ablation": (
        "Qwen3.6 July arms overwritten: the agentic array by the 08-26 Qwen3.8 run "
        "(ablation_runs/qwen38-27b/agentic.json now scores 174/15/10, the "
        "scorecard says 167/15/17) and the bare/rag arrays by the 08-25 Qwen3.8 "
        "arms, before the per-tag layout existed"),
    "2026-07-26_harness-ablation": (
        "agentic arm survives (agentic_2026-07-26_degraded.json reproduces "
        "137/46/14/2 exactly) but its bare and rag arms are the same overwritten "
        "July Qwen3.6 arrays, so the paired deltas cannot be recomputed"),
}


def _path(name: str) -> str:
    return os.path.join(_OUT, f"{name}.verdicts.csv")


def _sc(name: str) -> dict:
    return json.load(open(os.path.join(_SC, f"{name}.json")))


def _letter(v) -> str:
    return "" if v is None else str(v)


def _write(name: str, header: list[str], rows: list[list]) -> None:
    os.makedirs(_OUT, exist_ok=True)
    with open(_path(name), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _read(name: str) -> list[dict]:
    with open(_path(name), newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build() -> None:
    for name, arms in TRACK_D.items():
        rows = []
        for arm, rel in arms.items():
            for r in json.load(open(os.path.join(_ABL, rel)))["per_q"]:
                rows.append([r["qid"], arm, r["verdict"], _letter(r.get("letter"))])
        _write(name, ["qid", "arm", "verdict", "letter"], rows)
    for name, (pf, af) in C2B.items():
        rows = []
        for cond, rel in (("present", pf), ("absent", af)):
            for qid, r in json.load(open(os.path.join(_C2, rel))).items():
                assert r["qid"] == qid
                rows.append([qid, cond, r["verdict"], _letter(r.get("letter"))])
        _write(name, ["qid", "condition", "verdict", "letter"], rows)
    for name in FAITH_ARM:
        pq = _sc(name)["per_question"]
        rows = [[qid, arm, repr(float(v))] for arm in ("rag", "agentic")
                for qid, v in pq.get(arm, {}).items()]
        _write(name, ["qid", "arm", "frac_supported"], rows)
    for name in FAITH_LIVE:
        rows = [[r["qid"], r["n_claims"], repr(float(r["mean_support"])) if r["n_claims"] else "",
                 repr(float(r["frac_supported"])) if r["n_claims"] else "",
                 int(bool(r["fully_supported"])) if r["n_claims"] else ""]
                for r in _sc(name)["per_q"]]
        _write(name, ["qid", "n_claims", "mean_support", "frac_supported", "fully_supported"], rows)
    print(f"[verdicts] wrote {len(TRACK_D) + len(C2B) + len(FAITH_ARM) + len(FAITH_LIVE)} tables -> {_OUT}")


# ---------------------------------------------------------------------------
# verify (CSV only)
# ---------------------------------------------------------------------------

def load_arms(name: str) -> dict[str, dict[str, dict]]:
    """{arm: {qid: {"verdict", "letter"}}} from a Track D or C2b table, in file order."""
    out: dict[str, dict[str, dict]] = {}
    for r in _read(name):
        arm = r.get("arm") or r.get("condition")
        out.setdefault(arm, {})[r["qid"]] = {"verdict": r["verdict"], "letter": r["letter"] or None}
    return out


def correct_vector(arm: dict[str, dict], qids: list[str]) -> list[float]:
    return [1.0 if arm[q]["verdict"] == "correct" else 0.0 for q in qids]


def _pb_rounded(hi: list[float], lo: list[float]) -> dict:
    d = paired_bootstrap(hi, lo)
    return {"delta": round(d["mean_diff"], 4),
            "ci": [round(d["ci_low"], 4), round(d["ci_high"], 4)],
            "p": round(d["p_value_two_sided"], 4)}


def _trackd_recompute(data: dict[str, dict]) -> tuple[list[str], dict, dict]:
    """Mirror of ablation.compare: per-arm rates over the paired qids, paired deltas."""
    qids = sorted(set.intersection(*[set(v) for v in data.values()]))
    per_arm = {}
    for a, rows in data.items():
        c = Counter(rows[q]["verdict"] for q in qids)
        att = c["correct"] + c["incorrect"]
        per_arm[a] = {"accuracy": c["correct"] / len(qids),
                      "abstain_rate": c["abstain"] / len(qids),
                      "precision_of_attempted": (c["correct"] / att) if att else None,
                      "verdicts": dict(c)}
    deltas = {}
    present = [a for a in ("bare", "rag", "agentic") if a in data]
    for i in range(len(present) - 1):
        lo, hi = present[i], present[i + 1]
        deltas[f"{hi}_minus_{lo}"] = _pb_rounded(correct_vector(data[hi], qids),
                                                 correct_vector(data[lo], qids))
    if "bare" in data and "agentic" in data:
        deltas["agentic_minus_bare"] = _pb_rounded(correct_vector(data["agentic"], qids),
                                                   correct_vector(data["bare"], qids))
    return qids, per_arm, deltas


class Check:
    def __init__(self, name: str):
        self.name, self.n, self.fails = name, 0, []

    def eq(self, label: str, got, want, tol: float = 0.0) -> None:
        self.n += 1
        if isinstance(want, float) or isinstance(got, float):
            ok = got is not None and want is not None and abs(float(got) - float(want)) <= tol
        else:
            ok = got == want
        if not ok:
            self.fails.append(f"{label}: table={got!r} scorecard={want!r}")


def verify_trackd(name: str) -> Check:
    ck = Check(name)
    sc = _sc(name)
    data = load_arms(name)
    qids, per_arm, deltas = _trackd_recompute(data)
    if "tasks" in sc:  # run_all shape (the pre-repair gpt-oss file)
        ck.eq("n_paired", len(qids), 199)
        for a, m in sc["tasks"]["ablation"].items():
            mt = m["metrics"]
            ck.eq(f"{a}.accuracy", per_arm[a]["accuracy"], mt["accuracy"]["mean"])
            ck.eq(f"{a}.abstain_rate", per_arm[a]["abstain_rate"], mt["abstain_rate"]["mean"])
            ck.eq(f"{a}.precision", per_arm[a]["precision_of_attempted"], mt["precision"]["mean"])
            ck.eq(f"{a}.unparseable", float(per_arm[a]["verdicts"].get("unparseable", 0)),
                  mt["unparseable"]["mean"])
        for k, v in sc["meta"]["ablation_deltas"].items():
            ck.eq(f"delta.{k}", deltas[k], v)
        return ck
    ck.eq("n_paired", len(qids), sc["n_paired"])
    for a, m in sc["per_arm"].items():
        for k in ("accuracy", "abstain_rate", "precision_of_attempted", "verdicts"):
            ck.eq(f"{a}.{k}", per_arm[a][k], m[k])
    for k, v in sc["deltas"].items():
        ck.eq(f"delta.{k}", deltas[k], v)
    return ck


def full_vs_off(full: dict, off: dict, *, file_order: bool = False) -> dict:
    """agentic(full) - agentic(off), question-paired, plus verdict transitions.

    run_suite.sh (the 09-17 pairs) pairs in sorted-qid order. The 2026-09-15
    block was computed ad hoc in the arm files' own row order, which moves the
    CI by up to 0.005 (same delta and p); `file_order` reproduces it."""
    qids = [q for q in full if q in off] if file_order else sorted(set(full) & set(off))
    d = paired_bootstrap(correct_vector(full, qids), correct_vector(off, qids))
    trans = Counter(f"{full[q]['verdict']}->{off[q]['verdict']}" for q in qids)
    return {"n": len(qids), "boot": d, "transitions": dict(trans)}


def verify_egress_pairs() -> list[Check]:
    """The full-minus-off deltas the paper cites, recomputed across two tables."""
    out = []
    ck = Check("2026-09-15_harness-ablation-agentic-egressoff:paired_vs_egress_full")
    want = _sc("2026-09-15_harness-ablation-agentic-egressoff")["paired_vs_egress_full"]
    r = full_vs_off(load_arms("2026-08-26_harness-ablation")["agentic"],
                    load_arms("2026-09-15_harness-ablation-agentic-egressoff")["agentic"],
                    file_order=True)
    w = want["agentic_full_minus_agentic_off"]
    ck.eq("delta", round(r["boot"]["mean_diff"], 3), w["delta"])
    ck.eq("ci", [round(r["boot"]["ci_low"], 3), round(r["boot"]["ci_high"], 3)], w["ci"])
    ck.eq("p", round(r["boot"]["p_value_two_sided"], 3), w["p"])
    ck.eq("n", r["n"], w["n"])
    ck.eq("transitions", r["transitions"], want["transitions_full_to_off"])
    out.append(ck)
    # The 09-17 egress-off scorecards do not carry this block; the paper's
    # +0.166 / +0.080 are checked against the cited values (3 d.p.).
    for full_t, off_t, cited in (
            ("2026-09-17_harness-ablation-qwen3.6-35b-a3b",
             "2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b",
             (0.166, 0.101, 0.226, 0.0)),
            ("2026-09-16_harness-ablation-gpt-oss-20b",
             "2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b",
             (0.080, 0.015, 0.146, 0.02))):
        ck = Check(f"{off_t}:full_minus_off (05-RESULTS)")
        r = full_vs_off(load_arms(full_t)["agentic"], load_arms(off_t)["agentic"])
        b = r["boot"]
        ck.eq("delta/ci/p (3 d.p.)",
              tuple(round(x, 3) for x in (b["mean_diff"], b["ci_low"], b["ci_high"],
                                          b["p_value_two_sided"])), cited)
        out.append(ck)
    return out


def _deep_eq(ck: Check, label: str, got, want) -> None:
    if isinstance(want, dict):
        for k, v in want.items():
            _deep_eq(ck, f"{label}.{k}", got.get(k) if isinstance(got, dict) else None, v)
    else:
        ck.eq(label, got, want)


def verify_c2(name: str) -> Check:
    from .abstention.run_c2 import _correct_abstention_delta, _paired
    ck = Check(name)
    sc = _sc(name)
    arms = load_arms(name)
    got = _paired(arms["present"], arms["absent"], sc.get("date"), git_sha="-")
    for k in ("n_paired", "present", "absent", "answerable_n", "on_answerable_when_source_removed"):
        _deep_eq(ck, k, got[k], sc[k])
    if name in C2B_VS:
        key, old = C2B_VS[name]
        o = load_arms(old)
        d = _correct_abstention_delta((o["present"], o["absent"]), (arms["present"], arms["absent"]))
        _deep_eq(ck, key, d, sc[key])
    return ck


def verify_faith_arm(name: str) -> Check:
    ck = Check(name)
    sc = _sc(name)
    per: dict[str, dict[str, float]] = {}
    for r in _read(name):
        per.setdefault(r["arm"], {})[r["qid"]] = float(r["frac_supported"])
    for arm in ("rag", "agentic"):
        want = sc["frac_claims_supported"].get(arm)
        got = single_bootstrap(list(per[arm].values())) if per.get(arm) else None
        _deep_eq(ck, f"frac_claims_supported.{arm}", got, want)
    shared = sorted(set(per["rag"]) & set(per["agentic"]))
    d = paired_bootstrap([per["agentic"][q] for q in shared], [per["rag"][q] for q in shared])
    _deep_eq(ck, "paired_agentic_minus_rag", d, sc["paired_agentic_minus_rag"])
    return ck


def verify_faith_live(name: str) -> Check:
    ck = Check(name)
    sc = _sc(name)
    rows = _read(name)
    scored = [r for r in rows if int(r["n_claims"]) > 0]
    ck.eq("n_answers", len(rows), sc["n_answers"])
    ck.eq("n_scored", len(scored), sc["n_scored"])
    _deep_eq(ck, "mean_faithfulness",
             single_bootstrap([float(r["mean_support"]) for r in scored]), sc["mean_faithfulness"])
    _deep_eq(ck, "frac_claims_supported",
             single_bootstrap([float(r["frac_supported"]) for r in scored]), sc["frac_claims_supported"])
    ff = single_bootstrap([float(r["fully_supported"]) for r in scored])
    _deep_eq(ck, "frac_fully_supported", ff, sc["frac_fully_supported"])
    ck.eq("frac_any_unsupported", 1.0 - ff["mean"], sc["frac_any_unsupported"])
    ck.eq("mean_claims_per_answer", sum(int(r["n_claims"]) for r in scored) / len(scored),
          sc["mean_claims_per_answer"], tol=1e-12)
    return ck


def cross_backbone() -> list[Check]:
    """Question-paired Qwen3.6 (09-17) minus Qwen3.8, as cited in PAPER.md claim 1/2."""
    q36 = load_arms("2026-09-17_harness-ablation-qwen3.6-35b-a3b")
    q38 = load_arms("2026-08-26_harness-ablation")
    q38r = load_arms("2026-09-16_harness-ablation-qwen38-27b-recapture")
    out = []
    for label, a, b, cited, p_cited in (
            ("agentic Qwen3.6 - Qwen3.8 08-26", q36["agentic"], q38["agentic"], (-0.005, -0.050, 0.040), "0.93"),
            ("agentic Qwen3.6 - Qwen3.8 09-16 recapture", q36["agentic"], q38r["agentic"], (0.000, -0.040, 0.040), "1.00"),
            ("bare Qwen3.6 - Qwen3.8", q36["bare"], q38["bare"], (-0.050, -0.126, 0.030), "0.25"),
            ("rag Qwen3.6 - Qwen3.8", q36["rag"], q38["rag"], (-0.085, -0.141, -0.035), "0.002"),
            ("agentic Qwen3.8 recapture - 08-26", q38r["agentic"], q38["agentic"], (-0.005, -0.050, 0.040), "0.89")):
        qids = sorted(set(a) & set(b))
        d = paired_bootstrap(correct_vector(a, qids), correct_vector(b, qids))
        ck = Check(f"cross-backbone: {label} (n={len(qids)})")
        ck.eq("delta/ci (3 d.p.)", tuple(round(d[k], 3) for k in ("mean_diff", "ci_low", "ci_high")), cited)
        ck.eq("p (cited precision)", round(d["p_value_two_sided"], len(p_cited.split(".")[1])), float(p_cited))
        ck.detail = d
        out.append(ck)
    # Faithfulness, agentic arm, Qwen3.6 (09-17) - Qwen3.8 recapture: +0.088 [+0.037, +0.137], p < 0.001
    f36 = {r["qid"]: float(r["frac_supported"]) for r in _read("2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b") if r["arm"] == "agentic"}
    f38 = {r["qid"]: float(r["frac_supported"]) for r in _read("2026-09-16_harness-ablation-faithfulness-qwen38-recapture") if r["arm"] == "agentic"}
    qids = sorted(set(f36) & set(f38))
    d = paired_bootstrap([f36[q] for q in qids], [f38[q] for q in qids])
    ck = Check(f"cross-backbone: faithfulness agentic Qwen3.6 - Qwen3.8 recapture (n={len(qids)})")
    ck.eq("delta/ci (3 d.p.)", (round(d["mean_diff"], 3), round(d["ci_low"], 3), round(d["ci_high"], 3)),
          (0.088, 0.037, 0.137))
    ck.eq("p < 0.001", d["p_value_two_sided"] < 0.001, True)
    ck.detail = d
    out.append(ck)
    return out


def verify() -> int:
    checks: list[Check] = []
    checks += [verify_trackd(n) for n in TRACK_D]
    checks += verify_egress_pairs()
    checks += [verify_c2(n) for n in C2B]
    checks += [verify_faith_arm(n) for n in FAITH_ARM]
    checks += [verify_faith_live(n) for n in FAITH_LIVE]
    checks += cross_backbone()
    bad = 0
    for ck in checks:
        status = "OK  " if not ck.fails else "FAIL"
        bad += bool(ck.fails)
        extra = ""
        if getattr(ck, "detail", None):
            d = ck.detail
            extra = (f"  [{d['mean_diff']:+.4f} ({d['ci_low']:+.4f}, {d['ci_high']:+.4f}) "
                     f"p={d['p_value_two_sided']:.3f}]")
        print(f"{status} {ck.name}: {ck.n} values{extra}")
        for f in ck.fails:
            print(f"       {f}")
    for n, why in NOT_REPRODUCIBLE.items():
        print(f"SKIP {n}: {why}")
    print(f"\n{len(checks) - bad}/{len(checks)} checks reproduce exactly")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "verify"])
    a = ap.parse_args()
    if a.cmd == "build":
        build()
        return 0
    return verify()


if __name__ == "__main__":
    sys.exit(main())
