"""CLI for the user-failure track: validate, run, judge, score, compare.

    PYTHONPATH=$HOME/.cache/munin_bench_deps_userfail:. python -m munin_bench.userfail.run validate
    ... run   --tag 2026-11-24_sage [--ids uf-f0001,uf-f0002] [--workers 2]
    ... judge --tag 2026-11-24_sage          (needs ANTHROPIC_API_KEY or an `ant auth login` profile)
    ... score --tag 2026-11-24_sage          -> scorecards/userfail/<tag>.json
    ... compare scorecards/userfail/A.json scorecards/userfail/B.json

Records with answers stay in <store>/runs/<tag>/ (private). The scorecard
holds case ids, categories, tiers and verdicts only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from .cases import Case, load_cases, store_dir
from .checks import run_checks

BENCH = Path(__file__).resolve().parents[2]
SCORECARDS = BENCH / "scorecards" / "userfail"
EVAL_USER = "eval-userfail@munin.local"


# -- stamps -------------------------------------------------------------------

# Exceptions that mean the stack was unreachable (a redeploy, a restart), not
# that Munin failed the case. Such records are retried on resume and left out
# of the rates.
INFRA_ERRORS = ("RemoteProtocolError", "ReadError", "ConnectError", "ConnectTimeout",
                "ReadTimeout", "WriteError", "PoolTimeout")


def infra_error(rec: dict) -> bool:
    return bool(rec.get("error")) and rec["error"].split(":", 1)[0] in INFRA_ERRORS


def retrieval_image() -> Optional[str]:
    try:
        img = subprocess.run(["docker", "inspect", "-f", "{{.Image}}", "munin-retrieval"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return img[:19] or None
    except OSError:
        return None


def stamps(base: str) -> dict:
    out: dict = {"harness": {}, "model": {}}
    try:
        out["harness"]["git_sha"] = subprocess.run(
            ["git", "-C", str(BENCH), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except OSError:
        out["harness"]["git_sha"] = None
    out["harness"]["retrieval_image"] = retrieval_image()
    try:
        facts = httpx.get(f"{base}/api/system", timeout=10).json()
        b = facts.get("backbone") or {}
        out["model"] = {"served_name": b.get("served_name"), "display": b.get("display"),
                        "revision": (b.get("revision") or "")[:12] or None,
                        "fallback_active": bool((facts.get("fallback") or {}).get("active"))}
    except (httpx.HTTPError, ValueError):
        pass
    return out


# -- run ----------------------------------------------------------------------

def replay(case: Case, store: Path, base: str, keep: bool = False) -> dict:
    from ..labanalysis.client import LabClient

    rec: dict = {"id": case.id, "turns": [], "artifacts": [], "error": None,
                 "conversation_id": None, "started": datetime.now(timezone.utc).isoformat(),
                 "retrieval_image": retrieval_image(),
                 "prompts": [t.text for t in case.turns]}
    # persona "munin" = the router path the web UI uses; None would be raw model mode.
    client = LabClient(base=base, user=EVAL_USER, persona="munin", egress=case.egress)
    cid: Optional[str] = None
    docs: list[dict] = []
    seen: set[str] = set()
    try:
        for i, turn in enumerate(case.turns, 1):
            tdocs = []
            for a in turn.attachments:
                p = store / a
                d = client.upload(p)
                tdocs.append({"document_id": d["document_id"], "filename": p.name})
            docs += tdocs
            cid, tr = client.turn(turn.text, cid, tdocs)
            rec["conversation_id"] = cid
            rec["turns"].append({"answer": tr.answer, "tools": tr.tools, "error": tr.error,
                                 "seconds": tr.seconds, "tool_results": tr.tool_results,
                                 "revision_notes": tr.revision_notes})
            if not cid:
                raise RuntimeError("no conversation id")
            for a in client.artifacts(cid):
                if a["id"] not in seen:
                    seen.add(a["id"])
                    rec["artifacts"].append({"id": a["id"], "filename": a.get("filename", ""),
                                             "turn": i})
        if cid:   # the stored answer is canonical (tokens can be lost on a reconnect)
            stored = client.persisted_answers(cid)
            for t, text in zip(rec["turns"], stored[-len(rec["turns"]):]):
                if text:
                    t["answer"] = text
    except Exception as exc:   # recorded, never raised
        rec["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if not keep:
            if cid:
                try:
                    client.delete_chat(cid)
                except httpx.HTTPError:
                    pass
            for d in docs:
                try:
                    client.delete_doc(d["document_id"])
                except httpx.HTTPError:
                    pass
        client.close()
    rec["checks"] = run_checks(case.checks, rec)
    rec["mech_pass"] = all(c["pass"] for c in rec["checks"])
    return rec


def _jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["id"]] = d
    return out


def cmd_run(args) -> int:
    store = store_dir()
    cases = load_cases(store, args.ids)
    out_dir = store / "runs" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    rec_path = out_dir / "records.jsonl"
    done = {i: r for i, r in _jsonl(rec_path).items() if not infra_error(r)}
    todo = [c for c in cases if c.id not in done]
    meta_path = out_dir / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps({"tag": args.tag, **stamps(args.base),
                                         "started": datetime.now(timezone.utc).isoformat()}, indent=1))
    print(f"[run] {len(cases)} cases, {len(done)} already done, {len(todo)} to run")

    def one(c: Case):
        t0 = time.monotonic()
        r = replay(c, store, args.base, keep=args.keep)
        print(f"[run] {c.id} mech={'PASS' if r['mech_pass'] else 'FAIL'} "
              f"{time.monotonic() - t0:.0f}s {r['error'] or ''}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as pool, rec_path.open("a") as fh:
        for r in pool.map(one, todo):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
    return 0


# -- judge --------------------------------------------------------------------

def cmd_judge(args) -> int:
    from .judge import cost_usd, judge_batch, judge_one, make_client

    store = store_dir()
    cases = {c.id: c for c in load_cases(store)}
    out_dir = store / "runs" / args.tag
    recs = _jsonl(out_dir / "records.jsonl")
    judged_path = out_dir / "judged.jsonl"
    judged = _jsonl(judged_path)
    items = [(cases[i], r) for i, r in recs.items()
             if i in cases and cases[i].rubric and i not in judged and r.get("turns")
             and not infra_error(r)]
    print(f"[judge] {len(items)} to judge ({len(judged)} already judged)")
    if not items:
        return 0
    client = make_client()
    verdicts = judge_batch(client, items, batch_id=args.batch)
    batch = verdicts.pop("_batch", {})
    retry = [i for i, v in verdicts.items() if v.get("pass") is None]
    for i in retry:   # refusals / errors: one synchronous retry with fallbacks
        try:
            verdicts[i] = {**judge_one(client, cases[i], recs[i]), "retried": True}
        except Exception as exc:   # keep the batch result
            verdicts[i]["retry_error"] = str(exc)[:200]
    spent = 0.0
    with judged_path.open("a") as fh:
        for i, v in verdicts.items():
            spent += cost_usd(v.get("usage") or {}, batch=not v.get("retried"))
            fh.write(json.dumps({"id": i, "batch": batch.get("id"), **v}, ensure_ascii=False) + "\n")
    print(f"[judge] wrote {len(verdicts)} verdicts, about ${spent:.2f}")
    return 0


# -- score / compare ----------------------------------------------------------

def scorecard(tag: str) -> dict:
    store = store_dir()
    cases = {c.id: c for c in load_cases(store)}
    out_dir = store / "runs" / tag
    meta = json.loads((out_dir / "meta.json").read_text())
    recs = _jsonl(out_dir / "records.jsonl")
    judged = _jsonl(out_dir / "judged.jsonl")
    rows = []
    for cid, r in sorted(recs.items()):
        c = cases.get(cid)
        if c is None:
            continue
        j = judged.get(cid)
        judge_pass = None if not c.rubric else (j or {}).get("pass")
        if infra_error(r) or (c.rubric and judge_pass is None):
            verdict = None   # stack unreachable, or not judged yet: excluded from rates
        else:
            verdict = bool(r["mech_pass"] and (judge_pass if c.rubric else True))
        crit = [bool(x["pass"]) for x in (j or {}).get("criteria") or []] if c.rubric else []
        rows.append({"id": cid, "category": c.category, "tier": c.tier,
                     "mech_pass": r["mech_pass"], "judge_pass": judge_pass, "pass": verdict,
                     "criteria": crit,
                     "failed_checks": [x["type"] for x in r.get("checks") or [] if not x["pass"]]})

    def agg(sel):
        graded = [x for x in sel if x["pass"] is not None]
        return {"n": len(sel), "graded": len(graded), "pass": sum(x["pass"] for x in graded),
                "rate": round(sum(x["pass"] for x in graded) / len(graded), 3) if graded else None}

    cats = sorted({x["category"] for x in rows})
    return {"track": "userfail", "tag": tag,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "harness": meta.get("harness"), "model": meta.get("model"),
            # More than one image means munin-retrieval was redeployed mid-run.
            "retrieval_images": sorted({r.get("retrieval_image") or "unknown" for r in recs.values()}),
            "infra_errors": sorted(i for i, r in recs.items() if infra_error(r)),
            "judge": {"model": "claude-opus-5-5", "effort": "high"},
            "overall": agg(rows),
            "by_tier": {t: agg([x for x in rows if x["tier"] == t]) for t in ("mechanical", "rubric")},
            "mechanical_only": agg([{**x, "pass": x["mech_pass"]} for x in rows]),
            # Rubric points met, over judged cases: more sensitive than the
            # all-points case pass, which hides partial improvement.
            "criteria": {"met": sum(sum(x["criteria"]) for x in rows),
                         "total": sum(len(x["criteria"]) for x in rows),
                         "rate": round(sum(sum(x["criteria"]) for x in rows)
                                       / max(1, sum(len(x["criteria"]) for x in rows)), 3)},
            "by_category": {k: agg([x for x in rows if x["category"] == k]) for k in cats},
            "cases": rows}


def cmd_score(args) -> int:
    sc = scorecard(args.tag)
    SCORECARDS.mkdir(parents=True, exist_ok=True)
    path = SCORECARDS / f"{args.tag}.json"
    path.write_text(json.dumps(sc, indent=1) + "\n")
    o = sc["overall"]
    print(f"[score] {path.relative_to(BENCH)}: {o['pass']}/{o['graded']} pass "
          f"(mechanical {sc['by_tier']['mechanical']}, rubric {sc['by_tier']['rubric']})")
    if len(sc["retrieval_images"]) > 1:
        print(f"[score] WARNING: munin-retrieval changed mid-run {sc['retrieval_images']}; "
              "rerun under a new tag before comparing")
    if sc["infra_errors"]:
        print(f"[score] {len(sc['infra_errors'])} cases hit a stack outage and are ungraded; "
              "`run` again with the same tag to retry them")
    return 0


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar (binomial on discordant pairs)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def _means(cards: list[dict]):
    """Per-case pass rate and per-(case, criterion) met rate over replicates."""
    case_runs: dict[str, list] = {}
    crit_runs: dict[tuple, list] = {}
    tier: dict[str, str] = {}
    for sc in cards:
        for x in sc["cases"]:
            if x["pass"] is None:
                continue
            case_runs.setdefault(x["id"], []).append(bool(x["pass"]))
            tier[x["id"]] = x["tier"]
            for k, v in enumerate(x.get("criteria") or []):
                crit_runs.setdefault((x["id"], k), []).append(bool(v))
    mean = lambda d: {k: sum(v) / len(v) for k, v in d.items()}
    return mean(case_runs), mean(crit_runs), tier


def compare(a, b) -> dict:
    """a, b: one scorecard each, or lists of replicate scorecards per arm.

    A case is fixed when its pass rate over the replicates rises, regressed when
    it falls; the p value is an exact two-sided sign test over those (McNemar
    when each arm has one run). The same test runs over rubric points.
    """
    A_cards = a if isinstance(a, list) else [a]
    B_cards = b if isinstance(b, list) else [b]
    A, Ac, tier = _means(A_cards)
    B, Bc, _ = _means(B_cards)
    common = sorted(set(A) & set(B))

    def block(ids):
        fixed = [i for i in ids if B[i] > A[i]]
        regressed = [i for i in ids if B[i] < A[i]]
        return {"n": len(ids), "a_pass": round(sum(A[i] for i in ids), 2),
                "b_pass": round(sum(B[i] for i in ids), 2), "fixed": fixed, "regressed": regressed,
                "mcnemar_p": round(mcnemar_p(len(fixed), len(regressed)), 4)}

    ck = sorted(set(Ac) & set(Bc))
    up = sum(Bc[k] > Ac[k] for k in ck)
    down = sum(Bc[k] < Ac[k] for k in ck)
    first = lambda cards, k: [sc.get(k) for sc in cards][0]
    changed = {k: (first(A_cards, k), first(B_cards, k)) for k in ("harness", "model")
               if first(A_cards, k) != first(B_cards, k)}
    return {"a": [sc["tag"] for sc in A_cards], "b": [sc["tag"] for sc in B_cards],
            "replicates": [len(A_cards), len(B_cards)], "changed": changed, "all": block(common),
            "by_tier": {t: block([i for i in common if tier[i] == t])
                        for t in ("mechanical", "rubric")},
            "criteria": {"n": len(ck), "a_met": round(sum(Ac[k] for k in ck), 2),
                         "b_met": round(sum(Bc[k] for k in ck), 2), "up": up, "down": down,
                         "sign_p": round(mcnemar_p(up, down), 4)}}


def cmd_compare(args) -> int:
    load = lambda arg: [json.loads(Path(x).read_text()) for x in arg.split(",")]
    res = compare(load(args.a), load(args.b))
    print(json.dumps(res, indent=1))
    return 0


def cmd_recheck(args) -> int:
    """Re-run the mechanical checks on stored answers (after a checker fix), no replay."""
    store = store_dir()
    cases = {c.id: c for c in load_cases(store)}
    rec_path = store / "runs" / args.tag / "records.jsonl"
    recs = _jsonl(rec_path)
    flips = []
    for i, r in recs.items():
        if i not in cases or infra_error(r):
            continue
        old = r["mech_pass"]
        r["checks"] = run_checks(cases[i].checks, r)
        r["mech_pass"] = all(c["pass"] for c in r["checks"])
        if r["mech_pass"] != old:
            flips.append(f"{i} {'FAIL->PASS' if r['mech_pass'] else 'PASS->FAIL'}")
    tmp = rec_path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs.values()))
    tmp.replace(rec_path)
    print(f"[recheck] {len(recs)} records, {len(flips)} changed" + "".join(f"\n  {f}" for f in flips))
    return 0


def cmd_validate(args) -> int:
    cases = load_cases(store_dir())
    by = {}
    for c in cases:
        by.setdefault((c.tier, c.category), 0)
        by[(c.tier, c.category)] += 1
    print(f"[validate] {len(cases)} cases OK")
    for k, v in sorted(by.items()):
        print(f"  {k[0]:10} {k[1]:26} {v}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="munin_bench.userfail.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    r = sub.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--ids", type=lambda s: s.split(","), default=None)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--base", default=os.environ.get("MUNIN_RETRIEVAL_URL", "http://127.0.0.1:8080"))
    r.add_argument("--keep", action="store_true", help="keep the eval chats (debugging)")
    j = sub.add_parser("judge")
    j.add_argument("--tag", required=True)
    j.add_argument("--batch", help="collect this already submitted batch id instead of creating one")
    rc = sub.add_parser("recheck")
    rc.add_argument("--tag", required=True)
    s = sub.add_parser("score")
    s.add_argument("--tag", required=True)
    c = sub.add_parser("compare")
    c.add_argument("a", help="scorecard, or comma-separated replicate scorecards")
    c.add_argument("b", help="scorecard, or comma-separated replicate scorecards")
    args = ap.parse_args(argv)
    return {"validate": cmd_validate, "run": cmd_run, "judge": cmd_judge,
            "recheck": cmd_recheck, "score": cmd_score, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
