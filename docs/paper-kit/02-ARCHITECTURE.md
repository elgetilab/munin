# 02 Architecture

The agentic harness. This is the system contribution: what the harness is, why
each piece has the shape it does, and which design principles are load-bearing.

**Snapshot.** This file describes the harness as deployed at the refresh
commit (2026-09-14). The headline measurements in `05-RESULTS.md` were taken
at `df14dff` (2026-08-26); the search escalation ladder, the grounded `read`
stage in `search`, and the `list_documents` / `browse_tag_papers` tools
landed after that run and are described here because the paper describes the
shipped system, but no result in this kit was measured with them.

---

## 1. The shape, in one diagram

```
user turn
   │
   ▼
router.py ──── profile: chat | research | code       (before the first model call)
   │
   ▼
system prompt = base[pinned persona] + fragment[routed profile]
   │
   ▼
outer model loop (qwen3.8-27b, 60k budgeted context)
   │
   ├── 4 named agents (fat tools, isolated interior context)
   │      source(refs[], mode)        read 1..N documents
   │      search(query, filters?)     find and rank evidence
   │      compute(spec, data_handle?) spec + data -> verified code + artifacts
   │      deep_research(question)     long-running, job-shaped
   │
   ├── 41 plain MCP tools (deterministic, single-shot)
   │
   └── hooks: plan approval, audit log, memory extraction
   │
   ▼
post-turn audits (phantom URL, phantom paper, citation-claim grounding)
   │
   ▼
SSE stream (resumable, promotable to background) + save-always persistence
```

Three things distinguish this from a flat ReAct loop and each is measurable:

1. **The profile is decided before the first model call**, not by the model
   mid-turn.
2. **Sub-agents hold their own context** and return compact grounded results,
   so a 15k-35k token paper never enters the outer window.
3. **Every envelope states which guarantee it carries**, so a caller never has
   to infer whether a result was executed or merely parsed, read in full or
   skimmed from an abstract, sourced from the curated corpus or from a blog.

---

## 2. The per-turn router

`router.py`. Replaced an earlier mechanism in which one persona could hand a
turn to another mid-conversation (`delegate_to_persona`).

**Cheapest-first cascade:**

| Tier | Mechanism | Behaviour |
|---|---|---|
| 1 | **Rules** | Slash commands `/research`, `/code`, `/chat` force that profile absolutely, overriding both the pin and the KNN vote. The leading token is stripped before the query reaches the model. |
| 2 | **KNN** | BGE-embed the query, distance-weighted vote over a labelled example set, with the pinned profile injected as a prior (weight 0.5). Accepted only if the winner leads the runner-up by a margin (0.10 of total vote) **and** the nearest neighbour is in-distribution (cosine > 0.45). |
| 3 | **Fallback** | The pin if explicitly pinned, else `chat`. |

Parameters: `KNN_K = 8`, `MARGIN_THRESHOLD = 0.10`, `OOD_SIM_THRESHOLD = 0.45`,
`PIN_PRIOR = 0.5`. These are the 2026-06-24 initial values; the code still
labels them "NOT final" and they were never tuned against the eval. A tier-3
LLM classifier was specified but deliberately not built, on latency grounds
with a "build only if tiers 1+2 underperform" clause (A3 plan, 2026-06-24/25).

**What the routing anchor eval measures, stated carefully.** The number the
project quotes for it, **0.963**, is a **tool-trajectory pass rate**, not a
profile-routing accuracy: an anchor passes a rep when its gated tool checks
(first tool, required, forbidden, solo, no-tool, abstain routing) all hold,
and 0.963 is the mean over 16 anchors x 5 reps (77/80 turns) on 2026-07-10
under a `chat` pin. The router's profile choice is logged as a non-gating
diagnostic; it agreed with the gold profile on 7 of the 10 labelled anchors
(0.70) in that run, and the eval pins `chat` where the UI sends the unpinned
`munin` identity. The KNN tier is therefore covered by the eval but not
scored by it. Full derivation, anchor-set provenance and the cannot-claim list
are in `docs/ROUTING-EVAL-FACTS.md`.

The embedder is injected as a function, so the router is unit-testable without
loading BGE. This matters for the paper's reproducibility story: routing
behaviour is testable offline.

### Why delegation was deleted rather than disabled

A soak with delegation off, logging attempts, plus the chat database, showed it
fired roughly **15 times in 6 weeks**, and every case was a cross-profile need
that an up-front router handles directly (a chat user asking a research
question now routes to research immediately). Once the per-persona tool
allowlists were retired, every tool became reachable via a core set plus
`tool_search` regardless of profile, so delegation's real job (tool access)
vanished and it was vestigial.

**The deliberate trade-off, stated plainly:** the old per-persona allowlist was
a hard boundary (the research persona literally could not call `run_python`).
The router relaxes this to a soft bias: profiles shape tool *usage* through the
prompt fragment and a `resident_tools` surfacing set, but any tool is
reachable. If a specific tool ever needs a hard wall it gets an explicit
per-tool guard; the allowlists are not coming back.

### Personas after consolidation

One Munin identity with three routing profiles, not three assistants. The
personas retain display identities (Meitner for chat, Turing for code, Curie
for research) and per-profile sampling parameters, but the system prompt is
composed as `base[pin] + fragment[routed]`, so identity and capability are
orthogonal.

---

## 3. The four agents

The agents replaced a flat loop over low-level MCP primitives. The diagnosis
that motivated them is in `07-FINDINGS.md` §2 and is the most important
methodological point in the paper: **the model was never the bottleneck; the
plumbing was.**

### 3.1 `source` (the read agent)

```
source(refs[], mode: summary | qa | extract | compare, question?, focus?, schema?)
```

| refs | mode | returns | replaces |
|---|---|---|---|
| 1 | `summary` | summary + key findings | the old `read_paper` |
| 1 | `qa` | answer + supporting quotes | **the bottleneck fix** |
| N | `summary` | multi-summary digest | new |
| N | `compare` + focus | structured comparison | the old `compare_papers` |
| 1..N | `extract` + schema | data handle | new, feeds `compute` |

**Interior is pinned:** `resolve -> extract -> one LLM call`. Full text in a
single call. Chunking exists only as an overflow fallback, and it must justify
itself against the no-chunk baseline on cost rather than being assumed, because
chunk-and-rank introduces a retrieval-miss failure mode that the full-text
oracle never tested and so structurally cannot reach the ceiling it is chasing.

**Tagged refs, fuzzy allowed:**

```
ref := {doi} | {arxiv} | {id} | {url} | {title, authors?, year?}
```

The rationale is a specific, nasty failure mode. If the signature demands a
DOI, the outer model must *produce* one, and when it does not have one it will
generate one. DOIs are the worst possible field for that: structured,
publisher-prefixed, plausible. The bad outcome is not a failed lookup; it is a
**confabulated DOI that resolves to a real but wrong paper**, where the agent
returns `found=true` with genuine quotes and a perfect grounding envelope.
Every check passes and the answer is about the wrong document. A wrong title in
a trace is legible to a human; a wrong DOI is not.

Tagging also buys a cheap audit: **if a `{doi}` appears that was never in a
prior tool result this conversation, it was generated.** That turns speculation
into a log line and then into a measurement.

**Four-way (plus one) outcome.** `found=false` overloads three distinct
failures and destroys information both the router and the eval need:

| Outcome | Meaning | Caller should |
|---|---|---|
| `not_found` | read it fine, the answer is not in it | accept the negative |
| `extraction_failed` | resolved but unreadable (scanned PDF, GROBID failure) | flag for ingestion |
| `ambiguous(candidates[])` | a fuzzy ref matched several | disambiguate, never silently take the top hit |
| `out_of_scope` | exists consortium-wide but not in the active sub-corpus | tell the user to widen |
| resolution miss | not in the corpus, not downloadable | a genuine corpus gap |

The abstention benchmark measures exactly this distinction: calibrated refusal
versus confabulation versus plumbing breakage. Collapsed to one boolean it is
unscoreable and any certification threshold on it is meaningless.

**Abstain contract:** quote the exact supporting sentence or return
`not_found`. This is the precision guard. It does not prevent quoting a real
sentence and misreading it, which the oracle showed happens about 1 time in 11.

**Extract mode returns a handle, not rows** (`{handle, schema, n_rows,
preview[5], provenance}`). This is enforcement by construction rather than
policy: the outer model cannot transcribe values it never held. Five preview
rows let it narrate the schema to the user without being able to rebuild a
200-row plot.

Open-access resolution goes through Unpaywall (broader coverage than Semantic
Scholar's `openAccessPdf` field and it returns direct PDF links). Without it,
OA papers outside the local corpus fall back to abstract-only, which `qa` mode
then correctly abstains on.

### 3.2 `search` (the find-evidence agent)

```
search(query, filters?(year, tags), depth?: normal | deep, read?: 0..3, top_k?)
  -> {ranked: [{ref, title, snippet, score, source_type}],
      answers?: [...], coverage_note, thin_evidence: bool, counts, trace}
```

Consolidates local corpus search, Semantic Scholar, and web search behind one
call, and is the primary research tool: the plain `paper_search` /
`semantic_scholar_search` / `web_search` tools stay callable but the model is
told not to hand-chain them after it.

**Escalation is a ladder, not a fan-out** (since 2026-08-27, after the
headline runs). `depth=normal` starts with the local corpus, adds Semantic
Scholar, and reaches the web only if the scholarly tiers come up short;
`depth=deep` includes the web from the start. This replaced an unconditional
three-tier fan-out that was measured to over-tool on questions the corpus
already answered.

**`read=N` is a grounded read stage inside the search call.** Up to N of the
best hits are opened and the query is answered from their full text via the
`source` agent, one at a time, stopping at the first document that actually
answers, so it usually costs one extra read rather than N. Values, constants
and measurements live in paper bodies and never in snippets, and this puts
the full-text read where the model already is instead of relying on it to
call `source` afterwards.

**`source_type` is load-bearing.** Trust is not uniform across
`corpus_paper` / `oa_paper` / `web`, so a single similarity score cannot rank
across tiers: a Nature paper and an SEO-farmed listicle are not comparable on
embedding distance. The ranker applies **tier-aware quotas** (`WEB_QUOTA = 3`,
`OA_QUOTA = 4`) rather than one global score. Without quotas, web wins on
volume and shallow lexical match.

**The external-slot relevance floor is encoder-specific.** BGE cosine has a
high compressed baseline (observed band 0.57 to 0.77 on real candidates), so
the floor of 0.62 was calibrated over 30 real retrieved candidates and is
explicitly **not** a general-purpose "similarity is high" threshold. It is tied
to this encoder and is environment-overridable per domain. This is a good
example for the paper of a threshold that looks arbitrary and is not.

**Dedup runs on the alias set** `{doi[], arxiv, normalized-title}`, not a single
field, because the arXiv preprint and the published version are the same paper
with different DOIs. Without alias dedup, a compare call fans out over "two"
papers that are one.

**`coverage_note`** reports "X hits in scope, Y consortium-wide" when a
sub-corpus filter is active. This exists because a scoping artifact rendered as
`not_found` is a lie the user will act on: it reads as a corpus gap when it is
a filter effect. The unscoped query is the same query, so it costs almost
nothing to compute.

**`thin_evidence`** flags a genuinely thin result rather than padding with weak
hits (`THIN_EVIDENCE_MIN = 3` scholarly hits).

**Egress-aware:** which tiers fan out is driven by the egress control, not by
the model. `off` gives local corpus only, `oa_only` adds scholarly APIs, `full`
adds web.

### 3.3 `compute` (spec + data to verified artifacts)

```
compute(spec, data_handle?, language, budget?: quick | compute)
  -> {code, language, verify_level: executed | compiled | parsed | returned,
      ran, artifacts, attempts, errors, trace}
```

**Routing is deterministic, not an LLM judgment**: `language == python` goes to
the sandbox, everything else gets the strongest static check that language
offers. "Is this something I need to sandbox?" would be a stochastic call
inserted into a pipeline whose whole point is auditability, and one the model
has an incentive to get wrong in the lazy direction.

**The verifier ladder, and why per-language guarantees differ:**

| Language | Check | Resulting `verify_level` |
|---|---|---|
| Python | `ast.parse` + ruff, then sandbox execution | `executed` |
| C++ | `g++ -fsyntax-only` | `compiled` (full type checking, no link, no run) |
| R | `parse()` + lintr | `parsed` |

The C++ static rung is *stronger* than Python's static rung. A user receiving R
must not read it as carrying the guarantee that executed Python does, which is
why the level is in the envelope rather than implied.

**For plots, static rungs are nearly worthless.** A script that grabs the wrong
column, mislabels an axis, or plots against the index lints perfectly clean.
The only check that means anything is "did it run and produce a figure."

**The sandbox boundary falls between lint and import, not between lint and
test**, because `import foo` executes module-level code. There is no worthwhile
static import check in the general case; the narrow pinned environment is what
makes a cheap import allowlist work at all.

**Environment:** one broad pinned scientific image (numpy, scipy, pandas,
matplotlib with the Agg backend, seaborn, sympy, sklearn, statsmodels), prebuilt,
**no pip install ever**. Network namespace disabled, read-only filesystem plus
one scratch directory, memory cap, pid cap, wall clock. Budget tiers rather
than one wall clock: `quick` (~15s, the plot case, default) and `compute`
(~5min, opt-in), because a `scipy.optimize` run or an MCMC legitimately takes
minutes.

**BLAS threading is the protection lever, not core count.** numpy and scipy
link OpenBLAS/MKL and will grab every visible core; one unbounded `np.linalg`
call eats all of them. `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` /
`MKL_NUM_THREADS` are pinned and a cgroup CPU quota is set, which means the
agent can be throttled to 1-2 cores when the cluster goes LLM-only rather than
being shut down.

**Bounded repair loop (<= 3 attempts), stop on a repeated error signature.**
The error signature is hashed from exception type plus final frame; if the
identical error recurs the loop stops immediately, because the model is stuck
rather than converging. A code agent silently grinding 15 repair attempts is
the opacity problem relocated.

**The self-authored-test trap** is named explicitly in the design: if the agent
writes its own tests, the repair loop optimises against a test the same model
authored and converges on green by weakening the test. Tests come from the spec
or from a separate generation step that never sees the repair loop, otherwise
`passed=true` means nothing.

**Delivery is a bundle, not a script**: `plot.py` + `data.csv` + `figure.png`,
where the script reads `data.csv` from its own directory. This is the only form
in which "byte-identical to what ran" and "standalone-runnable" are
simultaneously true. A tidied-up script would mean the human verifies a figure
produced by code they did not receive.

**Data provenance rule, by origin rather than by syntax:** data the user typed
or uploaded may be literals; data a script generates (`linspace`, a simulation)
may be literals; data **from a document** arrives only as a handle. A blanket
ban on literals is unenforceable (syntax cannot distinguish `sigma = 1.4` as a
parameter from data), and a blanket allow leaves the hole. Extract mode's
handle-only return makes the bad path impossible rather than forbidden. The
delivered `data.csv` then carries `value, unit, ref, quote_id` columns, so
provenance rides into the artifact that ends up in a publication.

**Named residual:** runs does not mean correct. The human is the semantic
verifier, and returning the rendered figure through the artifact system is what
lets them be. A wrong plot is obvious at a glance in a way a wrong buried
number never is.

### 3.4 `deep_research` (the long-running agent)

```
deep_research(question, egress?, budget?)   [toggled, job-shaped]
  -> job_handle -> poll
  -> {document, plan[], citations: [{ref, source_type, read_depth}], trace}
```

**It runs in-process against the live vLLM, not as a SLURM job.** This is a
deliberate reversal of an earlier design: a multi-minute run cannot block a
chat turn, but it also does not need SLURM's job machinery, because the actual
contention is inside vLLM's continuous batching and SLURM has no visibility
into it. The delivery layer is a detached asyncio task with SSE progress,
stream-registry resume, and markdown-artifact delivery.

**The plan is a data structure, not a thought:**

```
plan: [{id, sub_question, status: open | resolved | unresolvable,
        notes[], evidence_refs[]}]
```

Loop: pick an open sub-question, `search`, screen, `source` fan-out, update
status, possibly spawn new sub-questions. If the loop were instead "model
thinks, calls things, thinks again", the flat loop and its opacity would be
rebuilt, only longer and more expensive.

Three properties fall out for free: the **stopping criterion** is "every
sub-question resolved or unresolvable" with budget exhaustion as a hard floor
rather than the rule; **unresolved sub-questions can be surfaced** rather than
papered over, because they are objects; and **checkpointing** is trivial
because the plan plus notes *is* the job state, so a run that dies at minute 18
resumes instead of restarting.

**The funnel:** ~100+ retrieved, screened down to ~25-30 full reads, notes,
outline, sections. Funnel widths are config, not agent-chosen, which is what
makes cost predictable: 1 search fan-out + 1 batched screen + ~25 source calls
+ outline + ~6 section drafts is roughly **35 LLM calls**.

**The screener is asymmetric and this is under-appreciated:**

| Error | Cost | Visible? |
|---|---|---|
| False positive (screen in a bad paper) | one source call | yes, recoverable |
| **False negative** (screen out the right paper) | **unrecoverable** | **no** |

A false negative is silent: the paper was in the corpus, it was retrieved, and
the answer simply never appears. It looks exactly like a corpus gap. So the
screener is tuned hard for recall, errs permissive, and **logs every drop and
why**, otherwise the most consequential decision in the pipeline is the least
visible one. The screener is cheap because of batching and context length
(~100 abstracts at ~200 tokens is one ~20k-token call), not because of model
size: there is one model and one vLLM instance for every call.

**`read_depth` per citation** (`full_text` | `abstract` | `snippet`). 100+
sources cited is not 100+ read. A document citing 100 sources where 70 were
only ever abstracts should say so per citation, otherwise "synthesised from 100
sources" overstates what happened and a reader cannot tell which citations to
trust. It also gives the screener a middle option rather than a binary.

**Grounding is three hops** (`quote -> note -> section prose -> document`) and
each hop is lossy. The rule is that the synthesiser drafts **from quotes, never
from paraphrase-of-paraphrase**. Notes carry the quote and the ref alongside
the claim.

**Notes are structured so contradiction is representable:**
`{claim, value?, unit?, quote, ref, sub_question_id}`. Thirty papers will
disagree, and for a research audience the disagreement is often the finding. A
document that silently averages them is worse than one that names the split.
Flat prose notes make contradiction invisible, because the synthesiser would
have to happen to see two of them in the same window, which it will not when
drafting section-wise. Conflicting claims on the same `sub_question_id` are
mechanically detectable.

**Sections retrieve notes by `sub_question_id`, not by embedding similarity.**
The plan already exists, so note selection is a structural lookup: no vector
search, no ranker, no retrieval-miss failure mode. The consequence is
deliberate rather than accidental: **the outline is not free-form.** The
synthesiser can order and merge sections but cannot invent one with no
sub-question behind it, because there would be no notes to draft it from. That
is what makes every section traceable.

**Snowballing** (following references from seed papers) is depth 1 only, routed
back through the screener and never read directly. Depth 2 needs measurement
first.

---

## 4. Cross-cutting design principles

These recur in every agent and are the design more than the roster is. They are
the most transferable part of the paper.

1. **Handles, not payloads.** Sub-agent output lands in an addressable store
   and the orchestrator passes references. The outer window never holds a
   table, a corpus of notes, or a paper's text. This is what makes context
   isolation hold rather than leak at every junction.
2. **The envelope always says which guarantee you got**: `verify_level`,
   `read_depth`, `origin`, `source_type`, `extraction_method`.
3. **Name the failure mode; never collapse it to a boolean.** Different
   failures demand different responses from the caller and different scoring
   from the eval.
4. **Expensive, irreversible, or policy-bearing choices are config, not
   inference.** Egress, funnel widths, verify ceilings, the deep-research
   toggle. The model never gets to decide to spend 20 minutes or to let data
   leave the premises.
5. **Pin as hard as the task allows.** A pinned interior makes a component a
   drop-in swap later with the contract unchanged and the delta measurable.
6. **Everything emits a trace.** Every agent records resolved sources, LLM
   calls made, decisions taken, and abstain or failure reasons. The eval reads
   the trace. Without this you rebuild the opacity gap one level down.

### The store

Three stores, not two, because traces differ from blobs and job state on every
axis that matters:

| | Scope | Reader | Query shape | Lifetime |
|---|---|---|---|---|
| Blobs (extracted text, CSVs, figures, snapshots) | one document or run | by handle | none | LRU + size cap |
| Plan + notes | one job | that job, then its document | structural lookup by id | job, then archived |
| Traces | all jobs, sessions, agents | **the eval, later** | analytical / aggregate | forever |

**Meaningful paths, not content addressing.** Dedup buys almost nothing here
(preprint and published text differ), "free" is false (you cannot find a blob
without its hash, so you need the index you were trying to avoid), and the cost
is legibility in the observability substrate:
`derived/text/10.1021_jacs.9b04421/grobid.txt` is greppable at 3am and
`blobs/sha256/a3/f1b2...` is not. Hash is a metadata field. The one place a
hash genuinely earns its keep is web-snapshot change detection.

**Eviction is by regenerability, not by blob-versus-state**, and it is already
recorded because `source.origin` encodes it exactly:

| `origin` | Derived artifacts | Why |
|---|---|---|
| `local_kb` | freely evictable | the PDF is still in the corpus |
| `oa_download` | conditionally evictable | re-fetch works only while egress is on |
| `web` | **never evict** | the snapshot is the only copy that will ever exist |

The irreproducible class is roughly two orders of magnitude smaller than the
regenerable class (web snapshots ~1.5 MB per run, notes ~125 KB per run, versus
~20 GB of cached OA PDFs). So: **LRU the regenerable class, never collect the
irreproducible class.** This kills refcounting, and the whole lifecycle policy
collapses to two cron lines using `find` and `rm`. That it collapses so far is
the test that the regenerable/irreproducible cut was the right axis.

No new database was added: files plus DuckDB over JSONL traces. Qdrant stays
the corpus index, Neo4j stays the citation graph.

---

## 5. Tool inventory (45 MCP tools)

The four agents are fat tools alongside these. Plain tools stay plain when they
are deterministic and single-shot: wrapping them adds ceremony with no plumbing
payoff.

| Category | Tools |
|---|---|
| **Agents** | `source`, `search`, `compute`, `deep_research` |
| Retrieval | `paper_search`, `semantic_scholar_search`, `paper_lookup`, `web_search`, `web_fetch`, `search_user_docs`, `list_documents`, `browse_tag_papers`, `check_papers_availability`, `get_paper_pdf` |
| Citation graph | `get_citations`, `get_references`, `s2_get_citations`, `s2_get_references`, `get_author_papers`, `export_citations` |
| LLM utility | `llm_summarize`, `transcribe_equation`, `view_attachment` |
| Artifacts | `create_artifact`, `read_artifact`, `update_artifact`, `list_artifacts`, `save_artifact_to_documents` |
| Memory | `remember`, `forget`, `recall`, `search_past_conversations` |
| Projects | `list_projects`, `get_current_project` |
| Execution | `run_python`, `edit_python`, `sandbox_reset`, `compile_latex`, `calculate` |
| Orchestration | `invoke_agent`, `tool_search`, `ask_clarification`, `faq` |
| Planning | `set_plan`, `update_plan_item` |

`is_concurrency_safe` defaults to **True**, with eleven explicit opt-outs for
the artifact, memory, sandbox and plan mutators. Default-False would force every new
tool to declare the flag and would under-parallelise anything anyone forgot to
mark; default-True means a new mutating tool that forgets the flag
over-parallelises until someone notices the race. With 34 tools that are
read-only or idempotent, explicit-only for the mutators is the smaller surface
area.

A separate **agent registry** (`config/agents.yml`) defines named workflows
invocable through `invoke_agent`, each with a system prompt, a tool allowlist,
and hard guardrails (`max_iterations`, `max_tool_calls`, `timeout_seconds`).
Examples: `research_orchestrator` (10 iterations / 30 calls / 300s),
`code_checker`, `writing_agent`. This predates and coexists with the four
first-class agents.

---

## 6. Guardrails and audits

| Mechanism | What it does |
|---|---|
| `CHAT_MAX_TOOL_CALLS = 30` | Hard code cap on tool calls per answer, routing into the existing wrap-up synthesis. Shipped after prompt-level attempts failed to move over-tooling (see `06-ABLATIONS.md` §4). |
| Phantom-URL audit | Flags URLs in the answer that appear in no tool result. |
| Phantom-paper audit | Flags papers cited that appear in no tool result. |
| `audit_citation_claims_in_content` | Flags any "X et al." whose surname appears in no tool result from that turn, prefixing a `[backend warning]` block. Matching is **word-boundary, not substring**, because one real fabrication ("Mun") escaped an earlier hand audit as a substring of "Munin", which appears in every tool payload. |
| `bibref.py` | Resolves DOI / PMID / PMCID / arXiv identifiers through corpus, Semantic Scholar, Crossref, and NCBI eutils. **No LLM is in this path, so there is nothing to hallucinate.** |
| Explicit `authors: null` | Where metadata genuinely cannot be had, the result carries `authors: null` plus `metadata_available: false`. A missing field reads to a model as "not applicable"; an explicit null reads as "unknown, do not guess". That difference is the point. |
| Three-tier context budget | Real Qwen tokenizer (staged as 7 MB of tokenizer files rather than mounting 19 GB of weights), with a 4-chars-per-token heuristic fallback that undercounts code and LaTeX by 1.5-2x. |
| Transport retry | All vLLM calls go through `vllm_client.py`, which retries 5xx / 429 / pre-first-byte stream drops with exponential backoff and honours `Retry-After`. 5 attempts foreground, 2 background. |

### The citation-grounding incident, worth a paragraph in the paper

A user reported two papers attributed to authors who had nothing to do with
them; investigation found a third, uncaught. **None of the three names appeared
in any tool result.** The cause was a metadata hole, not a retrieval error: the
web tier returns title, URL, and snippet with no author list (176 of 176 web
hits in a month), `web_fetch` returned an LLM summary of the page body with
metadata already discarded (20 of 26 PubMed/PMC fetches yielded no usable
metadata), and PMC served a bot-check page that the summariser described as
though it were the article. Given a title and an identifier but no authors, the
model supplied plausible names from memory.

Two decisions follow, and both are transferable results:

1. **Metadata is fetched structurally, never inferred.** The fix had to be
   mechanical, because the persona prompt already said "Do NOT fabricate paper
   titles, authors, abstracts or DOIs" and *that rule is what failed*. Another
   instruction would have been the same fix that had already not worked.
2. **The audit annotates, it does not rewrite.** A repair generation was
   deliberately not added: the annotation keeps the human in the loop instead
   of hiding the error behind a silent retry. A Prometheus counter tracks
   grounded versus ungrounded claims so the rate is measured rather than
   waiting for the next user report.
