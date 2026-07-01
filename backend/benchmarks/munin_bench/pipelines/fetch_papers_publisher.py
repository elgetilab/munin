"""Per-publisher PDF fetcher for institutionally-accessible papers.

Runs from a host on the institution's network (hugin has a Universitaet Leipzig
IP). The blocker for these DOIs is NOT a paywall but a Cloudflare-style
JS/TLS-fingerprint challenge that plain `requests` fails; `curl_cffi` with
Chrome impersonation passes it. On top of that we add per-publisher handlers
that know where each publisher's PDF lives (most don't expose the
`citation_pdf_url` meta tag).

LEGAL / ToS: this fetches content the institution legitimately subscribes to,
for a one-time research benchmark. It is rate-limited and single-threaded to
respect publisher terms and avoid getting the shared institutional IP flagged.
Not for bulk/systematic harvesting. Never touches sci-hub.

Usage:
    PYTHONPATH=<deps> python -m munin_bench.pipelines.fetch_papers_publisher \\
        --csv data/litqa2/manual_acquire.csv --out data/litqa2/pdfs \\
        --delay 3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time

from curl_cffi import requests as creq

CIT_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', re.I)
CIT_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']', re.I)
PII_RE = re.compile(r'/pii/([A-Z0-9]+)', re.I)
# generic: an <a> whose href looks like a PDF download
HREF_PDF_RE = re.compile(r'href=["\']([^"\']*(?:pdf|download)[^"\']*)["\']', re.I)


def _abs(base_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    from urllib.parse import urljoin
    return urljoin(base_url, href)


def citation_meta(html: str) -> str | None:
    for rx in (CIT_RE, CIT_RE2):
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


# --- per-publisher candidate PDF-URL builders -------------------------------
# Each takes (doi, landing_url, landing_html) and yields candidate PDF URLs in
# priority order. The driver tries each and keeps the first that is a real PDF.
def h_pnas(doi, url, html):
    yield f"https://www.pnas.org/doi/pdf/{doi}?download=true"


def h_science(doi, url, html):
    yield f"https://www.science.org/doi/pdf/{doi}?download=true"
    yield f"https://www.science.org/doi/epdf/{doi}"


def h_nature(doi, url, html):
    # landing url .../articles/<id>
    m = re.search(r"/articles/([^/?#]+)", url)
    if m:
        yield f"https://www.nature.com/articles/{m.group(1)}.pdf"


def h_elsevier(doi, url, html):
    # linkinghub redirect carries the PII; sciencedirect serves pdfft
    m = PII_RE.search(url) or PII_RE.search(html)
    if m:
        pii = m.group(1)
        yield (f"https://www.sciencedirect.com/science/article/pii/{pii}"
               f"/pdfft?isDTMRedir=true&download=true")


def h_mdpi(doi, url, html):
    # MDPI landing has an <a ...>/pdf?version=</a>; also base+/pdf
    for m in HREF_PDF_RE.finditer(html):
        if "/pdf" in m.group(1):
            yield _abs(url, m.group(1))
    yield url.rstrip("/") + "/pdf"


def h_elife(doi, url, html):
    m = re.search(r"/articles/(\d+)", url)
    if m:
        aid = m.group(1)
        yield f"https://elifesciences.org/articles/{aid}.pdf"
        yield f"https://elifesciences.org/download/articles/{aid}.pdf"


def h_wiley(doi, url, html):
    yield f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true"


def h_oxford(doi, url, html):
    cm = citation_meta(html)
    if cm:
        yield cm


def h_frontiers(doi, url, html):
    yield url.rstrip("/") + "/pdf"


HANDLERS = {
    "10.1073": h_pnas, "10.1126": h_science, "10.1038": h_nature,
    "10.1016": h_elsevier, "10.3390": h_mdpi, "10.7554": h_elife,
    "10.1002": h_wiley, "10.1111": h_wiley, "10.1093": h_oxford,
    "10.3389": h_frontiers,
}


def candidate_urls(doi, url, html):
    # publisher-specific first, then citation_pdf_url, then any pdf-ish href
    pref = doi.split("/")[0]
    seen = set()
    h = HANDLERS.get(pref)
    if h:
        for u in h(doi, url, html):
            if u and u not in seen:
                seen.add(u); yield u
    cm = citation_meta(html)
    if cm and cm not in seen:
        seen.add(cm); yield cm
    for m in HREF_PDF_RE.finditer(html):
        u = _abs(url, m.group(1))
        if u not in seen:
            seen.add(u); yield u


def _sanitize(doi):
    return doi.replace("/", "_").replace(":", "_")


def fetch_one(doi, out_dir, delay):
    dest = os.path.join(out_dir, _sanitize(doi) + ".pdf")
    if os.path.exists(dest):
        return "cached"
    s = creq.Session(impersonate="chrome")
    try:
        land = s.get(f"https://doi.org/{doi}", timeout=30, allow_redirects=True)
    except Exception as e:
        return f"landing ERR {type(e).__name__}"
    if land.content[:5].startswith(b"%PDF"):  # doi.org resolved straight to a PDF
        open(dest, "wb").write(land.content)
        return f"OK direct {len(land.content)//1024}kb"
    if land.status_code != 200:
        return f"landing {land.status_code}"
    for url in candidate_urls(doi, land.url, land.text):
        try:
            time.sleep(delay)
            p = s.get(url, timeout=45, headers={"Referer": land.url},
                      allow_redirects=True)
            if p.status_code == 200 and p.content[:5].startswith(b"%PDF") \
                    and len(p.content) > 10_000:
                open(dest, "wb").write(p.content)
                return f"OK {len(p.content)//1024}kb"
        except Exception:
            continue
    return "no pdf found"


# Publishers known to hard-block automated HTTP even with Chrome impersonation
# (Elsevier/ScienceDirect, Oxford, Wiley). Skipped up front in gentle mode so we
# don't waste hits / provoke the shared IP; they go straight to the browser CSV.
HARD_BLOCK_PREFIXES = {"10.1016", "10.1093", "10.1002", "10.1111"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=12.0,
                    help="seconds between papers (gentle: protect the shared IP)")
    ap.add_argument("--block-after", type=int, default=2,
                    help="skip a publisher after N consecutive failures")
    ap.add_argument("--skip-hard", action="store_true", default=True,
                    help="skip known hard-block publishers (Elsevier/Oxford/Wiley)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = list(csv.DictReader(open(args.csv)))
    if args.limit:
        rows = rows[: args.limit]

    got, blocked = [], []
    consec_fail: dict[str, int] = {}
    dead: set[str] = set()  # publishers tripped the circuit breaker
    for i, r in enumerate(rows, 1):
        doi = r["doi"]
        pre = doi.split("/")[0]
        if (args.skip_hard and pre in HARD_BLOCK_PREFIXES) or pre in dead:
            reason = "hard-block publisher" if pre in HARD_BLOCK_PREFIXES else "publisher circuit-broken"
            blocked.append({**r, "result": reason})
            print(f"  {i}/{len(rows)} [skip: {reason}] {doi}")
            continue
        res = fetch_one(doi, args.out, min(args.delay, 4.0))
        ok = res.startswith("OK") or res == "cached"
        if ok:
            got.append({**r, "result": res})
            consec_fail[pre] = 0
        else:
            blocked.append({**r, "result": res})
            consec_fail[pre] = consec_fail.get(pre, 0) + 1
            if consec_fail[pre] >= args.block_after:
                dead.add(pre)  # stop hammering this publisher
        print(f"  {i}/{len(rows)} [{res}] {doi}")
        time.sleep(args.delay)

    json.dump({"n": len(rows), "got": len(got), "blocked": len(blocked),
               "dead_publishers": sorted(dead)},
              open(os.path.join(args.out, "..", "publisher_fetch_results.json"), "w"),
              indent=2)
    # clean CSV of everything still needing a browser grab
    short = os.path.join(args.out, "..", "browser_grab.csv")
    with open(short, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "venue", "year", "why", "url"])
        for b in blocked:
            w.writerow([b["doi"], b.get("title", ""), b.get("venue", ""),
                        b.get("year", ""), b["result"], f"https://doi.org/{b['doi']}"])
    print(f"\ngentle publisher fetch: {len(got)}/{len(rows)} acquired; "
          f"{len(blocked)} -> browser_grab.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
