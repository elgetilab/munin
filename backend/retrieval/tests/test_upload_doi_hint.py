"""
The upload DOI hint is given only for unambiguous doi_*.pdf names.

Regression for the 2026-09 review: `_doi_from_upload_filename` restored only
the first `_` of `doi_<doi with / and : as _>.pdf`, and the pipeline used the
hint as the authoritative DOI, so an upload named for `10.1093/molehr/3.5.431`
was keyed to the nonexistent `10.1093/molehr_3.5.431`. Ambiguous names now
give no hint and take the GROBID path with its title-based DOI recovery.

    docker exec munin-retrieval python /app/tests/test_upload_doi_hint.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

CASES = {
    "doi_10.1038_nature12373.pdf": "10.1038/nature12373",
    "/tmp/x/doi_10.1021_cr400056a.pdf": "10.1021/cr400056a",
    "doi_10.1093_molehr_3.5.431.pdf": None,           # ambiguous
    "doi_10.21203_rs.3.rs-1484102_v1.pdf": None,      # ambiguous
    "paper.pdf": None,
    "doi_10.1038_.pdf": None,
}

failed = 0
for name, want in CASES.items():
    got = main._doi_from_upload_filename(name)
    ok = got == want
    failed += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name!r} -> {got!r}")
print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
sys.exit(1 if failed else 0)
