# A1 implementation plan — pin personas / kill auto-delegation

**Goal (IMPLEMENTATION-HANDOFF §A1).** Make pinning the default: the model
never hands a turn to another persona mid-conversation. The user-facing
Research/Chat/Code selector keeps working (it sets the pin via the request
`persona`). "Tiny, fully reversible. Deploy; soak ~1 week of real group
usage watching whether anything genuinely wanted a handoff."

**Status:** plan for review. No code until varghele signs off (plan-first).

**Dependency note:** A1 must NOT invert the A2→A4 order. It touches ONLY
the delegation hand-off, not the persona allowlists (A4) and not
tool_search (A2). Allowlists stay active through A1's soak.

---

## 1. How delegation works today (verified 2026-06-22)

`delegate_to_persona` reaches the model via two paths, and the handoff is
implemented as a loop re-entry, not an executor tool:

- **Schema exposure:** it is in `CORE_TOOLS` (`mcp/schemas.py:27`) AND
  auto-injected into every persona's allowlist (`personas.py:378`). The
  per-request schema is `CORE_TOOLS ∩ allowlist`, so it always reaches the
  model.
- **Intercept:** `chat_service.py:2061+` catches a `delegate_to_persona`
  tool call BEFORE `_run_tool_calls` (it has no executor counterpart),
  validates (bad target / self-delegation / `DELEGATION_BUDGET`), and on
  success rewinds to a pre-loop message snapshot (`~1848`) and re-enters the
  loop with the swapped persona. Emits `delegated` + `persona_changed` SSE,
  persists the new persona to `chats.db`.
- **The entanglement (matters for A1):** the persona-allowlist reject path
  (`_run_tool_calls`, `chat_service.py:932-948`) nudges the model toward
  delegation — when a persona calls an off-allowlist tool, the synthetic
  error says *"To use it, call delegate_to_persona (...)"*. qwen3 emits
  off-schema tool calls from training memory, so this fires in practice. If
  A1 disables delegation but leaves this nudge intact, the model is advised
  to call a mechanism that now rejects it: a two-rejection loop (terminating,
  but ugly during a soak).

What A1 deletes for real is deferred to **A4** (the intercept block, rewind/
snapshot, `delegated`/`persona_changed` choreography, budget, persistence).
A1 only DISABLES the hand-off.

---

## DECISION (2026-06-22, varghele): Option 2, env-gated.

`DELEGATION_ENABLED` env var, default `false`; intercept rejects + logs
attempts; nudge drops delegation advice when disabled. Rationale in §2.

## 2. The design choice (RESOLVED: Option 2, env var)

### Option 1 — remove `delegate_to_persona` from the schema

Drop it from `CORE_TOOLS` and the `personas.py` auto-inject. The model can't
call it; the intercept becomes dead code.
- **Con:** the allowlist-reject nudge (line 939) now points at a tool the
  model can't see → frustrating loops. Requires ALSO editing the nudge, so
  it's not actually a one-line change.
- **Con:** the soak can't measure "did anything want a handoff" — the model
  can't even express the intent.

### Option 2 (RECOMMENDED) — a `DELEGATION_ENABLED` flag, default off

Keep the tool visible; gate the BEHAVIOUR.
- In the intercept, when disabled, always synthesise a clean rejection
  ("persona handoff is disabled; answer directly with your own tools") and
  **log the attempted target + reason**. The existing reject path
  (`~2111-2157`) is reused verbatim — just a new `reject_reason` branch.
- In the allowlist-reject nudge (line 939), when disabled, drop the
  "call delegate_to_persona" sentence; advise answering with available
  tools. (One conditional; the fuller rewrite is A4.)
- **Pro: the soak becomes a measurement.** Every rejected attempt is a
  logged data point answering the handoff's exact question ("whether
  anything genuinely wanted a handoff"). Option 1 throws that signal away.
- **Pro: maximally reversible** — flip the flag back, zero behaviour change.
- **Pro: doesn't touch the schema, the allowlists, or tool_search** — clean
  separation from A2/A4.

**Recommendation: Option 2.** It is the more reversible of the two AND it
turns the one-week soak into the data that justifies the A4 deletion.

### Sub-choice: flag as env var vs module constant

- **Env var `DELEGATION_ENABLED` (recommended)** read once at startup
  (`munin.env`): flip + restart, no code redeploy — the most operationally
  reversible during a live soak. Adds one config line.
- Module constant: simplest, but reversing means a code change + redeploy.

Recommend env var for the soak; it is removed wholesale at A4 anyway.

---

## 3. Changes (Option 2, the minimal set)

1. **`config` / `munin.env.template`:** add `DELEGATION_ENABLED=false` (+ the
   docker-compose env passthrough, matching the `VLLM_MODEL_NAME` pattern).
2. **`chat_service.py` intercept (~2078):** if `not DELEGATION_ENABLED`,
   short-circuit every `delegate_to_persona` call to the existing reject
   path with `reject_reason = "persona handoff is disabled; answer the
   request directly with your own tools"`, and `logger.info` the attempted
   `target_id` + `reason` under a stable, greppable prefix (e.g.
   `delegation-disabled attempt`) so the soak can count them.
3. **`chat_service.py` nudge (~939):** when `not DELEGATION_ENABLED`, emit
   the nudge WITHOUT the delegate_to_persona advice (just "answer using only
   the tools you have"). Gate behind the same flag so it is a no-op when
   delegation is on.
4. **Nothing else.** Schema, allowlists, tool_search, the selector, and the
   persisted-persona flow are untouched. The selector already "sets the pin"
   (request `persona` is honoured as-is; conversations keep their persona
   because nothing switches it).

Everything is behind one flag → revert = `DELEGATION_ENABLED=true` + restart.

---

## 4. Verification before deploy

- **Routing eval re-run (cheap regression):** re-run the A0 harness post-
  change under the same chat policy; tag `routing-post-A1`. Expectation:
  items that never relied on delegation are unchanged; any that did shift.
  `compare` (when built) diffs A0 vs A1; for now eyeball the two scorecards.
  Note from the A0 baseline: `reroute_research_to_compute` was already 0/8
  WITH delegation available (the model never delegated for it), so A1 may
  barely move the eval — the soak, not the eval, is A1's real test.
- **A targeted manual check:** drive a chat-persona request that previously
  delegated (the `test_delegate_persona.py` default message) and confirm
  (a) no `delegated`/`persona_changed` SSE fires, (b) the model answers in
  chat persona or cleanly declines, (c) the rejection + attempt log line
  appears. `test_delegate_persona.py` itself will now FAIL (it asserts
  delegation fires) — that is expected; do NOT "fix" it here. Its router-era
  replacement is the routing eval (A4 retires the old script per the
  handoff).
- **No new unit test churn:** the change is a runtime flag; the existing
  `backend/eval/` pong scenario and the routing eval cover the behaviour.

---

## 5. Soak (the actual deliverable)

- Deploy with `DELEGATION_ENABLED=false`. Soak ~1 week of real group usage.
- **What to watch:** the `delegation-disabled attempt` log lines. Each is a
  turn where the model wanted to hand off. Tally by (from_persona,
  target_id, reason) — this is the evidence base for A4 ("delegation was
  rarely/never genuinely needed" or "these N cases need an explicit
  fallback").
- If the tally shows a class of requests that genuinely needed a handoff and
  degraded badly without it, that is a finding to bring back BEFORE A4
  deletes the machinery (the handoff keeps `delegate_to_persona` "at most as
  a rare explicit fallback" for exactly this).

---

## 6. Acceptance for A1

Code-level (done 2026-06-22, working tree, not yet deployed):
- [x] `DELEGATION_ENABLED` flag added (`chat_service.py` module constant,
      env-read; `munin.env.template` + docker-compose passthrough), default
      false. Parses; true/1/yes → on, anything else → off.
- [x] Intercept rejects + logs delegation attempts when disabled
      (`chat_service.py` ~2103, greppable `delegation-disabled attempt`);
      reuses the existing reject path.
- [x] Allowlist-reject nudge drops delegation advice when disabled
      (`chat_service.py` ~948, flag-gated branch).
- [x] `test_delegate_persona.py` left untouched (fails by design post-A1;
      retired at A4).

Deploy-gated (varghele — production change, not done unilaterally):
- [ ] Deploy (`backend/deploy.sh`) with `DELEGATION_ENABLED=false`.
- [ ] Manual check: a previously-delegating request now stays in-persona,
      no `delegated`/`persona_changed` SSE, `delegation-disabled attempt`
      logged.
- [ ] `routing-post-A1` scorecard captured (re-run the A0 harness against the
      DEPLOYED change) and eyeballed vs A0 — only delegation-dependent items
      may shift. NOTE: this can only run after deploy; against the current
      live service the eval just reproduces A0.
- [ ] Soak started; date logged.
- [ ] (A4, NOT now) intercept/rewind/SSE/budget/persistence deletion and the
      `test_delegate_persona.py` retirement.

## 7. Risks / notes

- **Loop risk** if the nudge is left advising a disabled mechanism — handled
  by change #3 (gate the nudge on the same flag).
- **`test_delegate_persona.py` will fail by design.** Expected; it is retired
  at A4, replaced by the routing eval. Do not patch it at A1.
- The flip-flag approach means A1 adds no net code debt: A4 removes the flag,
  the intercept, and the nudge branch together.
- Selector/UX unchanged: the front-end persona selector keeps setting the
  request `persona`; with delegation off, that pin simply holds for the whole
  conversation.
