"""
Hooks framework (P2 #23).

Three injection points for per-turn behaviour:

  - ``preToolUse``  fires before ``execute_mcp_tool``. First matching
                    hook to return non-None short-circuits the
                    executor: its return value becomes the tool result.
                    Use for gating ("for ``student@*``, ``run_python``
                    needs approval"), synthetic responses in tests,
                    deterministic faking.

  - ``postToolUse`` fires after ``execute_mcp_tool``. Hooks chain:
                    a non-None return replaces the result; the next
                    hook sees the replacement. Use for output
                    redaction, sanitisation, structured audit logs.

  - ``stop``        fires once per turn in ``stream_chat_completion``'s
                    finally block. Side-effect only (no return). The
                    natural home for memory extraction (P2 #25) and
                    end-of-turn audit.

Hooks are registered via the ``@register(kind, ...)`` decorator at
module import time. The framework auto-imports every ``hooks/*.py``
at retrieval startup (called from ``main.py``), so dropping a new
file in this directory is enough to wire it up.

A hook can scope itself by tool name (``match_tools=...``) or by user
email regex (``match_user=...``). Unmatched hooks are skipped without
being invoked.

Hook exceptions are caught + logged. A misbehaving hook NEVER breaks
the turn.
"""

from __future__ import annotations

from ._dispatcher import (
    HookContext,
    dispatch_pre_tool_use,
    dispatch_post_tool_use,
    dispatch_stop,
    load_all,
    register,
    _clear_registry_for_tests,
    _registry_snapshot_for_tests,
)

__all__ = [
    "HookContext",
    "dispatch_pre_tool_use",
    "dispatch_post_tool_use",
    "dispatch_stop",
    "load_all",
    "register",
]
