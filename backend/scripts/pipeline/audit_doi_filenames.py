#!/usr/bin/env python3
"""
Read-only audit of DOIs mangled by the old doi_*.pdf filename decoder.

doi_*.pdf filenames map `/` (and `:` in the crawler) to `_`. Until 2026-10
the decoder restored only the first `_` and its result overrode GROBID's DOI,
so a paper whose DOI has a second `/` or a `:` was keyed to a DOI that does
not exist (`10.1093/molehr/3.5.431` -> `10.1093/molehr_3.5.431`). This script
finds the papers that may be affected and says, per paper, what Crossref
thinks. It writes NOTHING to the corpus: it is the report read before any
repair is authorised.

For every point whose DOI has a `_` after the prefix:
  ok          the stored DOI resolves on Crossref and its title matches
              (a DOI with real underscores, e.g. Springer)
  mis-keyed   the stored DOI does not resolve (or names another paper), and a
              candidate with `_` read back as `/` or `:` resolves with a
              matching title: `proposed_doi` is the fix
  mismatch    the stored DOI resolves but to a different title, and no
              candidate matches (another problem, e.g. a citation DOI)
  candidate-title-mismatch
              the stored DOI does not resolve; a candidate does, but to a
              different title (`proposed_doi` = best such candidate). The
              PDF and its DOI disagree; needs a human, not a re-key
  unresolved  neither the stored DOI nor any candidate resolves
  error       Crossref could not be reached after retries (re-run later)

Usage (pipeline venv, on hugin):
    ./audit_doi_filenames.py --report doi-audit.json --csv doi-audit.csv \
        --markdown doi-audit.md [--limit N]

Environment: QDRANT_HOST / QDRANT_PORT / PAPERS_COLLECTION / ADMIN_EMAIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doi_filename  # noqa: E402
from paper_pipeline import _title_similarity  # noqa: E402

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "research@muninai.org")

TITLE_THRESHOLD = 0.3       # the pipeline's _MERGE_TITLE_SIM_THRESHOLD
_PAGE = 4096
_PACE_S = 0.3               # Crossref polite pool; the audit is not in a hurry
_RETRIES = 4


class CrossrefUnavailable(Exception):
    pass


def qdrant_scroll() -> list[dict]:
    out, offset = [], None
    while True:
        body = {"limit": _PAGE, "with_vector": False,
                "with_payload": ["doi", "title", "_grobid_doi", "pdf_path", "_ingest_source"]}
        if offset is not None:
            body["offset"] = offset
        req = urllib.request.Request(
            f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{PAPERS_COLLECTION}/points/scroll",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        result = json.load(urllib.request.urlopen(req, timeout=120))["result"]
        for point in result["points"]:
            out.append({**(point.get("payload") or {}), "_point_id": point.get("id")})
        offset = result.get("next_page_offset")
        if offset is None:
            return out


_cache: dict[str, dict | None] = {}


def crossref(doi: str) -> dict | None:
    """Crossref record, None on 404; raises CrossrefUnavailable otherwise."""
    key = doi.lower()
    if key in _cache:
        return _cache[key]
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"MuninBot/1.0 (https://muninai.org; mailto:{ADMIN_EMAIL})"})
    for attempt in range(_RETRIES):
        time.sleep(_PACE_S)
        try:
            record = json.load(urllib.request.urlopen(req, timeout=20))["message"]
            _cache[key] = record
            return record
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                _cache[key] = None
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise CrossrefUnavailable(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2 ** attempt)
    raise CrossrefUnavailable("retries exhausted")


def title_sim(record: dict | None, title: str) -> float:
    if not record:
        return 0.0
    titles = record.get("title") or []
    return max((_title_similarity(title, t) for t in titles), default=0.0)


def suspects(points: list[dict]) -> list[dict]:
    out = []
    for p in points:
        doi = (p.get("doi") or "").strip()
        if not doi.startswith("10.") or "/" not in doi:
            continue
        if "_" in doi.split("/", 1)[1]:
            out.append(p)
    return out


def classify(p: dict, known_dois: set[str]) -> dict:
    doi = p["doi"].strip()
    title = (p.get("title") or "").strip()
    prefix, suffix = doi.split("/", 1)
    row = {"point_id": p["_point_id"], "doi": doi, "title": title[:200],
           "grobid_doi": p.get("_grobid_doi"), "pdf": os.path.basename(p.get("pdf_path") or ""),
           "ingest_source": p.get("_ingest_source"), "status": None,
           "stored_sim": None, "proposed_doi": None, "proposed_sim": None,
           "proposed_in_corpus": None}
    try:
        stored = crossref(doi)
        row["stored_sim"] = round(title_sim(stored, title), 3)
        if stored and row["stored_sim"] >= TITLE_THRESHOLD:
            row["status"] = "ok"
            return row
        best = None  # best resolving candidate below the title bar
        for cand in doi_filename.candidates(prefix, suffix):
            if cand.lower() == doi.lower():
                continue
            rec = crossref(cand)
            sim = title_sim(rec, title)
            if rec and sim >= TITLE_THRESHOLD:
                row.update(status="mis-keyed", proposed_doi=cand, proposed_sim=round(sim, 3),
                           proposed_in_corpus=cand.lower() in known_dois)
                return row
            if rec and (best is None or sim > best[1]):
                best = (cand, sim)
        if stored:
            row["status"] = "mismatch"
        elif best:
            row.update(status="candidate-title-mismatch", proposed_doi=best[0],
                       proposed_sim=round(best[1], 3))
        else:
            row["status"] = "unresolved"
    except CrossrefUnavailable as e:
        row["status"] = "error"
        row["error"] = str(e)
    return row


def markdown(summary: dict, rows: list[dict]) -> str:
    lines = [f"# DOI filename audit, {summary['generated_at'][:10]}", "",
             f"Collection `{summary['collection']}`: {summary['points']} points, "
             f"{summary['suspects']} with `_` after the DOI prefix, {summary['checked']} checked.", "",
             "| status | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(summary["by_status"].items())]
    lines += ["", f"Mis-keyed whose proposed DOI is already another point: "
                  f"{summary['mis_keyed_duplicate_of_existing']}", "",
              "## Mis-keyed (first 50)", "", "| stored | proposed | sim | dup | title |",
              "|---|---|---|---|---|"]
    for r in [r for r in rows if r["status"] == "mis-keyed"][:50]:
        lines.append(f"| `{r['doi']}` | `{r['proposed_doi']}` | {r['proposed_sim']} | "
                     f"{'yes' if r['proposed_in_corpus'] else ''} | {r['title'][:70]} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", metavar="PATH", help="JSON report (summary + every row)")
    ap.add_argument("--csv", metavar="PATH", help="one row per suspect point")
    ap.add_argument("--markdown", metavar="PATH", help="human-readable summary")
    ap.add_argument("--limit", type=int, help="check only the first N suspects")
    args = ap.parse_args()

    points = qdrant_scroll()
    known = {(p.get("doi") or "").strip().lower() for p in points if p.get("doi")}
    sus = suspects(points)
    todo = sus[: args.limit] if args.limit else sus
    print(f"{len(points)} points, {len(sus)} suspects, checking {len(todo)}", flush=True)

    rows = []
    for i, p in enumerate(todo, 1):
        rows.append(classify(p, known))
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} {dict(Counter(r['status'] for r in rows))}", flush=True)

    by_status = Counter(r["status"] for r in rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collection": PAPERS_COLLECTION, "points": len(points), "suspects": len(sus),
        "checked": len(rows), "by_status": dict(by_status),
        "mis_keyed_duplicate_of_existing": sum(1 for r in rows if r["proposed_in_corpus"]),
        "crossref_lookups": len(_cache),
    }
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) + ["error"] if rows else ["doi"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    if args.markdown:
        Path(args.markdown).write_text(markdown(summary, rows))
    return 0 if not by_status.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
