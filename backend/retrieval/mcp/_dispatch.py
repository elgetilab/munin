"""
MCP dispatch registry (P2 #19).

Replaces the 39-branch if/elif chain in ``executor.py`` with a
decorator-driven map of tool names to dispatcher functions. A
dispatcher is a tiny shim that pulls arguments off the dict and
calls the underlying tool implementation in ``mcp/tools/*.py``
with explicit kwargs — exactly the per-tool argument shape the
old chain had, just localised.

The startup-time consistency check (``verify_dispatch_registry``)
catches the three failure modes the old shape allowed:

  - schema declares a tool with no executor branch (silent
    "Unknown tool: foo" at first call)
  - executor branch with no schema entry (validator does nothing;
    invalid arguments reach the tool)
  - duplicate registration (last-wins, hard to spot)

The check raises at boot — internal developer invariant, not
operator config. Wired from ``main.py`` startup.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

Dispatcher = Callable[[dict], Awaitable[dict]]


# Tools that appear in MCP_TOOLS for schema/discovery purposes but are NOT
# dispatched through the executor. Currently empty: delegate_to_persona (the
# former sole member, intercepted by chat_service) was removed at A4. Anything
# added here must have a written rationale.
_SCHEMA_ONLY: frozenset[str] = frozenset()


_REGISTRY: dict[str, Dispatcher] = {}


def register_tool(name: str):
    """Decorator: bind ``name`` to the decorated async dispatcher.

    Raises at registration time if ``name`` is already bound — a
    duplicate is almost always a copy-paste bug, and surfacing it at
    import is much friendlier than silently overwriting."""

    def decorator(fn: Dispatcher) -> Dispatcher:
        if name in _REGISTRY:
            raise RuntimeError(
                f"duplicate MCP tool dispatcher: {name!r} "
                f"(already bound to {_REGISTRY[name].__qualname__})"
            )
        _REGISTRY[name] = fn
        return fn

    return decorator


def get_dispatcher(name: str) -> Optional[Dispatcher]:
    """Lookup. Returns None if no dispatcher is registered for ``name``."""
    return _REGISTRY.get(name)


def registered_names() -> frozenset[str]:
    """Snapshot of every registered tool name."""
    return frozenset(_REGISTRY.keys())


def verify_dispatch_registry() -> None:
    """Cross-check the registry against ``MCP_TOOLS``. Raises
    ``RuntimeError`` on mismatch (drift in either direction)."""
    # Lazy import: ``schemas`` and ``_dispatch`` are siblings; importing
    # at module top would create an import cycle once schemas imports
    # back to register things (it doesn't today, but the lazy form is
    # cheap insurance).
    from .schemas import MCP_TOOLS

    declared = frozenset(MCP_TOOLS.keys())
    registered = registered_names()
    expected_registered = declared - _SCHEMA_ONLY

    missing = expected_registered - registered
    extra = registered - declared

    problems: list[str] = []
    if missing:
        problems.append(
            f"schemas declare {sorted(missing)} but no dispatcher is registered"
        )
    if extra:
        problems.append(
            f"dispatchers registered for {sorted(extra)} that are not in MCP_TOOLS"
        )
    if problems:
        raise RuntimeError(
            "MCP dispatch registry is out of sync with MCP_TOOLS:\n  - "
            + "\n  - ".join(problems)
        )


# ---------------------------------------------------------------------------
# Test helpers (NOT part of the public surface).
# ---------------------------------------------------------------------------


def _clear_registry_for_tests() -> None:
    _REGISTRY.clear()


def _snapshot_for_tests() -> dict[str, str]:
    return {name: fn.__qualname__ for name, fn in _REGISTRY.items()}
