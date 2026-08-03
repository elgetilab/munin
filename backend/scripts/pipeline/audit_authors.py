#!/usr/bin/env python3
"""
Read-only audit of author metadata in the papers corpus.

Classifies every record in the Qdrant papers collection as clean / damaged /
empty, counts the damage by kind, and reports how much of it a DOI lookup
could repair. Writes NOTHING: this is the report you read before authorising
the backfill (repair_authors.py, Phase C3).

Background: author lists come from GROBID's parse of the PDF text layer.
Until 2026-08 the Crossref merge in paper_pipeline.py enriched title, journal
and year but never authors, so PDF artefacts went straight into the corpus:
affiliation superscripts glued to surnames ("R Tait1"), footnote daggers,
split diacritics ("Gu ¨nther Schu ¨tz"), affiliations parsed as people
("Theodor-Kocher Institute"), and dropped authors.

Usage:
    ./audit_authors.py                        # summary to stdout
    ./audit_authors.py --report out.json      # + machine-readable report
    ./audit_authors.py --markdown out.md      # + human-readable report
    ./audit_authors.py --resolve 40           # sample N DOIs against Crossref
                                              #   to estimate repair yield
    ./audit_authors.py --list-damaged 50      # print example records

Environment: QDRANT_HOST / QDRANT_PORT / PAPERS_COLLECTION, same defaults as
the retrieval service (papers_bge since the 2026-07 encoder cutover).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from author_names import is_damaged, sanitize_authors, best_author_list  # noqa: E402

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "research@muninai.org")

_PAGE = 4096
_CROSSREF_PACE_S = 0.6      # polite pool; the audit is never in a hurry


def _qdrant_scroll() -> list[dict]:
    """Page the whole collection, payload only (no vectors)."""
    out: list[dict] = []
    offset = None
    while True:
        body = {
            "limit": _PAGE,
            "with_payload": ["authors", "title", "doi", "year", "journal",
                             "_ingest_source", "_crossref_title_rejected"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        req = urllib.request.Request(
            f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/"
            f"{PAPERS_COLLECTION}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        result = json.load(urllib.request.urlopen(req, timeout=120))["result"]
        for point in result["points"]:
            payload = dict(point.get("payload") or {})
            payload["_point_id"] = point.get("id")
            out.append(payload)
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return out


def _crossref_authors(doi: str) -> list[str] | None:
    """Author list for a DOI, or None on any failure. Never raises."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"MuninBot/1.0 (https://muninai.org; mailto:{ADMIN_EMAIL})"})
    try:
        msg = json.load(urllib.request.urlopen(req, timeout=20))["message"]
    except Exception:
        return None
    return [f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (msg.get("author") or []) if a.get("family")]


def classify(records: list[dict]) -> dict:
    """Split the corpus into clean / damaged / empty and tally the damage."""
    buckets = {"clean": [], "damaged": [], "empty": []}
    reasons = Counter()
    for r in records:
        authors = r.get("authors") or []
        if not authors:
            buckets["empty"].append(r)
            continue
        damaged, why = is_damaged(authors)
        if damaged:
            buckets["damaged"].append(r)
            reasons.update(why)
        else:
            buckets["clean"].append(r)
    return {"buckets": buckets, "reasons": reasons}


def _repairable(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """(has DOI, no DOI) split. Only the first group is in Phase C3 scope."""
    with_doi = [r for r in records if r.get("doi")]
    without = [r for r in records if not r.get("doi")]
    return with_doi, without


def sample_yield(records: list[dict], n: int) -> dict:
    """Ask Crossref for N random affected records and report what comes back.

    Reports not just "did it answer" but WHICH source would win. Note that
    both outcomes are still a write: when the stored names win they win in
    their sanitized form ("Herv6 Bottin" -> "Hervé Bottin"), so the record
    changes either way. The split matters only for judging how much of the
    repair depends on the network.
    """
    pool = [r for r in records if r.get("doi")]
    if not pool:
        return {}
    picked = random.sample(pool, min(n, len(pool)))
    resolved = adopted = kept = failed = 0
    examples = []
    for r in picked:
        names = _crossref_authors(r["doi"])
        time.sleep(_CROSSREF_PACE_S)
        if names is None:
            failed += 1
            continue
        if not names:
            failed += 1
            continue
        resolved += 1
        current = sanitize_authors(r.get("authors"))
        chosen = best_author_list(current, names)
        if chosen == current and current:
            kept += 1
        else:
            adopted += 1
            if len(examples) < 8:
                examples.append({
                    "doi": r["doi"],
                    "stored": (r.get("authors") or [])[:6],
                    "crossref": names[:6],
                })
    return {"sampled": len(picked), "crossref_resolved": resolved,
            "would_adopt_crossref": adopted,
            "would_keep_sanitized_stored": kept,
            "failed": failed, "examples": examples}


def build_report(records: list[dict], resolve_n: int) -> dict:
    cls = classify(records)
    b, reasons = cls["buckets"], cls["reasons"]
    affected = b["damaged"] + b["empty"]
    with_doi, without_doi = _repairable(affected)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": PAPERS_COLLECTION,
        "totals": {
            "records": len(records),
            "clean": len(b["clean"]),
            "damaged": len(b["damaged"]),
            "empty": len(b["empty"]),
        },
        "repair_scope": {
            "affected": len(affected),
            "with_doi": len(with_doi),
            "without_doi": len(without_doi),
        },
        "damage_reasons": dict(reasons.most_common()),
        "quarantine_candidates": sum(
            1 for r in affected if r.get("_crossref_title_rejected")),
    }
    if resolve_n:
        report["crossref_sample"] = sample_yield(affected, resolve_n)
    return report


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "n/a"


def to_markdown(report: dict) -> str:
    t = report["totals"]
    s = report["repair_scope"]
    lines = [
        f"# Author metadata audit - `{report['collection']}`",
        "",
        f"Generated {report['generated_at']}. Read-only.",
        "",
        "| State | Records | Share |",
        "|---|---:|---:|",
        f"| Clean | {t['clean']} | {_pct(t['clean'], t['records'])} |",
        f"| Damaged | {t['damaged']} | {_pct(t['damaged'], t['records'])} |",
        f"| No authors | {t['empty']} | {_pct(t['empty'], t['records'])} |",
        f"| **Total** | **{t['records']}** | |",
        "",
        f"In repair scope (damaged or empty, DOI present): **{s['with_doi']}**. "
        f"Out of scope for lack of a DOI: {s['without_doi']}.",
        "",
        "## Damage by kind",
        "",
        "| Kind | Author strings |",
        "|---|---:|",
    ]
    for kind, count in report["damage_reasons"].items():
        lines.append(f"| `{kind}` | {count} |")
    if report.get("quarantine_candidates"):
        lines += ["", f"{report['quarantine_candidates']} affected records also "
                      "carry `_crossref_title_rejected`, i.e. the ingest guard "
                      "already flagged their metadata as belonging to a "
                      "different paper. These are report-only: the backfill "
                      "must not rewrite authors on a record whose identity is "
                      "in doubt."]
    sample = report.get("crossref_sample")
    if sample:
        lines += [
            "", "## Crossref yield (sampled)", "",
            f"- sampled: {sample['sampled']}",
            f"- Crossref returned authors: {sample['crossref_resolved']}",
            f"- would adopt Crossref: {sample['would_adopt_crossref']}",
            f"- would keep stored names, sanitized (already fuller): "
            f"{sample['would_keep_sanitized_stored']}",
            f"- failed / no authors: {sample['failed']}",
        ]
        if sample.get("examples"):
            lines += ["", "### Examples", ""]
            for ex in sample["examples"]:
                lines += [f"- `{ex['doi']}`",
                          f"  - stored: `{ex['stored']}`",
                          f"  - crossref: `{ex['crossref']}`"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", metavar="PATH", help="write the JSON report here")
    ap.add_argument("--markdown", metavar="PATH", help="write a Markdown report here")
    ap.add_argument("--resolve", type=int, default=0, metavar="N",
                    help="sample N affected DOIs against Crossref (slow, network)")
    ap.add_argument("--list-damaged", type=int, default=0, metavar="N",
                    help="print N damaged records as examples")
    ap.add_argument("--seed", type=int, default=7, help="sampling seed")
    args = ap.parse_args()

    random.seed(args.seed)
    print(f"[audit] scrolling {PAPERS_COLLECTION} on "
          f"{QDRANT_HOST}:{QDRANT_PORT} ...", file=sys.stderr)
    records = _qdrant_scroll()
    print(f"[audit] {len(records)} records", file=sys.stderr)

    report = build_report(records, args.resolve)
    t, s = report["totals"], report["repair_scope"]
    print(f"records   : {t['records']}")
    print(f"  clean   : {t['clean']} ({_pct(t['clean'], t['records'])})")
    print(f"  damaged : {t['damaged']} ({_pct(t['damaged'], t['records'])})")
    print(f"  empty   : {t['empty']} ({_pct(t['empty'], t['records'])})")
    print(f"in repair scope (has DOI): {s['with_doi']}  "
          f"(no DOI: {s['without_doi']})")
    print("\ndamage by kind:")
    for kind, count in report["damage_reasons"].items():
        print(f"  {kind:20s} {count}")
    if report.get("crossref_sample"):
        cs = report["crossref_sample"]
        print(f"\ncrossref sample: {cs['crossref_resolved']}/{cs['sampled']} "
              f"resolved, adopt crossref {cs['would_adopt_crossref']}, "
              f"keep sanitized stored {cs['would_keep_sanitized_stored']}, "
              f"failed {cs['failed']}")

    if args.list_damaged:
        cls = classify(records)
        for r in cls["buckets"]["damaged"][:args.list_damaged]:
            print(f"\n  doi   : {r.get('doi')}")
            print(f"  title : {(r.get('title') or '')[:70]}")
            print(f"  authors: {r.get('authors')}")

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n[audit] JSON report -> {args.report}", file=sys.stderr)
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(report))
        print(f"[audit] Markdown report -> {args.markdown}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
