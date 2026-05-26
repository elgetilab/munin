"""
Tests for the MCP dispatch registry (P2 #19).

Pins the three properties the new shape guarantees:
  1. Every name in MCP_TOOLS has a dispatcher (except the explicit
     schema-only allowlist, currently just delegate_to_persona)
  2. Every dispatcher name is in MCP_TOOLS — no orphans
  3. register_tool rejects duplicates at decoration time
  4. The executor routes by registry, returns Unknown tool for
     missing names, surfaces exceptions as {'error': ...}

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_dispatch_registry.py
Or locally:
    python backend/retrieval/tests/test_dispatch_registry.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing executor triggers the dispatchers module and populates
# the registry. The startup-time verify_dispatch_registry is also
# safe to call multiple times.
from mcp import executor  # noqa: E402, F401
from mcp._dispatch import (  # noqa: E402
    _SCHEMA_ONLY,
    get_dispatcher,
    register_tool,
    registered_names,
    verify_dispatch_registry,
)
from mcp.executor import _dispatch_mcp_tool  # noqa: E402
from mcp.schemas import MCP_TOOLS  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Consistency invariants
# ---------------------------------------------------------------------------

def test_every_schema_tool_has_dispatcher() -> bool:
    declared = set(MCP_TOOLS.keys()) - set(_SCHEMA_ONLY)
    registered = set(registered_names())
    missing = declared - registered
    return _check(
        "every MCP_TOOLS entry (sans schema-only) has a dispatcher",
        not missing, f"missing dispatchers: {sorted(missing)}",
    )


def test_no_orphan_dispatchers() -> bool:
    declared = set(MCP_TOOLS.keys())
    registered = set(registered_names())
    extra = registered - declared
    return _check(
        "no dispatcher registered without a matching MCP_TOOLS entry",
        not extra, f"orphan dispatchers: {sorted(extra)}",
    )


def test_verify_at_startup_passes_today() -> bool:
    """The real startup hook calls this; the test asserts the
    current tree is in a shippable state."""
    try:
        verify_dispatch_registry()
        return _check("verify_dispatch_registry passes for the live registry", True)
    except RuntimeError as e:
        return _check("verify_dispatch_registry passes for the live registry", False, str(e))


def test_delegate_to_persona_is_schema_only() -> bool:
    """delegate_to_persona is in MCP_TOOLS but intentionally has no
    dispatcher (chat_service intercepts it before the executor)."""
    return _check(
        "delegate_to_persona is in schema but not registered",
        "delegate_to_persona" in MCP_TOOLS
        and "delegate_to_persona" in _SCHEMA_ONLY
        and get_dispatcher("delegate_to_persona") is None,
    )


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------

def test_register_tool_rejects_duplicate() -> bool:
    """Trying to register a name that's already bound should raise
    at decoration time — copy-paste protection."""
    try:
        @register_tool("web_search")  # already registered
        async def _conflict(arguments: dict) -> dict:
            return {}
        return _check("register_tool rejects duplicate", False, "no error")
    except RuntimeError as e:
        return _check(
            "register_tool rejects duplicate",
            "duplicate" in str(e).lower(),
        )


def test_unknown_tool_returns_error() -> bool:
    """Names not in the registry route through the missing-dispatcher
    branch. The error shape is what the P1 #12 outcome classifier
    looks for (Unknown tool: ...)."""
    async def go() -> bool:
        out = await _dispatch_mcp_tool("definitely_not_a_real_tool", {})
        return (
            isinstance(out, dict)
            and "error" in out
            and out["error"].startswith("Unknown tool:")
        )
    return _check("unknown tool returns 'Unknown tool: ...' error", asyncio.run(go()))


def test_registry_dispatch_routes_to_function() -> bool:
    """Register a one-off tool, dispatch it, verify the dispatcher
    function ran with the arguments dict."""
    captured: dict = {}

    @register_tool("__test_only_router_probe__")
    async def _probe(arguments: dict) -> dict:
        captured.update(arguments)
        return {"ok": True}

    async def go() -> bool:
        out = await get_dispatcher("__test_only_router_probe__")({"x": 1, "y": 2})
        return out == {"ok": True} and captured == {"x": 1, "y": 2}

    return _check("registry routes name -> function with arguments dict", asyncio.run(go()))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_every_schema_tool_has_dispatcher,
    test_no_orphan_dispatchers,
    test_verify_at_startup_passes_today,
    test_delegate_to_persona_is_schema_only,
    test_register_tool_rejects_duplicate,
    test_unknown_tool_returns_error,
    test_registry_dispatch_routes_to_function,
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
