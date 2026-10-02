#!/usr/bin/env python3
"""
Independent, layered verification of the DOI re-keys planned by
repair_doi_filenames.py, before anything is applied. READ-ONLY.

Why a separate check: an earlier corpus repair (2026-08 authors) let a single
external source decide, and Semantic Scholar wrote 3 wrong author lists out of
16 because its title matched while its authors were junk. Here no one source
can wave a record through. The proposed DOI is only ever the stored DOI with
`_` read back as `/` or `:` (never a search result), and on top of that each
planned repair must survive every layer that has evidence:

  pdf       the PDF on disk, read with pdftotext (not GROBID, not the web):
            its first two pages contain the proposed DOI, or the Crossref
            title's words. MANDATORY: the PDF is what the record is.
  crossref  Crossref's title for the proposed DOI matches the stored title
            (the audit's test, re-run here, >= 0.5)
  openalex  a second registry's title for the proposed DOI matches too
  authors   a stored author surname matches a Crossref surname (accent-
            folded, fuzzy: GROBID gave us "Dieter Soil" for Dieter Soell)
  year      stored year within 1 of Crossref's
  graph     (support only) a :Paper node for the corrected DOI already exists
            because other papers cite it

Verdict per row:
  accept   pdf agrees, at least 3 layers agree, none contradicts
  reject   any layer contradicts (a resolving title that does not match, a
           disjoint author list, a year off by more than 1, a readable PDF
           that is clearly a different paper)
  review   everything else (no readable PDF, too little evidence)

Negative control (--negative-control N, default 40): N real papers are each
paired with ANOTHER paper's corrected DOI and run through the same layers.
Every one of those deliberately wrong pairings must come out `reject`; if any
is accepted the layers are not trustworthy, the run exits non-zero and no
allow-list is written.

Outputs a CSV of every row, an allow-list of accepted point ids for
`repair_doi_filenames.py --allow-ids`, and a Markdown sheet with every reject
and a seeded random sample of accepts for a human to eyeball.

Usage (pipeline venv):
    ./spotcheck_doi_repair.py --plan dry.json --out-dir spotcheck/ [--sample 30]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_pipeline import _normalize_title_tokens, _title_similarity  # noqa: E402

QDRANT_URL = os.getenv("QDRANT_URL") or (
    f"http://{os.getenv('QDRANT_HOST', 'localhost')}:{os.getenv('QDRANT_PORT', '6333')}")
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
PDF_DIR = os.getenv("PAPERS_PDF_DIR", "/opt/munin/data/papers/pdf")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "research@muninai.org")
UA = f"MuninBot/1.0 (https://muninai.org; mailto:{ADMIN_EMAIL})"
PACE_S = 0.3

TITLE_AGREE, TITLE_CONTRADICT = 0.5, 0.3
PDF_TITLE_COVER_AGREE, PDF_TITLE_COVER_CONTRADICT = 0.6, 0.2


# ----------------------------------------------------------------------------
# sources
# ----------------------------------------------------------------------------

class Unavailable(Exception):
    pass


def http_json(url: str) -> dict | None:
    """JSON from url; None on 404; Unavailable on anything else after retries."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        time.sleep(PACE_S)
        try:
            return json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise Unavailable(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2 ** attempt)
    raise Unavailable("retries exhausted")


def crossref(doi: str) -> dict | None:
    d = http_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe=""))
    return d["message"] if d else None


def openalex(doi: str) -> dict | None:
    return http_json("https://api.openalex.org/works/https://doi.org/"
                     + urllib.parse.quote(doi, safe="/:()<>;") + f"?mailto={ADMIN_EMAIL}")


def qdrant_payloads(ids: list) -> dict:
    out = {}
    for i in range(0, len(ids), 256):
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{PAPERS_COLLECTION}/points",
            data=json.dumps({"ids": ids[i:i + 256], "with_payload":
                             ["title", "authors", "year", "pdf_path", "doi"]}).encode(),
            headers={"Content-Type": "application/json"})
        for p in json.load(urllib.request.urlopen(req, timeout=60))["result"]:
            out[p["id"]] = p["payload"]
    return out


def pdf_text(pdf_path: str | None) -> str | None:
    """First two pages as text, or None when there is no readable text."""
    if not pdf_path:
        return None
    path = Path(PDF_DIR) / Path(pdf_path).name
    if not path.is_file():
        return None
    try:
        out = subprocess.run(["pdftotext", "-l", "2", "-q", str(path), "-"],
                             capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    text = out.stdout.decode("utf-8", errors="replace")
    return text if len(text.strip()) >= 200 else None


# ----------------------------------------------------------------------------
# layers
# ----------------------------------------------------------------------------

def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def surname(name: str) -> str:
    parts = re.findall(r"[a-z][a-z'\-]+", fold(name))
    return parts[-1] if parts else ""


def authors_layer(stored: list, record: dict | None) -> tuple[str, str]:
    cr = [a.get("family") or "" for a in (record or {}).get("author") or []]
    a = {surname(x) for x in stored or [] if surname(x)}
    b = {surname(x) for x in cr if surname(x)}
    if not a or not b:
        return "none", f"stored={len(a)} crossref={len(b)}"
    hit = [(x, y) for x in a for y in b
           if x == y or (min(len(x), len(y)) >= 4 and SequenceMatcher(None, x, y).ratio() >= 0.8)]
    if hit:
        return "agree", f"{hit[0][0]}~{hit[0][1]}"
    return "contradict", f"stored {sorted(a)[:3]} vs crossref {sorted(b)[:3]}"


def year_layer(stored, record: dict | None) -> tuple[str, str]:
    parts = ((record or {}).get("issued") or {}).get("date-parts") or [[None]]
    cy = parts[0][0] if parts and parts[0] else None
    try:
        sy = int(str(stored)[:4]) if stored else None
    except ValueError:
        sy = None
    if not sy or not cy:
        return "none", f"stored={stored} crossref={cy}"
    return ("agree" if abs(sy - cy) <= 1 else "contradict"), f"{sy} vs {cy}"


def title_layer(stored: str, titles: list[str]) -> tuple[str, float]:
    sim = max((_title_similarity(stored, t) for t in titles if t), default=None)
    if sim is None:
        return "none", 0.0
    if sim >= TITLE_AGREE:
        return "agree", sim
    return ("contradict" if sim < TITLE_CONTRADICT else "none"), sim


def pdf_layer(text: str | None, new_doi: str, cr_title: str) -> tuple[str, str]:
    if text is None:
        return "none", "no readable PDF text"
    flat = re.sub(r"\s+", "", fold(text))
    doi_hit = re.sub(r"\s+", "", new_doi.lower()) in flat
    want = _normalize_title_tokens(cr_title)
    have = _normalize_title_tokens(text[:6000])
    cover = len(want & have) / len(want) if want else 0.0
    if doi_hit:
        return "agree", f"DOI printed in PDF (title cover {cover:.2f})"
    if cover >= PDF_TITLE_COVER_AGREE:
        return "agree", f"title cover {cover:.2f}"
    if want and cover < PDF_TITLE_COVER_CONTRADICT:
        return "contradict", f"title cover {cover:.2f}: PDF looks like another paper"
    return "none", f"title cover {cover:.2f}"


def check(plan: dict, payload: dict) -> dict:
    new = plan["new_doi"]
    row = {"point_id": plan["point_id"], "old_doi": plan["old_doi"], "new_doi": new,
           "stored_title": (payload.get("title") or "")[:160], "graph": plan.get("graph"),
           "graph_new_degree": plan.get("graph_new_degree")}
    try:
        cr = crossref(new)
        oa = openalex(new)
    except Unavailable as e:
        row.update(verdict="review", why=f"source unavailable: {e}")
        return row
    cr_titles = (cr or {}).get("title") or []
    oa_title = (oa or {}).get("title") or ""
    layers = {
        "crossref": title_layer(row["stored_title"], cr_titles),
        "openalex": title_layer(row["stored_title"], [oa_title]),
        "authors": authors_layer(payload.get("authors"), cr),
        "year": year_layer(payload.get("year"), cr),
        "pdf": pdf_layer(pdf_text(payload.get("pdf_path")), new, cr_titles[0] if cr_titles else ""),
    }
    if cr is None:
        layers["crossref"] = ("contradict", 0.0)  # it resolved at audit time; not now
    row.update({k: v[0] for k, v in layers.items()})
    row.update({f"{k}_detail": (round(v[1], 3) if isinstance(v[1], float) else v[1])
                for k, v in layers.items()})
    row["crossref_title"] = (cr_titles[0] if cr_titles else "")[:160]
    row["openalex_title"] = oa_title[:160]
    states = Counter(v[0] for v in layers.values())
    if states["contradict"]:
        row.update(verdict="reject", why=", ".join(k for k, v in layers.items() if v[0] == "contradict"))
    elif layers["pdf"][0] == "agree" and states["agree"] >= 3:
        row.update(verdict="accept", why=f"{states['agree']} layers agree")
    else:
        row.update(verdict="review", why=f"pdf={layers['pdf'][0]}, {states['agree']} layers agree")
    return row


# ----------------------------------------------------------------------------

def sheet(rows: list[dict], sample: list[dict]) -> str:
    def block(r: dict) -> str:
        return (f"### `{r['old_doi']}` -> [{r['new_doi']}](https://doi.org/{r['new_doi']})\n\n"
                f"- verdict: **{r['verdict']}** ({r['why']})\n"
                f"- stored title: {r['stored_title']}\n"
                f"- Crossref title: {r.get('crossref_title', '')}\n"
                f"- OpenAlex title: {r.get('openalex_title', '')}\n"
                f"- pdf: {r.get('pdf')} ({r.get('pdf_detail')}); authors: {r.get('authors')} "
                f"({r.get('authors_detail')}); year: {r.get('year')} ({r.get('year_detail')}); "
                f"graph: {r['graph']}\n")
    counts = Counter(r["verdict"] for r in rows)
    out = ["# DOI repair spot check", "",
           " | ".join(f"{k}: {v}" for k, v in sorted(counts.items())), "",
           "Tick each sample row only after opening the doi.org link and the PDF.", "",
           "## Every reject", ""]
    out += [block(r) for r in rows if r["verdict"] == "reject"]
    out += ["", f"## Random sample of accepts ({len(sample)})", ""]
    out += [block(r) for r in sample]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="repair_doi_filenames.py --report JSON")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sample", type=int, default=30, help="accepted rows for the human sheet")
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--negative-control", type=int, default=40,
                    help="wrong pairings that must all be rejected (default 40)")
    args = ap.parse_args()

    plans = [p for p in json.loads(Path(args.plan).read_text())["plans"] if p["action"] == "repair"]
    plans = plans[: args.limit] if args.limit else plans
    payloads = qdrant_payloads([p["point_id"] for p in plans])
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, p in enumerate(plans, 1):
        payload = payloads.get(p["point_id"])
        rows.append(check(p, payload) if payload else
                    {**p, "verdict": "review", "why": "point not found"})
        if i % 50 == 0 or i == len(plans):
            print(f"  {i}/{len(plans)} {dict(Counter(r['verdict'] for r in rows))}", flush=True)

    # Negative control: shift every chosen paper onto the next one's DOI.
    rng = random.Random(args.seed + 1)
    pool = [p for p in plans if p["point_id"] in payloads]
    picks = rng.sample(pool, min(args.negative_control, len(pool)))
    controls = []
    for a, b in zip(picks, picks[1:] + picks[:1]):
        if a["new_doi"].lower() == b["new_doi"].lower():
            continue
        wrong = {**a, "new_doi": b["new_doi"]}
        controls.append({**check(wrong, payloads[a["point_id"]]), "control": True})
    leaked = [c for c in controls if c["verdict"] == "accept"]
    control_ok = bool(controls) and not leaked

    accepted = [r for r in rows if r["verdict"] == "accept"] if control_ok else []
    sample = random.Random(args.seed).sample(accepted, min(args.sample, len(accepted)))
    fields = sorted({k for r in rows for k in r})
    with open(out / "spotcheck.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    if control_ok:
        (out / "allow-ids.txt").write_text("".join(f"{r['point_id']}\n" for r in accepted))
    elif (out / "allow-ids.txt").exists():
        (out / "allow-ids.txt").unlink()
    with open(out / "negative-control.csv", "w", newline="") as f:
        cf = sorted({k for r in controls for k in r})
        w = csv.DictWriter(f, fieldnames=cf)
        w.writeheader()
        w.writerows(controls)
    (out / "spotcheck-sheet.md").write_text(sheet(rows, sample))
    summary = {"rows": len(rows), "by_verdict": dict(Counter(r["verdict"] for r in rows)),
               "negative_control": {"pairings": len(controls),
                                    "by_verdict": dict(Counter(c["verdict"] for c in controls)),
                                    "accepted_wrong_pairings": len(leaked),
                                    "trustworthy": control_ok},
               "reject_reasons": dict(Counter(r["why"] for r in rows if r["verdict"] == "reject")),
               "pdf": dict(Counter(r.get("pdf") for r in rows)),
               "openalex": dict(Counter(r.get("openalex") for r in rows)),
               "authors": dict(Counter(r.get("authors") for r in rows)),
               "year": dict(Counter(r.get("year") for r in rows))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if not control_ok:
        print("NEGATIVE CONTROL FAILED: a wrong pairing was accepted (or none ran); "
              "no allow-list written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
