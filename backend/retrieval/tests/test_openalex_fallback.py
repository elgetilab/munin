"""A failed Semantic Scholar query is answered by OpenAlex, in the same row shape.

Run in the retrieval image; see tests/README.md. Standalone: prints [PASS]/[FAIL]
and exits non-zero on failure. No network: the HTTP transport is faked.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from mcp.tools import papers  # noqa: E402

WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1/abc",
    "title": "Nanodisc gating",
    "publication_year": 2022,
    "cited_by_count": 7,
    "authorships": [{"author": {"display_name": "A. Author"}}],
    "primary_location": {"source": {"display_name": "J. Membranes"}},
    "open_access": {"oa_url": "https://oa.example/p.pdf"},
    "abstract_inverted_index": {"gating": [1], "Channel": [0]},
}
failures = 0


def check(name, ok):
    global failures
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def run(s2_status=429, openalex_status=200):
    seen = {}

    def handler(request):
        if "semanticscholar" in request.url.host:
            seen["s2"] = True
            return httpx.Response(s2_status, json={"data": []})
        seen["openalex"] = request.url
        return httpx.Response(openalex_status, json={"results": [WORK]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await papers._semantic_scholar_one(client, "gating", 5, "2020-2024")

    return asyncio.run(go()), seen


rows, seen = run()
row = rows[0] if rows else {}
check("a 429 falls back to OpenAlex", len(rows) == 1 and "openalex" in seen)
check("DOI loses its URL prefix", row.get("doi") == "10.1/abc")
check("abstract is rebuilt in word order", row.get("abstract") == "Channel gating")
check("fields map across", row.get("journal") == "J. Membranes" and row.get("citation_count") == 7
      and row.get("authors") == ["A. Author"] and row.get("open_access_pdf"))
check("year becomes a date filter", "from_publication_date:2020-01-01" in str(seen["openalex"]).replace("%3A", ":"))

rows, seen = run(s2_status=500)
check("a 500 falls back too", len(rows) == 1)
rows, seen = run(s2_status=200)
check("a working Semantic Scholar is not replaced", "openalex" not in seen)
rows, _ = run(openalex_status=503)
check("both failing is an empty result", rows == [])

for given, want in [("2020", "from_publication_date:2020-01-01,to_publication_date:2020-12-31"),
                    ("2020-", "from_publication_date:2020-01-01"),
                    ("-2024", "to_publication_date:2024-12-31"),
                    ("", "")]:
    check(f"year {given!r}", papers._openalex_year_filter(given) == want)

sys.exit(1 if failures else 0)
