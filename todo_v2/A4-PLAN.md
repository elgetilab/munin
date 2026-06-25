# A4 implementation plan — delete delegation + retire allowlists

**Goal (IMPLEMENTATION-HANDOFF §A4).** Remove the now-dead delegation
machinery from the hot path, and retire the per-persona tool allowlists (the
router's profile choice replaces them). This is the largest and riskiest step
of the migration: it DELETES load-bearing-looking code and RELAXES a hard
security-ish boundary (the allowlist).

**Prerequisites met:** A1 (delegation disabled by flag, soaked), A2
(tool_search hardened, proven to carry deferred tools), A3 (router live,
validated: research queries route to research and reach their tools without
the allowlist workaround). A4 is unblocked.

**Gate:** full routing-eval regression vs the **A0 scorecard** (no
previously-passing item may fail), then **1 week of real-user soak**.

**Status:** design fully specified — all 5 open questions decided (2026-06-25):
Q1 split A4a (delete delegation, no-op) then A4b (retire allowlists, soak);
Q2 fully delete delegate_to_persona (data-backed); Q3 remove tool_allowlist
field (None-path IS the retirement mechanism); Q4 soft bias via per-profile
`resident_tools` surfacing (repurpose the allowlist's positive signal,
targets the follow-through residual); Q5 retire `_eval_full`. Ready to build.
No code until varghele gives the explicit go (plan-first).

---

## 1. The deletion surface (verified 2026-06-25)

### 1a. Delegation machinery (A1 disabled it via `DELEGATION_ENABLED=false`; A4 deletes it)

~78 delegation-related lines across the service. To remove:
- **`delegate_to_persona`** tool: from `CORE_TOOLS` (`mcp/schemas.py`), its
  schema entry, and the `personas.py` auto-inject into every allowlist.
- **The intercept block** (`chat_service.py` ~2090-2300): detection,
  validation, rewind to the pre-loop message snapshot, re-entry with swapped
  persona, the `DELEGATION_BUDGET`/`delegations_used` budget.
- **`delegated` / `persona_changed` SSE** emission + the post-delegation
  persona persistence to `chats.db` + the second `_build_full_system_prompt`
  rebuild (`chat_service.py` ~2384, the delegation re-build).
- **The "nudge toward delegation"** in the allowlist-reject path
  (`chat_service.py` ~960): the synthetic error telling the model to call
  `delegate_to_persona`. (A1 already drops the advice when the flag is off;
  A4 deletes the branch. Note: the whole allowlist-reject path goes away in
  1b anyway.)
- **The plan-approval gate FOR delegation** (`chat_service.py` ~2278, "P2 #24
  Phase 2 plan-approval gate for delegate_to_persona") — the delegation-
  specific inline check ONLY; plan mode itself stays.
- **The `DELEGATION_ENABLED` flag** itself (`chat_service.py`, munin.env,
  docker-compose) — no longer needed once the machinery is gone.

**Survives:** the `persona` request field (now a routing-profile alias / the
pin); the frontend Chat/Code/Research selector (sets the pin).

### 1b. Allowlist retirement (the hard tool boundary)

Currently the per-persona `tool_allowlist` is a HARD boundary in two places:
- **`_openai_tools_schema`** (`chat_service.py` ~259): visible tools =
  `(CORE | unlocked) & allowlist_universe`. tool_search is scoped to the
  allowlist (`mcp/tools/tool_search.py`).
- **`_run_tool_calls`** (`chat_service.py` ~937-980): `allowed_tools`
  enforcement — an off-allowlist tool call is short-circuited with a
  synthetic reject.

**Retire to:** tool availability = `CORE | resident_tools(routed) |
tool_search-unlocked` over the **FULL registry**, regardless of profile. The
routed profile biases tools two ways, both SOFT: (1) its PROMPT FRAGMENT
guides usage ("use run_python" / "use deep_research"); (2) a small
`resident_tools` surfacing set puts its high-value deferred tools in the
schema directly (Q4 soft bias). Neither is a hard wall — everything is still
reachable via tool_search, nothing is rejected.
Concretely:
- `_openai_tools_schema`: universe becomes the full registry (drop the
  allowlist intersection). tool_search searches the full registry.
- `_run_tool_calls`: remove the `allowed_tools` reject path entirely.
- `personas.py`: `tool_allowlist` field + `tool_allowlist()` become dead;
  remove or deprecate. (The `_eval_full` fixture — a no-allowlist persona —
  becomes redundant; retire it too, per A3 Q5.)
- **Deliberate trade-off to document IN CODE:** the allowlist was a hard
  boundary (research literally could not run_python); per-turn routing relaxes
  it. If any tool needs a hard wall, add an explicit per-tool guard — do NOT
  resurrect allowlists (handoff rule).

---

## 2. Sequencing — DECIDED (2026-06-25): SPLIT, A4a then A4b

The two deletions have very different risk:
- **Delete delegation (1a):** LOW risk — it is dead code under the disabled
  flag; deleting it changes no runtime behaviour.
- **Retire allowlists (1b):** HIGHER risk — it RELAXES tool availability
  (profiles can now reach tools they couldn't), the "research can run code"
  trade-off. Needs the soak.

**DECISION (2026-06-25): SPLIT into A4a then A4b.** Rationale:

- **A4a is a no-op at runtime.** A1 already disabled delegation via
  `DELEGATION_ENABLED=false` (live in prod), so production ALREADY behaves as
  if delegation is gone. A4a deletes code that is already dead. Zero runtime
  change -> lands immediately, gated only by the routing-eval + pong
  regression (no soak).
- **A4b is the genuine behaviour change.** Retiring the allowlists relaxes a
  hard boundary (research can now run code; chat can reach any tool). This is
  what needs the 1-week soak + the A0 regression gate.
- **Clean attribution + independence.** Split = clear bisect if anything
  breaks. The two are cleanly independent (delegation is flag-disabled so it
  is harmless to keep through A4b; retiring allowlists removes the
  allowlist-reject nudge's trigger regardless).
- **A4a does not strand tools:** A3's router provides cross-profile tool
  access by routing to the right profile (+ core tools + tool_search), and
  this is identical to the current live A3 state.

**Ordering:** A4a (delete delegation) FIRST — shrinks the surface before the
riskier change, pure deletion, service stays trivially runnable. Then A4b,
gated on a **short A3 confidence window** (router routing reliably in real
use) since A4b pulls the allowlist backstop that would otherwise contain a
misroute, plus its own 1-week soak.

(Combined A4 — the handoff's literal framing — was considered and rejected:
it bundles the safe deletion with the risky relaxation under one gate, losing
attribution and forcing the dead-code removal to wait on the soak.)

---

## 3. Consequential updates the master plan predates

### 3a. Router-era pong test (NEW — `backend/eval/` does not exist yet)

Build `backend/eval/` per master-plan §8: `registry.py` (wraps, doesn't move,
the `backend/scripts/` QA tools) + `scenarios/pong_test.py`. The router-era
pong test asserts:
- (a') the router picks the **code** profile for "code me pong" (read the
  `routing` SSE event), OR the turn routes to code tools — NOT
  `delegate_to_persona -> Turing`.
- (b') every emitted `tool_call` name is a **real registered tool**
  (hallucinated-tool detector) — drop the allowlist-membership reference.
- (c/d/e) unchanged: artifact lands (`create_artifact`/`run_python`), no
  `error` SSE, non-empty final content.
- Keep flakiness-suite rep/variant semantics. The "start in Turing" variant
  becomes "pin = code profile".
- Seed from the existing `backend/retrieval/evals/run_eval.py` pong harness
  (todo/ Phase-1 seed).

### 3b. Retire the delegation QA tools

`backend/scripts/test_delegate_persona.py` (already FAILS under A1) + any
delegate-persona assertions in friends. Their replacement IS the routing eval.
Retire per the archive-over-delete pattern; update `shared/docs/DECISIONS.md`
with a dated entry recording the delegation retirement + the allowlist
trade-off.

### 3c. Scorecard header

`make_run_header()` / routing scorecard: rename "persona versions" ->
"routing-profile versions".

---

## 4. The gate

- **Routing-eval regression vs A0** (`scorecards/<date>_routing-pre-migration`
  from A0): run the full routing eval at A4; **no item that passed at A0 may
  fail**. Note the harnesses differ (A0 = persona+delegation; A4 = router, no
  allowlists), so this is a "don't regress" check, not like-for-like. A3
  already improved many items, so headroom exists; watch specifically for any
  item that A0 passed and A4 breaks (e.g. an item relying on a behaviour the
  allowlist enforced).
- **1 week soak** (A4b): real-user usage with allowlists retired. Watch for
  inappropriate cross-profile tool calls (e.g. a chat turn running code
  unexpectedly), tool-call error rate, and any user-visible regression. The
  per-tool-guard escape hatch is the remedy if a specific tool misbehaves.

---

## 5. Open questions (needs varghele)

1. **Sequencing (§2): DECIDED 2026-06-25 — split A4a then A4b** (A4a is a
   dead-code no-op, lands immediately; A4b is the risky allowlist relaxation
   with its own A0 regression gate + 1-week soak).
2. **`delegate_to_persona` — DECIDED (2026-06-25): FULLY DELETE.** Remove from
   CORE_TOOLS, the schema, the personas auto-inject, and the intercept. NOT
   kept as a tool_search fallback. Data-backed:
   - **Rare usage:** 15 delegation attempts in chats.db over ~6 weeks
     (declining: 7 on 05-28, then 2-3/date, latest 06-19), small user base.
   - **Use cases are router-covered:** the attempts were cross-profile
     (chat->research, research->code) — exactly what A3 now routes up-front,
     PROVEN by the A3 gate (citing_papers/group_corpus_qa/sota_phip route to
     research and reach their tools).
   - **Post-A4b, delegation has NO functional purpose:** its real job was tool
     access (research couldn't run_python -> delegated); once allowlists are
     retired, every tool is reachable via CORE + tool_search regardless of
     profile, so a single routed profile can use any tool. Delegation becomes
     vestigial.
   - Re-add from git history if a genuine need ever appears.
3. **Allowlist field — DECIDED (2026-06-25): REMOVE.** Key finding that
   de-risks A4b: the code ALREADY treats "no allowlist" as "full universe, no
   enforcement" via the None-path — `_openai_tools_schema` (line 277:
   `set(allow) if allow is not None else set(MCP_TOOLS)`) and `_run_tool_calls`
   (`allowed_tools_set` stays None -> reject skipped). So REMOVING
   `tool_allowlist` from the 3 persona JSONs IS the retirement mechanism (None
   -> full universe), with ZERO schema/enforcement code change. A4b's allowlist
   retirement is largely a config change, not code surgery.
   - **A4b behaviour change (soak this):** remove `tool_allowlist` from the 3
     persona JSONs -> full universe via the None-path. Retire `_eval_full` (Q5).
   - **A4b cleanup (post-soak, safe — the code is no longer executing):**
     remove the dead allowlist code: `tool_allowlist()`, the
     `_Persona.tool_allowlist` schema field, the `allowed_tools` enforcement
     branch in `_run_tool_calls`, the universe-intersection, and the
     `_UNSCOPED_PERSONA_WARNED` warning (would fire for every persona as noise).
   - Reversibility: re-add the field (git preserves the exact allowlists); the
     planned remedy for a problematic tool is a per-tool guard, NOT allowlist
     revival (handoff rule). "Deprecate-in-place" rejected: the None-path
     already ignores absence, so keeping-but-ignoring would mean ADDING code,
     backwards.
4. **Soft tool bias — DECIDED (2026-06-25): INCLUDE in A4b.** The routed
   profile surfaces a small set of its high-value deferred tools in the
   resident schema (beyond CORE), so the model calls them WITHOUT a
   tool_search hop. Rationale: it directly targets the deferred-tool
   follow-through residual (A2/A3: even under full-universe `_eval_full`,
   completion was only get_citations 0.31 / export_citations 0.62 because the
   tool_search->tool hop is unreliable; surfacing the tools removes the hop).
   And (varghele): with low traffic the soak can't A/B the bias vs the
   retirement anyway, so fold it in and measure via the DETERMINISTIC routing
   eval instead.

   **Mechanism (ADDITIVE surfacing, NOT a restriction — distinct from the
   retired allowlist):**
   - New persona field `params.resident_tools` (name a build detail): the
     profile's high-value deferred tools to surface by default.
   - `_openai_tools_schema`: visible = `CORE ∪ resident_tools(routed) ∪
     unlocked`, over the FULL registry (no universe intersection; everything
     else still reachable via tool_search; nothing rejected).
   - This REPURPOSES the old allowlists' POSITIVE signal (which tools a
     profile uses) while dropping their RESTRICTION function. Source
     `resident_tools` from each old allowlist, TRIMMED to ~5-8 high-value
     deferred tools (NOT the whole 27/17/35 — keep the schema small):
     - research: get_citations, get_references, export_citations,
       deep_research, compare_papers, semantic_scholar_search, paper_lookup
     - code: compile_latex, sandbox_reset, save_artifact_to_documents
     - chat: web_fetch, remember, recall (chat is mostly CORE already)
   - Prefill: CORE (~5K) + ~8 bias tools + unlocked stays ~8-10K, far under
     the 64K limit (A2 finding). The exact tool lists are a build-time
     curation, tuned against the routing eval's completed_in_turn diagnostic.
5. **`_eval_full` retirement — DECIDED (2026-06-25): RETIRE in A4b.** Once A4b
   removes every persona's allowlist (Q3), chat itself is full-universe via
   the None-path, so `_eval_full` (chat prompt + no allowlist) is byte-
   equivalent to chat and pure redundancy (and already unused — A3 gate runs
   used `--persona chat`). Remove `shared/personas/_eval_full.json`, redeploy
   personas. KEEP: the `public_personas()` underscore-hiding filter (clean
   general convention for internal personas, harmless when dormant) and the
   `--persona`/`--items` runner override (useful). Minor: scrub the
   `_eval_full` mention from `run.py` help text. A2/A3 plan references stay as
   historical record.

## 6. Acceptance for A4

- [ ] (A4a) delegation machinery deleted: tool, intercept, rewind, budget,
      delegated/persona_changed SSE + persistence, the nudge branch, the
      delegation plan-approval check, the DELEGATION_ENABLED flag.
- [ ] (A4b) allowlists retired: full-universe schema + tool_search; no
      allowed_tools reject; tool_allowlist field removed; `_eval_full` retired;
      per-tool-guard pattern documented in code.
- [ ] (A4b) soft bias: `resident_tools` per-profile surfacing set (additive,
      not a restriction); `_openai_tools_schema` unions it in; tool lists
      tuned vs completed_in_turn. Schema stays well under the 64K limit.
- [ ] Router-era pong test built in `backend/eval/` (+ registry wrapping the
      scripts/ QA tools); old pong assertions replaced.
- [ ] `test_delegate_persona.py` + delegate assertions retired;
      `DECISIONS.md` dated entry.
- [ ] Scorecard header "persona versions" -> "routing-profile versions".
- [ ] Routing-eval regression vs A0: no previously-passing item fails.
- [ ] 1-week soak clean (A4b).

## A4a BUILD STATUS (2026-06-25): delegation machinery deleted

Done (backend, working tree):
- `chat_service.py`: deleted the ~330-line delegation intercept block (detect/
  validate/reject/plan-approval-gate/rewind/re-entry), the message-snapshot +
  `DELEGATION_BUDGET` setup, `_narrow_delegate_enum` (+ its call + dead
  `src_persona_id`), the delegation branch of the allowlist-reject nudge, and
  the `DELEGATION_ENABLED` flag. Stale docstrings updated.
- `mcp/schemas.py`: removed `delegate_to_persona` from CORE_TOOLS (now 10) and
  its MCP_TOOLS schema entry (registry now 41); fixed the tool_search
  description.
- `mcp/_dispatch.py`: `_SCHEMA_ONLY` emptied (delegate was its sole member).
- `personas.py`: removed the `delegate_to_persona` auto-inject.
- `shared/personas/research.json`: removed `plan_approval:
  ["delegate_to_persona"]` — **caught by tests: it would have made
  load_personas SKIP the research persona entirely** (plan_approval
  cross-checks against MCP_TOOLS). A real averted breakage.
- config: removed `DELEGATION_ENABLED` from munin.env.template + compose.
- Comment/example cleanups in chat_store.py, plan_store.py, plan_approval.py.

Validated: all retrieval .py parse; tool_search/persona-split/router tests
green (25); all personas (incl. research) load. A4a is a runtime no-op
(delegation was already flag-disabled), so no behaviour change expected.

**A4a FOLLOW-UP (separate, frontend deploy target):** the frontend
(`webui/src`) still has dead `delegated`/`persona_changed` SSE handlers
(`chatStore.ts`, `types.ts`, `PersonaDivider.tsx`, App/MessageList comments).
After A4a the backend never emits these, so the handlers are dead but HARMLESS
(never fire; no error). Clean them up (remove, or wire PersonaDivider to the
`routing` event) in a frontend-side change. Not bundled into the backend A4a.

Also done (A4a follow-ups):
- **Router-era pong test (§3a):** updated the working harness
  `backend/retrieval/evals/run_eval.py` (the todo/ Phase-1 seed) — replaced
  `delegated_to_code` with (a') `routed_profile == "code"` (reads the
  `routing` SSE event) OR an html game, and added (b') hallucinated-tool
  detector (every tool_call ∈ MCP_TOOLS), (d') no `error` SSE, (e') non-empty
  content. The master-plan §8 `backend/eval/` formalization (registry +
  scenarios/, wrapping the scripts/ QA tools) is a Track E item, NOT A4a.
- **§3b:** `test_delegate_persona.py` retired to a stub (points to the routing
  eval); `DECISIONS.md` dated entry added (delegation retirement + the
  allowlist trade-off).
- **§3c scorecard header rename: N/A now** — there is no literal "persona
  versions" field. The routing scorecard already uses `persona_policy`
  (routing-aware). The rename targets the future `make_run_header()` (Track A
  eval suite, not built); note it there when built.

**Still pending for A4a completion:** routing-eval regression vs A0 (needs
deploy), deploy + smoke. Frontend dead-handler cleanup is a separate
frontend-deploy follow-up.

## 7. Risks / notes

- **Biggest blast radius of the migration.** Deleting the intercept touches
  the hottest generator in the service. Mitigate: delete in small commits,
  each leaving the service runnable; lean on the routing eval + pong test +
  flakiness suite after each.
- **`grep` for stragglers:** after deletion, grep the whole repo (frontend
  too) for `delegate_to_persona`, `persona_changed`, `delegated`,
  `tool_allowlist`, `allowed_tools` — the frontend may render
  `persona_changed`/`delegated`; that handling becomes dead and should go (or
  switch to the `routing` event).
- **The allowlist relaxation is the real risk, not the deletion.** A research
  turn that now runs code, or a chat turn reaching a sensitive tool, is the
  failure mode the soak exists to catch. Keep the per-tool-guard escape hatch
  ready; do not resurrect allowlists.
- **Do this AFTER A3 is soaked/confident** (the router must reliably route, or
  retiring the allowlist backstop exposes misroutes). A3 is live; a short A3
  confidence window before A4b is prudent.
