"""Ceiling oracle: does the FULL paper text let the model answer what it abstained on?

Track D diagnosis showed the agentic arm reads the source paper (16/20 over-
abstentions did) yet still abstains, because read_paper returns only a lossy
summary, not the raw text where a buried numeric answer lives. Before building a
within-document-retrieval lever, quantify the ceiling: feed the source paper's
FULL extracted text (same GROBID->pypdf path read_paper uses, but WITHOUT the
summarisation step) to the model on those same over-abstention MCQs.

  - accuracy jumps  -> the answer IS in the text; surfacing it is worth building.
  - stays abstain   -> genuine extraction/table ceiling; the lever won't help.

Covers the over-abstention qids whose source PDF is in the local corpus
(/opt/munin/data/papers/pdf); reports coverage. No production change, no deploy.

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.diag_fulltext_oracle
"""

from __future__ import annotations

import io
import json
import os
import re
from collections import Counter

import httpx

from ..benchmarks.litqa2_runner import load_litqa2, build_mcq
from .run_arm import _verdict
from . import vllm_answer as V

_HERE = os.path.dirname(__file__)
PDF_DIR = "/opt/munin/data/papers/pdf"
GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070")
MAX_TEXT_CHARS = 150_000  # ~37k tokens; leaves room in the 65k window + completion

_ORACLE_SYSTEM = (
    "You are a research assistant. Use the FULL paper text provided to answer the "
    "question; you may also draw on your own knowledge. The exact answer (a number, "
    "a value, a specific result) may be buried in the body, a table, or a figure "
    "caption. Pick the insufficient-information option only if the text and your "
    "knowledge genuinely cannot determine the answer."
)


def _pdf_path(doi: str) -> str | None:
    s = doi.replace("/", "_").replace(":", "_")
    for cand in (f"doi_{s}.pdf", f"{s}.pdf"):
        p = os.path.join(PDF_DIR, cand)
        if os.path.isfile(p):
            return p
    return None


def _extract(pdf_bytes: bytes) -> str:
    """GROBID first (matches document_store._extract_pdf), pypdf fallback."""
    try:
        r = httpx.post(f"{GROBID_URL}/api/processFulltextDocument",
                       files={"input": ("doc.pdf", pdf_bytes, "application/pdf")},
                       data={"consolidateHeader": "0"}, timeout=120.0)
        if r.status_code == 200 and r.text:
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 100:
                return text
    except Exception:
        pass
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return "\n\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
    except Exception:
        return ""


def _oracle_prompt(q: dict, mcq: dict) -> str:
    opts = "\n".join(f"{l}) {mcq['options'][l]}" for l in mcq["letters"])
    return ("Answer this multiple-choice question using the full paper text above. "
            "Give brief reasoning, then end with a line exactly: 'Answer: <letter>'. "
            "If the paper genuinely does not contain enough information, pick the "
            "'Insufficient information' option.\n\n"
            f"Question: {q['question']}\n\n{opts}")


def main() -> int:
    over = json.load(open(os.path.join(_HERE, "..", "..", "ablation_runs",
                                        "diag_overabstain.json")))["rows"]
    qmap = {q["qid"]: q for q in load_litqa2()}
    rows, no_pdf = [], []
    for r in over:
        qid, src = r["qid"], r["src"]
        path = _pdf_path(src)
        if not path:
            no_pdf.append(qid); continue
        text = _extract(open(path, "rb").read())
        if len(text) < 200:
            no_pdf.append(qid); continue
        text = text[:MAX_TEXT_CHARS]
        q = qmap[qid]
        mcq = build_mcq(q)
        prompt = f"Full text of the paper:\n{text}\n\n---\n\n{_oracle_prompt(q, mcq)}"
        out = V.complete(prompt, system=_ORACLE_SYSTEM, max_tokens=4096)
        vd = _verdict(mcq, out["content"])
        rows.append({"qid": qid, "src": src, "chars": len(text),
                     "verdict": vd["verdict"], "letter": vd["letter"],
                     "correct": mcq["correct"], "answer": out["content"][-400:]})
        print(f"  {qid[:8]} chars={len(text):>7} -> {vd['verdict']:11s} "
              f"(picked {vd['letter']}, correct {mcq['correct']})")

    n = len(rows)
    vc = Counter(r["verdict"] for r in rows)
    print("\n=== full-text oracle on over-abstention set ===")
    print(f"  covered (local PDF): {n}/{len(over)}   uncovered: {len(no_pdf)}")
    print(f"  was: all {n} abstained in the live agentic run")
    print(f"  now: correct={vc['correct']} incorrect={vc['incorrect']} "
          f"abstain={vc['abstain']} unparseable={vc['unparseable']}")
    flip = vc["correct"]
    print(f"  -> {flip}/{n} flipped abstain->CORRECT")
    if n:
        print(f"     {'LEVER WORTH BUILDING' if flip >= n/3 else 'CEILING: full text does not rescue these'}"
              f" (flip rate {flip/n:.0%}; incorrect {vc['incorrect']}/{n} is the precision cost)")
    json.dump({"n": n, "uncovered": no_pdf, "verdicts": dict(vc), "rows": rows},
              open(os.path.join(_HERE, "..", "..", "ablation_runs",
                                "diag_fulltext_oracle.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
