"""
Standalone unit tests for source.py robustness fixes (2026-07-23):

1. `_mode_findings` parsing + thinking. Regression guard for the empty-report
   failure after the cluster restart: `findings` mode called vLLM with
   `enable_thinking=False`, and qwen3.6 then returned `[]` even on plainly
   relevant text, so every Deep Research read produced zero notes. The fix runs
   with thinking on (8000-token budget) and parses tolerantly: strip any
   exposed <think> block, then greedily take the outermost JSON array.

2. Web-fetch retry helpers. `_web_should_retry` / `_web_backoff` back the bounded
   retry that recovers NCBI/PMC burst rate-limiting, while hard datacenter-IP
   bot walls (MDPI 403) are given up on and reported as `blocked`.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_source_robustness.py

Exit 0 = pass. No network required (the findings test mocks the vLLM call).
"""

from __future__ import annotations

import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

# NB: `mcp/tools/__init__.py` does `from .source import source`, so the package
# attribute `mcp.tools.source` is the re-exported FUNCTION, and even
# `import mcp.tools.source as S` rebinds S to that function. sys.modules always
# holds the real module object, so reach the helpers through it.
import mcp.tools.source  # noqa: E402,F401  (ensure the submodule is imported)
S = sys.modules["mcp.tools.source"]
from agent_trace import AgentTrace  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _web_should_retry
# ---------------------------------------------------------------------------

def test_should_retry() -> bool:
    retry = all(S._web_should_retry(c) for c in (401, 403, 429, 500, 502, 503, 504))
    no_retry = not any(S._web_should_retry(c) for c in (200, 301, 404, 410))
    return _check("_web_should_retry: transient yes, terminal no", retry and no_retry)


# ---------------------------------------------------------------------------
# _web_backoff
# ---------------------------------------------------------------------------

def test_backoff() -> bool:
    honours = S._web_backoff(0, "2") == 2.0
    caps_retry_after = S._web_backoff(0, "999") == 5.0
    bad_header = S._web_backoff(1, "soon") == 1.0          # falls back to exponential
    exp = S._web_backoff(0, None) == 0.5 and S._web_backoff(1, None) == 1.0
    caps_exp = S._web_backoff(10, None) == 4.0
    ok = honours and caps_retry_after and bad_header and exp and caps_exp
    return _check("_web_backoff: honours + caps Retry-After, exponential fallback", ok,
                  f"honours={honours} cap={caps_retry_after} bad={bad_header} exp={exp} capexp={caps_exp}")


# ---------------------------------------------------------------------------
# _mode_findings parsing (mock the vLLM call)
# ---------------------------------------------------------------------------

def _findings_with_content(content: str) -> list:
    async def _fake_vllm(system, user, *, max_tokens=4096, temperature=0.7, enable_thinking=True):
        # The cheap triage gate runs first; wave it through so these cases test
        # the EXTRACTION call. (Asserting here would trip on triage's
        # thinking=False/max_tokens=8 and silently exercise the fail-open path.)
        if "YES or NO" in system:
            return {"content": "YES"}
        # The fix must NOT disable thinking (that caused the empty-report bug).
        assert enable_thinking is True, "findings mode must run with thinking on"
        assert max_tokens >= 8000, "findings mode must give the reasoning pass headroom"
        return {"content": content}

    orig = S._vllm_answer
    S._vllm_answer = _fake_vllm
    try:
        ext = {"full_text": "some paper text", "ref_resolved": {"title": "t"}}
        out = _run(S._mode_findings(ext, "does X cause Y?", AgentTrace("source", mode="findings")))
    finally:
        S._vllm_answer = orig
    return out.get("findings", [])


def test_findings_clean_array() -> bool:
    f = _findings_with_content('[{"claim":"a","quote":"q1"},{"claim":"b","quote":"q2"}]')
    return _check("findings: clean array -> 2", len(f) == 2, f"got {f!r}")


def test_findings_strips_think_block() -> bool:
    # A bracket inside the reasoning must not hijack the array match.
    f = _findings_with_content('<think>maybe [something] here</think>\n[{"claim":"a","quote":"q"}]')
    return _check("findings: <think> stripped, real array parsed", len(f) == 1, f"got {f!r}")


def test_findings_quote_with_bracket() -> bool:
    # Greedy match must capture the whole array even when a quote contains ']'.
    f = _findings_with_content('[{"claim":"Ca rose","quote":"the [Ca2+] rose to 5 mM"}]')
    return _check("findings: ']' inside a quote survives", len(f) == 1, f"got {f!r}")


def test_findings_empty_and_prose() -> bool:
    empty = _findings_with_content("[]")
    prose = _findings_with_content("The text does not address the question.")
    return _check("findings: [] and prose -> 0", empty == [] and prose == [])


def test_findings_skips_bad_items() -> bool:
    f = _findings_with_content('[{"quote":"no claim"},{"claim":"ok","quote":"q"}]')
    return _check("findings: item without claim skipped", len(f) == 1, f"got {f!r}")



# ---------------------------------------------------------------------------
# Full-text triage gate (2026-07-24)
# ---------------------------------------------------------------------------

def _findings_with_triage(triage_reply: str, findings_content: str):
    """Run _mode_findings with both vLLM calls stubbed. Returns (out, n_calls)."""
    calls = []

    async def _fake_vllm(system, user, *, max_tokens=4096, temperature=0.7,
                         enable_thinking=True):
        calls.append({"system": system, "max_tokens": max_tokens,
                      "enable_thinking": enable_thinking})
        if "YES or NO" in system:
            return {"content": triage_reply}
        return {"content": findings_content}

    orig = S._vllm_answer
    S._vllm_answer = _fake_vllm
    try:
        ext = {"full_text": "some paper text", "ref_resolved": {"title": "t"}}
        out = _run(S._mode_findings(ext, "does X cause Y?", AgentTrace("source", mode="findings")))
    finally:
        S._vllm_answer = orig
    return out, calls


def test_triage_skips_the_expensive_extraction() -> bool:
    out, calls = _findings_with_triage("NO", '[{"claim":"a","quote":"q"}]')
    ok = (out.get("findings") == [] and out.get("triaged_out") is True
          and len(calls) == 1 and calls[0]["max_tokens"] == 8)
    return _check("triage NO -> extraction skipped, only the cheap call made", ok,
                  f"out={out!r} calls={len(calls)}")


def test_triage_yes_runs_the_extraction() -> bool:
    out, calls = _findings_with_triage("YES", '[{"claim":"a","quote":"q"}]')
    ok = (len(out.get("findings") or []) == 1 and len(calls) == 2
          and calls[1]["enable_thinking"] is True and calls[1]["max_tokens"] >= 8000)
    return _check("triage YES -> extraction runs with thinking + full budget", ok,
                  f"out={out!r} calls={[c['max_tokens'] for c in calls]}")


def test_triage_fails_open_on_error() -> bool:
    """A wrongly skipped paper is an unrecoverable silent drop (D8), so any
    triage failure MUST proceed to extraction rather than skip."""
    calls = []

    async def _fake_vllm(system, user, *, max_tokens=4096, temperature=0.7,
                         enable_thinking=True):
        calls.append(max_tokens)
        if "YES or NO" in system:
            return {"error": "vLLM exploded"}
        return {"content": '[{"claim":"a","quote":"q"}]'}

    orig = S._vllm_answer
    S._vllm_answer = _fake_vllm
    try:
        ext = {"full_text": "t", "ref_resolved": {"title": "t"}}
        out = _run(S._mode_findings(ext, "q?", AgentTrace("source", mode="findings")))
    finally:
        S._vllm_answer = orig
    ok = len(out.get("findings") or []) == 1 and len(calls) == 2
    return _check("triage error -> fails OPEN, extraction still runs", ok,
                  f"out={out!r} calls={calls}")


def test_triage_can_be_disabled() -> bool:
    orig_flag = S.TRIAGE_ENABLED
    S.TRIAGE_ENABLED = False
    try:
        out, calls = _findings_with_triage("NO", '[{"claim":"a","quote":"q"}]')
    finally:
        S.TRIAGE_ENABLED = orig_flag
    ok = len(out.get("findings") or []) == 1 and len(calls) == 1
    return _check("triage disabled -> no gate call, extraction runs", ok,
                  f"out={out!r} calls={len(calls)}")


TESTS = [
    test_should_retry,
    test_backoff,
    test_findings_clean_array,
    test_findings_strips_think_block,
    test_findings_quote_with_bracket,
    test_findings_empty_and_prose,
    test_findings_skips_bad_items,
    test_triage_skips_the_expensive_extraction,
    test_triage_yes_runs_the_extraction,
    test_triage_fails_open_on_error,
    test_triage_can_be_disabled,
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
