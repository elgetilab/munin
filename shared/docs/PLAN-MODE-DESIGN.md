# Plan mode — design doc

> **Status**: design, not yet implemented.
> **Audit row**: P2 #24 ("Plan mode for `research` persona") — broadened to a
> general-purpose primitive per the 2026-05-28 design discussion.
> **Owner**: backend (cluster) + webui frontend
> **Branch**: TBD (suggest `audit-24-plan-mode-phase1` and
> `audit-24-plan-mode-phase2`)

## 1. Motivation and goals

Two distinct features are bundled under the audit row, and they have very
different value profiles. We split them deliberately:

**Phase 1 — Structural plan** (high-frequency, broad value)
The model decomposes work into discrete steps and the UI renders them as a
checkbox list. Items flip pending → in_progress → done as work proceeds. This
is structural scaffolding — no user gate, no approval — and it pays off for
every persona that does multi-step work (chat, code, research alike). The
model also benefits: a visible plan acts as a commitment device that keeps
long turns coherent. Claude Code's `TodoList` is the existence proof.

**Phase 2 — Approval gate** (lower-frequency, higher-stakes value)
A hard control-flow stop before an expensive or irreversible tool runs. The
model writes a plan, the UI shows Approve / Edit / Reject, the gated tool
fires only on approval. The audit row called this out for `research` because
a `delegate_to_persona → research → 20-call deep_research run` is genuinely
"burn minutes + a lot of tokens." We additionally provide a Claude-Code-
style "auto-approve all for this session" toggle so a user who has reviewed
the plan once doesn't have to confirm every subsequent gated call.

## 2. Non-goals

- **Workflow engine**. Plans are user-visible scaffolding, not a programmable
  step graph. No conditionals, no branching, no parallelism declarations.
- **Inter-conversation plans**. A plan belongs to one conversation.
- **Plan templates / saved plans**. Out of scope until a real use case lands.
- **Nested per-agent sub-plans**. `invoke_agent` runs to completion within
  the parent's plan item; the agent itself doesn't get its own plan UI.

## 3. Codebase landscape — what we reuse

| Need | Existing pattern | Location |
|---|---|---|
| Tool registration | `@register_tool("name")` decorator (P2 #19) | `mcp/_dispatch.py`, `mcp/dispatchers.py` |
| Tool schema | `MCP_TOOLS` dict with jsonschema validation | `mcp/schemas.py`; validation at `mcp/executor.py:118` |
| Always-shipped tools | `CORE_TOOLS` frozenset | `mcp/schemas.py:19` |
| Persistent versioned state with side-panel UI | `artifacts` + `artifact_versions` tables | `chat_store.py:202-227`, `artifact_store.py:410+` |
| Mutable per-conversation state | `proposed_memories` table | `chat_store.py:170+`, `memory_proposals_store.py` |
| SSE event emission | `_sse(event, payload)` + emitter ContextVar | `chat_service.py:71`, `mcp/context.py:current_sse_emitter` |
| SSE event catalog | `BACKEND-API.md §5` | `shared/docs/BACKEND-API.md` |
| Turn short-circuit on tool-call | `ask_clarification` pattern: persist + emit + `finish_reason: "clarification"` | `chat_service.py:2150-2204` |
| preToolUse / postToolUse gate hooks | Hooks framework (P2 #23) | `hooks/_dispatcher.py`, wired at `chat_service.py:921+947` |
| Persona param reader | `params.max_turns`, `params.tool_allowlist` clamp/parse | `personas.py:233-301` |
| Persona schema validation | `_Persona`/`_Params` Pydantic models, `extra="forbid"` (P2 #20) | `personas.py:33-90` |
| Conversation ownership check | `_verify_conversation_owned(conv_id, user_email)` | `artifact_store.py:368` (reusable shape) |
| Frontend SSE event handling | switch in `useChat.ts` | `frontend/webui/src/hooks/useChat.ts` |
| Per-message persistent UI state | `message.memory_proposals`, `message.compact_boundary` | `frontend/webui/src/lib/types.ts:43-60` |
| Inline below/above bubble UI | `MemoryProposalPill`, `CompactBoundaryDivider` | `frontend/webui/src/components/` |
| System prompt block assembly | `_build_full_system_prompt` ambient blocks | `chat_service.py:1157+` |

**Implication**: Phase 1 is mostly mechanical. We add one table, two MCP
tools, one SSE event, two REST endpoints, a system-prompt block, and one UI
component. Phase 2 adds one persona param, one preToolUse hook, three more
REST endpoints, a couple of buttons, and a small extension to the table
schema. No load-bearing refactors.

---

## Phase 1 — Structural plan

### 4. Data model

#### 4.1 New table `conversation_plans`

Add to the `chat_store.py` CREATE block:

```sql
CREATE TABLE IF NOT EXISTS conversation_plans (
    conversation_id  TEXT PRIMARY KEY,           -- one plan per conversation
    user_email       TEXT NOT NULL,              -- ownership check fast-path
    items            TEXT NOT NULL,              -- JSON list of items (below)
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- Phase 2 fields (added in a second migration, here for context):
    requires_approval INTEGER NOT NULL DEFAULT 0,  -- 0/1
    approved_at      TEXT,
    approval_mode    TEXT NOT NULL DEFAULT 'each', -- 'each' | 'auto'
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_plans_user
    ON conversation_plans(user_email, updated_at DESC);
```

**Rationale for single-table-mutable (not versioned)**: plans are inherently
mutable transient scaffolding. The audit log of plan changes lives in the
conversation transcript (each `set_plan` / `update_plan_item` call is
persisted as a `message.tool_call`). A separate `plan_versions` table would
duplicate that information without adding query power. If we later want plan
history, we already have it in `messages.tool_calls`.

#### 4.2 Item JSON shape (`items` column)

```jsonc
[
  {
    "id": "p-1",                            // stable id (uuid or model-supplied "p-1")
    "title": "Search arxiv for cryo-EM 2024 reviews",
    "status": "pending",                    // pending | in_progress | done | cancelled
    "notes": "Focus on membrane-protein subfield",  // optional
    "updated_at": "2026-05-28T..."          // for sub-item timestamps
  }
]
```

Cap: **20 items per plan**, **200 chars per title**, **500 chars per notes**.
Enforced at the store layer so the MCP tool stays thin. Same shape rationale
as `memory_store`.

#### 4.3 New module `plan_store.py`

```python
# Public surface — mirror of memory_store.py's shape.

async def get_plan(conversation_id: str) -> Optional[dict]:
    """Return the full plan dict (items + metadata) or None."""

async def set_plan(
    *, user_email: str, conversation_id: str, items: list[dict],
    requires_approval: bool = False,
) -> dict:
    """Replace the conversation's plan with `items`. Resets approval state
    (Phase 2). Returns the persisted plan dict."""

async def update_item(
    *, user_email: str, conversation_id: str, item_id: str, status: str,
    notes: Optional[str] = None,
) -> dict:
    """Flip one item's status (+ optional notes). 404 on missing item.
    Returns the full plan post-update."""

async def clear_plan(*, user_email: str, conversation_id: str) -> bool:
    """Delete the plan (e.g. on conversation deletion or explicit reset).
    Returns True iff a row was removed."""

# Phase 2 additions (declared here for completeness):

async def mark_approved(
    *, user_email: str, conversation_id: str, approval_mode: str,
) -> dict:
    """Mark the plan approved. `approval_mode` ∈ {'each', 'auto'}."""

async def reject_plan(*, user_email: str, conversation_id: str) -> bool:
    """Delete the plan + record the rejection so the model sees the
    short-circuit on its next turn (the gated tool result will say
    'user rejected the plan; pick a different approach')."""

# System prompt rendering:
def build_plan_block(plan: Optional[dict]) -> Optional[str]:
    """Format the plan as a system-prompt block. Returns None if empty so
    the assembler can skip the section entirely."""
```

### 5. MCP tools

Two tools, both in `CORE_TOOLS` so they're available to every persona by
default without tool_search.

#### 5.1 `set_plan`

**Schema** (`mcp/schemas.py`):

```jsonc
{
  "name": "set_plan",
  "description": "REPLACE the current task list for this conversation with `items`. Use this at the start of any multi-step request to break the work into discrete checkboxed steps the user can see. Always set the FIRST item's status to 'in_progress' when you immediately start working on it; subsequent items stay 'pending' until you start them. To FLIP a single item's status (pending → in_progress → done), use `update_plan_item` instead — it's cheaper than retyping the whole list. CALLING RULES: (1) call this BEFORE any other tool on a multi-step task; (2) keep titles short and action-oriented ('Search arxiv for X' not 'I will search arxiv for X'); (3) at most 20 items, 200 chars per title; (4) `requires_approval: true` is for Phase 2 (see persona-specific guidance); leave false on routine multi-step work.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "minItems": 1,
        "maxItems": 20,
        "items": {
          "type": "object",
          "properties": {
            "id":     { "type": "string", "description": "Stable id ('p-1', 'p-2', ...). Auto-assigned if omitted." },
            "title":  { "type": "string", "maxLength": 200 },
            "status": { "type": "string", "enum": ["pending", "in_progress", "done", "cancelled"], "default": "pending" },
            "notes":  { "type": "string", "maxLength": 500 }
          },
          "required": ["title"]
        }
      },
      "requires_approval": { "type": "boolean", "default": false }
    },
    "required": ["items"]
  },
  "is_concurrency_safe": false   // writes per-conversation state; serialise
}
```

#### 5.2 `update_plan_item`

```jsonc
{
  "name": "update_plan_item",
  "description": "Flip ONE plan item's status by id, or update its notes. Use this between tool calls so the user sees progress as you go. Common pattern: call set_plan once with the full list at turn start, then update_plan_item('p-1', 'in_progress') before starting the work, then update_plan_item('p-1', 'done') when finished, then update_plan_item('p-2', 'in_progress') for the next item.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "id":     { "type": "string" },
      "status": { "type": "string", "enum": ["pending", "in_progress", "done", "cancelled"] },
      "notes":  { "type": "string", "maxLength": 500 }
    },
    "required": ["id"]
  },
  "is_concurrency_safe": false
}
```

#### 5.3 Dispatchers (`mcp/dispatchers.py`)

Both register via the P2 #19 decorator and pull `user_email` /
`conversation_id` from ContextVars (the same way artifacts already do).

```python
@register_tool("set_plan")
async def _set_plan(arguments: dict) -> dict:
    user_email = current_user_email.get()
    conversation_id = current_conversation_id.get()
    if not user_email or not conversation_id:
        return {"error": "set_plan requires an authenticated, persistent chat"}
    items = arguments.get("items", [])
    requires_approval = bool(arguments.get("requires_approval", False))
    try:
        plan = await plan_store.set_plan(
            user_email=user_email,
            conversation_id=conversation_id,
            items=items,
            requires_approval=requires_approval,
        )
    except plan_store.PlanError as e:
        return {"error": str(e)}
    # Emit the SSE event so the inline UI updates in real time, then
    # return a compact tool_result that the model can reason from.
    emit = current_sse_emitter.get()
    if emit is not None:
        emit("plan_updated", plan)
    return {
        "ok": True,
        "item_count": len(plan["items"]),
        "ids": [i["id"] for i in plan["items"]],
    }


@register_tool("update_plan_item")
async def _update_plan_item(arguments: dict) -> dict:
    # ... same pattern; emits plan_updated; returns the full plan.
```

### 6. SSE event

Add to `BACKEND-API.md §5`:

| Event | Payload | Emitted when |
|---|---|---|
| `plan_updated` | `{"items": [...], "requires_approval": bool, "approved_at": str\|null, "approval_mode": "each"\|"auto", "updated_at": str}` | After every successful `set_plan` or `update_plan_item` call. Frontend renders an inline plan panel above the assistant bubble that last touched the plan. State persists across reloads via `Message.plan_snapshot` (see §9). |

### 7. System prompt injection

Extend `_build_full_system_prompt` in `chat_service.py`. The plan block sits
between the memory block and the persona prompt — high enough to be salient
but below user-curated context:

```
project_block
artifact_block
plan_block                  ← NEW
memory_block
profile_block
<persona system prompt>
ambient (current date)
agent_hint
capabilities_block
```

`build_plan_block(plan)` renders as:

```
=== CURRENT PLAN ===
You committed to the following plan earlier in this conversation. Update
each item's status as you work through it. Use update_plan_item to flip
status; use set_plan to revise the structure.

- [in_progress] p-1: Search arxiv for cryo-EM 2024 reviews
- [pending]     p-2: Read the top 3 hits via paper_lookup
- [pending]     p-3: Summarise common findings
=== END ===
```

If no plan exists, the block is omitted (no header at all).

Tokens cost ~80-300 per turn depending on plan size. The parallel ambient
fetches (P2 #21) extend cleanly to pull the plan alongside profile/memory/
artifact (one more `asyncio.gather` item).

### 8. REST endpoints

Mirror the `/api/memories` shape. Add to `main.py` under the existing
conversation routes:

```
GET    /api/chats/{cid}/plan          -> {plan} | 404 if no plan
DELETE /api/chats/{cid}/plan          -> {deleted: true}
```

Phase 2 adds three more (§13).

`PATCH /api/chats/{cid}/plan` (user-edits-an-item) is intentionally
**deferred to Phase 2** because user-side editing makes more sense once
the gate exists — until then a user mostly observes, doesn't tweak.

### 9. Frontend

#### 9.1 Types (`types.ts`)

```typescript
export type PlanItemStatus = 'pending' | 'in_progress' | 'done' | 'cancelled';

export interface PlanItem {
  id: string;
  title: string;
  status: PlanItemStatus;
  notes?: string | null;
  updated_at?: string;
}

export interface Plan {
  items: PlanItem[];
  requires_approval: boolean;
  approved_at: string | null;
  approval_mode: 'each' | 'auto';
  updated_at: string;
}

// extend SSEEvent union
| { type: 'plan_updated'; data: Plan }

// extend Message
plan_snapshot?: Plan | null;
```

#### 9.2 `useChat.ts` handler

```typescript
case 'plan_updated': {
  // Latch the plan onto the most recent assistant message (the one
  // whose turn invoked set_plan / update_plan_item). On done, the
  // snapshot moves to a stable field on the assistant Message.
  currentPlanSnapshot = event.data;
  setStreaming(s => ({ ...s, plan: event.data }));
  break;
}
```

In the `done` case, attach `plan_snapshot: currentPlanSnapshot` onto the
assistant Message (same pattern as `memory_proposals`, `compact_boundary`).

On `loadConversation`, the backend's `GET /api/chats/{id}` is extended to
include the plan; we attach it to the **most recent assistant message** that
has tool_calls touching the plan. Falls back to attaching to the last
assistant message if no precise match.

#### 9.3 `PlanCard.tsx` component

Inline card rendered above the assistant bubble. Renders the checkbox list,
status badges, and (Phase 2) Approve / Edit / Reject buttons.

```
┌────────────────────────────────────────────┐
│ Plan                            5 items    │
│ ────────────────────────────────────────── │
│ ✅ p-1: Search arxiv for cryo-EM reviews   │
│ 🔄 p-2: Read the top 3 hits                │
│ ⬜ p-3: Summarise common findings          │
│ ⬜ p-4: Cite + present                     │
│ ⬜ p-5: Ask user for follow-up direction   │
└────────────────────────────────────────────┘
```

Read-only in Phase 1. Hovering an item with notes shows a tooltip.

#### 9.4 MessageList integration

Same pattern as `CompactBoundaryDivider`: render the `PlanCard` **above**
the assistant bubble whose `plan_snapshot` is set. If multiple consecutive
assistant turns touch the same plan, only render the card on the **first**
of the run (the next turns are continuations of the same plan state).

### 10. Persona prompt nudges

The biggest risk for Phase 1 is **Qwen3 ignoring the tool**. We mitigate
via:

1. Per-persona system-prompt addition. Add to each shipped persona JSON:

   ```
   === TASK PLANNING ===
   For ANY multi-step request, call `set_plan` FIRST with 2-20 short
   action-oriented items. As you work through them, call
   `update_plan_item(id, status)` to flip each item from 'pending'
   → 'in_progress' → 'done'. The user sees this list and uses it to
   follow your progress. Skip set_plan only for one-step requests
   (single web_search, single calculate, single paper_lookup).
   === END TASK PLANNING ===
   ```

2. Smoke-test prompt suite. Hand-curated set of "should-trigger-set_plan"
   prompts that we run end-to-end on hugin (see §11). If Qwen3
   doesn't call `set_plan` on these, we tighten the prompt or add a
   forced-tool retry analogous to `ask_clarification`'s
   `_force_clarification_retry` (`chat_service.py:541-619`).

3. Optional: a `_force_set_plan_retry` mirror of the clarification
   retry. Only fires if the persona's `params.plan_required: true` is
   set (default false). Defer this to a follow-up if smoke tests show
   the system prompt is enough.

### 11. Tests

**Backend** (`tests/test_plan_store.py`, `tests/test_plan_dispatchers.py`):

- Store CRUD: create, get, update_item, clear; cap enforcement (20 items,
  200/500 char titles/notes); cross-user 404; item-id collision behaviour.
- Dispatchers: `set_plan` emits `plan_updated` SSE via the buffering
  emitter; `update_plan_item` returns full post-update plan; invalid
  status rejected by jsonschema validator.
- System prompt: `build_plan_block` renders the right shape; returns None
  for empty plan; ambient-block parallel fetch (P2 #21) extends cleanly.

**Frontend** (`PlanCard.test.tsx`):

- Renders correct status icons; items in order; notes-on-hover.
- `useChat` `plan_updated` handler attaches to the most recent assistant
  message; latch survives transcript reload.

**Smoke-test prompts** (curated; run manually on hugin until we automate):

- "Help me find recent papers on polymer self-assembly and summarise key
  findings." → expects 3-5 items
- "Refactor this Python script to use type hints and add tests." → expects
  3-4 items including update_plan_item between steps
- "What's the weather in Berlin?" → expects NO set_plan (single-step)

Pass criteria: model invokes `set_plan` correctly on multi-step prompts,
skips it on single-step prompts, and updates item status between actions.
This is the **non-trivial behavioural test** flagged by the user.

### 12. Migration + deploy

1. SQLite schema add → no migration tool needed; the CREATE block in
   `chat_store.py` is idempotent (`IF NOT EXISTS`). Same shape as P2 #25.
2. `deploy.sh retrieval` rebuilds the container. New tables auto-create on
   first `get_db()` call.
3. Personas: edit `shared/personas/*.json` to add the task-planning block →
   `deploy.sh personas` syncs to `/opt/munin/personas/`.
4. Dockerfile: `*.py` glob picks up `plan_store.py`. No Dockerfile change
   needed.
5. Hooks: no Phase 1 hooks (`hooks/audit_log.py` continues unchanged).

---

## Phase 2 — Approval gate

### 13. Persona param

Extend the `_Params` Pydantic model (P2 #20) in `personas.py`:

```python
class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ...existing fields...
    plan_approval: Optional[list[str]] = None
    # List of MCP tool names that require user approval of the current
    # plan before they run. The model can also opt into approval ad-hoc
    # via `requires_approval: true` on set_plan, independent of this.
```

Validation: each entry must be a known MCP tool name (cross-checked at
startup via the dispatch registry — natural extension of the P2 #19
verify_dispatch_registry check).

Example `research.json`:

```json
"params": {
  "...": "...",
  "plan_approval": ["delegate_to_persona", "deep_research"]
}
```

Reader helper in `personas.py`:

```python
def plan_approval_tools(persona: Optional[dict]) -> frozenset[str]:
    """Return the set of tool names that require plan approval for this
    persona. Empty frozenset when not configured."""
```

### 14. Plan schema additions

The Phase 1 table already declares the Phase 2 columns:

```sql
requires_approval INTEGER NOT NULL DEFAULT 0,
approved_at      TEXT,
approval_mode    TEXT NOT NULL DEFAULT 'each'    -- 'each' | 'auto'
```

State machine:

```
                  set_plan(requires_approval=true OR persona-gated tool incoming)
                                       │
                                       ▼
                              ┌──────────────┐
       reject_plan ◄──────────│   pending    │──────► approve(mode='auto')
       (DELETE row)           │ approved_at  │
                              │   = NULL     │──────► approve(mode='each')
                              └──────────────┘
                                       │                       │
                                       ▼                       ▼
                              ┌──────────────┐         ┌──────────────────┐
                              │   approved   │         │  approved-each   │
                              │   (auto)     │         │  next gated tool │
                              └──────────────┘         │  flips back to   │
                                                       │  pending         │
                                                       └──────────────────┘
```

- `approval_mode: 'each'` → after one gated tool call runs, `approved_at` is
  cleared and the next gated call re-triggers the gate.
- `approval_mode: 'auto'` → `approved_at` persists until plan is replaced or
  conversation ends. The "yes-and-don't-ask-again" Claude-Code button.

### 15. preToolUse hook

New file: `hooks/plan_approval.py`. Registered via the P2 #23 framework.

```python
@register("preToolUse")
async def plan_approval_gate(
    ctx: HookContext, name: str, arguments: dict,
) -> Optional[dict]:
    # Determine if this tool is gated for this conversation's persona.
    persona = persona_module.get_persona(ctx.persona_id) if ctx.persona_id else None
    gated_tools = persona_module.plan_approval_tools(persona) if persona else frozenset()
    plan = await plan_store.get_plan(ctx.conversation_id) if ctx.conversation_id else None
    model_gated = bool(plan and plan.get("requires_approval"))
    if name not in gated_tools and not model_gated:
        return None  # not a gated call; let it run

    # Look up plan + approval state.
    if not plan:
        # No plan at all; the gate has nothing to gate. Synthesize a
        # short-circuit nudging the model to set_plan first.
        return {
            "error": "plan_approval_required",
            "message": (
                f"This persona requires an approved plan before calling "
                f"{name!r}. Call set_plan with the steps you intend to "
                f"take and wait for the user to approve."
            ),
        }

    approved_at = plan.get("approved_at")
    mode = plan.get("approval_mode", "each")
    if approved_at:
        if mode == "auto":
            # Auto-approve: let it run, no state change.
            return None
        # mode == 'each': consume the approval and let it run. The
        # postToolUse hook below clears approved_at so the NEXT gated
        # call re-triggers the gate.
        return None

    # Not approved. Short-circuit + emit the SSE event so the UI shows
    # the Approve / Edit / Reject buttons.
    ctx.emit_sse("plan_approval_required", {
        "tool": name,
        "arguments": arguments,
        "plan": plan,
    })
    return {
        "status": "awaiting_user_approval",
        "tool": name,
        "plan_summary": [i["title"] for i in plan["items"]],
    }


@register("postToolUse")
async def consume_each_approval(
    ctx: HookContext, name: str, arguments: dict, result: dict, duration_ms: int,
) -> None:
    # If this was a gated tool that just ran on a 'each' approval, clear
    # the approval so the next gated call re-triggers the gate.
    persona = persona_module.get_persona(ctx.persona_id) if ctx.persona_id else None
    gated_tools = persona_module.plan_approval_tools(persona) if persona else frozenset()
    if name not in gated_tools:
        return None
    plan = await plan_store.get_plan(ctx.conversation_id) if ctx.conversation_id else None
    if plan and plan.get("approval_mode") == "each" and plan.get("approved_at"):
        await plan_store.clear_approval(ctx.conversation_id, ctx.user_email)
    return None
```

### 16. SSE events (additions)

| Event | Payload | When |
|---|---|---|
| `plan_approval_required` | `{"tool": str, "arguments": dict, "plan": Plan}` | preToolUse hook gates a call. Frontend renders Approve / Edit / Reject buttons on the PlanCard. Stream continues — does NOT short-circuit `done`. The synthetic `tool_result` is dispatched normally; the model sees `{"status": "awaiting_user_approval"}` and typically stops generating; the turn ends with `done` and the user takes action via REST. |
| `plan_updated` extension | (existing event; now also includes `requires_approval`, `approved_at`, `approval_mode`) | Any plan write. |

### 17. REST endpoints

```
POST   /api/chats/{cid}/plan/approve
       body: {"mode": "each" | "auto"}
       → 200 {"approved": true, "mode": ..., "approved_at": ...}

POST   /api/chats/{cid}/plan/reject
       → 200 {"rejected": true}
       (deletes the plan row, persists a message tool_result the model
        sees on the next turn explaining the user rejected the plan)

PATCH  /api/chats/{cid}/plan
       body: {"items": [...]}
       → 200 {plan}
       (user-edits-the-plan; on save, implicitly approves and sets
        approval_mode based on a body field; the model sees the edited
        plan on the next iteration via the system-prompt block)
```

All scoped by `X-Munin-Email`; 404 on cross-user.

### 18. Frontend additions

`PlanCard` becomes interactive when `plan.requires_approval && !approved_at`:

```
┌────────────────────────────────────────────┐
│ ⚠ Approval required           5 items     │
│ ────────────────────────────────────────── │
│ ⬜ p-1: ...                                │
│ ⬜ p-2: ...                                │
│ ...                                        │
│ ────────────────────────────────────────── │
│ [Approve] [Approve all]  [Edit]  [Reject]  │
└────────────────────────────────────────────┘
```

- **Approve** → POST `/api/chats/{cid}/plan/approve` with `mode: "each"`
- **Approve all** → POST with `mode: "auto"` (Claude-Code-style "don't ask
  me again this session"). A small indicator on the PlanCard shows
  `auto-approve on` so the user knows the gate is suppressed for the rest
  of this conversation.
- **Edit** → opens a textarea for each item (notes editable too); on save
  PATCHes the plan, implicitly approving in the configured mode.
- **Reject** → POST `/api/chats/{cid}/plan/reject`. The frontend then sends
  a synthetic user message (`"I'd like to take a different approach."`) so
  the model gets a turn to respond. Alternatively the user can just type a
  follow-up themselves; the rejection is recorded either way.

When `approval_mode === 'auto'`, the PlanCard shows a small "auto-approve
on" badge with a `[revoke]` button that POSTs `/api/chats/{cid}/plan/approve`
with `mode: "each"` (toggles back to per-call approval).

### 19. Phase 2 tests

Backend:

- preToolUse hook gates the right tools for the right personas.
- `mode: 'each'` clears `approved_at` after one gated tool call.
- `mode: 'auto'` persists `approved_at` across multiple gated calls.
- Approve / Reject / Edit endpoints scoped by user_email.
- Rejection deletes the plan row.
- Edit-with-approve atomically writes both fields.

Frontend:

- `PlanCard` renders approval buttons iff `requires_approval &&
  !approved_at`.
- Click Approve → POST fires; pill updates optimistically.
- Approve-all sets a visible "auto-approve on" indicator.
- Revoke toggles auto-approve back to per-call.

Smoke tests (manual, on hugin):

- Research persona with `plan_approval: ["delegate_to_persona"]`:
  1. User asks "do a deep lit review on X".
  2. Model calls `set_plan` with 5 items.
  3. Model calls `delegate_to_persona` → preToolUse short-circuits → SSE
     `plan_approval_required` fires.
  4. UI shows Approve/Edit/Reject buttons.
  5. User clicks Approve → delegation runs.
  6. After delegation, second gated call re-triggers the gate
     (because `mode='each'`).
  7. User clicks Approve all → subsequent calls run without prompt.

---

## 20. Open risks

| Risk | Mitigation |
|---|---|
| Qwen3 ignores `set_plan` despite persona prompt | Smoke-test suite gates merge; if model misses, add a forced-tool retry (mirrors `_force_clarification_retry`). |
| Qwen3 calls `set_plan` then forgets to `update_plan_item` | Document the failure mode in the persona prompt; accept the worst case as "plan looks stale but doesn't block anything." |
| Plan tokens push prompts over the budget on long turns | The plan block sits BELOW the artifact and project blocks but ABOVE the memory block; the existing compaction pipeline (P2 #22) summarises old messages, not system blocks, so the plan stays. Cap 20 items × 200 chars = 4000 chars total. Acceptable. |
| Two consecutive turns each set their own plan, second clobbering the first | `set_plan` overwrites by design. The audit trail in `messages.tool_calls` preserves history. |
| User edits plan mid-turn (while model is streaming) | PATCH endpoint takes a write lock on the conversation row via SQLite default behaviour. Inflight model turn sees the post-edit plan on its NEXT system-prompt assembly. Brief inconsistency window is acceptable. |
| Phase 2: model invokes gated tool ad-hoc before any plan exists | preToolUse hook synthesizes a "set_plan first" error. Model has a turn to retry with set_plan. |
| Phase 2: user rejects then changes mind | They can just send a new message describing what they want; the model writes a fresh plan. No "undo reject" affordance needed. |
| Phase 2: race between approve REST call and an inflight tool dispatch | Approval state is read inside the preToolUse hook, which runs per tool call. Worst case the user approves a millisecond after the hook short-circuited; they'll see the await-approval state, click Approve again, and the next dispatch resolves it. |

## 21. Sequencing + estimated effort

| Phase | Item | Effort | Branch |
|---|---|---|---|
| 1 | Schema + plan_store + dispatchers + SSE event + system-prompt block | S-M | `audit-24-plan-mode-phase1` |
| 1 | REST endpoints (GET /plan, DELETE /plan, extend GET /chats/{id}) | XS | same |
| 1 | Frontend: types, useChat, PlanCard, MessageList integration | M | same |
| 1 | Persona prompt edits + shipped persona JSONs | S | same |
| 1 | Tests (backend store + dispatchers; frontend PlanCard) | S | same |
| 1 | Smoke-test on hugin (the Qwen3-cooperates check) | S | manual |
| 2 | Persona param `plan_approval` + Pydantic validation | XS | `audit-24-plan-mode-phase2` |
| 2 | Schema extension (3 columns already declared in Phase 1) | (already done) | same |
| 2 | preToolUse + postToolUse hooks | S | same |
| 2 | REST endpoints (approve, reject, PATCH /plan) | S | same |
| 2 | Frontend: approval buttons + auto-approve mode + revoke | M | same |
| 2 | Tests (gate, mode-each, mode-auto, edit, reject) | S | same |

Total: **Phase 1 ≈ M effort; Phase 2 ≈ S-M on top.**

If Phase 1 ships and Qwen3 doesn't reliably call the tool, sequencing
changes: a `_force_set_plan_retry` follows in a third commit before Phase 2.

## 22. Out of scope (explicit non-goals)

- **Memory of plans across conversations**. Each chat starts fresh.
- **Plan templates / saved plans / "favourite plans" UI**. Not until users
  ask for it.
- **Multi-tenant plan sharing** (showing one user's plan to another).
- **Plan analytics** ("you completed N plans this week"). Easy enough to
  bolt on later via `messages.tool_calls` greps if interesting.
- **Replan voting** (multiple suggested plans, user picks one). Could be
  layered on top of Phase 2's Edit flow if needed.
- **`research` persona-specific deep-research plan presets**. The whole
  point of broadening this is that `research` becomes one configuration of
  the generic feature, not a special-case branch.

## 23. Documentation impact

- `BACKEND-API.md §4`: new endpoints (`GET /api/chats/{id}/plan`,
  `DELETE`, Phase 2's POSTs and PATCH).
- `BACKEND-API.md §5`: new SSE events (`plan_updated`,
  `plan_approval_required`).
- `BACKEND-API.md §6` (Persona context injection): document the new plan
  block in the stack ordering.
- `BACKEND-API.md §9` (MCP tool registry): add `set_plan` +
  `update_plan_item` to the CORE_TOOLS table.
- Per-persona docstrings (in the JSON `params.system`): add the TASK
  PLANNING section.
- `munin-audit.md`: mark row 24 phased FIXED after Phase 1 lands; fully
  FIXED after Phase 2.
