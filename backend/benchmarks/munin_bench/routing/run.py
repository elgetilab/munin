"""Routing-eval runner — drives the seed set against the live chat harness,
aggregates over N reps, and writes a rep-aware scorecard.

A0 use (KICKOFF-QUESTIONS Q7, A0-PLAN): produce the pre-migration baseline
scorecard the later Part-A steps regress against. NOT a paper number.

Usage:
    python -m routing.run --reps 8 --tag routing-pre-migration --seed 42
    python -m routing.run --reps 1 --only weather_with_location   # smoke

Reads:
    BASE  (--base / env RETRIEVAL_BASE, default http://127.0.0.1:8080)
Writes:
    scorecards/<date>_<tag>.json   and   .md twin
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
from datetime import datetime, timezone

import httpx

from .routing_eval import (
    A0_PERSONA,
    SEED_ITEMS,
    ItemResult,
    RoutingEvalItem,
    items_for_tier,
    run_item,
)

DEFAULT_BASE = os.environ.get("RETRIEVAL_BASE", "http://127.0.0.1:8080")
HTTP_TIMEOUT = 600.0
SCORECARD_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "scorecards"
)


# --- header / provenance -------------------------------------------------

def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _package_versions() -> dict:
    out = {}
    for pkg in ("httpx", "pydantic"):
        try:
            out[pkg] = __import__(pkg).__version__
        except Exception:
            out[pkg] = "unknown"
    return out


def make_header(tag: str, reps: int, seed: int, base: str, persona: str) -> dict:
    if persona == A0_PERSONA:
        policy = f"{persona} (A0 decision A; not expected.profile)"
    else:
        policy = f"{persona} (test-config persona override)"
    return {
        "tag": tag,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "model": os.getenv("VLLM_MODEL_NAME", "unknown"),
        "persona_policy": policy,
        "base_url": base,
        "reps": reps,
        "seed": seed,
        "python_version": sys.version.split()[0],
        "package_versions": _package_versions(),
        "note": "PRE-MIGRATION regression baseline. Not a paper result.",
    }


# --- aggregation ---------------------------------------------------------

def _flip_rate(passes: list[bool]) -> float:
    """Fraction of adjacent rep-pairs whose pass/fail differs — the routing-
    stability signal. 0 = perfectly stable, ->1 = thrashing."""
    if len(passes) < 2:
        return 0.0
    flips = sum(1 for a, b in zip(passes, passes[1:]) if a != b)
    return flips / (len(passes) - 1)


def _ci95(values: list[float]) -> tuple[float, float]:
    """Normal-approx 95% CI on a mean (rough; the per-item n is small).
    Phase-1's paired bootstrap supersedes this for paper numbers; A0 only
    needs an honest spread on the aggregate pass rate."""
    if len(values) < 2:
        v = values[0] if values else 0.0
        return (v, v)
    mean = statistics.mean(values)
    sd = statistics.pstdev(values)
    half = 1.96 * sd / (len(values) ** 0.5)
    return (max(0.0, mean - half), min(1.0, mean + half))


def aggregate(
    per_item_reps: dict[str, list[ItemResult]],
    reps: int,
    category_by_id: dict[str, str] | None = None,
) -> dict:
    category_by_id = category_by_id or {}
    per_item = {}
    item_mean_pass = []
    for item_id, results in per_item_reps.items():
        passes = [r.passed for r in results]
        pass_rate = sum(passes) / len(passes) if passes else 0.0
        item_mean_pass.append(pass_rate)
        # union of checks seen across reps, with per-check pass rate
        check_keys = sorted({k for r in results for k in r.checks})
        checks_pass_rate = {
            k: round(
                sum(1 for r in results if r.checks.get(k)) / len(results), 3
            )
            for k in check_keys
        }
        sample_failures = [
            {"rep": i, "failures": r.failures}
            for i, r in enumerate(results)
            if not r.passed and r.failures
        ][:3]
        # Non-gating diagnostics (A2 Q4), reported separately so they're
        # never mistaken for the gate. Per-key pass rate across reps.
        diag_keys = sorted({k for r in results for k in r.diagnostics})
        diagnostics_rate = {
            k: round(
                sum(1 for r in results if r.diagnostics.get(k)) / len(results), 3
            )
            for k in diag_keys
        }
        category = category_by_id.get(
            item_id,
            next((it.category for it in SEED_ITEMS if it.id == item_id), "?"),
        )
        per_item[item_id] = {
            "category": category,
            "pass_rate": f"{sum(passes)}/{len(passes)}",
            "pass_fraction": round(pass_rate, 3),
            "flip_rate": round(_flip_rate(passes), 3),
            "checks_pass_rate": checks_pass_rate,
            "diagnostics_rate": diagnostics_rate,
            "sample_failures": sample_failures,
        }
    ci_low, ci_high = _ci95(item_mean_pass)
    aggregate_block = {
        "n_items": len(per_item_reps),
        "mean_pass_rate": round(
            statistics.mean(item_mean_pass) if item_mean_pass else 0.0, 3
        ),
        "ci95_low": round(ci_low, 3),
        "ci95_high": round(ci_high, 3),
        "mean_flip_rate": round(
            statistics.mean([per_item[i]["flip_rate"] for i in per_item])
            if per_item else 0.0, 3
        ),
    }
    # Per-category roll-up (A5): mean + spread of per-item pass fractions in
    # each category. The phrasing-robustness signal — a category whose mean
    # sits well below 1.0 with high spread has brittle wordings to tune.
    by_cat: dict[str, list[float]] = {}
    for item_id, d in per_item.items():
        by_cat.setdefault(d["category"], []).append(d["pass_fraction"])
    by_category = {
        cat: {
            "n_items": len(fracs),
            "mean_pass": round(statistics.mean(fracs), 3),
            "min_pass": round(min(fracs), 3),
            "stdev": round(statistics.pstdev(fracs), 3) if len(fracs) > 1 else 0.0,
        }
        for cat, fracs in sorted(by_cat.items())
    }
    return {
        "aggregate": aggregate_block,
        "by_category": by_category,
        "per_item": per_item,
    }


# --- markdown twin -------------------------------------------------------

def to_markdown(scorecard: dict) -> str:
    h = scorecard["header"]
    agg = scorecard["aggregate"]
    lines = [
        f"# Routing scorecard — {h['tag']}",
        "",
        f"- **{h['note']}**",
        f"- model: `{h['model']}`  git: `{h['git_sha']}`  reps: {h['reps']}  seed: {h['seed']}",
        f"- tier: `{h.get('tier', 'anchor')}`",
        f"- persona policy: {h['persona_policy']}",
        f"- timestamp: {h['timestamp']}",
        "",
        f"**Mean pass rate: {agg['mean_pass_rate']:.3f}** "
        f"(95% CI {agg['ci95_low']:.3f}–{agg['ci95_high']:.3f}) "
        f"over {agg['n_items']} items; mean flip-rate {agg['mean_flip_rate']:.3f}",
        "",
    ]
    by_cat = scorecard.get("by_category") or {}
    if by_cat:
        lines += [
            "### By category (phrasing-robustness — paraphrase mean + spread)",
            "",
            "| category | n | mean | min | stdev |",
            "|---|---|---|---|---|",
        ]
        for cat, d in by_cat.items():
            lines.append(
                f"| {cat} | {d['n_items']} | {d['mean_pass']:.3f} | "
                f"{d['min_pass']:.3f} | {d['stdev']:.3f} |"
            )
        lines.append("")
    lines += [
        "| item | category | pass | flip | top failure |",
        "|---|---|---|---|---|",
    ]
    for item_id, d in sorted(scorecard["per_item"].items()):
        top_fail = ""
        if d["sample_failures"]:
            fl = d["sample_failures"][0]["failures"]
            top_fail = (fl[0] if fl else "")[:70].replace("|", "\\|")
        lines.append(
            f"| `{item_id}` | {d['category']} | {d['pass_rate']} | "
            f"{d['flip_rate']:.2f} | {top_fail} |"
        )
    # Non-gating diagnostics (A2 Q4): reached-vs-completed for deferred
    # tools. Shown separately so they're never read as the gate.
    diag_rows = [
        (item_id, k, rate)
        for item_id, d in sorted(scorecard["per_item"].items())
        for k, rate in sorted(d.get("diagnostics_rate", {}).items())
    ]
    if diag_rows:
        lines += [
            "",
            "### Diagnostics (non-gating — completed-in-turn vs the permissive gate)",
            "",
            "| item | diagnostic | rate |",
            "|---|---|---|",
        ]
        for item_id, k, rate in diag_rows:
            lines.append(f"| `{item_id}` | {k} | {rate:.2f} |")
    if scorecard.get("skipped"):
        lines += ["", "### Skipped at A0", ""]
        for s in scorecard["skipped"]:
            lines.append(f"- `{s['id']}` — {s['reason']}")
    return "\n".join(lines) + "\n"


# --- driver --------------------------------------------------------------

async def run_all(items, base, email, reps, persona) -> dict[str, list[ItemResult]]:
    per_item_reps: dict[str, list[ItemResult]] = {it.id: [] for it in items}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for rep in range(reps):
            for it in items:
                try:
                    res = await run_item(client, base, email, it, persona=persona)
                except Exception as e:  # network/parse failure for this rep
                    res = ItemResult(
                        item_id=it.id, passed=False,
                        failures=[f"runner exception: {e}"], checks={},
                    )
                per_item_reps[it.id].append(res)
            print(f"  rep {rep + 1}/{reps} done", file=sys.stderr)
    return per_item_reps


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=8)
    ap.add_argument("--tag", default="routing-pre-migration")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--email", default="routing-eval@munin.local")
    ap.add_argument("--only", default=None, help="run a single item id (smoke)")
    ap.add_argument("--items", default=None,
                    help="comma-separated item ids to run (subset; e.g. the A2 gate items)")
    ap.add_argument("--persona", default=A0_PERSONA,
                    help="request persona pin (default chat)")
    ap.add_argument("--tier", default="anchor", choices=["anchor", "paraphrase", "all"],
                    help="anchor=hand-authored seeds (fast tuning loop); "
                         "paraphrase=frozen robustness set; all=both (A5)")
    ap.add_argument("--out-dir", default=SCORECARD_DIR)
    args = ap.parse_args()

    random.seed(args.seed)
    subset = set(args.items.split(",")) if args.items else None

    # Decision B: items needing inject_tool_result are skipped at A0.
    runnable, skipped = [], []
    for it in items_for_tier(args.tier):
        if args.only and it.id != args.only:
            continue
        if subset and it.id not in subset:
            continue
        if it.context.inject_tool_result is not None:
            skipped.append({"id": it.id, "reason": "needs inject_tool_result (deferred to A2)"})
        else:
            runnable.append(it)
    category_by_id = {it.id: it.category for it in runnable}

    if not runnable:
        print("no runnable items selected", file=sys.stderr)
        return 2

    print(
        f"Running {len(runnable)} items x {args.reps} reps against {args.base} "
        f"as persona={args.persona!r} (skipping {len(skipped)})", file=sys.stderr,
    )
    per_item_reps = await run_all(
        runnable, args.base, args.email, args.reps, args.persona
    )

    scorecard = {
        "header": make_header(args.tag, args.reps, args.seed, args.base, args.persona)
    }
    scorecard["header"]["tier"] = args.tier
    scorecard.update(aggregate(per_item_reps, args.reps, category_by_id))
    scorecard["skipped"] = skipped

    os.makedirs(args.out_dir, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = os.path.join(args.out_dir, f"{date}_{args.tag}")
    with open(f"{stem}.json", "w") as f:
        json.dump(scorecard, f, indent=2)
    with open(f"{stem}.md", "w") as f:
        f.write(to_markdown(scorecard))

    agg = scorecard["aggregate"]
    print(
        f"\nmean pass rate {agg['mean_pass_rate']:.3f} "
        f"(CI {agg['ci95_low']:.3f}-{agg['ci95_high']:.3f}); "
        f"wrote {stem}.json (+ .md)", file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
