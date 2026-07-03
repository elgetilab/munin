"""Track E — diff two scorecards with paired-bootstrap significance.

    python -m munin_bench.pipelines.compare <old>.json <new>.json

For every task/system/metric present in both, reports the delta of means and,
where per-query arrays exist for the SAME qids in both runs, a paired bootstrap
(mean diff, 95% CI, p). This is the before/after check for a model or encoder
swap: same queries, so the pairing is valid and the p-value is meaningful.
"""

from __future__ import annotations

import argparse
import json

from ..metrics import paired_bootstrap


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def compare(a: dict, b: dict) -> dict:
    rows = []
    for task in sorted(set(a["tasks"]) & set(b["tasks"])):
        sa, sb = a["tasks"][task], b["tasks"][task]
        for system in sorted(set(sa) & set(sb)):
            ma = sa[system]["metrics"]
            mb = sb[system]["metrics"]
            pqa = sa[system].get("per_query", {})
            pqb = sb[system].get("per_query", {})
            shared_qids = sorted(set(pqa) & set(pqb))
            for metric in sorted(set(ma) & set(mb)):
                mean_a = ma[metric]["mean"]
                mean_b = mb[metric]["mean"]
                row = {"task": task, "system": system, "metric": metric,
                       "mean_a": mean_a, "mean_b": mean_b,
                       "delta": mean_b - mean_a}
                # paired bootstrap where both runs have this metric per-query
                arr_a = [pqa[q][metric] for q in shared_qids if metric in pqa.get(q, {})]
                arr_b = [pqb[q][metric] for q in shared_qids if metric in pqb.get(q, {})]
                if len(arr_a) == len(arr_b) and len(arr_a) >= 2:
                    # note: b - a to match "new minus old"
                    pb = paired_bootstrap(arr_b, arr_a)
                    row["paired_ci"] = [pb["ci_low"], pb["ci_high"]]
                    row["p_value"] = pb["p_value_two_sided"]
                    row["n_paired"] = pb["n"]
                rows.append(row)
    return {"a": a["meta"], "b": b["meta"], "rows": rows}


def _fmt(diff) -> str:
    ma, mb = diff["a"], diff["b"]
    out = [
        "# Scorecard compare",
        "",
        f"- **A (old)** `{ma.get('tag')}` — model `{ma.get('model')}` "
        f"encoder `{ma.get('encoder')}` git `{ma.get('git_sha')}`",
        f"- **B (new)** `{mb.get('tag')}` — model `{mb.get('model')}` "
        f"encoder `{mb.get('encoder')}` git `{mb.get('git_sha')}`",
        "",
        "| task | system | metric | A | B | Δ (B−A) | 95% CI | p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in diff["rows"]:
        ci = (f"[{r['paired_ci'][0]:.3f}, {r['paired_ci'][1]:.3f}]"
              if "paired_ci" in r else "—")
        p = f"{r['p_value']:.3f}" if "p_value" in r else "—"
        star = " *" if r.get("p_value", 1) < 0.05 else ""
        out.append(f"| {r['task']} | {r['system']} | {r['metric']} | "
                   f"{r['mean_a']:.4f} | {r['mean_b']:.4f} | {r['delta']:+.4f}{star} "
                   f"| {ci} | {p} |")
    out += ["", "`*` = paired bootstrap significant at p<0.05 (same queries)."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json", action="store_true", help="emit JSON not markdown")
    args = ap.parse_args()
    diff = compare(_load(args.old), _load(args.new))
    print(json.dumps(diff, indent=2) if args.json else _fmt(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
