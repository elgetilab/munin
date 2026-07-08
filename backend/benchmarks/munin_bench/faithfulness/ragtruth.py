"""RAGTruth loader for the Track B judge-validation (B2).

RAGTruth (Niu et al. 2024, arXiv 2401.00396) is a span-annotated RAG-
hallucination corpus. We use it ungated from the authors' GitHub (MIT license)
rather than the gated LLM-AggreFact mirror. A response is labelled
**hallucinated** iff it carries >=1 annotated hallucination span
(``labels`` non-empty); otherwise **grounded**.

We validate the local MiniCheck judge against these human labels: a grounded
response should score high support, a hallucinated one lower.
"""

from __future__ import annotations

import json
import os
import random
import urllib.request

_CACHE = os.path.expanduser("~/.cache/munin_bench_data/ragtruth")
_BASE = "https://raw.githubusercontent.com/ParticleMedia/RAGTruth/main/dataset"
_FILES = ("response.jsonl", "source_info.jsonl")


def _ensure_files() -> None:
    os.makedirs(_CACHE, exist_ok=True)
    for f in _FILES:
        path = os.path.join(_CACHE, f)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            urllib.request.urlretrieve(f"{_BASE}/{f}", path)


def _document(source_info) -> str:
    """Normalise a source_info record to the grounding document string. QA rows
    carry a dict {question, passages}; the evidence is the passages. Summary /
    Data2txt rows carry the article / structured data as a string."""
    if isinstance(source_info, dict):
        return str(source_info.get("passages") or source_info.get("source_info")
                   or json.dumps(source_info))
    return str(source_info)


def load_sample(
    n_per_cell: int = 20,
    task_types: tuple[str, ...] = ("QA", "Summary", "Data2txt"),
    split: str = "test",
    seed: int = 42,
) -> list[dict]:
    """Deterministic stratified sample: up to ``n_per_cell`` responses per
    (task_type, hallucinated) cell. Returns
    [{id, task_type, document, response, hallucinated}]."""
    _ensure_files()
    src: dict = {}
    with open(os.path.join(_CACHE, "source_info.jsonl")) as fh:
        for line in fh:
            d = json.loads(line)
            src[d["source_id"]] = d

    cells: dict = {}
    with open(os.path.join(_CACHE, "response.jsonl")) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("split") != split:
                continue
            s = src.get(r["source_id"])
            if not s or s.get("task_type") not in task_types:
                continue
            hall = bool(r.get("labels"))
            rec = {
                "id": r["id"],
                "task_type": s["task_type"],
                "document": _document(s.get("source_info")),
                "response": r.get("response") or "",
                "hallucinated": hall,
            }
            cells.setdefault((s["task_type"], hall), []).append(rec)

    rng = random.Random(seed)
    out: list[dict] = []
    for key in sorted(cells):
        pool = cells[key]
        rng.shuffle(pool)
        out.extend(pool[:n_per_cell])
    rng.shuffle(out)
    return out
