# Munin agentic design — review of the three configured agents

**Date:** 2026-05-12
**Scope:** `backend/config/agents.yml` + `mcp/tools/agents.py` + `mcp/tools/research.py` (deterministic `deep_research`) + `scripts/deepresearch/` (SLURM daemon).
**Companion to:** `munin-audit.md`.

---

## The actual question is "agent vs deterministic workflow vs just persona"

There are three structurally different things in the codebase that all get called "agentic":

1. **`invoke_agent` with the three configured agents** (`backend/config/agents.yml`): `research_orchestrator`, `code_checker`, `writing_agent`. Model-driven sub-loop, bounded budget, allowlist.
2. **The `deep_research` MCP tool** (`mcp/tools/research.py:288`). Deterministic pipeline that chains existing primitives. The file comment explicitly calls it "deterministic and cheaper."
3. **The `deep_research` SLURM daemon** (`scripts/deepresearch/`). Long-running, async, multi-step.

Conflating these is part of why the agentic story feels muddy. Each has a different purpose, lifetime, observability story, and failure mode. The audit treats #2 and #3 as "what they obviously are"; this doc is about #1.

---

## Do the three configured agents earn their keep?

- **`research_orchestrator`** — yes. Its real value is not "different system prompt"; it's **context isolation**. A deep lit review burns 20+ paper abstracts, citation graphs, web fetches — all intermediate state the parent persona's context window doesn't need to carry. The agent returns one structured answer. Keep it.
- **`code_checker`** — no, as currently designed. Tools are `web_search` + `web_fetch` + `llm_summarize` + `search_user_docs`. No `run_python`, no diff inspection. It is literally "the `code` persona but prompted to be more careful." That's a system-prompt change, not an agent — and the model cannot even verify code by running it.
- **`writing_agent`** — borderline. Its toolset is a strict subset of the `research` persona's. The only argument for separate-agent status is the budget cap (6 iterations / 20 calls / 240s). That is not enough to justify an extra vLLM loop with its own forced-retry handling.

Of the three configured agents, **one** is doing the job an agent is structurally good at.

---

## The framework I'd use

|  | **Bounded scope** | **Open scope** |
|---|---|---|
| **Known workflow** | **Deterministic pipeline** | Persona (with strong system prompt) |
| **Unknown workflow** | **Agent** (model-driven sub-loop) | Plain prompting |

- **Deterministic pipeline** = known steps, known order, fixed tool sequence. `deep_research` is this. Cheaper, more observable, no forced-retry surface area.
- **Agent** (model-driven sub-loop) = shape of the work depends on intermediate findings. Which paper to follow next genuinely depends on what the previous one cited.
- **Persona** = the user's main mode. Open-ended; orchestrates everything else.
- **Plain prompting** = bake the behaviour into the system prompt; no separate loop.

Most of `agents.yml` lives in the **Known-workflow / Bounded** cell, which is the **wrong** cell — those should be deterministic pipelines, not agents. Only `research_orchestrator` lives in the Open-shape cell where agents pay off.

The decision rule: **if you can write down the steps in advance, it's a pipeline. If the next step depends on what the previous step returned, it's an agent.**

---

## Toggleable vs model-driven

Both. Not as either/or — as a layer cake:

1. **User-triggerable via slash commands** (`/research <question>`, `/write …`). Users *know* when they want a deep lit review and shouldn't have to hope the model decides to call `invoke_agent`. Given the prose-action-promise failure mode you already have heuristics for, "model reliably calls `invoke_agent`" is exactly the qwen3-coder regime where you'd expect drops.
2. **Model-driven** stays as the fallback path for cases the user didn't explicitly ask for. Use the existing `invoke_agent` tool.
3. **Plan-mode interception for expensive agents.** When the model picks `research_orchestrator`, render an inline approval card (cost estimate: ~30 tool calls, ~5 min). Same UX shape as `ask_clarification`. Cheap to add; lets the user cancel before a 5-minute burn.

The user does not have to pick between "user-triggered" and "model-triggered." Both exist; one is explicit, the other is the backup.

---

## What to make agentic per persona

### `chat` persona — none

Lightweight default. Agents add latency and complexity the user doesn't want here. If the model decides the question is research-grade, `delegate_to_persona` to `research` is already the correct escape hatch (and is well-shaped — bounded budget, single-shot, rewinds on rejection).

### `research` persona — this is where agents pay off

Candidates:

- **`research_orchestrator`** (have) — deep lit review. Keep.
- **`citation_snowball`** — seed paper(s) → expand citations + references → rank by relevance to a question. Bounded scope, but open shape (which neighbours look promising depends on intermediate results).
- **`contradiction_finder`** — given a claim, find papers that disagree. Open shape, context-heavy. Returns a structured "supports / contradicts / mixed" breakdown.
- **`author_trajectory`** — given an author or group, summarise their research arc over N years. Bounded scope but variable depth depending on prolificity.

What *not* to make an agent here: anything with a fixed pipeline ("get top-N papers by SPECTER → check Neo4j citations → return"). That's `deep_research`-style deterministic, not agentic.

### `code` persona — agents are mostly the wrong shape

Better fits:

- **`self_test_loop`** — write code → run in sandbox → if fails, iterate. Looks like an agent but is really a tight scripted loop. Easier to ship as a deterministic workflow (or a `verify_code` MCP tool that internally calls `run_python` with retries).
- **`dependency_search`** — "what library does X" → web_search + read docs. Agentic shape, but bounded enough to just live in the persona's main tool loop without a sub-agent.

Drop `code_checker` as a separate agent. Fold its prompt-guidance into the `code` persona's system prompt or a `/review` slash command that injects a code-review preamble.

### Cross-persona — `paper_summary_to_artifact`

User asks "summarise this paper into a slide deck": `paper_lookup` → `read_paper` → `llm_summarize` → `create_artifact` (`.tex`) → `compile_latex`. Bounded scope, partially-open shape (depends on paper length/structure). This is a **workflow**, not really an agent — easier and cheaper to ship deterministically.

---

## What I'd actually do, in order of effort

1. **Drop `code_checker` and `writing_agent`.** Move their guidance into persona prompts or slash commands. Fewer moving parts, less to debug, fewer ways for forced-retry logic to misfire.
2. **Add `/research <question>` slash command** that directly invokes `research_orchestrator` (bypasses model-driven calling). User-triggerable path; matches the audit's #2 recommendation for explicit control.
3. **Add a plan-mode card before invoking an agent.** "About to run `research_orchestrator` (budget: 30 tool calls, ~5 min). Approve / refine / cancel." Same shape as `ask_clarification`; reuses the SSE event machinery.
4. **Recast fixed-shape workflows as deterministic pipelines, not agents.** `deep_research` is the existing template. Anything else with a known step list goes the same way.
5. **Add one or two new research-persona agents** (`citation_snowball`, `contradiction_finder`) — but only after the above. Don't grow the agent count until you've trimmed it.

The order matters: trimming first means the remaining agents are clearly the ones that earn it, which makes the slash-command and plan-mode work simpler (fewer surfaces to wire up).

---

## How this interacts with the main audit

A few items in `munin-audit.md` directly support the changes here:

- **#7 (`tool_search` / deferred tools)** — fewer agents means fewer schema entries to defer, but doesn't remove the need. ToolSearch and agent slash commands compose cleanly.
- **#22 (plan mode for `research` persona)** — the audit's plan-mode item is half of recommendation #3 above. The other half is the cost-estimate display.
- **#24 (memory auto-extraction)** — a `research_orchestrator` run is exactly the kind of context-heavy event where post-run memory extraction would compound value. Worth pairing.
- **#3 (cancellation propagation)** — long-running agents are the worst case for the abandoned-tab problem. Trimming agents reduces the absolute risk; fixing cancellation removes it.

If you do #1 and #2 from this doc, expect to revisit `chat_service.py:1757-1946` (the delegate intercept) and `chat_service.py:2087-2128` (the nested-event queue) — both will get simpler when fewer agents flow through them.
