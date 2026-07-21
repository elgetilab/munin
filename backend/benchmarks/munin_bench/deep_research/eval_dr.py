"""Deep Research eval + certification gate (D13, first version).

Deep Research is the one agent whose eval seam can't ship WITH it: a composite
document has no exact-match score. This is the seam - a self-contained automatic
metric over DR outputs so the agent has a certification gate now:

  Structural (from the plan/citations, free, deterministic):
    resolution_rate         sub-questions resolved / total
    grounding_rate          notes carrying a verbatim quote / total notes
    full_text_citation_rate citations read in full text / total citations

  LLM-judge (rubric, 1-5): grounding, coverage, clarity of the document.

Adopting the NAMED external benchmarks (AutoResearchBench, ScholarQA-CS2,
ArxivDIGESTables) is a further data-acquisition step; this gives DR a working gate
in the meantime, of the "different gate shape" kind the plan (D13/§8) anticipated.

Run in the retrieval-env runner (needs retrieval + benchmarks on the path):

    PYTHONPATH=<pipeline site-packages>:backend/retrieval:backend/benchmarks \\
    PAPER_ENCODER=bge-large PAPERS_COLLECTION=papers_bge GROBID_URL=http://localhost:8070 \\
    <runner-python> -m munin_bench.deep_research.eval_dr
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

import deep_research_agent as DR
from database import VLLM_MODEL_NAME, VLLM_URL

_HERE = os.path.dirname(__file__)

SEED_QUESTIONS = [
    "What are the main off-target risks of CRISPR base editing and how are they detected?",
    "How do prime editors differ from base editors in mechanism and editing precision?",
    "What experimental methods are used to map genome-wide Cas9 off-target activity?",
]

# Provisional DR certification thresholds (first version; tighten with data).
THRESHOLDS = {
    "resolution_rate": 0.50,          # at least half the sub-questions resolve
    "grounding_rate": 0.80,           # notes are quote-backed
    "full_text_citation_rate": 0.50,  # citations are real reads, not abstracts
    "judge_grounding": 3.0,           # LLM-judge grounding >= 3/5
}

_JUDGE_SYSTEM = (
    "You grade a research report for a technical audience. Score 1-5 on each of: "
    "grounding (are claims supported by the cited evidence, no fabrication), "
    "coverage (does it address the question's facets), clarity. Return ONLY a JSON "
    "object {\"grounding\": n, \"coverage\": n, \"clarity\": n, \"comment\": \"...\"}.")


async def _judge(question: str, document: str) -> dict:
    body = {"model": VLLM_MODEL_NAME,
            "messages": [{"role": "system", "content": _JUDGE_SYSTEM},
                         {"role": "user", "content": f"Question: {question}\n\nReport:\n{document[:8000]}"}],
            "max_tokens": 400, "temperature": 0.0, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False}}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
        content = r.json()["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(m.group(0)) if m else {"error": "unparseable judge output"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _structural_metrics(env: dict) -> dict:
    plan = env.get("plan") or []
    notes = [n for node in plan for n in node.get("notes", [])]
    cits = env.get("citations") or []
    n_sub = len(plan)
    n_resolved = sum(1 for n in plan if n.get("status") == "resolved")
    ft = sum(1 for c in cits if c.get("read_depth") == "full_text")
    quoted = sum(1 for n in notes if n.get("quote"))
    return {
        "resolution_rate": round(n_resolved / n_sub, 3) if n_sub else 0.0,
        "grounding_rate": round(quoted / len(notes), 3) if notes else 0.0,
        "full_text_citation_rate": round(ft / len(cits), 3) if cits else 0.0,
        "n_sub": n_sub, "n_notes": len(notes), "n_citations": len(cits)}


def _mean(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


async def run(questions=None, **caps) -> dict:
    questions = questions or SEED_QUESTIONS
    rows = []
    for i, q in enumerate(questions, 1):
        env = await DR.deep_research(q, **caps)
        m = _structural_metrics(env)
        j = await _judge(q, env.get("document", ""))
        rows.append({"question": q, **m, "judge": j})
        print(f"  {i}/{len(questions)} resolution={m['resolution_rate']} "
              f"grounding={m['grounding_rate']} ft_cites={m['full_text_citation_rate']} "
              f"judge={ {k: j.get(k) for k in ('grounding','coverage','clarity')} }")

    agg = {k: _mean(rows, k) for k in
           ("resolution_rate", "grounding_rate", "full_text_citation_rate")}
    agg["judge_grounding"] = round(
        sum(r["judge"].get("grounding", 0) for r in rows if isinstance(r["judge"].get("grounding"), (int, float)))
        / max(1, len(rows)), 2)

    checks = [{"metric": k, "value": agg.get(k), "threshold": v,
               "pass": (agg.get(k) or 0) >= v} for k, v in THRESHOLDS.items()]
    overall = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    return {"aggregate": agg, "checks": checks, "overall": overall, "rows": rows}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap seed questions")
    ap.add_argument("--read-cap", type=int, default=3)
    ap.add_argument("--screen-keep", type=int, default=8)
    ap.add_argument("--max-subq", type=int, default=3)
    args = ap.parse_args()
    qs = SEED_QUESTIONS[:args.limit] if args.limit else SEED_QUESTIONS
    print(f"[dr-eval] {len(qs)} question(s)")
    res = asyncio.run(run(qs, read_cap=args.read_cap, screen_keep=args.screen_keep,
                          max_subq=args.max_subq))
    print("\n=== Deep Research certification gate (first version) ===")
    for c in res["checks"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['metric']} = {c['value']} "
              f"(>= {c['threshold']})")
    print(f"OVERALL: {res['overall']}")
    out = os.path.join(_HERE, "..", "..", "results", "deep_research_eval.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(res, open(out, "w"), indent=2)
    return 0 if res["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
