"""
Standalone unit tests for compile_latex's argument-validation
branches. Covers the new artifact_id / diff modes added 2026-04-28
plus pre-existing source / bibliography / extra_files validation.

These tests exercise only the early-return branches in compile_latex
that fire BEFORE the conversation-context lookup and the artifact-
store fetch. So they are pure-function: no DB, no sandbox, no model
call, no httpx.

Behavioural coverage of the artifact_id+diff happy path lives in
scripts/test_compile_latex_diff_flow.py (end-to-end smoke test
against a deployed retrieval service).

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_compile_latex_args.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

from mcp.tools.latex import (  # noqa: E402
    _MAX_SOURCE_CHARS,
    _MAX_BIBLIOGRAPHY_CHARS,
    _MAX_EXTRA_FILES,
    compile_latex,
)


def _run(coro) -> dict:
    return asyncio.run(coro)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


def _is_error(result: dict, needle: str) -> bool:
    """Helper: result must have an `error` field containing ``needle``
    (case-insensitive substring match)."""
    err = (result or {}).get("error") or ""
    return needle.lower() in err.lower()


# ---------------------------------------------------------------------------
# Mode-selection: exactly one of source / artifact_id required
# ---------------------------------------------------------------------------

def test_no_source_no_artifact_id_returns_error() -> bool:
    res = _run(compile_latex())
    return _check(
        "no source and no artifact_id → error",
        _is_error(res, "must pass either 'source'") or _is_error(res, "artifact_id"),
        f"got {res!r}",
    )


def test_empty_source_treated_as_missing() -> bool:
    """Empty / whitespace-only source must NOT be silently accepted —
    it'd waste a sandbox compile. Same shape as missing source."""
    res = _run(compile_latex(source="   "))
    return _check(
        "whitespace-only source → error",
        _is_error(res, "must pass either"),
        f"got {res!r}",
    )


def test_both_source_and_artifact_id_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            artifact_id="art_abc",
        )
    )
    return _check(
        "both source and artifact_id → error",
        _is_error(res, "not both"),
        f"got {res!r}",
    )


def test_diff_without_artifact_id_returns_error() -> bool:
    """diff is only valid alongside artifact_id — guard against the
    model emitting a diff while passing full source."""
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            diff="@@ -1,1 +1,1 @@\n-x\n+y\n",
        )
    )
    return _check(
        "diff without artifact_id → error",
        _is_error(res, "diff' is only valid"),
        f"got {res!r}",
    )


def test_diff_alone_returns_error() -> bool:
    """No source, no artifact_id, just diff → falls through to the
    no-source-no-artifact-id branch."""
    res = _run(compile_latex(diff="@@ -1,1 +1,1 @@\n-x\n+y\n"))
    return _check(
        "diff alone (no source, no artifact_id) → error",
        _is_error(res, "must pass either") or _is_error(res, "diff' is only"),
        f"got {res!r}",
    )


# ---------------------------------------------------------------------------
# Source size cap
# ---------------------------------------------------------------------------

def test_source_too_large_returns_error() -> bool:
    huge = "\\documentclass{article}\\begin{document}" + ("x" * (_MAX_SOURCE_CHARS + 1)) + "\\end{document}"
    res = _run(compile_latex(source=huge))
    return _check(
        "source over cap → error",
        _is_error(res, "source too large"),
        f"got {res!r}",
    )


def test_source_at_cap_passes_validation() -> bool:
    """Exactly at the cap should NOT trigger the size error. We can't
    actually compile here (no conv context), so we expect the next
    error in the chain — the user/conversation context refusal."""
    at_cap = "x" * _MAX_SOURCE_CHARS
    res = _run(compile_latex(source=at_cap))
    return _check(
        "source at cap → does NOT trip size error",
        not _is_error(res, "source too large"),
        f"got {res!r}",
    )


# ---------------------------------------------------------------------------
# Bibliography validation
# ---------------------------------------------------------------------------

def test_bibliography_wrong_type_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            bibliography=123,  # type: ignore[arg-type]
        )
    )
    return _check(
        "non-string bibliography → error",
        _is_error(res, "bibliography must be a string"),
        f"got {res!r}",
    )


def test_bibliography_too_large_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            bibliography="@article{x}\n" * (_MAX_BIBLIOGRAPHY_CHARS // 5),
        )
    )
    return _check(
        "bibliography over cap → error",
        _is_error(res, "bibliography too large"),
        f"got {res!r}",
    )


# ---------------------------------------------------------------------------
# extra_files validation
# ---------------------------------------------------------------------------

def test_extra_files_not_dict_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            extra_files=["foo.sty", "bar.cls"],  # type: ignore[arg-type]
        )
    )
    return _check(
        "extra_files as list → error",
        _is_error(res, "extra_files must be a dict"),
        f"got {res!r}",
    )


def test_extra_files_too_many_returns_error() -> bool:
    too_many = {f"f{i}.sty": "% noop" for i in range(_MAX_EXTRA_FILES + 1)}
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            extra_files=too_many,
        )
    )
    return _check(
        "extra_files over cap → error",
        _is_error(res, "too many extra_files"),
        f"got {res!r}",
    )


def test_extra_files_empty_key_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            extra_files={"": "content"},
        )
    )
    return _check(
        "extra_files with empty key → error",
        _is_error(res, "non-empty strings"),
        f"got {res!r}",
    )


def test_extra_files_non_string_value_returns_error() -> bool:
    res = _run(
        compile_latex(
            source="\\documentclass{article}\\begin{document}x\\end{document}",
            extra_files={"foo.sty": 42},  # type: ignore[arg-type]
        )
    )
    return _check(
        "extra_files with non-string value → error",
        _is_error(res, "must be a string"),
        f"got {res!r}",
    )


# ---------------------------------------------------------------------------
# Negative: artifact_id mode validation passes through to the next branch
# ---------------------------------------------------------------------------

def test_artifact_id_alone_passes_validation() -> bool:
    """artifact_id (without source/diff) is a valid mode — the next
    error should be the missing user/conversation context, NOT a
    validation refusal."""
    res = _run(compile_latex(artifact_id="art_xyz"))
    return _check(
        "artifact_id alone → does NOT trip mode-selection error",
        not _is_error(res, "must pass either")
        and not _is_error(res, "not both")
        and not _is_error(res, "diff' is only valid"),
        f"got {res!r}",
    )


def test_artifact_id_with_diff_passes_validation() -> bool:
    """The new iteration mode — artifact_id + diff. Should pass
    validation; later branches (context lookup) will then surface."""
    res = _run(
        compile_latex(
            artifact_id="art_xyz",
            diff="@@ -1,1 +1,1 @@\n-x\n+y\n",
        )
    )
    return _check(
        "artifact_id + diff → does NOT trip mode-selection error",
        not _is_error(res, "must pass either")
        and not _is_error(res, "not both")
        and not _is_error(res, "diff' is only valid"),
        f"got {res!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_no_source_no_artifact_id_returns_error,
    test_empty_source_treated_as_missing,
    test_both_source_and_artifact_id_returns_error,
    test_diff_without_artifact_id_returns_error,
    test_diff_alone_returns_error,
    test_source_too_large_returns_error,
    test_source_at_cap_passes_validation,
    test_bibliography_wrong_type_returns_error,
    test_bibliography_too_large_returns_error,
    test_extra_files_not_dict_returns_error,
    test_extra_files_too_many_returns_error,
    test_extra_files_empty_key_returns_error,
    test_extra_files_non_string_value_returns_error,
    test_artifact_id_alone_passes_validation,
    test_artifact_id_with_diff_passes_validation,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
