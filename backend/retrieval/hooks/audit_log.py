"""
Demo hook (P2 #23): structured audit-log line for sensitive tools.

Targets ``run_python``, ``compile_latex``, and ``deep_research`` —
the three tools that either execute user code (run_python),
compile arbitrary LaTeX in firejail (compile_latex), or burn the
most vLLM time + external HTTP calls (deep_research). Emits one
structured log line per invocation that operators can grep:

    audit_tool user=foo@bar.org tool=run_python duration_ms=420 ok=True

Proof-of-life for the framework wiring. Not load-bearing; remove
this file and the framework still works.
"""

from __future__ import annotations

import logging

from . import register, HookContext

logger = logging.getLogger("munin.audit")


_SENSITIVE = {"run_python", "compile_latex", "deep_research"}


@register("postToolUse", match_tools=_SENSITIVE)
async def audit_sensitive_tools(
    ctx: HookContext,
    name: str,
    arguments: dict,
    result: dict,
    duration_ms: int,
) -> None:
    """One structured line per invocation. Returns None — we never
    rewrite the result, only observe it."""
    ok = isinstance(result, dict) and "error" not in result
    logger.info(
        "audit_tool user=%s conv=%s persona=%s tool=%s duration_ms=%d ok=%s",
        ctx.user_email or "?",
        ctx.conversation_id or "?",
        ctx.persona_id or "?",
        name,
        duration_ms,
        ok,
    )
    return None
