"""
Plan-approval gate (P2 #24 Phase 2) — preToolUse + postToolUse pair.

Registered via the P2 #23 hooks framework. Two hooks live here:

  preToolUse: ``plan_approval_gate``
    Fires before every tool dispatch. Determines whether this call
    is "gated" (persona.params.plan_approval lists the tool, OR the
    in-flight plan has ``requires_approval=True``). On a gated call:
      - If no plan exists, returns a synthetic
        ``{"error": "plan_approval_required", "message": ...}``
        nudging the model to call set_plan first.
      - If a plan exists but is unapproved, emits a
        ``plan_approval_required`` SSE so the UI shows the
        Approve / Approve-all / Edit / Reject buttons, and returns
        a synthetic ``{"status": "awaiting_user_approval", ...}``
        result that short-circuits the tool dispatch (no executor
        call). The model sees this, writes a "waiting for approval"
        message, and the turn ends.
      - If approved (``approved_at`` set), returns None so the
        tool runs normally. The postToolUse companion below clears
        the approval afterwards on mode='each'.

  postToolUse: ``consume_each_approval``
    Fires after every tool dispatch. If the tool that just ran was
    gated AND the plan's approval_mode is 'each' AND an approval
    was actually consumed (approved_at was set), clears
    approved_at so the next gated call re-triggers the gate. On
    'auto' mode, leaves the approval intact for subsequent calls.

The hooks framework's dispatcher (P2 #23) catches per-hook
exceptions — a misbehaving hook can never break a turn.
"""

from __future__ import annotations

import logging
from typing import Optional

import personas as persona_module
import plan_store
from . import HookContext, register


logger = logging.getLogger(__name__)


def _is_gated_call(
    persona: Optional[dict], plan: Optional[dict], tool_name: str,
) -> bool:
    """True iff the tool needs an approved plan to run.

    The plan tools themselves (``set_plan``, ``update_plan_item``)
    are NEVER gated — the model has to be able to write a plan in
    order to submit one for approval; gating them would deadlock
    the loop.

    Two trigger sources, with the persona policy winning when it
    exists:

      a) Persona-level: ``params.plan_approval`` declares the
         gated tools. Operator-authoritative. When the persona
         declares ANY gated tools, those (and only those) get
         gated regardless of what the model flags on the plan.

      b) Model-flag: when the persona has no plan_approval list,
         the model can voluntarily request the gate by setting
         ``requires_approval: True`` on its set_plan call. In
         that case all non-plan tools are gated until the user
         approves. Fallback for unconfigured personas where the
         model itself judges the work as heavy.

    Picking persona-over-model preserves operator policy: if a
    persona says only `compile_latex` needs approval, a model-flagged
    plan doesn't ALSO start gating `web_search` on that persona.
    """
    if tool_name in {"set_plan", "update_plan_item"}:
        return False
    gated_tools = persona_module.plan_approval_tools(persona)
    if gated_tools:
        return tool_name in gated_tools
    return bool(plan and plan.get("requires_approval"))


@register("preToolUse")
async def plan_approval_gate(
    ctx: HookContext, name: str, arguments: dict,
) -> Optional[dict]:
    if not ctx.conversation_id:
        # Ephemeral or unauthenticated; no persistence to anchor a
        # gate against. Let it run.
        return None
    persona = persona_module.get_persona(ctx.persona_id) if ctx.persona_id else None
    plan = await plan_store.get_plan(ctx.conversation_id)

    if not _is_gated_call(persona, plan, name):
        return None  # not gated; executor runs normally

    if plan is None:
        # Persona declares the tool gated but the model hasn't
        # written a plan yet. Nudge it to set_plan first.
        return {
            "error": "plan_approval_required",
            "message": (
                f"This persona requires an approved plan before "
                f"calling {name!r}. Call set_plan first with the "
                f"steps you intend to take, then wait for the user "
                f"to approve it via the UI."
            ),
        }

    if plan.get("approved_at"):
        # Approved. Let the call through; the postToolUse hook
        # below will consume the 'each' approval afterwards.
        return None

    # Not approved. Short-circuit with the synthetic result + emit
    # the SSE so the UI surfaces Approve / Approve-all / Edit /
    # Reject buttons on the inline PlanCard.
    ctx.emit_sse(
        "plan_approval_required",
        {
            "tool": name,
            "arguments": arguments,
            "plan": plan,
        },
    )
    logger.info(
        "plan_approval_gate: short-circuited tool=%r conv=%s persona=%s",
        name, ctx.conversation_id, ctx.persona_id,
    )
    return {
        "status": "awaiting_user_approval",
        "tool": name,
        "message": (
            "User approval is required before this call can run. The UI "
            "is asking the user to Approve / Approve-all / Edit / Reject. "
            "Wait for the user — do NOT retry this tool in the current "
            "turn; you will get a new turn once they decide."
        ),
        "plan_summary": [it.get("title") for it in (plan.get("items") or [])],
    }


@register("postToolUse")
async def consume_each_approval(
    ctx: HookContext,
    name: str,
    arguments: dict,
    result: dict,
    duration_ms: int,
) -> None:
    """If this dispatch consumed an 'each'-mode approval, clear
    ``approved_at`` so the next gated call re-triggers the gate.
    No-op for: non-gated tools, plans on 'auto' mode, plans with
    no approval, and short-circuited dispatches (the preToolUse
    hook would have run instead of the executor)."""
    if not ctx.conversation_id:
        return None
    # Detect the short-circuit case via the synthetic result shape.
    # If preToolUse already short-circuited, no approval was
    # consumed and there's nothing to clear.
    if isinstance(result, dict) and result.get("status") == "awaiting_user_approval":
        return None
    persona = persona_module.get_persona(ctx.persona_id) if ctx.persona_id else None
    plan = await plan_store.get_plan(ctx.conversation_id)
    if not _is_gated_call(persona, plan, name):
        return None
    if plan is None or not plan.get("approved_at"):
        return None
    if plan.get("approval_mode") != "each":
        return None  # auto-mode persists across calls
    try:
        await plan_store.clear_approval(ctx.conversation_id)
        logger.info(
            "plan_approval: consumed 'each' approval after tool=%r conv=%s",
            name, ctx.conversation_id,
        )
    except Exception:
        logger.exception(
            "plan_approval: failed to clear approved_at for conv %s",
            ctx.conversation_id,
        )
    return None
