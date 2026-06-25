"""
Plan store (P2 #24 Phase 1) — one mutable plan per conversation.

Backs the ``set_plan`` / ``update_plan_item`` MCP tools and the
``/api/chats/{cid}/plan`` endpoints. The plan is structural
scaffolding the model maintains during multi-step turns; the user
sees it as an inline checkbox list above the assistant bubble and
uses it to follow along.

Data shape: a single row per conversation_id in
``conversation_plans``. The ``items`` column is a JSON list of
dicts, each shaped::

    {
      "id":     "p-1",                    # stable across updates
      "title":  "Search arxiv for ...",   # required, <=200 chars
      "status": "pending",                # pending|in_progress|done|cancelled
      "notes":  "...",                    # optional, <=500 chars
      "updated_at": "2026-..."
    }

Caps are enforced at this layer (set_plan rejects bad input with
PlanError) so the MCP tool stays thin and the database can't grow
unbounded. The 20-item cap is generous for a UI checkbox list;
real plans are usually 3-8 items.

Phase 2 fields (``requires_approval``, ``approved_at``,
``approval_mode``) are declared on the row but only flipped by the
Phase 2 approval endpoints — Phase 1 always leaves them at the
safe defaults (0, NULL, 'each').
"""

from __future__ import annotations

import json
from typing import Any, Optional

from chat_store import _iso_now, get_db


# ---------------------------------------------------------------------------
# Caps. Tune-once constants. Document the rationale rather than gate
# behind env vars (operator dials that nobody touches are footguns).
# ---------------------------------------------------------------------------

MAX_ITEMS = 20            # checkbox list past this is unreadable
MAX_TITLE_CHARS = 200     # one-line action ("Search arxiv for ...")
MAX_NOTES_CHARS = 500     # optional context block
_ALLOWED_STATUSES = frozenset({"pending", "in_progress", "done", "cancelled"})


class PlanError(ValueError):
    """Raised on any unusable plan input (cap violations, missing
    fields, invalid status). Callers (the MCP dispatcher) convert
    this into a tool error result the model can self-correct from."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_status(status: Any) -> str:
    if not isinstance(status, str):
        raise PlanError("status must be a string")
    if status not in _ALLOWED_STATUSES:
        raise PlanError(
            f"status must be one of {sorted(_ALLOWED_STATUSES)}, got {status!r}"
        )
    return status


def _validate_title(value: Any) -> str:
    if not isinstance(value, str):
        raise PlanError("item.title must be a string")
    value = value.strip()
    if not value:
        raise PlanError("item.title must be non-empty")
    if len(value) > MAX_TITLE_CHARS:
        raise PlanError(
            f"item.title exceeds {MAX_TITLE_CHARS}-character cap "
            f"(got {len(value)})"
        )
    return value


def _validate_notes(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlanError("item.notes must be a string or null")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_NOTES_CHARS:
        raise PlanError(
            f"item.notes exceeds {MAX_NOTES_CHARS}-character cap "
            f"(got {len(value)})"
        )
    return value


def _normalise_items(raw_items: Any) -> list[dict]:
    """Validate + auto-assign ids. Sequential `p-1`, `p-2`, ... when
    the model omits an id; user-supplied ids are preserved verbatim
    so subsequent ``update_plan_item`` calls remain stable."""
    if not isinstance(raw_items, list):
        raise PlanError("items must be a list")
    if not raw_items:
        raise PlanError("items must contain at least one entry")
    if len(raw_items) > MAX_ITEMS:
        raise PlanError(
            f"items exceeds {MAX_ITEMS}-item cap (got {len(raw_items)})"
        )
    now = _iso_now()
    out: list[dict] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise PlanError(f"items[{i - 1}] must be an object")
        item_id = item.get("id")
        if item_id is None or item_id == "":
            item_id = f"p-{i}"
        if not isinstance(item_id, str):
            raise PlanError(f"items[{i - 1}].id must be a string")
        item_id = item_id.strip()
        if not item_id:
            raise PlanError(f"items[{i - 1}].id must be non-empty")
        if item_id in seen_ids:
            raise PlanError(f"duplicate item id {item_id!r}")
        seen_ids.add(item_id)
        title = _validate_title(item.get("title"))
        status = _validate_status(item.get("status") or "pending")
        notes = _validate_notes(item.get("notes"))
        out.append({
            "id": item_id,
            "title": title,
            "status": status,
            "notes": notes,
            "updated_at": now,
        })
    return out


# ---------------------------------------------------------------------------
# Row <-> dict
# ---------------------------------------------------------------------------


def _row_to_plan(row) -> dict:
    return {
        "conversation_id": row["conversation_id"],
        "items": json.loads(row["items"]),
        "requires_approval": bool(row["requires_approval"]),
        "approved_at": row["approved_at"],
        "approval_mode": row["approval_mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_plan(conversation_id: str) -> Optional[dict]:
    """Return the plan dict for this conversation, or None if no plan
    has been set yet. Not user-scoped at read time because the
    endpoints + dispatchers wrap this in an ownership check (the
    Phase 1 system-prompt assembly trusts chat_service to have
    validated ownership upstream)."""
    db = await get_db()
    cur = await db.execute(
        "SELECT conversation_id, items, requires_approval, approved_at, "
        "approval_mode, created_at, updated_at "
        "FROM conversation_plans WHERE conversation_id = ?",
        (conversation_id,),
    )
    row = await cur.fetchone()
    return _row_to_plan(row) if row else None


async def set_plan(
    *,
    user_email: str,
    conversation_id: str,
    items: list[dict],
    requires_approval: bool = False,
) -> dict:
    """Replace the conversation's plan with ``items``. Resets approval
    state (Phase 2 fields). Returns the persisted plan dict.

    Caps are enforced via PlanError before any DB write."""
    normalised = _normalise_items(items)
    now = _iso_now()
    items_json = json.dumps(normalised)

    db = await get_db()
    # Idempotent upsert; SQLite ON CONFLICT clause refreshes everything
    # but the created_at column on a repeat call so the audit trail of
    # plan revisions is preserved in the messages.tool_calls history.
    await db.execute(
        """
        INSERT INTO conversation_plans (
            conversation_id, user_email, items,
            created_at, updated_at,
            requires_approval, approved_at, approval_mode
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'each')
        ON CONFLICT(conversation_id) DO UPDATE SET
            items = excluded.items,
            updated_at = excluded.updated_at,
            requires_approval = excluded.requires_approval,
            approved_at = NULL,
            approval_mode = 'each',
            user_email = excluded.user_email
        """,
        (
            conversation_id, user_email, items_json,
            now, now,
            1 if requires_approval else 0,
        ),
    )
    await db.commit()
    plan = await get_plan(conversation_id)
    if plan is None:
        # Impossible in practice — the row was just inserted in the same
        # transaction. Defensive only.
        raise PlanError("set_plan: row vanished after insert")
    return plan


async def update_item(
    *,
    user_email: str,
    conversation_id: str,
    item_id: str,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Flip one item's status (+ optional notes). Raises PlanError if
    the plan or the item id doesn't exist, or the status is invalid.
    Returns the full post-update plan dict."""
    plan = await get_plan(conversation_id)
    if plan is None:
        raise PlanError(
            "no plan exists for this conversation; call set_plan first"
        )
    items: list[dict] = plan["items"]
    target_idx = next(
        (i for i, it in enumerate(items) if it["id"] == item_id),
        None,
    )
    if target_idx is None:
        raise PlanError(
            f"no plan item with id {item_id!r}; "
            f"known ids: {[it['id'] for it in items]}"
        )
    now = _iso_now()
    target = dict(items[target_idx])  # don't mutate the shared dict
    if status is not None:
        target["status"] = _validate_status(status)
    if notes is not None:
        target["notes"] = _validate_notes(notes)
    target["updated_at"] = now
    items[target_idx] = target

    db = await get_db()
    await db.execute(
        "UPDATE conversation_plans SET items = ?, updated_at = ?, "
        "user_email = ? WHERE conversation_id = ?",
        (json.dumps(items), now, user_email, conversation_id),
    )
    await db.commit()
    return (await get_plan(conversation_id)) or plan  # second fetch picks
                                                     # up timestamps


async def clear_plan(*, conversation_id: str) -> bool:
    """Delete the plan. Returns True iff a row was removed."""
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM conversation_plans WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Phase 2 — approval state transitions
# ---------------------------------------------------------------------------


_ALLOWED_APPROVAL_MODES = frozenset({"each", "auto"})


async def mark_approved(
    *, conversation_id: str, approval_mode: str = "each",
) -> dict:
    """Mark the conversation's plan approved. ``approval_mode='each'``
    consumes the approval on the next gated tool call (the
    postToolUse hook in ``hooks/plan_approval.py`` clears
    ``approved_at`` after one run). ``'auto'`` persists the
    approval across all subsequent gated calls until either the
    plan is replaced (``set_plan``) or the user revokes via a
    fresh approve with mode='each'."""
    if approval_mode not in _ALLOWED_APPROVAL_MODES:
        raise PlanError(
            f"approval_mode must be one of {sorted(_ALLOWED_APPROVAL_MODES)}, "
            f"got {approval_mode!r}"
        )
    plan = await get_plan(conversation_id)
    if plan is None:
        raise PlanError("no plan exists for this conversation to approve")
    now = _iso_now()
    db = await get_db()
    await db.execute(
        "UPDATE conversation_plans SET approved_at = ?, approval_mode = ?, "
        "updated_at = ? WHERE conversation_id = ?",
        (now, approval_mode, now, conversation_id),
    )
    await db.commit()
    return (await get_plan(conversation_id)) or plan


async def clear_approval(conversation_id: str) -> None:
    """Set ``approved_at`` back to NULL without touching the plan
    itself. Called by the postToolUse hook after one gated tool
    runs on ``approval_mode='each'`` so the next gated call
    re-triggers the gate."""
    db = await get_db()
    await db.execute(
        "UPDATE conversation_plans SET approved_at = NULL "
        "WHERE conversation_id = ?",
        (conversation_id,),
    )
    await db.commit()


async def replace_items(
    *,
    user_email: str,
    conversation_id: str,
    items: list[dict],
    approval_mode: str = "each",
) -> dict:
    """Edit-with-implicit-approve (P2 #24 Phase 2 PATCH endpoint).
    Replaces the items list, validates the new items, and marks
    the plan approved with the supplied mode. The user-edit flow
    in the UI submits the full list (mirrors set_plan's shape)
    plus a mode field; Approve-all clicks set mode='auto', plain
    Edit-save uses the default 'each'.

    Distinct from set_plan only in that this preserves the
    ``requires_approval`` flag (the model still wants approval on
    future gated calls) and immediately approves the current
    revision."""
    if approval_mode not in _ALLOWED_APPROVAL_MODES:
        raise PlanError(
            f"approval_mode must be one of {sorted(_ALLOWED_APPROVAL_MODES)}, "
            f"got {approval_mode!r}"
        )
    existing = await get_plan(conversation_id)
    if existing is None:
        raise PlanError("no plan exists for this conversation to edit")
    normalised = _normalise_items(items)
    now = _iso_now()
    items_json = json.dumps(normalised)
    db = await get_db()
    await db.execute(
        "UPDATE conversation_plans SET items = ?, updated_at = ?, "
        "approved_at = ?, approval_mode = ?, user_email = ? "
        "WHERE conversation_id = ?",
        (items_json, now, now, approval_mode, user_email, conversation_id),
    )
    await db.commit()
    return (await get_plan(conversation_id)) or existing


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------


def build_plan_block(plan: Optional[dict]) -> Optional[str]:
    """Render the plan as the ``=== CURRENT PLAN ===`` block that
    ``_build_full_system_prompt`` prepends to the persona system
    prompt. Returns None when there's no plan so the caller can skip
    the block entirely.

    Status icons mirror the frontend's `PlanCard` rendering so a
    grep for `[in_progress] p-2: ...` in the server logs lines up
    with what the user sees in the UI.

    P2 #24 Phase 2: when ``requires_approval`` is set, an APPROVAL
    STATUS line is included so the model can see what the gate sees
    (awaiting approval vs approved-each vs approved-auto). This is
    load-bearing for the prompt nudge that tells the model to wait
    rather than retry endlessly when a gated tool returns
    ``awaiting_user_approval``."""
    if plan is None:
        return None
    items = plan.get("items") or []
    if not items:
        return None
    lines = ["=== CURRENT PLAN ==="]
    lines.append(
        "You committed to the following plan earlier in this conversation. "
        "Update each item's status as you work through it: call "
        "update_plan_item(id, status) to flip pending -> in_progress -> done, "
        "and call set_plan with a fresh list if you need to revise the "
        "structure."
    )
    # P2 #24 Phase 2: surface the approval state inline so the model
    # knows whether to proceed, wait, or expect another gate check.
    if plan.get("requires_approval"):
        approved_at = plan.get("approved_at")
        mode = plan.get("approval_mode", "each")
        if not approved_at:
            lines.append(
                "APPROVAL STATUS: AWAITING APPROVAL — the user must approve "
                "this plan before gated tool calls (e.g. deep_research) "
                "run. If a gated tool returns "
                "'awaiting_user_approval', WAIT for the user; do NOT retry "
                "the tool in this turn — the UI is asking them for approval "
                "and you'll get a new turn once they decide."
            )
        elif mode == "auto":
            lines.append(
                "APPROVAL STATUS: APPROVED (auto-mode) — the user pre-approved "
                "every gated tool call for this plan. Proceed normally."
            )
        else:  # mode == 'each', approved_at set
            lines.append(
                "APPROVAL STATUS: APPROVED (single use) — the user approved "
                "the next gated tool call. After it runs, future gated calls "
                "will require fresh approval."
            )
    for it in items:
        title = (it.get("title") or "").strip()
        status = it.get("status") or "pending"
        item_id = it.get("id") or "?"
        lines.append(f"- [{status}] {item_id}: {title}")
        notes = (it.get("notes") or "").strip()
        if notes:
            lines.append(f"    notes: {notes}")
    lines.append("=== END ===")
    return "\n".join(lines)
