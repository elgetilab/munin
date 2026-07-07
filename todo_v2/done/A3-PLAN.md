# A3 implementation plan — up-front per-turn router

**Goal (IMPLEMENTATION-HANDOFF §A3).** Decide tool subset + prompt fragment +
sampling preset BEFORE the first model call, per turn, from the query. This
is the centerpiece of the persona->router migration: it replaces "the
conversation has one fixed persona (+ delegation)" with "each turn's profile
is chosen up-front from the query, biased by the pin."

**Gate:** `reroute_research_to_compute` green (turn 1 research, turn 2 plot ->
THIS turn routes to run_python, no handoff, no re-search), plus `clarify`
(`weather_no_location`: solo `ask_clarification`) and `no_tool`
(`define_nmr`) green. Plus: activate `expected.profile` assertions against
the backend's routing event.

**Status:** design fully specified — all 5 open questions decided (2026-06-23):
Q1 slash set (`/research`/`/code`/`/chat`), Q2 advisory pin + layered prompt
(d), Q3 prompt factoring (base[pin] + fragment[routed], behavior-preserving),
Q4 KNN policy (distance-weighted + pin prior + OOD guard, fallback pin->chat),
Q5 A3/A4 boundary (routed-profile allowlist; A4 retires to soft bias). Ready
to build. No code until varghele gives the explicit go (plan-first).

**Order:** A3 comes AFTER A2 (done) and BEFORE A4. A3 builds the router; A4
then retires the allowlists the router's tool decision will replace. A1's
delegation-off flag stays; the router is NOT a delegating state machine.

---

## 1. How routing works today (verified 2026-06-23)

- **Persona resolution** (`main.py:1213-1242`): body `persona` wins; else
  existing conversation's stored persona; else project>profile>global
  default. Produces one `persona_id` for the whole turn.
- That `persona_id` flows to `run_chat_completion` (`chat_service.py:1543`),
  which does `persona = get_persona(persona_id)` once (1575) and uses it for
  EVERYTHING: system prompt (`build_system_prompt`), sampling
  (`sampling_params`, the 6 keys temperature/top_p/top_k/min_p/
  presence_penalty/max_tokens), and tool schema (`_openai_tools_schema`,
  CORE ∩ allowlist + tool_search unlocks).
- A persona IS a profile: `{system prompt, sampling, tool_allowlist}`. The
  three are chat / code / research.
- **No slash-command handling exists** (grep confirms): the router's tier-1
  is net-new.
- **Embedders available:** `get_bge()` (BGE-base, general text, ideal for
  query KNN) and `get_specter()` (papers). Use BGE for query routing.

So the insertion point is clean: a router that runs after persona_id is
resolved (the PIN) and decides the per-turn PROFILE, then the existing
prompt/sampling/tool machinery consumes the routed profile instead of the
raw pin.

---

## 2. Architecture

```
route(query, pin_persona_id, context) -> RoutingDecision {
    profile: "chat" | "research" | "code",   # the turn's classified profile (eval asserts this)
    method: "rule" | "knn" | "classifier" | "pin",  # how it was decided (telemetry)
    confidence: float,
    # applied as (option d, Q2): system_prompt = base(pin) + fragment(profile),
    # plus sampling preset + tool bias from the profile's fragment.
}
```

**Cheapest-first cascade (handoff §A3):**

1. **Rules (tier 1):** explicit signals decided for free.
   - Slash commands (Q1, decided): `/research`, `/code`, `/chat` -> force that
     profile for the turn (absolute; overrides pin + KNN). Syntax
     `/<profile> <query>`, leading token stripped. `/write` dropped. Net-new.
   - Strong lexical/structural signals (a bare URL -> simple; a DOI ->
     paper). Keep tiny and high-precision; everything ambiguous falls
     through.
2. **KNN (tier 2):** embed the query with BGE, distance-weighted k-NN against
   a labelled set of example queries (label = profile), pin prior folded in.
   No extra LLM call, no per-turn latency beyond one embed. The labelled set =
   the routing-eval seed items + A5 paraphrases (they already carry `category`
   and `expected.profile`). Accept iff the top profile clears a margin over
   the runner-up AND the nearest neighbour is within a distance cutoff (OOD
   guard). Below threshold -> fallback to pin else chat (NOT tier-3 routinely;
   see §6 Q4). Threshold values tuned against the eval, not fixed.
3. **One-shot classifier (tier 3):** a single cheap vLLM classification pass.
   **Build ONLY if rules+KNN underperform on the routing eval** (adds
   per-turn latency; needs varghele's explicit sign-off, handoff rule).

**Pin strength — DECIDED (Q2, 2026-06-23): advisory pin + LAYERED prompt
(option d).** The pin is advisory, not a lock, but the override does NOT swap
the whole persona. Instead:

- The **pinned persona supplies the BASE prompt + voice** (the user's choice
  is respected — a Research user keeps the research voice).
- The **router layers a per-turn TASK FRAGMENT + sampling preset + tool bias**
  on top, selected from the turn's classified profile.
- System prompt = `base(pinned persona) + fragment(routed profile)`.

This is the handoff's exact "prompt **fragment**" wording. It passes the
reroute gate via prompt GUIDANCE, not tool availability: the A0 failure was
the model re-searching for a "plot those values" turn (run_python is already
CORE, always available) because the research prompt guides search. The code
fragment supplies the missing "this turn is a compute/plot task, use
run_python, don't re-search" guidance while the research base voice stays.

**Why (a)/(c) were ruled out:** (a) hard-lock keeps the research prompt ->
fails reroute. (c) tool-only routing changes nothing (run_python is already
CORE) -> the research prompt still guides search -> fails reroute. Only a
per-turn PROMPT change fixes it, which (d) provides without discarding the
pinned voice that (b) would.

**Pin-strength ladder:**
- Slash command (`/code`) -> absolute (user typed it).
- Explicit persona pin -> strong bias in the routing vote; overridable only
  by a clear cross-profile query signal (a plot request beats a research
  pin).
- Default / unpinned -> no bias, pure query routing.

**Sampling presets MUST survive (handoff).** The router sets the sampling
preset from the routed profile's fragment; the existing `sampling_params()`
is reused. Do NOT let temperature default to the floor — the routed preset is
authoritative.

**Q3 implication (profile granularity):** (d) requires a TASK-FRAGMENT library
separate from the full persona prompts. The router still CLASSIFIES each turn
into a profile ∈ {chat, research, code} (what `expected.profile` asserts);
that classification selects the fragment to layer. Full factoring in §2a.

### 2a. Prompt factoring for (d) — DECIDED (Q3, 2026-06-23)

Verified persona prompt structure (consistent across all three, `=== X ===`
sections):

| Section | chat | research | code | shared? |
|---|---|---|---|---|
| CORE RULES | ✓ | ✓ | ✓ | differs per persona |
| OUTPUT STYLE (voice) | ✓ | ✓ | ✓ | differs (research cold / chat warm / code terse) |
| **task-guidance middle** | TOOL USAGE STRATEGY | RESEARCH METHODOLOGY + SYNTHESIS | Python/DL/HPC domain | **differs by TASK = the fragment** |
| TASK PLANNING | ✓ | ✓ | ✓ | **byte-identical** |
| DISCOVERING TOOLS | ✓ | ✓ | ✓ | differs slightly (carries the A2 follow-through cue) |

**The split** (cut at the task-section boundary: between OUTPUT STYLE end and
TASK PLANNING start):

- `base[persona]` = everything EXCEPT the task-guidance middle (CORE RULES +
  OUTPUT STYLE + TASK PLANNING + DISCOVERING TOOLS). Carries the pin's voice +
  rules.
- `fragment[profile]` = the extracted task-guidance middle section.
- **system prompt = `base[pin] + fragment[routed]`.**

**Decisions:**
- **(i) Fragments are EXTRACTED from the existing task sections** (not authored
  fresh) — this is what preserves current behavior.
- **(ii) Per-pin base** (the rest of the pinned persona), NOT a slim shared
  base. Keeps each persona's voice/rules.
- **Behavior-preserving guarantee:** when `pin == routed` (the common,
  non-override case), `base[pin] + fragment[pin]` reconstructs the ORIGINAL
  persona prompt exactly — zero regression on ordinary turns; behavior only
  changes when the router overrides (e.g. reroute: `base[research] +
  fragment[code]`, research voice + code task guidance, with research's own
  methodology absent so there is no search-vs-plot conflict).
- **Fragment CONTENT tuning** (e.g. making the code fragment explicitly say
  "when asked to plot, use run_python, don't re-search") happens during the
  gate-measurement loop (like A2's prompt edit) — refinement, not
  architecture.
- **Note:** a "When you write LaTeX" sub-block sits inside each persona's task
  section, so it rides along in each fragment (fine; shared-LaTeX is a small
  optional follow-up).

---

## 3. Integration + the routing event

- **Where:** in `run_chat_completion` (or just before it), after `persona_id`
  is resolved (the PIN). Compute `decision = route(latest_user_query,
  persona_id, ctx)`; let `effective_profile = decision.profile`. Then per (d)
  + Q5:
  - system prompt = `base[pin] + fragment[effective_profile]`
  - sampling = `sampling_params(effective_profile)`
  - tools (pre-A4) = `_openai_tools_schema(effective_profile)` — the ROUTED
    profile's allowlist applies, NOT the pin's (Q5). Post-A4 it becomes the
    routed profile's soft tool bias + CORE/tool_search.

  The pin sets the base VOICE and biases the route; it does NOT set sampling
  or tool availability. The pin `persona_id` is retained for telemetry + as
  the router prior.
- **Routing event (net-new SSE):** emit a `routing` event
  `{profile, method, confidence, pin}` at turn start, BEFORE the first model
  call. The routing eval asserts `expected.profile` against THIS event (NOT
  the request persona — hard rule #3). Catalogue it in BACKEND-API.md §5.
  - This REPLACES the retired `persona_changed`/`delegated` events (A4) as
    the thing the frontend/eval reads for "what profile is active".
- **Per-turn, not sticky:** the decision is recomputed each turn from that
  turn's query. No persistence of a switched persona (that was the
  delegation model, now gone). The conversation's stored persona remains the
  PIN; the per-turn profile is ephemeral.

---

## 4. Wiring the routing eval (activate expected.profile)

- A0/A2 ran every item under a fixed request persona (chat / _eval_full) and
  did NOT assert `profile` (no router existed). A3 turns it on:
  `score_item` already supports a `profile` check (it's in `reward_basis`);
  the runner must capture the `routing` SSE event and pass the emitted
  profile into scoring.
- Add `profile` capture to `trajectory.py` (read the `routing` event) and a
  `profile` check in `score_item` comparing emitted vs `expected.profile`.
- Hard rule #3 stays intact: send the PIN (or no pin) as the request persona,
  assert `expected.profile` against the emitted routing event — never send
  `expected.profile` as the request persona.

---

## 5. Build order

1. **Prompt factoring (d): DONE (2026-06-23).** Implemented in `personas.py`:
   `split_system_prompt(persona) -> (prefix, fragment, suffix)` (lossless,
   computed at load time from the OUTPUT-STYLE/TASK-PLANNING markers, NO
   separate fragment files -> the persona JSON stays the single source of
   truth, zero drift) and `compose_system_prompt(pin, routed)` =
   `prefix(pin) + fragment(routed) + suffix(pin)` (graceful fallback to the
   routed persona whole if the pin lacks markers). Load-time warning if a
   persona lacks the markers. Tests
   (`tests/test_persona_prompt_split.py`, 4/4): lossless split, the IDENTITY
   property `compose(p,p) == original[p]`, cross-composition (research voice +
   code fragment, research's own fragment absent), and the fallback. Behavior
   is provably unchanged on ordinary turns until the router calls compose with
   pin != routed (integration, step 5).
2. **`router.py`: DONE (2026-06-23).** `parse_slash` (tier-1) +
   distance-weighted BGE-KNN `route()` with pin prior + margin + OOD guard +
   fallback (pin->chat). Embedder INJECTED (`embed_fn`) so it's unit-testable
   without BGE. Tunable constants (KNN_K, MARGIN_THRESHOLD, OOD_SIM_THRESHOLD,
   PIN_PRIOR) in one place for the Q4 tuning step. Tests
   (`tests/test_router.py`, 9/9, fake deterministic embedder): slash
   force/strip/bare/unknown/override, KNN clear-code/clear-research, pin-prior
   (clear signal overrides), OOD fallback (pin + chat), real-file load.
3. **Labelled set: DONE (2026-06-23).** `router_examples.json` (24 examples:
   7 chat / 10 research / 7 code) generated by `router_examples_build.py` from
   persona `prompt_suggestions` + seed items with `expected.profile`. Committed
   QUERIES only (re-embedded at load -> robust to BGE changes), provenance in
   `meta`. **Label-noise curation:** dropped 2 chat paper-finding/web-search
   suggestions that COLLIDED with near-identical research ones (paper-finding
   leans research, consistent with the seed items) — documented in the
   generator. A5 expands this set.
   - **Deferred: real-BGE routing smoke.** sentence-transformers is
     container-only and the router isn't deployed yet, so real-embedding
     routing quality (does reroute route to code? etc.) is validated at the
     post-integration GATE MEASUREMENT (step 7), not now. The fake-embedder
     unit tests cover the logic.
4. **`routing` SSE event: DONE (2026-06-24).** Emitted once at stream start
   (before the first model call): `{profile, pin, method, confidence}`.
   Documented in BACKEND-API.md §5; noted as the replacement for the retired
   `persona_changed`/`delegated` events.
5. **Integration: DONE (2026-06-24), behind `ROUTER_ENABLED` (default false).**
   In `run_chat_completion`, after the pin is resolved: `route()` the turn ->
   `routed` profile drives `current_persona` (tool scoping, Q5), sampling, and
   `_openai_tools_schema`; the system prompt is `compose_system_prompt(pin,
   routed)` via the new `routed_persona` arg on `_build_full_system_prompt`;
   slash commands strip the leading token from the user message. Router
   failures fall back to the pin (logged). `ROUTER_ENABLED` env flag +
   docker-compose passthrough + munin.env (mirrors the A1 DELEGATION_ENABLED
   pattern). When false, `routed == pin` -> `compose(pin,pin)` == original ->
   zero behaviour change; flip true + restart to activate. Lazy
   `_get_router_index()` embeds the labelled set once via `get_bge`. Syntax +
   router/split unit tests green; real-embedding behaviour validated at the
   gate measurement (step 7).
6. **Wire `profile` assertion into the routing eval: DONE (2026-06-24).**
   `trajectory.py` captures the `routing` SSE event (`routed_profile`,
   `routing_method`, `pin`); `score_item(item, trajectory, emitted_profile=)`
   adds a first-class `profile` check comparing emitted vs `expected.profile`
   (always messages a mismatch; skipped when `emitted_profile is None` so
   A0/A2 baselines stay comparable, hard rule #3 intact). `run_item` threads
   `traj.routed_profile`. Tests (`tests/test_profile_assertion.py`, 5/5):
   capture, match-passes, mismatch-fails-and-messages, None-skips, no-expected
   skips. Full benchmark suite 20/20.
7. **Gate measured live (2026-06-25), DONE.** Router deployed
   (ROUTER_ENABLED=true) and measured across v1->v3. Result + decision below.
8. **Deployed + live-measured, DONE.** ROUTER_ENABLED=true in prod; routing
   validated end-to-end with real BGE.

### Gate result (v3, 2026-06-25) — router works; reroute deferred to A5

Trajectory of the labelled-set + scoring work:
- **Deploy bug found + fixed:** `COPY *.py` skipped router_examples.json ->
  router silently fell back to pin. Dockerfile now COPYs the JSON; the
  failure path reports method "error" (not "pin").
- **Test/train LEAKAGE found + removed:** seed items had leaked the verbatim
  test queries into the labelled set. Now disjoint, with a build-time guard.
- **Labelled set expanded** 24 -> 229 (~80/class), adding the implicit-code
  plotting sub-pattern.
- **profile became a REPORTED METRIC, not a gate** (the handoff gates item
  pass/fail on tool OUTCOMES; ambiguous queries meet the tool gate but sit on
  a profile boundary).

**v3 numbers (chat pin, ROUTER on, N=8):** mean pass 0.696 (v1 0.643 -> v2
0.679 -> v3 0.696). **Profile routing accuracy: 6/8 profiled items correct**
(citing_papers, group_corpus_qa, known_doi_read, sota_phip, weather x2 = 1.00;
define_nmr, reroute = 0.00). Research queries reliably route to research and
reach their tools (the Q5 thesis, validated). `define_nmr` gate green.

**reroute (the one routing-caused failure):** "plot those polarization values
vs field strength" is genuinely code+research ambiguous (verb=code,
nouns=research domain); margin 0.034 (near-tie) -> falls back to chat ->
the chat fragment lacks "just plot, don't re-search" guidance -> the model
re-searches (paper_search, forbidden). Routing it to CODE would fix it. A
near-tie won't yield to margin-lowering alone; it needs the code signal to
win.

**Decision (varghele, 2026-06-25): ACCEPT, revisit at A5.** The router works
well (75% routing accuracy, research routing solid, define_nmr green).
reroute is a known hard case to revisit when A5's larger paraphrase
expansion densifies the code cluster near domain-plotting queries.

**Deferred (A5 / separate, NOT router bugs):**
- reroute -> code (hard ambiguous routing; densify code cluster at A5).
- weather_no_location: the model doesn't reliably call ask_clarification
  (model behavior; routes to chat correctly).
- known_url_fetch: web_search vs web_fetch on a URL (tool routing).
- remember / export_bibtex: deferred-tool follow-through (carried from A2).

---

## 6. Open questions (needs varghele)

1. **Slash-command vocabulary (tier 1). DECIDED (2026-06-23).** Command set =
   **`/research`, `/code`, `/chat`** (1:1 with the three routing profiles).
   Each forces that profile for the turn (absolute tier-1, overrides the pin
   and the KNN vote). **`/write` DROPPED** (writing is a chat task, not a
   profile; users use `/chat` or natural language, which KNN routes).
   - Syntax: `/<profile> <query>` — leading token sets the profile and is
     STRIPPED before the query is routed/processed. Bare `/<profile>` with no
     query is a no-op.
   - Scope: routing only. App-level commands (`/help`, `/clear`) are out of
     scope (separate frontend concern).
   - Backend-parsed for v1; frontend autocomplete is a later nice-to-have.
     Slash commands are a deterministic power-user override, NOT the primary
     path (KNN handles natural language), so discoverability is secondary.
   - No slash handling exists anywhere today (verified) — fully net-new.
2. **Pin strength. DECIDED (2026-06-23): advisory pin + layered prompt
   (option d).** Pin supplies the base voice; the router layers a per-turn
   task fragment + sampling + tool bias. See §2 for the full resolution and
   the pin-strength ladder (slash=absolute, explicit pin=strong bias,
   default=none).
3. **Profile granularity / fragment library. DECIDED (2026-06-23): split each
   persona at the task-section boundary; compose `base[pin] + fragment[routed]`.**
   See §2a below for the full factoring. Key property: when `pin == routed`,
   the composition reconstructs the original persona prompt exactly, so the
   refactor is behavior-preserving on ordinary turns and only differs on a
   router override.
4. **KNN acceptance threshold + fallback. DECIDED (2026-06-23).**
   - **Vote:** distance-weighted KNN over the labelled set (closer neighbours
     count more), with the pin prior folded in (Q2: explicit pin = strong
     prior toward its profile, overridable by a clear cross-profile signal).
   - **Accept** the KNN profile iff it clears a confidence MARGIN over the
     runner-up AND the nearest neighbour is within a DISTANCE cutoff
     (in-distribution / OOD guard — the labelled set is small, so a query
     unlike anything labelled must NOT force a spurious match).
   - **Below threshold (ambiguous OR out-of-distribution) -> fallback to the
     PIN if explicitly pinned, else CHAT** (the safe generalist; chat is
     always a good default — it can search/answer/etc.).
   - **Tier-3 LLM classifier is NOT the routine fallback** (adds a vLLM call
     per uncertain turn). Added later ONLY if the pin/chat fallback measurably
     hurts routing-eval accuracy, with varghele's sign-off (handoff rule).
   - **Threshold values NOT fixed now:** the margin + distance cutoff are
     tuned during the build against the routing eval (maximise
     `expected.profile` match without over-routing). Policy decided here;
     values are an empirical build step (like the A2 gate loop).
5. **A3 vs A4 coupling. DECIDED (2026-06-23).**
   - **A3 lands with allowlists INTACT.** The ROUTED profile's allowlist
     applies as a hard scope (pre-A4) — NOT the pin's. This is the key design
     point: under (d), `fragment[routed]` and the tool set must agree, so the
     routed profile drives `fragment + sampling + allowlist`; the pin sets
     voice + biases the route only (it does NOT set tool availability).
     Otherwise a research-fragment ("use get_citations") under a chat pin
     (allowlist lacks citations) would tell the model to use a blocked tool.
   - **A4 retires allowlists** -> the routed profile contributes a SOFT tool
     bias (which tools to emphasise/surface); `CORE + tool_search` over the
     full universe carries availability. The hard boundary relaxes (handoff:
     "per-turn routing relaxes the hard boundary; if a tool needs a hard wall,
     an explicit per-tool guard, not allowlists").
   - **Consequence — A3 obsoletes the `_eval_full` workaround.** A2 needed the
     full-universe fixture because everything ran under chat (no citation
     tools). In A3 the router sends citation queries to RESEARCH (whose
     allowlist HAS them, and A2's matcher surfaces them), so the citation
     items are reachable NATURALLY. `_eval_full` can retire after A3 (or stay
     as a full-universe stress test).
   - **A3 gate is testable with allowlists intact:** reroute (->code,
     run_python is CORE), clarify (->chat, ask_clarification CORE), no_tool
     (->chat, none) — none need a deferred tool an allowlist would block.

## 7. Acceptance for A3

- [ ] Prompt factoring (d): personas split into base + fragment; identity
      property tested (`base[p]+fragment[p]` == original); composition helper.
- [ ] `router.py`: tier-1 rules + tier-2 BGE-KNN; unit-tested.
- [ ] Labelled KNN example set committed (provenance-tagged).
- [ ] `routing` SSE event emitted before the first model call; documented.
- [ ] Integrated; routed profile drives prompt + sampling + tools; sampling
      presets preserved (no temperature-floor regression).
- [ ] Routing eval `profile` assertion wired (trajectory + score_item).
- [ ] Gate green: `reroute_research_to_compute`, `weather_no_location`
      (solo clarify), `define_nmr` (no_tool). Full routing eval no worse than
      A0 on previously-green items.
- [ ] Tier-3 classifier: built only if 1+2 underperform, with sign-off.
- [ ] Deployed; live-measured.

## 8. Risks / notes

- **Latency:** tier-1+2 add only one BGE embed per turn (cheap). Tier-3 adds
  a vLLM call — the reason it's gated on sign-off.
- **The reroute gate is the hard one:** it requires per-turn re-routing
  within a pinned conversation, the exact behaviour the old delegation
  machinery did clumsily. The router must read THIS turn's query, not the
  conversation's pin alone. A0 baseline: reroute was 0-1/8 (never routed to
  run_python) — A3's job is to fix that.
- **`remember` recognition gap (from A2):** the router's profile choice
  won't directly fix "model doesn't recognise a memory-worthy statement";
  that is tool-level recognition, possibly A5. Note, don't conflate.
- **Don't resurrect delegation:** the router picks a profile up-front; it
  does NOT hand off mid-turn, rewind, or persist a switch. If a hard tool
  wall is ever needed, an explicit per-tool guard (handoff A4 note), not
  allowlists or delegation.
