"""
Hook registry, dispatcher, and auto-loader. Public surface is
re-exported from ``hooks/__init__.py``.

A ``HookContext`` is built once per dispatch from the active
ContextVars (``current_user_email``, ``current_conversation_id``,
``current_persona``) so hooks don't need to thread the same args
through their signatures every call.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class HookContext:
    """Per-turn snapshot of identity + conversation state.

    Read at dispatch time from ContextVars; never mutated by hooks.
    ``emit_sse`` is a thin wrapper around ``current_sse_emitter`` so a
    hook can surface state to the user without importing chat_service
    internals."""

    user_email: Optional[str]
    conversation_id: Optional[str]
    persona_id: Optional[str]

    def emit_sse(self, event_name: str, data: dict) -> None:
        """Push an SSE event via the active emitter (no-op if unset)."""
        # Lazy import: mcp/context imports cleanly but the hooks package
        # ships its own context object, so we keep the dependency local.
        from mcp.context import current_sse_emitter

        emit = current_sse_emitter.get()
        if emit is None:
            return
        try:
            emit(event_name, data)
        except Exception:
            logger.exception("hook emit_sse failed for %r", event_name)


# Public hook signatures.
PreToolUseHook = Callable[[HookContext, str, dict], Awaitable[Optional[dict]]]
PostToolUseHook = Callable[
    [HookContext, str, dict, dict, int], Awaitable[Optional[dict]]
]
StopHook = Callable[[HookContext, str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class _Registration:
    kind: str
    func: Callable
    match_tools: Optional[set[str]]
    match_user: Optional[re.Pattern]
    name: str  # For logging / introspection.


_registrations: list[_Registration] = []


def register(
    kind: str,
    *,
    match_tools: Optional[set[str] | list[str]] = None,
    match_user: Optional[str] = None,
):
    """Decorator. Register an async function as a hook.

    ``kind`` must be one of ``"preToolUse"``, ``"postToolUse"``,
    ``"stop"``. ``match_tools`` restricts the hook to a subset of MCP
    tool names (no-op for ``stop`` hooks). ``match_user`` is a regex
    matched against ``ctx.user_email``; e.g. ``r"^student@"``.

    Hooks must be ``async def``; the dispatcher awaits them. A
    non-async callable raises at registration time so the failure is
    visible at startup rather than first invocation."""
    if kind not in {"preToolUse", "postToolUse", "stop"}:
        raise ValueError(f"unknown hook kind: {kind!r}")
    tool_set: Optional[set[str]] = None
    if match_tools is not None:
        tool_set = set(match_tools)
    user_re: Optional[re.Pattern] = None
    if match_user is not None:
        user_re = re.compile(match_user)

    def decorator(func: Callable) -> Callable:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"hook {func.__qualname__!r} must be `async def` "
                f"(kind={kind!r})"
            )
        _registrations.append(
            _Registration(
                kind=kind,
                func=func,
                match_tools=tool_set,
                match_user=user_re,
                name=func.__qualname__,
            )
        )
        logger.info(
            "registered %s hook: %s (tools=%s, user=%s)",
            kind, func.__qualname__,
            sorted(tool_set) if tool_set else "*",
            match_user or "*",
        )
        return func

    return decorator


def _matches(reg: _Registration, ctx: HookContext, tool_name: Optional[str]) -> bool:
    if reg.match_tools is not None and (tool_name is None or tool_name not in reg.match_tools):
        return False
    if reg.match_user is not None:
        if not ctx.user_email or not reg.match_user.search(ctx.user_email):
            return False
    return True


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _build_context() -> HookContext:
    from mcp.context import (
        current_user_email,
        current_conversation_id,
        current_persona,
    )
    return HookContext(
        user_email=current_user_email.get(),
        conversation_id=current_conversation_id.get(),
        persona_id=current_persona.get(),
    )


async def dispatch_pre_tool_use(
    name: str, arguments: dict
) -> Optional[dict]:
    """First matching hook to return non-None wins; the rest are
    skipped. Hooks raising an exception are logged and treated as if
    they returned None."""
    ctx = _build_context()
    for reg in _registrations:
        if reg.kind != "preToolUse" or not _matches(reg, ctx, name):
            continue
        try:
            result = await reg.func(ctx, name, arguments)
        except Exception:
            logger.exception(
                "preToolUse hook %s raised; treating as no-op", reg.name,
            )
            continue
        if result is not None:
            logger.info(
                "preToolUse hook %s short-circuited tool %r", reg.name, name,
            )
            return result
    return None


async def dispatch_post_tool_use(
    name: str, arguments: dict, result: dict, duration_ms: int,
) -> dict:
    """Chain hooks in registration order; each non-None return
    replaces the result for the next hook in the chain. Returns the
    final (possibly replaced) result."""
    ctx = _build_context()
    current = result
    for reg in _registrations:
        if reg.kind != "postToolUse" or not _matches(reg, ctx, name):
            continue
        try:
            replacement = await reg.func(ctx, name, arguments, current, duration_ms)
        except Exception:
            logger.exception(
                "postToolUse hook %s raised; result unchanged", reg.name,
            )
            continue
        if replacement is not None:
            current = replacement
    return current


async def dispatch_stop(terminal_reason: str) -> None:
    """Run every matching stop hook concurrently. One hook raising
    does not stop the others (gather with return_exceptions=True;
    exceptions are logged after the fact)."""
    ctx = _build_context()
    matching = [
        reg for reg in _registrations
        if reg.kind == "stop" and _matches(reg, ctx, None)
    ]
    if not matching:
        return
    results = await asyncio.gather(
        *(reg.func(ctx, terminal_reason) for reg in matching),
        return_exceptions=True,
    )
    for reg, res in zip(matching, results):
        if isinstance(res, Exception):
            logger.error(
                "stop hook %s raised: %s: %s",
                reg.name, type(res).__name__, res,
            )


# ---------------------------------------------------------------------------
# Auto-loader
# ---------------------------------------------------------------------------


def load_all() -> int:
    """Import every ``hooks/*.py`` (except ``_*.py``) so decorator
    registrations land in ``_registrations``. Called once at retrieval
    startup. Returns the number of hooks registered."""
    here = os.path.dirname(__file__)
    package = __package__ or "hooks"
    # Strip the trailing dispatcher segment so we import the public
    # subpackage (``hooks.audit_log``) not the private internal
    # (``hooks._dispatcher.audit_log``).
    if package.endswith("._dispatcher"):
        package = package.rsplit(".", 1)[0]
    before = len(_registrations)
    for entry in sorted(os.listdir(here)):
        if not entry.endswith(".py"):
            continue
        if entry.startswith("_") or entry == "__init__.py":
            continue
        mod_name = entry[:-3]
        try:
            importlib.import_module(f"{package}.{mod_name}")
        except Exception:
            logger.exception("failed to load hook module %s", mod_name)
    return len(_registrations) - before


# ---------------------------------------------------------------------------
# Test helpers (NOT exported from hooks/__init__.py public surface)
# ---------------------------------------------------------------------------


def _clear_registry_for_tests() -> None:
    """Drop every registration. Tests use this between cases."""
    _registrations.clear()


def _registry_snapshot_for_tests() -> list[dict[str, Any]]:
    """Return a serialisable view of the live registry for inspection."""
    return [
        {
            "kind": r.kind,
            "name": r.name,
            "match_tools": sorted(r.match_tools) if r.match_tools else None,
            "match_user": r.match_user.pattern if r.match_user else None,
        }
        for r in _registrations
    ]
