# Specialised Agents Roster and Contracts (brainstorming sketch)

Status: DRAFT for brainstorming. Nothing here is built. This is a design
artifact meant to be read standalone (e.g. pulled into a separate Claude
session), so the motivating context is inlined below.

## 1. Why this exists (the diagnosis that led here)

Munin's research assistant today is a **single model** (`qwen3.6-35b-a3b`)
running **one flat loop**: it emits MCP tool calls (`paper_search`,
`semantic_scholar_search`, `read_paper`, `compare_papers`, `web_search`,
`faq`, ...), gets results back, and keeps going in one shared context window
until it answers. There is no delegation and no nested reasoning.

An eval-suite diagnosis (Track D, LitQA2 multiple-choice) surfaced a concrete
limit:

- The agentic arm scores **0.56 accuracy / 0.86 precision**. It abstains a lot.
- Of 20 questions where it abstained even though the source paper WAS
  retrievable, **16/20 actually read the source paper** and still abstained.
- Root cause: `read_paper` is *summarise-and-discard*. It extracts the full
  PDF text, then returns only an LLM-generated 3-5 sentence summary plus 3-6
  key-finding bullets. The raw text is thrown away. A buried numeric answer
  (a surface area in Angstrom^2, a fold-change, a table cell) is in the paper
  the agent read, but is compressed out before the agent sees it.
- Ceiling oracle: feeding the **full raw text** to the same model on those
  abstentions flips **9/11 to correct (82%)**, 1 incorrect, 1 still abstain.

Conclusion: **the model is not the bottleneck.** Given the right ~2k tokens it
already answers correctly. What is missing is *plumbing*: getting the right
passage in front of the model without wrecking the shared context budget.

## 2. Why specialised agents (the architectural bet)

A specialised agent here buys three things, in priority order:

1. **Observability.** A named, typed, loggable call (`paper_qa(doi, question)
   -> {answer, quotes, found}`) instead of reconstructing intent from a
   5-to-30 primitive-call sequence. We know exactly what ran. This was the
   single hardest thing during diagnosis: the flat loop is opaque.
2. **Context isolation.** A full paper is ~15-35k tokens. Injecting that (or
   several papers' worth of chunks) into the outer loop bloats a 65k window
   that is already under pressure (the chat service already elides old tool
   results under fan-out). A sub-agent holds the big context in its OWN window
   and returns a compact grounded result.
3. **Fewer tools at the layer that matters.** Collapsing N primitives behind
   one agent shrinks the outer model's branching factor where wrong turns are
   most expensive and least visible. (The outer loop's tool-call count has an
   ugly tail: median 13.5, p90 40, capped at 30.) Complexity is relocated
   inward, not removed, but it is relocated to a bounded, testable place.

What a specialised agent does NOT buy: reasoning horsepower (the model already
has it) and better table/figure extraction (see the residual-ceiling note in
section 6).

### The determinism dial

"Full agent" and "deterministic" are in slight tension. An agent with its own
free LLM tool-loop is still stochastic inside; you move the non-determinism
down a level, you do not remove it. So each agent has a **determinism dial**:

- **Pinned pipeline**: fixed steps, no LLM routing inside (e.g. retrieve ->
  extract -> answer). Most deterministic, most auditable. Best where the task
  decomposes cleanly.
- **Semi-pinned**: fixed skeleton, one bounded LLM decision (e.g. "which
  sub-queries to run").
- **Free loop**: its own open-ended tool loop. Most capable, least
  deterministic. Reserve for tasks that genuinely need iteration.

Default stance: pin as hard as the task allows. The paper-reading bottleneck
is a pinned pipeline, not a free loop.

## 3. The contract template

Every agent is defined by the same fields. The outer model sees only the
name, purpose, input, and output; the rest is the implementation contract.

- **Name / intent** the user intent it owns (draw boundaries around intents,
  not around tools).
- **Purpose** one sentence.
- **Input** typed arguments.
- **Output** typed, ALWAYS a grounding envelope:
  `{answer|result, evidence:[{quote, source}], confidence|found, abstained}`.
- **Owns** the tools/data it encapsulates (the outer model no longer sees
  these directly).
- **Interior** Pinned / Semi-pinned / Free (the determinism dial).
- **Abstain contract** when and how it returns "insufficient information"
  rather than guessing (this is what protects precision).
- **Cost profile** rough LLM-call count and latency class.
- **Eval seam** how the eval suite measures it in isolation.
- **Status** exists-as / new.

## 4. The roster

Ordered roughly by how cleanly they decompose and how directly they hit the
diagnosed bottleneck. The first is the obvious first build.

### A. Paper-QA agent  (RECOMMENDED FIRST)

- **Intent** "answer a specific question against a specific paper."
- **Input** `{doi | paper_ref, question}`.
- **Output** `{answer, supporting_quotes:[{quote, section}], found:bool,
  confidence}`.
- **Owns** paper resolution (local corpus -> cache -> OA download), text
  extraction (GROBID -> pypdf), **within-document retrieval** (chunk the
  extracted text with the existing 512-token/50-overlap chunker, rank chunks
  against the question with the already-loaded BGE encoder), and one focused
  extraction/answer LLM call over the top-k chunks.
- **Interior** Pinned pipeline (resolve -> extract -> chunk -> rank -> answer).
- **Abstain contract** "quote the exact supporting sentence or return
  found=false." This is the precision guard; the oracle showed raw text can
  also mislead (1/11), so the quote-or-abstain rule matters.
- **Cost** 1 LLM call (the answer step), plus embedding of chunks. Cheap.
- **Eval seam** re-run the Track D agentic arm on the 20 over-abstention
  questions; measure abstain->correct conversion and the precision cost.
  The full-text oracle (0.82 flip) is the ceiling this agent chases.
- **Status** new, but ~80% of the parts exist (`read_paper` extraction path +
  `document_store.chunk_text` + `database.get_bge`). It is essentially
  PaperQA2's core move, and it makes the current `read_paper` summariser one
  of two output modes rather than the only one.
- **Note** this agent OWNS its within-paper query generation, which sidesteps
  the open "does the flat loop remember to pass `focus`?" question entirely.

### B. Literature-search / survey agent

- **Intent** "find and rank the evidence relevant to a topic/question."
- **Input** `{query, filters?(year, corpus)}`.
- **Output** `{ranked_papers:[{doi, title, snippet, score, source}],
  coverage_note}`.
- **Owns** `paper_search` (local corpus, SPECTER/BGE), `semantic_scholar_search`
  (external), multi-query fan-out, dedup-by-DOI, ranking, and an honest
  coverage note ("local corpus thin on X, branched to S2").
- **Interior** Semi-pinned (fixed fan-out + dedup skeleton; one bounded LLM
  step to phrase sub-queries).
- **Abstain contract** returns an explicit "thin evidence" flag rather than
  padding with weak hits.
- **Cost** 0-1 LLM calls (query phrasing) + retrieval. Cheap.
- **Eval seam** LitQA2 retrieval recall@10 (already in the suite) becomes this
  agent's metric directly.
- **Status** new wrapper over existing tools; consolidates 2+ tools into 1.

### C. Compare agent

- **Intent** "compare N papers on an axis."
- **Input** `{dois:[...], focus}`.
- **Output** structured comparison + `{failed:[...]}` for unreadable papers.
- **Owns** fan-out over the Paper-QA agent (one per paper) + one synthesis
  call.
- **Interior** Pinned (fan-out -> synthesise).
- **Cost** N Paper-QA calls + 1 synthesis call.
- **Eval seam** grounding/faithfulness track (MiniCheck) on the synthesis.
- **Status** ALREADY EXISTS as `compare_papers` (fans out `read_paper` +
  synthesis). Formalising it as an agent means pointing it at the Paper-QA
  agent instead of raw `read_paper`, so it inherits the grounding contract.
  Precedent proof that the pattern works in Munin.

### D. Deep-research / synthesis agent

- **Intent** "answer an open research question end to end."
- **Input** `{question}`.
- **Output** grounded answer + citations + confidence.
- **Owns** planning (decompose into sub-questions), dispatch to the
  Search agent and Paper-QA agent, and final synthesis.
- **Interior** Free loop (this is the one place iteration genuinely earns the
  non-determinism: read paper A, which changes what you search for in B).
- **Abstain contract** surfaces unresolved sub-questions rather than
  papering over them.
- **Cost** high (planner + many sub-agent calls + synthesis). Needs a call/
  token ceiling.
- **Eval seam** end-to-end LitQA2 answer accuracy (Track D), faithfulness,
  and cost per answer.
- **Status** new; this is effectively what the outer loop does today, but with
  the messy middle replaced by clean agent calls. Could also just BE the outer
  orchestrator rather than a distinct agent (open question, section 7).

### E. Citation-graph agent  (optional / later)

- **Intent** "find foundational / citing / related work for a paper or claim."
- **Input** `{doi | claim}`.
- **Output** `{related:[{doi, relation, title}]}`.
- **Owns** the Neo4j citation graph.
- **Interior** Pinned (graph traversal, no LLM inside) or Semi-pinned.
- **Status** new; only worth it if citation-graph queries are a real user
  intent. Cheap and very deterministic if so.

### What stays a plain tool (not everything needs to be an agent)

Deterministic, single-shot primitives with no internal reasoning stay tools:
`faq` (how-to lookup), PDF/download-link resolution, raw `web_search` for a
current fact. Wrapping these in agents adds ceremony with no plumbing payoff.

## 5. Orchestration model

The outer model becomes a **router/planner + synthesizer** over a small set of
high-level agents, instead of a driver of many low-level tools. This is the
same shape as the persona->router migration already shipped, moved up one
level. Each agent runs in its own context window and returns a compact
grounded envelope, so the outer window stays lean.

Implementation-wise, the pragmatic path is: **an agent is an MCP tool with a
fat, typed contract and an isolated internal context.** This reuses the
existing tool-dispatch machinery, keeps the outer model's mental model simple
(it just sees fewer, higher-level tools), and matches the `compare_papers`
precedent. A separate orchestration layer is a bigger change and probably not
needed for the first agents.

## 6. Residual ceiling this does NOT fix

**Table and figure extraction.** The oracle used GROBID text where the TEI XML
is regex-stripped to a flat "number soup" and still hit 82%, so access is the
bigger lever right now. But values that live only in a rendered table or a
figure panel are where even PaperQA2 tops out (~0.66). A specialised agent
improves *access*, not *extraction*. If measurement shows the residual misses
are table/figure-bound, that is a separate lever (structured TEI table
parsing, or a vision pass on figures), orthogonal to the agent question. Keep
the two distinct.

## 7. Decide these (open questions for brainstorming)

1. **Which agent first?** Recommendation: **Paper-QA (A)**. It hits the
   measured bottleneck directly, is a pinned pipeline (max determinism), reuses
   ~80% existing parts, and has a ready-made eval seam (the over-abstention set
   + the 0.82 oracle ceiling).
2. **Agent boundaries.** Confirm the intent-based cut (answer-against-paper /
   survey-topic / compare / deep-research). Any intents missing? Any that
   should merge?
3. **Determinism per agent.** Confirm A/B/C stay pinned or semi-pinned, and D
   is the only free loop. Comfortable with that split?
4. **Model per agent.** Same `qwen3.6-35b-a3b` everywhere, or a smaller/cheaper
   model for the pinned extraction step (Paper-QA) to cut cost, reserving the
   big model for synthesis?
5. **How the outer model selects agents.** Agents-as-fat-MCP-tools (recommended)
   vs a distinct orchestration layer. Confirm the former for v1.
6. **Nesting and cost ceilings.** Deep-research calls Paper-QA calls
   extraction. How deep do we allow, and what is the per-answer token/call
   budget (there is already an outer over-tooling cap of 30 to mirror)?
7. **Rollout.** Does the Paper-QA agent replace `read_paper`, or run alongside
   it (read_paper for "summarise this paper", Paper-QA for "what value did they
   report")? Alongside-first is lower risk.
8. **Eval-first.** Each agent ships with its isolation metric wired into the
   run_all scorecard and the certification gate before it goes live, so we can
   prove it beats the flat loop rather than assume it.

## 8. One-line summary of the bet

Turn the flat, opaque, context-hungry tool loop into a small set of named,
grounded, mostly-pinned specialised agents. Start with Paper-QA, because the
eval already proved the answer is in the text and the only thing missing is
handing the model the right passage.
