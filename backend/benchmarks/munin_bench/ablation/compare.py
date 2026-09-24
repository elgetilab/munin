"""Track D compare: paired accuracy + cost across bare / rag / agentic arms.

Reads ablation_runs/<tag>/{bare,rag,agentic}.json (same question set), aligns
by qid, and reports per-arm accuracy + cost with paired-bootstrap deltas (the
harness-value signal): bare->rag = value of retrieval, rag->agentic = value of
the agentic loop. The tag comes from --tag / MUNIN_ABLATION_TAG (see
ablation.runs_dir); the two-run old-vs-new comparison is pipelines.compare.

Per arm it also reports the unparseable breakdown (empty_content /
deadline_hit / letter_not_found) and the number of answers carrying raw
tool-call markup, so a tool-parser mismatch on a new backbone is visible as
what it is rather than folded into "accuracy".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter

from ..metrics.bootstrap import paired_bootstrap
from . import runs_dir

_HERE = os.path.dirname(__file__)


def _load(arm: str, runs: str) -> dict:
    return {r["qid"]: r for r in json.load(open(os.path.join(runs, f"{arm}.json")))["per_q"]}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def corpus_of(meta: dict) -> dict | None:
    """Hoist the per-arm corpus stamps to one scorecard-level field.

    Returns the shared stamp when every arm that recorded one agrees, so a
    reader does not have to dig through arm_meta to answer "same corpus?".
    Disagreement is legitimate rather than an error: the egress-off runs copy
    the bare and rag arms in from an earlier capture, and a C2b-style pair
    searches different corpora BY DESIGN. That case keeps both stamps and says
    so instead of silently picking one. `counted_at` is excluded from the
    comparison because two arms of the same run are counted minutes apart.
    """
    stamps = {a: m["corpus"] for a, m in meta.items() if m.get("corpus")}
    if not stamps:
        return None
    uniq = {json.dumps({k: v for k, v in c.items() if k != "counted_at"},
                       sort_keys=True) for c in stamps.values()}
    if len(uniq) == 1:
        return next(iter(stamps.values()))
    return {"note": "arms searched different corpora; see arm_meta",
            "per_arm": stamps}


def _cost(rows: list[dict]) -> dict:
    def mean(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
    return {
        "mean_prompt_tokens": mean("prompt_tokens"),
        "mean_completion_tokens": mean("completion_tokens"),
        "mean_elapsed_s": mean("elapsed_s"),
        "mean_tool_calls": mean("tool_calls"),
    }


def compare(arms=("bare", "rag", "agentic"), date: str | None = None,
            tag: str | None = None) -> dict:
    runs = runs_dir(tag)
    data = {a: _load(a, runs) for a in arms if os.path.exists(os.path.join(runs, f"{a}.json"))}
    arms = [a for a in arms if a in data]
    if not arms:
        raise SystemExit(f"[d:compare] no arm files under {runs}")
    qids = sorted(set.intersection(*[set(data[a]) for a in arms]))
    print(f"[d:compare] runs={runs} arms={arms} | paired questions={len(qids)}")

    per_arm = {}
    for a in arms:
        rows = [data[a][q] for q in qids]
        c = Counter(r["verdict"] for r in rows)
        attempted = c["correct"] + c["incorrect"]  # parseable, non-abstain
        reasons = Counter(r.get("reason", "unrecorded") for r in rows
                          if r["verdict"] == "unparseable")
        per_arm[a] = {
            "accuracy": c["correct"] / len(rows),
            "abstain_rate": c["abstain"] / len(rows),
            "precision_of_attempted": (c["correct"] / attempted) if attempted else None,
            "verdicts": dict(c),
            # Parser-vs-model split. Pre-2026-09-15 captures carry no reason
            # and report "unrecorded" rather than a guessed bucket.
            "unparseable_reasons": dict(reasons),
            # direct-vLLM arms only: why content was empty (truncated vs the
            # model ending its turn without a final message). Pre-2026-09-15
            # captures carry no kind.
            "empty_kinds": dict(Counter(r["empty_kind"] for r in rows if r.get("empty_kind"))),
            "tool_markup_in_content": sum(1 for r in rows if r.get("tool_markup_in_content")),
            "deadline_hits": sum(1 for r in rows if r.get("deadline_hit")),
            "cost": _cost(rows),
        }

    def corr(a):  # per-question 0/1 correctness in qid order
        return [1.0 if data[a][q]["verdict"] == "correct" else 0.0 for q in qids]

    def _pb(hi, lo):
        d = paired_bootstrap(corr(hi), corr(lo))
        return {"delta": round(d["mean_diff"], 4),
                "ci": [round(d["ci_low"], 4), round(d["ci_high"], 4)],
                "p": round(d["p_value_two_sided"], 4)}

    deltas = {}
    present = [a for a in ("bare", "rag", "agentic") if a in arms]
    for i in range(len(present) - 1):
        lo, hi = present[i], present[i + 1]
        deltas[f"{hi}_minus_{lo}"] = _pb(hi, lo)
    if "bare" in arms and "agentic" in arms:
        deltas["agentic_minus_bare"] = _pb("agentic", "bare")

    meta = {}
    for a in arms:
        mp = os.path.join(runs, f"{a}.meta.json")
        if os.path.exists(mp):
            meta[a] = json.load(open(mp))
    sc = {"track": "harness-ablation", "n_paired": len(qids), "git_sha": _git_sha(),
          "date": date, "runs_dir": os.path.relpath(runs, os.path.join(_HERE, "..", "..")),
          "arm_meta": meta, "per_arm": per_arm, "deltas": deltas}
    corpus = corpus_of(meta)
    if corpus:
        sc["corpus"] = corpus
    if date:
        out = os.path.join(_HERE, "..", "..", "scorecards", f"{date}_harness-ablation.json")
        json.dump(sc, open(out, "w"), indent=2)
        print(f"[d:compare] scorecard -> {out}")

    print("\n=== Track D — harness ablation ===")
    for a in arms:
        pa = per_arm[a]; co = pa["cost"]
        print(f"  {a:8s} acc={pa['accuracy']:.3f} abstain={pa['abstain_rate']:.3f} "
              f"| tok~{co['mean_completion_tokens']}c {co['mean_elapsed_s']}s "
              f"calls={co['mean_tool_calls']}"
              + (f" | unparseable={pa['unparseable_reasons']}" if pa["unparseable_reasons"] else "")
              + (f" | markup_leaks={pa['tool_markup_in_content']}" if pa["tool_markup_in_content"] else ""))
    print("  deltas (paired):")
    for k, v in deltas.items():
        print(f"    {k}: {v['delta']:+.3f} {v['ci']} p={v['p']}")
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--tag", default=None, help="ablation_runs/<tag>/ (or MUNIN_ABLATION_TAG)")
    args = ap.parse_args()
    compare(date=args.date, tag=args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
