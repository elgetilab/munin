"""
Regression guard for chat 56b39f33 (2026-06-03).

The chat persona ran five `run_python` calls. The actual errors were:
  - SyntaxError in the model's code
  - OpenBLAS pthread_create failed
  - matplotlib import exception
  - successful run with garbage values (modulation depth = 4.246)
  - 30 s timeout

For all five, the model's narration said "the sandbox hit resource
limits" / "the sandbox is struggling" -- never quoting the actual
error and never proposing a fix targeted at it. The persona's CORE
RULES (5 of them) had nothing about reading the error / stderr field
of a tool result, so the model defaulted to a generic shrug.

Fix: a new CORE RULE 6 ("TOOL-ERROR DIAGNOSIS") that mandates
quoting the error verbatim, proposing a fix that targets the
specific error, and forbidding generic-shrug language unless paired
with the quote.

This test asserts the rule is present in both chat and code personas
(both can fire run_python; both can hit the same misdiagnosis pattern).

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_persona_tool_error_rule.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Persona files live under shared/personas/. In the deployed
# retrieval container they're mounted at /opt/munin/personas; in the
# repo they're under shared/personas/ relative to the repo root.
# Preferring the repo path means a developer running these tests
# locally after editing the persona JSON sees their change
# immediately, without having to redeploy first.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONA_DIR_CANDIDATES = [
    _REPO_ROOT / "shared" / "personas",
    Path("/opt/munin/personas"),
]


def _persona_path(name: str) -> Path:
    for d in _PERSONA_DIR_CANDIDATES:
        p = d / f"{name}.json"
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Could not find {name}.json under any of {_PERSONA_DIR_CANDIDATES}"
    )


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _system_prompt(persona_name: str) -> str:
    raw = json.loads(_persona_path(persona_name).read_text())
    return raw.get("params", {}).get("system", "")


# ── Tests ───────────────────────────────────────────────────────────────


def test_chat_persona_has_tool_error_rule() -> bool:
    sp = _system_prompt("chat")
    return _check(
        "chat: CORE RULE 6 TOOL-ERROR DIAGNOSIS present",
        "TOOL-ERROR DIAGNOSIS" in sp,
        "marker absent from chat.json params.system",
    )


def test_code_persona_has_tool_error_rule() -> bool:
    sp = _system_prompt("code")
    return _check(
        "code: CORE RULE 6 TOOL-ERROR DIAGNOSIS present",
        "TOOL-ERROR DIAGNOSIS" in sp,
        "marker absent from code.json params.system",
    )


def test_rule_mandates_quoting_error_verbatim() -> bool:
    """The rule's core obligation is quoting the actual error text.
    A future edit that drops 'quote' or 'verbatim' would gut the
    rule; this guards against silent erosion."""
    sp = _system_prompt("chat")
    ok = ("quote" in sp.lower()) and ("verbatim" in sp.lower())
    return _check(
        "chat: rule still mandates verbatim error quoting",
        ok,
        "'quote' or 'verbatim' missing from the rule body",
    )


def test_rule_forbids_generic_shrug_language() -> bool:
    """The trigger chat used "the sandbox is struggling" 4 times.
    The rule explicitly names that phrase as forbidden. Future edits
    that drop the example would weaken the rule's discoverability for
    the model."""
    sp = _system_prompt("chat")
    has_example = "sandbox is struggling" in sp.lower()
    return _check(
        "chat: rule names the trigger phrase as forbidden",
        has_example,
        "expected the literal phrase 'sandbox is struggling' as a "
        "named-forbidden example",
    )


def test_rule_blocks_first_failure_pivot() -> bool:
    """The other arm of the rule: don't pivot to a different
    framework / library / approach on the first failure. The chat
    persona did exactly that five times (numpy -> matplotlib
    removal -> Monte Carlo abandonment -> analytical pivot -> giving
    up). The rule must keep that prohibition visible."""
    sp = _system_prompt("chat")
    text = sp.lower()
    ok = ("pivot" in text) and ("first failure" in text)
    return _check(
        "chat: rule forbids first-failure pivot",
        ok,
        "the rule must still mention 'pivot' and 'first failure'",
    )


def main() -> int:
    tests = [
        test_chat_persona_has_tool_error_rule,
        test_code_persona_has_tool_error_rule,
        test_rule_mandates_quoting_error_verbatim,
        test_rule_forbids_generic_shrug_language,
        test_rule_blocks_first_failure_pivot,
    ]
    results = [t() for t in tests]
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
