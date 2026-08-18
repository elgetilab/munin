"""
Tests for database.get_pdf_path, the single DOI -> file resolver.

The bug these cover (UPLOAD-INGEST-REPAIR-PLAN.md defect 3): there used to be
two resolvers. main.py's matched the exact lowercase filename only, while the
MCP one also tried a legacy layout and a case-insensitive pass. The pipeline
lowercases DOIs when it names files; publishers and the crawler do not. So
GET /paper/{doi}/pdf returned 404 for papers that were sitting on disk under
the registrant's original case, while read_paper served the very same file.

Pure filesystem, no Qdrant and no network.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_pdf_path_resolution.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{f' - {detail}' if detail and not ok else ''}")
    return ok


def _corpus(*filenames: str):
    """Temp dir populated with `filenames`, wired in as PAPERS_PDF_DIR."""
    td = tempfile.TemporaryDirectory()
    for n in filenames:
        (Path(td.name) / n).write_bytes(b"%PDF-1.4 fake")
    database.PAPERS_PDF_DIR = td.name
    database._pdf_index = None          # drop any index from a prior case
    database._pdf_index_mtime = -1.0
    return td


def test_exact_match() -> bool:
    with _corpus("doi_10.1234_example.pdf") as _:
        got = database.get_pdf_path("10.1234/example")
        return _check("exact filename resolves", got is not None and got.endswith("doi_10.1234_example.pdf"), str(got))


def test_case_mismatch_resolves() -> bool:
    """The regression. Payload DOI is lowercased, the file kept its case."""
    with _corpus("doi_10.1017_S0033583506004306.pdf") as _:
        got = database.get_pdf_path("10.1017/s0033583506004306")
        return _check(
            "case-mismatched DOI still resolves",
            got is not None and got.endswith("doi_10.1017_S0033583506004306.pdf"),
            f"got {got!r}; this is the 404 users saw on 22 papers",
        )


def test_legacy_layout_resolves() -> bool:
    with _corpus("10.1234_legacy.pdf") as _:
        got = database.get_pdf_path("10.1234/legacy")
        return _check("legacy no-prefix filename resolves", got is not None, str(got))


def test_colon_in_doi_resolves() -> bool:
    """paper_crawler substitutes ':' as well as '/'."""
    with _corpus("doi_10.1234_a_b.pdf") as _:
        got = database.get_pdf_path("10.1234/a:b")
        return _check("colon-substituted filename resolves", got is not None, str(got))


def test_missing_returns_none() -> bool:
    with _corpus("doi_10.1234_other.pdf") as _:
        return _check("absent DOI returns None", database.get_pdf_path("10.1234/absent") is None)


def test_empty_doi_returns_none() -> bool:
    with _corpus("doi_10.1234_example.pdf") as _:
        return _check(
            "empty / None DOI returns None",
            database.get_pdf_path("") is None and database.get_pdf_path(None) is None,
        )


def test_index_refreshes_when_corpus_changes() -> bool:
    """A newly ingested paper must be findable without a restart: the index
    is keyed on the directory mtime, which changes when a file appears."""
    with _corpus("doi_10.1234_first.pdf") as td:
        database.get_pdf_path("10.1234/absent")          # force an index build
        (Path(td) / "doi_10.1234_SECOND.pdf").write_bytes(b"%PDF-1.4 fake")
        got = database.get_pdf_path("10.1234/second")     # lowercase, as the payload has it
        return _check("index picks up a newly written PDF", got is not None, str(got))


def test_exact_hit_does_not_build_the_index() -> bool:
    """The common path must not pay for a listdir of ~66k entries."""
    with _corpus("doi_10.1234_example.pdf") as _:
        database.get_pdf_path("10.1234/example")
        return _check("exact hit skips the index build", database._pdf_index is None)


def main() -> int:
    tests = [
        test_exact_match,
        test_case_mismatch_resolves,
        test_legacy_layout_resolves,
        test_colon_in_doi_resolves,
        test_missing_returns_none,
        test_empty_doi_returns_none,
        test_index_refreshes_when_corpus_changes,
        test_exact_hit_does_not_build_the_index,
    ]
    original = database.PAPERS_PDF_DIR
    results: list[bool] = []
    try:
        for t in tests:
            try:
                results.append(t())
            except Exception:
                traceback.print_exc()
                results.append(False)
    finally:
        database.PAPERS_PDF_DIR = original
        database._pdf_index = None
        database._pdf_index_mtime = -1.0
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
