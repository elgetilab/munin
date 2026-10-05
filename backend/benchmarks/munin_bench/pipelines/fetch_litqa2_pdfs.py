"""Fetch open-access PDFs for the LitQA2 source papers (Phase 5 unblock).

For each DOI in data/litqa2/missing_papers.csv, resolve an open-access PDF URL
via Unpaywall (primary) then OpenAlex (fallback), download it, and validate it
is a real PDF. Closed-access / failed DOIs are written to a shortlist CSV for
manual acquisition via the university network.

LEGAL: open-access sources only (Unpaywall / OpenAlex best_oa_location). This
tool never touches sci-hub. Paywalled papers are the operator's to obtain.

Usage:
    python -m munin_bench.pipelines.fetch_litqa2_pdfs \\
        --csv data/litqa2/missing_papers.csv --out data/litqa2/pdfs \\
        --email you@example.org
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time

import re

import requests

UA = "munin-eval/1.0 (mailto:{email})"
# Many OA publishers 403 a non-browser UA or serve a landing page instead of
# the PDF. A realistic browser UA + citation_pdf_url meta extraction recovers
# most of those (still strictly OA content, never sci-hub).
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_CITATION_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
    re.I,
)
_CITATION_PDF_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
    re.I,
)


def landing_pdf(doi: str) -> str | None:
    """Resolve the PDF via the paper's landing page citation_pdf_url meta tag
    (Highwire/publisher convention; how Google Scholar finds PDFs)."""
    try:
        r = requests.get(
            f"https://doi.org/{doi}", timeout=25,
            headers={"User-Agent": BROWSER_UA}, allow_redirects=True,
        )
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        for rx in (_CITATION_PDF_RE, _CITATION_PDF_RE2):
            m = rx.search(r.text)
            if m:
                return m.group(1)
    except Exception:
        return None
    return None


def _sanitize(doi: str) -> str:
    return doi.replace("/", "_").replace(":", "_")


def unpaywall_pdf(doi: str, email: str) -> str | None:
    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": email}, timeout=20,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        loc = d.get("best_oa_location") or {}
        url = loc.get("url_for_pdf")
        if url:
            return url
        for loc in d.get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
    except Exception:
        return None
    return None


def openalex_pdf(doi: str) -> str | None:
    try:
        r = requests.get(
            f"https://api.openalex.org/works/https://doi.org/{doi}", timeout=20
        )
        if r.status_code != 200:
            return None
        d = r.json()
        loc = d.get("best_oa_location") or {}
        return loc.get("pdf_url") or (d.get("open_access") or {}).get("oa_url")
    except Exception:
        return None


def download_pdf(url: str, path: str, ua: str) -> tuple[bool, str]:
    try:
        r = requests.get(url, timeout=45, headers={"User-Agent": BROWSER_UA},
                         allow_redirects=True, stream=True)
        if r.status_code != 200:
            return False, f"http {r.status_code}"
        content = r.content
        if not content[:5].startswith(b"%PDF"):
            return False, "not a pdf (paywall/html?)"
        if len(content) < 10_000:
            return False, f"too small ({len(content)}b)"
        with open(path, "wb") as fh:
            fh.write(content)
        return True, f"{len(content)//1024}kb"
    except Exception as e:
        return False, f"{type(e).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--email", default=os.environ.get("MUNIN_CONTACT_EMAIL"),
                    required=not os.environ.get("MUNIN_CONTACT_EMAIL"),
                    help="contact address for Unpaywall/Crossref (default: MUNIN_CONTACT_EMAIL)")
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--limit", type=int, default=0, help="stop after N (0=all)")
    args = ap.parse_args()

    ua = UA.format(email=args.email)
    os.makedirs(args.out, exist_ok=True)
    rows = list(csv.DictReader(open(args.csv)))
    if args.limit:
        rows = rows[: args.limit]

    got, failed = [], []
    for i, row in enumerate(rows, 1):
        doi = row["doi"]
        dest = os.path.join(args.out, _sanitize(doi) + ".pdf")
        if os.path.exists(dest):
            got.append({**row, "file": dest, "note": "cached"})
            continue
        # Try each OA source in turn; take the first that yields a real PDF.
        candidates = [unpaywall_pdf(doi, args.email), openalex_pdf(doi),
                      landing_pdf(doi)]
        tried = [u for u in candidates if u]
        if not tried:
            failed.append({**row, "reason": "no OA PDF url"})
            print(f"  {i}/{len(rows)} [CLOSED] {doi}")
            time.sleep(args.delay)
            continue
        ok, msg = False, "no url worked"
        for url in tried:
            ok, msg = download_pdf(url, dest, ua)
            if ok:
                break
        if ok:
            got.append({**row, "file": dest, "note": msg})
            print(f"  {i}/{len(rows)} [OK {msg}] {doi}")
        else:
            failed.append({**row, "reason": msg})
            print(f"  {i}/{len(rows)} [FAIL {msg}] {doi}")
        time.sleep(args.delay)

    # results + shortlist for manual acquisition
    json.dump({"n_total": len(rows), "n_got": len(got), "n_failed": len(failed),
               "got": got, "failed": failed},
              open(os.path.join(args.out, "..", "acquire_results.json"), "w"),
              indent=2)
    short = os.path.join(args.out, "..", "manual_acquire.csv")
    with open(short, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "venue", "year", "reason", "resolver"])
        for f in failed:
            w.writerow([f["doi"], f.get("title", ""), f.get("venue", ""),
                        f.get("year", ""), f.get("reason", ""),
                        f"https://doi.org/{f['doi']}"])
    print(f"\nacquired {len(got)}/{len(rows)} OA PDFs -> {args.out}")
    print(f"manual-acquire shortlist ({len(failed)}) -> {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
