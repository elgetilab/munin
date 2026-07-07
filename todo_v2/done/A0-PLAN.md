# A0 implementation plan — pre-migration routing baseline

**Goal (from IMPLEMENTATION-HANDOFF §A0).** Wire the routing eval's runner
and judge, run the full seed set against the CURRENT (persona) harness, and
commit a tagged pre-migration scorecard. This scorecard is the regression
baseline every later Part-A step compares against (A4 gate: "no previously-
passing item may fail"). It is explicitly a regression baseline, **never a
paper result**.

**Placement (Q7/Q10).** `backend/benchmarks/munin_bench/routing/`.
`routing_eval.py` moves here from `todo_v2/` as part of this commit, no
duplicate left behind.

**Status:** plan for review. No code until varghele signs off (plan-first).

---

## 1. What the contract actually is (verified 2026-06-19)

Grounded in `chat_service.py`, `vllm_client.py`, the MCP schemas, and the
existing SSE parser in `scripts/test_delegate_persona.py`.

- **Endpoint:** `POST {BASE}/api/chat/completions`, header `X-Munin-Email`,
  body `{persona, conversation_id, messages, ephemeral}`. SSE frames are
  `event: <name>\ndata: <json>\n\n`. `BASE` default `http://127.0.0.1:8080`.
- **Events we consume:** `tool_call` `{id, name, arguments}` (chat_service
  lines 850, 2049); `tool_result`; `error` `{message}`; `done`;
  `conversation` `{id, title}`; plus the soon-to-be-retired `delegated`
  `{to_persona, reason}` and `persona_changed`.
- **`tool_call` carries NO step/iteration field.** The routing scorer needs
  `ToolCall.step` for the `solo` check, so the client must reconstruct it
  from the stream (see §3).
- **All 9 seed-item tool names exist** in `mcp/schemas.py` (`calculate`,
  `export_citations`, `get_citations`, `paper_search`, `read_paper`,
  `remember`, `run_python`, `web_fetch`, `web_search`), as do
  `ask_clarification`, `deep_research`, `semantic_scholar_search`,
  `tool_search`. No name-mismatch spurious failures. Good.
- **Judge client:** `vllm_client.vllm_post_json(body, *, timeout, foreground,
  purpose) -> dict`. `judge_abstention` wires to this with a one-line binary
  rubric. Use `foreground=False`, `purpose="routing_eval_judge"`.
- **`ephemeral=true`** gives the minimal stack: `want_profile/memory/
  artifact/plan = not ephemeral` (chat_service ~1349). Good for clean
  routing measurement, but see the memory-tool caveat in §6.

---

## 2. Components to build

```
backend/benchmarks/munin_bench/routing/
  __init__.py
  routing_eval.py        # moved from todo_v2/, with run_item/judge wired
  run.py                 # CLI: drive seed set, N reps, write scorecard
  trajectory.py          # SSE parse + step reconstruction (reused by both)
  README.md              # how varghele re-runs it
scorecards/
  <date>_routing-pre-migration.json   # committed (+ .md twin)
```

### 2a. `trajectory.py` — SSE capture + step reconstruction

Reuse the `test_delegate_persona.py` parse loop (event/data line pairs).
Produce `list[ToolCall]` plus side-channel events (`delegated`,
`persona_changed`, `error`, final assistant text for the judge).

**Step reconstruction (the one non-trivial bit).** `tool_call` events arrive
in per-iteration batches; `tool_result` events mark an iteration's execution
boundary. Algorithm: `step=0`; tag each `tool_call` with the current `step`;
set a `saw_result` flag on any `tool_result`; on the next `tool_call` after
`saw_result`, increment `step` and clear the flag. This groups same-
iteration calls into one step, which is exactly what `solo` needs
(ask_clarification must be alone in its step).

### 2b. `routing_eval.py::run_item` — wire it

```
async def run_item(client, base_url, email, item) -> ItemResult:
    body = {messages from item (prior_turns + query), ephemeral: True, persona: <policy>}
    stream POST; capture trajectory + side events via trajectory.py
    if item.expected.abstain and judge wired: judge final text
    return score_item(item, trajectory)
```

- Build `messages` from `item.context.prior_turns + [{"role":"user",
  "content": item.query}]`.
- `persona` per the §5 policy decision (NOT `expected.profile`, per hard
  rule #3).
- Honour `context.inject_tool_result` per the §6 decision.

### 2c. `judge_abstention` — wire it

One vLLM call, binary rubric, only for `expected.abstain` items
(`web_search_degraded` is the only A0 one). Rubric kept one-line/binary to
keep judge variance low: "Does this answer honestly signal it could not
find/verify the information, rather than fabricating an answer or asserting
the topic does not exist? Answer YES or NO." Parse leading yes/no.

### 2d. `run.py` — CLI + scorecard

`python -m munin_bench.routing.run --reps 8 --tag routing-pre-migration
--seed 42`. Drives all seed items × N reps, aggregates, writes the
scorecard. Reuses `make_run_header()` once Phase-1 infra exists; until then
an inline header (model+revision, harness git SHA, persona policy, seed,
timestamp, package versions).

---

## 3. Scorecard format (rep-aware, per Q7)

Q7 mandates **mean ± CI over N reps + a flip-rate / stability metric**, not a
single pass/fail. Proposed JSON:

```json
{
  "header": {"tag", "timestamp", "git_sha", "model", "persona_policy",
             "reps", "seed", "package_versions"},
  "aggregate": {"mean_pass_rate", "ci_low", "ci_high"},
  "per_item": {
    "<item_id>": {"category", "pass_rate": "k/N", "flip_rate",
                  "checks_pass_rate": {...}, "sample_failures": [...]}
  }
}
```

- `flip_rate` per item = fraction of adjacent rep-pairs whose pass/fail
  differs (the routing-stability signal; high flip = unreliable routing on
  that phrasing, a finding not noise).
- `sample_failures` keeps a couple of failing trajectories per item so the
  migration work can see WHY (mirrors the todo/ eval-pipeline decision that
  scorecards carry failure detail, not just rates).
- A human-readable `.md` twin for quick reading.

---

## 4. Build order

1. `trajectory.py` + unit test for step reconstruction (synthetic SSE: two
   iterations, assert step grouping; the `solo` case = one call in step 0).
2. Move `routing_eval.py` into the package; wire `run_item` against
   `trajectory.py`.
3. `judge_abstention` wired to `vllm_post_json`.
4. `run.py` + scorecard writer.
5. Smoke: run ONE item (e.g. `weather_with_location`) end-to-end against the
   live service; eyeball the captured trajectory and score.
6. Full run, N reps, commit the scorecard.

---

## DECISIONS (2026-06-22, varghele approved all three recommendations)

- **A — persona policy:** all items run under the `chat` persona (option 1).
- **B — `inject_tool_result`:** defer `web_search_degraded` to A2; run 14/15
  at A0, marking it `SKIPPED (injection not wired)` (option 1).
- **C — reps + ephemeral caveat:** N=8 reps. **RESOLVED at smoke
  (2026-06-22):** `ephemeral` does NOT suppress the memory tools.
  `want_memory = not ephemeral` (chat_service.py:1350) gates only whether
  stored memories are FETCHED into the system prompt; the toolset is built
  separately (`CORE_TOOLS | unlocked`, line 236) and `remember`/`recall` are
  deferred tools reachable via `tool_search` regardless of ephemeral. So
  `remember_research_area` stays at `ephemeral=True` like every other item;
  its failure is real deferred-tool routing behaviour (A2 territory), not a
  harness artifact.

### 1-rep validation triage (2026-06-22, 9/14 passed)

Full pipeline ran end-to-end across all 14 runnable items. The 5 failures
are all REAL pre-migration behaviour, none are harness artifacts (the seed
items were written for the post-migration router, so failures against the
current persona harness are expected and are the point of a baseline):

- `remember_research_area`, `export_bibtex` — deferred tool not reached via
  `tool_search` (exactly what the A2 gate hardens).
- `sota_phip` — `deep_research` is deferred, so it cannot be the literal
  first call; documents the limitation A3's router addresses.
- `known_doi_read` — `read_paper` (core) not called; real routing choice.
- `weather_no_location` — model searched instead of solo `ask_clarification`;
  real routing choice (A3 territory).

Conclusion: clear to run the full N=8 baseline; no item needs reworking for
A0.

Full rationale for each is in §5/§6/§7 below.

---

## 5. OPEN QUESTION A — persona policy at A0 (RESOLVED: option 1, all `chat`)

The handoff forbids sending `expected.profile` as the request persona (hard
rule #3: it grades the router on an answer you handed it). But pre-migration
there is no router; the request persona IS how a persona is selected, and
the endpoint needs one (or it falls back to `default_persona`). So A0 needs a
fixed policy. Options:

1. **All items under `chat` (recommended).** The routing eval's items are
   grounded in chat-persona routing rules (its docstring), and `chat` is the
   entry persona that is supposed to route/delegate. This measures "does the
   current chat-persona harness route each query correctly," which is exactly
   the behaviour A1-A4 will change. Items expecting research/code behaviour
   exercise the current delegation path (and may legitimately fail pre-
   migration; that is a true baseline number).
2. All items under their `expected.profile` as persona. **Rejected** —
   violates hard rule #3 and inflates the baseline.
3. Send no persona, let `default_persona` apply. Rejected — non-reproducible
   (depends on per-user default) and muddies the baseline.

**Recommendation: option 1 (all `chat`).** Record the policy in the scorecard
header so the A4 regression compares like-for-like.

---

## 6. OPEN QUESTION B — `inject_tool_result` at A0 (RESOLVED: option 1, defer to A2)

Exactly ONE seed item (`web_search_degraded`) needs a canned tool result
injected (first `web_search` returns the degraded-backend envelope). The
backend executes real tools; injecting means a test-only hook in the MCP
dispatch path. Options:

1. **Defer the single item to A2 (recommended).** Run 14/15 items at A0; mark
   `web_search_degraded` as `SKIPPED (injection not wired)` in the scorecard.
   Build the injection hook at A2, where the robustness/tool_search items are
   the actual focus. Keeps A0 tight and avoids a backend test-hook landing
   before the migration work that will touch that code anyway.
2. Build a minimal injection hook now (test-only header/env gating a canned
   result in the MCP dispatcher). More complete A0 baseline, but adds a
   backend change to the A0 surface for 1/15 items.

**Recommendation: option 1 (defer).** The pre-migration baseline is for
regression comparison; one deferred robustness item does not weaken it, and
A4's "no previously-passing item may fail" simply will not include an item
that was SKIPPED at A0.

---

## 7. OPEN QUESTION C — rep count + ephemeral/memory caveat (RESOLVED: N=8, check at smoke)

- **Rep count `N`.** Q7 wants rep-based mean±CI + flip-rate. Propose **N=8**
  (matches the existing pong baseline convention in
  `todo/eval-scenarios.md`). Cost: 15 items × 8 reps × ~5-30 s/turn shared
  with prod vLLM. Acceptable off-peak. Confirm N.
- **ephemeral vs the memory item.** `ephemeral=true` sets
  `want_memory=False`. Need to confirm during the §4.5 smoke whether this
  also suppresses the `remember`/`recall` TOOLS (vs only the memory context
  block). If the tools are suppressed under ephemeral, the
  `remember_research_area` item cannot pass structurally; then that item runs
  with `ephemeral=false` or is deferred. Flagged as a validation step, not a
  blocker. (Most items are unaffected; ephemeral is correct for clean
  routing measurement.)

---

## 8. Acceptance for A0

- [ ] `trajectory.py` step-reconstruction unit test green.
- [ ] `run_item`/`judge_abstention` wired; one-item smoke produces a correct
      scored trajectory against the live service.
- [ ] Full seed set run at N reps under the agreed persona policy.
- [ ] `scorecards/<date>_routing-pre-migration.json` (+ `.md`) committed,
      header records model+revision, git SHA, persona policy, reps, seed.
- [ ] `routing_eval.py` relocated into the package; no `todo_v2/` duplicate.
- [ ] Decisions A/B/C recorded in KICKOFF-QUESTIONS or a short A0 note.

## 9. Risks / notes

- Driving the live endpoint shares the prod vLLM GPU; run off-peak, N reps is
  the main cost. Concurrency stays low (sequential or small) to avoid
  load-coupling the routing measurement (echoes the Q5 concurrency point,
  though here it is about not perturbing prod, not about timing).
- The `via_tool_search_ok` scorer branch is permissive (handoff A2 note);
  acceptable for the A0 baseline, tighten later if it masks failures.
- No answer-content scoring anywhere (hard rule: score the trajectory). The
  judge touches only abstention wording for the one abstain item.
