"""
Auto-memory extraction stop hook (P2 #25).

Wires the memory-extraction classifier into the chat-turn lifecycle
via the hooks framework (P2 #23). One vLLM call per accepted turn
asks the model which user-side facts from the just-finished exchange
are worth saving across conversations; each surviving candidate is
persisted as a ``proposed_memories`` row and emitted as a
``memory_proposed`` SSE event the frontend renders as a pill with
accept/reject buttons.

Gates (skip extraction silently if any fail):
  - ``terminal_reason`` must be ``done`` or ``max_turns``. We never
    extract from cancelled / stream_error / error paths because the
    user may not have seen the assistant response, so a proposal
    based on it would be confusing.
  - ``conversation_id`` must be set (ephemeral chats have no
    persistence to anchor the proposal against).
  - The conversation must have at least one user + one assistant
    message at this point.

Hook exceptions bubble into ``dispatch_stop``'s catch (logged, never
breaks the turn).
"""

from __future__ import annotations

import logging

import chat_store
import memory_extract
import memory_proposals_store
from . import HookContext, register


logger = logging.getLogger(__name__)


_ACCEPTED_TERMINAL_REASONS = {"done", "max_turns"}


async def _last_exchange(conversation_id: str) -> tuple[str, str]:
    """Return (last_user_text, last_assistant_text) for the conversation.
    Empty strings if either is missing; the classifier short-circuits
    on empty input."""
    db = await chat_store.get_db()
    # Two queries instead of one because content may be NULL for tool-
    # only assistant turns and we want the most recent NON-EMPTY entry
    # of each role. Tiny tables; cost is negligible.
    cur = await db.execute(
        "SELECT content FROM messages "
        "WHERE conversation_id = ? AND role = 'user' AND content IS NOT NULL "
        "ORDER BY index_in_conversation DESC LIMIT 1",
        (conversation_id,),
    )
    user_row = await cur.fetchone()
    cur = await db.execute(
        "SELECT content FROM messages "
        "WHERE conversation_id = ? AND role = 'assistant' AND content IS NOT NULL "
        "ORDER BY index_in_conversation DESC LIMIT 1",
        (conversation_id,),
    )
    assistant_row = await cur.fetchone()
    return (
        (user_row["content"] if user_row else "") or "",
        (assistant_row["content"] if assistant_row else "") or "",
    )


@register("stop")
async def extract_memories_at_turn_end(
    ctx: HookContext, terminal_reason: str,
) -> None:
    if terminal_reason not in _ACCEPTED_TERMINAL_REASONS:
        return
    if not ctx.user_email or not ctx.conversation_id:
        return

    try:
        user_text, assistant_text = await _last_exchange(ctx.conversation_id)
    except Exception:
        logger.exception("memory_extract: failed to fetch last exchange")
        return
    if not user_text.strip() and not assistant_text.strip():
        return

    try:
        existing = await memory_proposals_store.all_known_keys(ctx.user_email)
    except Exception:
        logger.exception("memory_extract: failed to load existing keys")
        existing = set()

    proposals = await memory_extract.extract_memories(
        user_message=user_text,
        assistant_message=assistant_text,
        exclude_keys=sorted(existing),
    )
    if not proposals:
        return

    # Persist each survivor and emit one SSE event per accepted
    # proposal. The store applies the FIFO cap (10/user); we double-
    # check the dedupe here because the classifier may have ignored
    # the EXCLUDE list and the all_known_keys set is the source of
    # truth.
    for p in proposals:
        key = p["key"]
        if key in existing:
            continue
        try:
            row = await memory_proposals_store.create_proposal(
                user_email=ctx.user_email,
                conversation_id=ctx.conversation_id,
                key=key,
                value=p["value"],
                reason=p.get("reason"),
            )
        except Exception:
            logger.exception("memory_extract: persist failed for key %r", key)
            continue
        ctx.emit_sse("memory_proposed", {
            "id": row["id"],
            "key": row["key"],
            "value": row["value"],
            "reason": row.get("reason"),
        })
        # Keep the local set in sync so a second identical proposal
        # in the same batch can't slip through.
        existing.add(key)
