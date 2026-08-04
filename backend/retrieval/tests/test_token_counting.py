"""
Standalone tests for real-tokenizer context-budget counting
(chat_context.approx_tokens, P1 #8 from docs/architecture/HARNESS-AUDIT-2026-05.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_token_counting.py
Or locally:
    python backend/retrieval/tests/test_token_counting.py

The fallback path (no tokenizer file) is always exercised. The
real-tokenizer path needs the `tokenizers` library; when it isn't
installed the corresponding cases print [SKIP] instead of failing —
the container run, where the tokenizer is mounted, covers them fully.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_context  # noqa: E402

try:
    import tokenizers  # noqa: F401
    _HAVE_TOKENIZERS = True
except Exception:
    _HAVE_TOKENIZERS = False


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _skip(name: str, why: str) -> bool:
    print(f"[SKIP] {name} - {why}")
    return True


def _reset(path: str) -> None:
    """Point chat_context at `path` and clear the cached tokenizer so the
    next approx_tokens call re-attempts the load."""
    chat_context.QWEN_TOKENIZER_PATH = path
    chat_context._tokenizer = None
    chat_context._tokenizer_tried = False


def _make_toy_tokenizer(path: str) -> None:
    """Write a minimal whitespace-splitting tokenizer to `path` in the
    HF tokenizers JSON format. Every whitespace-delimited word maps to
    the [UNK] id — enough to prove approx_tokens routes through the
    loaded tokenizer rather than the char heuristic."""
    from tokenizers import Tokenizer, models, pre_tokenizers

    tok = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(path)


# ---------------------------------------------------------------------------
# Fallback path (no tokenizer file)
# ---------------------------------------------------------------------------

def test_fallback_uses_char_heuristic() -> bool:
    _reset("/nonexistent/path/tokenizer.json")
    # 40 chars // 4 == 10 under the heuristic.
    n = chat_context.approx_tokens("x" * 40)
    return _check(
        "missing tokenizer falls back to len//4 heuristic",
        n == 10,
        f"approx_tokens('x'*40)={n}, expected 10",
    )


def test_fallback_empty_string() -> bool:
    _reset("/nonexistent/path/tokenizer.json")
    return _check(
        "approx_tokens('') == 0 on the fallback path",
        chat_context.approx_tokens("") == 0,
    )


def test_missing_tokenizer_does_not_raise() -> bool:
    _reset("/nonexistent/path/tokenizer.json")
    try:
        chat_context._get_tokenizer()
        chat_context.approx_tokens("hello world")
        ok = True
    except Exception as e:
        ok = False
        print(f"  unexpected exception: {e}")
    return _check("a missing tokenizer file is swallowed, not raised", ok)


def test_get_tokenizer_is_singleton() -> bool:
    """_tokenizer_tried guards against re-attempting the load every call."""
    _reset("/nonexistent/path/tokenizer.json")
    chat_context._get_tokenizer()
    tried_after_first = chat_context._tokenizer_tried
    # A second call must not flip anything or raise.
    chat_context._get_tokenizer()
    return _check(
        "_get_tokenizer attempts the load once and caches the result",
        tried_after_first is True and chat_context._tokenizer is None,
    )


# ---------------------------------------------------------------------------
# Real-tokenizer path (needs the `tokenizers` library)
# ---------------------------------------------------------------------------

def test_real_tokenizer_is_used() -> bool:
    if not _HAVE_TOKENIZERS:
        return _skip(
            "real tokenizer is used over the heuristic",
            "tokenizers lib not installed in this env",
        )
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "tokenizer.json")
        _make_toy_tokenizer(path)
        _reset(path)
        # Toy tokenizer is whitespace-split: 6 words -> 6 tokens.
        # The char heuristic would give len//4 = 11//4 = 2 for "a b c d e f".
        text = "a b c d e f"
        n = chat_context.approx_tokens(text)
    return _check(
        "approx_tokens routes through the loaded tokenizer",
        n == 6,
        f"approx_tokens({text!r})={n}, expected 6 (word count), "
        f"heuristic would give {len(text) // 4}",
    )


def test_real_tokenizer_load_logged_once() -> bool:
    if not _HAVE_TOKENIZERS:
        return _skip(
            "tokenizer load is cached after first success",
            "tokenizers lib not installed in this env",
        )
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "tokenizer.json")
        _make_toy_tokenizer(path)
        _reset(path)
        first = chat_context._get_tokenizer()
        second = chat_context._get_tokenizer()
    return _check(
        "loaded tokenizer is cached (same object on repeat calls)",
        first is not None and first is second,
    )


def test_corrupt_tokenizer_falls_back() -> bool:
    """A present-but-unparseable tokenizer.json must degrade to the
    heuristic, not crash."""
    if not _HAVE_TOKENIZERS:
        return _skip(
            "corrupt tokenizer file falls back cleanly",
            "tokenizers lib not installed in this env",
        )
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "tokenizer.json")
        Path(path).write_text("{ this is not valid tokenizer json")
        _reset(path)
        n = chat_context.approx_tokens("x" * 40)
    return _check(
        "corrupt tokenizer.json falls back to the heuristic",
        n == 10 and chat_context._tokenizer is None,
        f"approx_tokens('x'*40)={n}, expected 10",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_fallback_uses_char_heuristic,
    test_fallback_empty_string,
    test_missing_tokenizer_does_not_raise,
    test_get_tokenizer_is_singleton,
    test_real_tokenizer_is_used,
    test_real_tokenizer_load_logged_once,
    test_corrupt_tokenizer_falls_back,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
