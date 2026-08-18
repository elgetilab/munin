#!/usr/bin/env python3
"""
==============================================================================
MUNIN - seed a fresh corpus with open-access papers
==============================================================================
A brand-new instance has an empty corpus, so paper search returns nothing and
the knowledge map is blank. That is indistinguishable from a broken install on
first impression, which is the whole reason this exists.

WHAT IT DOES
    Queries the public arXiv API, downloads N open-access PDFs into the
    pipeline's drop directory, and stops. The pipeline watcher picks them up
    from there and does the actual ingest (GROBID -> Crossref -> embed ->
    Qdrant + Neo4j), so this script never touches a database.

WHY IT QUERIES INSTEAD OF SHIPPING A LIST OF IDS
    A hardcoded list of DOIs or arXiv ids is a list that can be wrong: ids get
    transposed, papers get withdrawn, and a plausible-but-wrong identifier
    resolves to a real paper that is not the one meant. Munin's own evaluation
    is partly about that failure mode. Asking the API means every id is real by
    construction, and it lets you seed YOUR field instead of the reference
    deployment's:

        --query 'abs:"transformer" AND cat:cs.CL'
        --query 'cat:physics.bio-ph'

REDISTRIBUTION
    Nothing is committed to the repository. The PDFs are fetched at run time
    from arXiv, which permits automated access under its Terms of Use provided
    requests are paced. The default 3s delay is arXiv's own guidance; do not
    lower it.

USAGE
    seed_corpus.py [--query Q] [--limit N] [--dir PATH] [--delay SECS]

EXIT CODES
    0  papers downloaded, or nothing to do, OR the network was unreachable.
       Deliberate: a firewalled host should get a clear message and a working
       (if empty) instance, not a failed startup.
    2  bad arguments.
==============================================================================
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

# arXiv asks for a descriptive User-Agent and a delay between calls.
UA = "munin-seed/1.0 (+https://github.com/elgetilab/munin; research corpus seeding)"

# Matches the reference deployment's domain (membrane biophysics), so the demo
# corpus is topically coherent and the clustering on the knowledge map has
# something to find. Override with --query for your own field.
DEFAULT_QUERY = (
    'abs:"lipid bilayer" OR abs:"lipid membrane" OR abs:"membrane protein"'
)


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def search(query: str, limit: int, timeout: float) -> list:
    """Return [{id, title, pdf_url}] from the arXiv API, newest-relevance first."""
    qs = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
    })
    root = ET.fromstring(_get(f"{ARXIV_API}?{qs}", timeout).decode("utf-8"))

    out = []
    for entry in root.findall("a:entry", ATOM):
        raw_id = (entry.findtext("a:id", default="", namespaces=ATOM) or "")
        arxiv_id = raw_id.rsplit("/", 1)[-1]
        title = " ".join((entry.findtext("a:title", default="",
                                         namespaces=ATOM) or "").split())
        pdf = next((l.get("href") for l in entry.findall("a:link", ATOM)
                    if l.get("title") == "pdf"), None)
        if arxiv_id and pdf:
            out.append({"id": arxiv_id, "title": title, "pdf": pdf})
    return out


def _safe_name(arxiv_id: str) -> str:
    """arXiv ids contain '/' in the pre-2007 scheme (hep-th/9901001)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", arxiv_id) + ".pdf"


def download(papers: list, dest: str, delay: float, timeout: float) -> tuple:
    os.makedirs(dest, exist_ok=True)
    got = skipped = failed = 0

    for i, p in enumerate(papers, 1):
        path = os.path.join(dest, _safe_name(p["id"]))
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  [{i}/{len(papers)}] skip (already present): {p['id']}")
            skipped += 1
            continue

        if got or failed:
            time.sleep(delay)      # pace only between actual network calls

        try:
            blob = _get(p["pdf"], timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  [{i}/{len(papers)}] FAILED {p['id']}: {e}", file=sys.stderr)
            failed += 1
            continue

        if not blob.startswith(b"%PDF"):
            # arXiv serves an HTML interstitial while a PDF is being generated.
            print(f"  [{i}/{len(papers)}] FAILED {p['id']}: not a PDF "
                  f"({len(blob)} bytes)", file=sys.stderr)
            failed += 1
            continue

        # Write to a temp name first: the pipeline watcher polls this directory
        # and would happily pick up a half-written file.
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)

        print(f"  [{i}/{len(papers)}] ok {p['id']}  {p['title'][:60]}")
        got += 1

    return got, skipped, failed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Seed a fresh Munin corpus with open-access arXiv papers.")
    ap.add_argument("--query", default=os.getenv("SEED_QUERY", DEFAULT_QUERY),
                    help="arXiv API search query")
    ap.add_argument("--limit", type=int,
                    default=int(os.getenv("SEED_LIMIT", "20")),
                    help="how many papers to fetch (default 20)")
    ap.add_argument("--dir", default=os.getenv("SEED_DIR", "/papers"),
                    help="drop directory the pipeline watcher polls")
    ap.add_argument("--delay", type=float,
                    default=float(os.getenv("SEED_DELAY", "3")),
                    help="seconds between downloads (arXiv asks for 3)")
    ap.add_argument("--timeout", type=float,
                    default=float(os.getenv("SEED_TIMEOUT", "60")))
    args = ap.parse_args()

    if args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        return 2

    print(f"[seed] query : {args.query}")
    print(f"[seed] target: {args.limit} papers -> {args.dir}")

    try:
        papers = search(args.query, args.limit, args.timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as e:
        # Not an error worth failing the stack over. An offline host should end
        # up with an empty corpus and an explanation, not a boot loop.
        print(f"[seed] arXiv unreachable ({e}). Skipping.", file=sys.stderr)
        print("[seed] The instance will start with an EMPTY corpus. Add papers "
              "later by dropping PDFs into the drop directory.", file=sys.stderr)
        return 0

    if not papers:
        print("[seed] query matched nothing; try a broader --query")
        return 0

    got, skipped, failed = download(papers, args.dir, args.delay, args.timeout)
    print(f"[seed] done: {got} downloaded, {skipped} already present, "
          f"{failed} failed")
    if got:
        print("[seed] the pipeline watcher will ingest them within "
              "WATCH_POLL_SECS; follow it with:")
        print("[seed]   docker compose logs -f pipeline-watcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
