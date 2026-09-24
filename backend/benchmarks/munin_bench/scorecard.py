"""Scorecard schema + provenance for Track E (regression harness).

A scorecard is one committed JSON per `run_all` invocation:

    {
      "meta":  <make_run_header: model, encoder, git sha, corpus snapshot, ...>,
      "tasks": {
        "<task>": {
          "<system>": {
            "metrics":   {"<metric>": {"mean","ci_low","ci_high"}},
            "per_query": {"<qid>": {"<metric>": value}}   # for compare's paired bootstrap
          }
        }
      }
    }

`per_query` is what makes `compare` able to run a *paired* bootstrap between two
runs over the same queries (before/after a model or encoder swap), not just eyeball
overlapping CIs. Scorecards are committed to git; the per-query arrays are the
price of paired significance.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone

from . import config


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:
        return "unknown"


def _pkg_versions():
    out = {}
    for mod in ("numpy", "scipy", "sentence_transformers", "qdrant_client"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            out[mod] = "n/a"
    return out


def corpus_collections() -> tuple[str, str]:
    """(papers, chunks) collection names this arm searches.

    Defaults to the production pair, overridable per arm because an arm does
    not always search production: the Track C2b ABSENT arm runs against a
    shadow instance on `papers_shadow` / `papers_chunks_shadow`, and stamping
    it with the production counts would name the one corpus it deliberately
    does not search.
    """
    return (os.getenv("MUNIN_EVAL_PAPERS_COLLECTION", config.PAPERS_COLLECTION),
            os.getenv("MUNIN_EVAL_CHUNKS_COLLECTION", config.CHUNKS_COLLECTION))


def corpus_snapshot(qc=None) -> dict:
    """Point counts for the collections an arm searched, for the run sidecar.

    Stamped at CAPTURE time, not at scoring time: `papers_bge` grows with the
    ingest pipeline and `papers_chunks` was built mid-2026-08, so a count taken
    when a scorecard is written can be a different corpus from the one the arm
    read. The 2026-08-26 / 2026-09-15 egress pair is exactly this gap: neither
    scorecard recorded a count, so whether the two arms searched the same corpus
    is not answerable after the fact (docs/EGRESS-PAIR-COMMIT-AUDIT.md).

    Best-effort, like the rest of the header: a count that cannot be taken is
    recorded as None rather than failing a ten-hour arm.
    """
    papers, chunks = corpus_collections()
    snap = {"papers_collection": papers, "chunks_collection": chunks,
            "papers_points": None, "chunks_points": None,
            "counted_at": datetime.now(timezone.utc).isoformat()}
    if qc is None:
        try:
            from .clients import get_qdrant
            qc = get_qdrant()
        except Exception:
            return snap
    for key, name in (("papers_points", papers), ("chunks_points", chunks)):
        try:
            snap[key] = qc.count(name).count
        except Exception:
            pass          # absent collection (chunks pre-08-30) stays None
    return snap


def make_run_header(qc=None, neo4j=None, *, encoder="specter-v1", tag="",
                    extra=None) -> dict:
    """Provenance stamped on every scorecard: model, encoder, git SHA, corpus
    snapshot, package versions, seed. Best-effort — missing pieces degrade to
    'n/a' rather than failing a run."""
    hdr = {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "encoder": encoder,
        "seed": 42,
        "packages": _pkg_versions(),
    }
    # served model
    try:
        with urllib.request.urlopen(config.VLLM_URL + "/v1/models", timeout=8) as r:
            d = json.load(r)["data"][0]
        hdr["model"] = d.get("id")
        hdr["model_max_len"] = d.get("max_model_len")
        # Checkpoint directory the server loaded; names the quantized build,
        # which the served id alone does not (the 16 GB question for a small
        # model is entirely which checkpoint, THIRD-MODEL-REVIEW section 4).
        hdr["model_path"] = d.get("root")
    except Exception:
        hdr["model"] = config.VLLM_MODEL_NAME
    # corpus snapshot
    if qc is not None:
        papers, chunks = corpus_collections()
        hdr["corpus_collections"] = {"papers": papers, "chunks": chunks}
        for key, name in (("corpus_papers", papers), ("corpus_chunks", chunks)):
            try:
                hdr[key] = qc.count(name).count
            except Exception:
                hdr[key] = "n/a"
    if neo4j is not None:
        try:
            with neo4j.session() as s:
                hdr["graph_cites_edges"] = s.run(
                    "MATCH ()-[r:CITES]->() RETURN count(r) AS c").single()["c"]
        except Exception:
            hdr["graph_cites_edges"] = "n/a"
    if extra:
        hdr.update(extra)
    return hdr


def task_from_per_query(per_query: dict, qids: list, summary: dict) -> dict:
    """Build a scorecard task entry from a runner's internal arrays.

    per_query: {system: {metric: [value per qid, aligned to `qids`]}}
    summary:   {metric: {system: {mean,ci_low,ci_high}}}  (already bootstrapped)

    metrics come from `summary` (may include aggregate-only metrics like the
    answer track's precision); per_query holds only the metrics that have arrays
    (what `compare` can pair-bootstrap).
    """
    systems = set(per_query) | {s for mv in summary.values() for s in mv}
    out = {}
    for system in systems:
        by_metric = per_query.get(system, {})
        pq = {qid: {m: by_metric[m][i] for m in by_metric}
              for i, qid in enumerate(qids)}
        metrics = {m: summary[m][system] for m in summary if system in summary[m]}
        out[system] = {"metrics": metrics, "per_query": pq}
    return out


def write_scorecard(meta: dict, tasks: dict, results_root: str, date_str: str):
    """Write scorecards/<date>_<tag>.{json,md}. `date_str` passed in (no
    Date.now in the writer path so runs are reproducible-stampable)."""
    tag = meta.get("tag", "run")
    out_dir = os.path.join(results_root, "..", "scorecards")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{date_str}_{tag}")
    card = {"meta": meta, "tasks": tasks}
    with open(base + ".json", "w") as fh:
        json.dump(card, fh, indent=2)
    _write_md(base + ".md", card)
    return base + ".json"


def _write_md(path, card):
    m = card["meta"]
    lines = [f"# Scorecard — {m.get('tag','')}", "",
             f"- model `{m.get('model')}` | encoder `{m.get('encoder')}` "
             f"| git `{m.get('git_sha')}`",
             f"- corpus papers {m.get('corpus_papers','n/a')} | "
             f"chunks {m.get('corpus_chunks','n/a')} | "
             f"graph CITES {m.get('graph_cites_edges','n/a')} | seed {m.get('seed')}",
             f"- {m.get('generated_at')}", ""]
    for task, systems in card["tasks"].items():
        lines += [f"## {task}", "",
                  "| system | " + " | ".join(_task_metrics(systems)) + " |",
                  "|" + "---|" * (len(_task_metrics(systems)) + 1)]
        for sysname, entry in systems.items():
            row = [sysname]
            for mk in _task_metrics(systems):
                s = entry["metrics"].get(mk)
                row.append(f"{s['mean']:.4f}" if s else "-")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def _task_metrics(systems):
    keys = []
    for entry in systems.values():
        for k in entry["metrics"]:
            if k not in keys:
                keys.append(k)
    return keys
