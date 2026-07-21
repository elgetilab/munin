# Munin Specialised Agents — Design v2

Status: DESIGN. Supersedes `SPECIALIZED-AGENTS-ROSTER.md` (v1 brainstorming
sketch). Nothing is built. Written to be read standalone; motivating context is
inlined.

---

## 1. The diagnosis (unchanged, and still the foundation)

Munin's research assistant is a **single model** (`qwen3.6-35b-a3b`) running
**one flat loop** over MCP primitives (`paper_search`, `read_paper`,
`compare_papers`, `web_search`, `faq`, ...) in one shared 65k context window.
No delegation, no nested reasoning.

Track D (LitQA2 multiple-choice) surfaced the limit:

- Agentic arm: **0.56 accuracy / 0.86 precision**. It abstains a lot.
- Of 20 questions where it abstained despite the source paper being
  retrievable, **16/20 actually read the paper** and still abstained.
- Root cause: `read_paper` is *summarise-and-discard*. Full PDF text is
  extracted, then compressed to a 3-5 sentence summary + bullets. The raw text
  is thrown away. Buried numerics (a surface area, a fold-change, a table cell)
  are compressed out before the model sees them.
- **Oracle: full raw text flips 9/11 to correct (0.82).** 1 incorrect,
  1 still abstain.

**The model is not the bottleneck.** Given the right ~2k tokens it already
answers. What is missing is plumbing.

Two things this buys that are worth restating, because they drive every
decision below: **observability** (a named typed call instead of reconstructing
intent from a 5-30 primitive sequence — the single hardest thing during
diagnosis) and **context isolation** (a paper is 15-35k tokens; a sub-agent
holds it in its own window and returns a compact grounded result).

---

## 2. What changed from v1

| v1 | v2 | Why |
|---|---|---|
| 5 agents (A-E) | **4 agents** | Paper-QA + Compare + `read_paper` + summariser collapse into one agent with modes. Citation-graph (E) folds into Deep Research as snowball expansion. |
| Paper-QA interior: resolve → extract → **chunk → rank** → answer | resolve → extract → **answer(full_text)** | The oracle proved 0.82 with *full text*. Chunk+rank adds a retrieval-miss failure mode the oracle never tested, so it structurally cannot reach the ceiling it's chasing. Chunking is a cost optimisation and must earn its place against the no-chunk baseline. Overflow papers get chunking as fallback. |
| D = distinct free-loop agent, "could just BE the outer loop" | **D is distinct, because it's a job** | A research run is 20 minutes; a chat turn is seconds. They cannot be the same loop. Two free loops at different timescales: interactive router, and the dispatched job. |
| "agent = fat MCP tool", no orchestration layer needed | **True for Paper/Search/Compute; Research is a job — but Slurm already runs jobs** | A 20-minute run can't live in a chat turn, so kick off → poll → handle is a different dispatch path. It is *not* new infra: `sbatch` returns a job id, `sacct` is the poll (§4.4). What's left to build is the plan semantics, not the job machinery. |
| — | **Compute agent (new)** | Not in v1. Mostly plots; serious users take the code to the API. |
| — | **The store (new)** | Handles-not-payloads. Serves both Compute's data handoff and Research's notes. The real backbone. |
| `{doi \| paper_ref}` | **tagged ref**, fuzzy allowed | An untyped union doesn't say who decides. See §4.1. |
| `found: bool` | **Four-way outcome** | `found=false` was overloading three different failures into one bool. |

---

## 3. Cross-cutting principles

These recur in every agent below. They are the design, more than the roster is.

1. **Handles, not payloads.** Sub-agent output lands in an addressable store;
   the orchestrator passes references. The outer window never holds a table,
   a corpus of notes, or a paper's text. This is what makes context isolation
   *hold* rather than leak at every junction.
2. **The envelope always says which guarantee you got.** `verify_level`,
   `read_depth`, `origin`, `source_type`, `extraction_method`. A caller must
   never have to guess whether a result was executed or merely parsed, read in
   full or skimmed from an abstract, sourced from the curated corpus or a blog.
3. **Name the failure mode; never collapse it to a bool.** `found=false` and
   `passed=false` are useless unless the envelope says *which way* it failed.
   Different failures demand different responses from the caller and different
   scoring from the eval.
4. **Expensive, irreversible, or policy-bearing choices are config, not
   inference.** `verify_ceiling`, `egress`, funnel widths, the Research toggle.
   The model never gets to decide to spend 20 minutes or to let data leave the
   premises.
5. **Pin as hard as the task allows.** The determinism dial from v1 stands.
   Pinning is what makes the table-extraction lever a drop-in swap later (§8).
6. **Everything emits a trace.** Observability was priority #1 in v1 but had no
   field in the contract template. Every agent emits: resolved sources, LLM
   calls made, decisions taken, abstain/failure reason. The eval reads the
   trace. Otherwise you rebuild the opacity gap one level down.

---

## 4. Shared infrastructure

### 4.1 Resolution and identity

**Input is a tagged ref, and fuzzy is legal:**

```
ref := {doi: "..."} | {arxiv: "..."} | {id: "..."} | {url: "..."}
     | {title: "...", authors?, year?}
```

Rationale: if the signature demands a DOI, the outer model must *produce* one,
and when it doesn't have one it will generate one. DOIs are the worst possible
field for that — structured, publisher-prefixed, plausible. The bad outcome
isn't a failed lookup; it's a **confabulated DOI resolving to a real but wrong
paper**, where Paper returns `found=true` with genuine quotes and a perfect
grounding envelope. Every check passes; the answer is about the wrong document.
A wrong *title* in a trace is legible to a human. A wrong DOI is not.

Tagging (rather than an opaque string) keeps type safety and buys a cheap
audit: **if a `{doi}` appears that was never in a prior tool result this
conversation, it was generated.** That's a log line, and it turns speculation
into measurement.

**Resolution outcomes:** exact id → deterministic lookup. Fuzzy → search →
match, or **`ambiguous(candidates[])` rather than silently taking the top hit**.

**Loose in, canonical out.** Every envelope returns
`ref_resolved: {internal_id, doi?, arxiv?, title, origin}`. First call can be
fuzzy; subsequent calls use the handle. The loop tightens itself.

**Primary key: the local store id, with DOI as a first-class alias.** DOI is a
good identity for a paper corpus and stays the display handle. But aliasing
bites today, with no ELN involved: the arXiv preprint and the published version
are the same paper with different DOIs. Search fans out to Semantic Scholar,
dedups by DOI, returns both; Compare then fans out over "two" papers that are
one. **Dedup runs against the alias set** `{doi[], arxiv, title}`, not a single
field. Cheap now, ugly to retrofit after a decade of DOI-keyed cross-refs.

### 4.2 The store

Sub-agent output lands somewhere addressable; the orchestrator passes handles.
This is the enforcement mechanism for Principle 1, Compute's data path,
Research's notes, the checkpoint substrate — **and the observability substrate,
since Principle 6's traces live here and the eval reads them.** If it's flaky or
unqueryable you don't lose a feature; you lose the ability to tell whether any
of this worked. Build it first and thin. Resist making it clever.

#### It's three stores, not two

An earlier draft said two (blobs / job state) and filed traces under job state.
Wrong — traces differ on every axis that matters:

| | Scope | Reader | Query shape | Lifetime |
|---|---|---|---|---|
| **Blobs** — extracted text, CSVs, figures, snapshots, artifacts | one document or run | by handle | none | per §4.2 lifecycle |
| **Plan + notes** | one job | that job, then its document | structural lookup by id | job, then archived with the doc |
| **Traces** | **all jobs, all sessions, all agents** | **the eval, later** | **analytical / aggregate** | **forever** |

Notes are job-local and read by the thing that wrote them. Traces are global and
read by something that doesn't exist yet, asking questions we haven't thought of
yet. Different store.

#### Meaningful paths, not content-addressing

An earlier draft said blobs want content-addressing for free dedup. Retracted —
it's ceremony here:

- **Dedup buys ~nothing.** When do two sources produce identical bytes? Preprint
  and published text differ. It's a rounding error.
- **"Free" is false.** You can't find a blob without its hash, so you need a
  `(doc_id, method) → hash` index — which is the thing you were trying to avoid
  building.
- **The cost is legibility, in the observability substrate.**
  `derived/text/10.1021_jacs.9b04421/grobid.txt` is greppable at 3am.
  `blobs/sha256/a3/f1b2...` is not.

Meaningful paths; hash as a metadata field. The one place a hash genuinely earns
its keep is web-snapshot change detection — and that's change detection, not
addressing.

#### Layout

```
store/
  derived/          # regenerable — LRU + size cap
    text/<doc_id>/<method>.txt
    summaries/<doc_id>-<model>.json
    pdfs/<doc_id>.pdf
  retained/         # irreproducible — never collected
    snapshots/<run_id>/<n>.{html,meta.json}
    artifacts/<run_id>/{plot.py,data.csv,figure.png}
  jobs/
    <job_id>/plan.json          # single writer, temp+rename
    <job_id>/notes.jsonl        # single writer, append
  traces/
    <date>.jsonl                # append; queried with DuckDB
```

| Consumer | Store | Nature |
|---|---|---|
| Blobs | filesystem, two namespaces | directories, not infra |
| Plan + notes | one dir per job | ~250 records, single writer, ~125KB |
| Traces | JSONL + DuckDB | greppable when on fire, SQL when analysing |

**Nothing new.** Qdrant stays the corpus chunk index — notes retrieve
structurally, so they never need vectors. Neo4j stays the citation graph (a data
*source* for snowballing, not part of the store). No new database, no server, no
schema migration.

DuckDB isn't infrastructure — it's `pip install duckdb`, then
`SELECT ... FROM 'traces/*.jsonl'`. The eval imports a library. That's the whole
trace query layer, and it's what makes the confabulated-DOI audit (§4.1) a SQL
query rather than a project.

**The payoff: §4.2's entire lifecycle policy becomes `find` and `rm`.** LRU on
`derived/` is `find -atime` plus a size check (relatime is the Linux default and
refreshes atime once it's >24h stale — exactly LRU's granularity here).
`retained/` is never touched. Abandoned jobs are an mtime check and `rm -rf`. No
refcounting, no GC, no reachability analysis. That the policy collapses into two
cron lines is the test that the regenerable/irreproducible cut was the right one.

JSONL also wins the crash case on legibility: a job dying mid-append leaves a
partial trailing line; resume drops it and continues. The failure mode is visible
in a text editor.

#### Handles and writes

The **handle is issued by the store, not the agent**, and the write completes
before the Paper call returns — otherwise a note-write failure loses an LLM call
you already paid for.

#### Concurrency: there isn't any

An earlier draft designed for "notes: append-only, many writers." There are no
many writers. **Paper is a tool call** — parallel Paper calls fan out from the
research loop and their results *return to the loop*, which converts them to
notes and appends. The loop is the single writer for both plan and notes.

What that draft conflated: Paper writes *blobs* (extracted text, extract-mode
CSVs) concurrently — but those are distinct paths under `derived/`, so there's no
conflict there either. Plan writes are temp+rename; notes are append. Done.

#### Note retrieval, and what it constrains

25 papers × 5-10 notes is 150-250 notes — too many for one section draft. So
retrieval matters, and the cheap option is the right one: **retrieve by
`sub_question_ids`, not by embedding similarity.** The plan already exists, so
note selection is a structural lookup — no vector search, no ranker, no
retrieval-miss failure mode. (Second time plan-as-data-structure has paid for
itself.)

Notes carry `sub_question_ids[]` — plural, many-to-many. A note from paper X can
bear on sub-questions 2 and 5.

**Consequence, and it should be a decision rather than an accident: if sections
retrieve by sub-question, the outline is not free-form.** Sections map onto the
plan. The synthesiser can order and merge them but cannot invent a section with
no sub-question behind it — there would be no notes to draft it from. This is
good: it's what makes every section traceable, and it makes the outline step
semi-pinned instead of a free-form write. But it constrains the output document's
shape.

#### Lifecycle

The blob/job-state axis is the wrong one for eviction. The real cut is
**regenerable vs irreproducible** — and it's already recorded, because
`source.origin` (§5) encodes it exactly:

| `origin` | Derived artifacts | Why |
|---|---|---|
| `local_kb` | **freely evictable** | the PDF is still in the corpus; re-extract costs latency |
| `oa_download` | evictable, conditionally | re-fetch works *only while egress is on* |
| `web` | **never evict** | the snapshot is the only copy that will ever exist |

Regenerability is a property of the *source*, not the blob. Extracted GROBID
text, chunk embeddings, cached summaries — all derived from something that still
exists, all disposable. A web snapshot is derived from something that may
already be gone. They are indistinguishable in the store (a CSV is a CSV), which
is why the class must be tagged at write time and cannot be reconstructed later.
It isn't a new field: it's `origin`, riding along for a second reason.

Then the magnitudes make the policy trivial (order-of-magnitude, 10k-paper
corpus):

- *Regenerable*: extracted text ~1GB, chunk embeddings ~1.6GB, cached OA PDFs
  ~20GB.
- *Irreproducible*: web snapshots ~1.5MB per research run, delivered artifacts a
  few hundred KB, completed-job notes ~125KB per run, eval traces a few MB per
  Track D pass.

The irreproducible class is **two orders of magnitude smaller**, and it's the
precious one:

> **LRU + size cap on the regenerable class. Never collect the irreproducible
> class.**

This kills refcounting — it only pays if you're reclaiming space, and the class
worth reclaiming from is the one you can re-derive. Nice inversion: the stuff
that looks like junk (caches) is the big stuff and is safe to drop; the stuff
that looks precious is tiny and should just be kept.

**One genuine TTL: abandoned jobs.** A run that dies at minute 18 and is never
resumed leaves a plan and notes nobody wants. Resume window ~7 days, then
collect. Completed jobs whose document was delivered keep their notes — that's
the document's provenance, and it's 125KB.

### 4.3 Corpus scope, egress, and isolation

#### The corpus is consortium-wide; sub-corpora are a relevance filter

**Decided.** The corpus spans the consortium. Users narrow to a sub-corpus with
a slash command (`/elgeti`), because a consortium-wide corpus carries many
papers irrelevant to any one group.

The consequence that matters: **a sub-corpus is a relevance filter, not a
security boundary.** Everything is visible to everyone; scoping is about signal,
not access. This simplifies a lot — notably, the summary cache can key on
document id alone. (Under a per-group corpus it would have been an
exfiltration channel: ask for a summary of a paper you can't see, get one back
instantly from cache. Not a problem here.)

Sub-corpus membership is a **label set, not a partition** — a paper can be
relevant to several groups, so it's many-to-many, applied at ingestion.

Implementation: `/elgeti` sets a **session-level default** that the router
passes into every Search and Paper call (v1's §B already had
`filters?(year, corpus)` — this is that field). It rides in the trace, because a
result is uninterpretable without knowing what scope produced it.

**Open, and it's the interesting one — scoped abstention.** If a user is in
`/elgeti` and the answer isn't there but *is* in the consortium corpus,
`not_found` is actively misleading: it reads as a corpus gap when it's a
scoping artifact. The four-way outcome (§5) needs a fifth case, or Search's
`coverage_note` must say *"0 hits in /elgeti, 14 consortium-wide."* Cheap to
compute — the unscoped query is the same query — and it's the difference
between "we don't have this" and "you're not looking at it."

#### `corpus_scope` is a different control from `egress`

```
egress:       off | oa_only | full          # controls the network
corpus_scope: curated_only | curated+oa_cache | all   # controls provenance
```

**`egress: off` is necessary and not sufficient for certification.** The
property a certification run needs is *provenance* — prove the answer came from
the curated corpus. But a cached OA paper sits on local disk and answers the
question without touching the network at all; egress-off sails straight past it.
Separate namespaces (below) keep the distinction *visible*; visible is not
excluded.

→ A private-corpus benchmark run forces **both** `egress: off` **and**
`corpus_scope: curated_only`.

#### Egress

- A research query *is* the group's research interest — competitively sensitive
  even though it isn't data.
- **Downloaded/fetched content caches into a separate namespace, not the
  curated corpus.** Otherwise "local corpus" silently accretes internet PDFs and
  the private-corpus claim quietly stops being true. `origin` in the envelope
  keeps the distinction visible at query time; namespaces keep it true at rest.
- Web content is volatile: **snapshot at read time** (content hash, timestamp,
  stored text). An unsnapshotted web source makes a certification re-run
  incomparable, and you'd misread the delta as a model regression.

#### Isolation

- **Traces are privileged-read only. Decided.** A trace contains the query, and a
  research query is the group's competitive interest — so traces are more
  sensitive than the artifacts they explain. Eval and admin read them; users read
  their own; shared scorecards carry aggregates only.
- **Handles: unguessable ids, default private.** Capability-style access almost
  for free, which is the right default when a job handle crosses a session
  boundary (Research runs detached; the router polls). A share model is worth
  considering later — a 20-minute run is expensive enough that a colleague
  re-running it is real waste.
- **Sandbox: Apptainer container per run** (§4.4), scratch torn down after, image
  read-only and shared. Blast radius is bounded to the artifacts the requesting
  user already gets — *because network is off*. The control that exists for
  RQ-M1's no-data-leaves-premises claim pays for the multi-tenancy story for
  free.

### 4.4 Deployment topology

```
  user ── VPS (frontend) ──network── box (Slurm, GPU, store, LLM)
```

**One box, running Slurm, with a VPS in front for the frontend.** Two
consequences, and the first is a large simplification.

#### Slurm is the job infrastructure

§2 called kick-off → poll → handle "the one piece of genuinely new orchestration
infra." Mostly retracted: **Slurm already is that.** `sbatch` returns a job id;
`squeue`/`sacct` is the poll. What's left to build is the *plan* semantics
(§8), not the job machinery.

It maps almost item-for-item onto controls this design already specified as
config-not-inference:

| Design control | Slurm mechanism |
|---|---|
| Deep Research: one run at a time, queue the rest | QoS / partition `MaxJobs=1` |
| 20-min budget, hard floor (§8) | `--time=00:25:00` |
| Compute `quick` vs `compute` tiers (§7) | `--time` per tier |
| Compute core throttle / BLAS threads (§7) | `--cpus-per-task` + cgroup, already enforced |
| Cost/latency per run for the AstaBench-style Pareto | `sacct` — free, already recorded |

That last one is worth noticing: the briefing wants cost-aware Pareto
evaluation, and Slurm accounting is already collecting exactly that data.

**But the row that was in this table and is now gone: "Research priority below
chat."** That is *not* a Slurm control — see §4.5. Slurm schedules jobs. LLM
contention is inside vLLM, and Slurm has no visibility into it.

**What Slurm does not give you:** network isolation. `--cpus-per-task` and
`--mem` are cgroup-enforced, but network-off is not a Slurm concept. The
Compute sandbox therefore wants **Apptainer** (the Slurm-native container
runtime — no root daemon), with the network namespace disabled there. The
verifier ladder and resource tiers are Slurm's; the isolation is Apptainer's.

**One thing to check:** *queue latency on the interactive path.* A 20-minute
Research job queuing is fine. A `quick` Compute plot (~15s) queuing behind one is
not — the user is waiting in a chat turn. Wants a small dedicated partition with
reserved cores for interactive Compute, so it never lines up behind a research
run.

#### The VPS boundary validates handles

The store lives on the box, next to the agents and the GPU. **The VPS never
mounts it** — a handle crosses the network, and bytes are served on demand
through an endpoint. That's the handles-not-payloads design (Principle 1)
arriving as a deployment fact rather than a preference. `retained/artifacts/`
hands off to whatever the existing artifact path already does; that's the one
place this touches existing plumbing.

#### Open: is the VPS on-premise?

RQ-M1's claim is that **no data leaves the premises**. Every chat message,
document, and artifact traverses the frontend VPS. If that VPS is rented from a
cloud provider, the claim needs to be scoped precisely — the *corpus* and
*inference* stay on-prem, but queries and outputs transit a third-party host.
That may be perfectly defensible and it may already be an on-prem VM; it is not
currently written down anywhere, and it is the kind of thing a reviewer of the
M1 claim will ask about. Worth one sentence in the proposal either way.

### 4.5 The inference layer (vLLM) — where contention actually lives

**Decided: vLLM serves the model, and vLLM itself runs as a Slurm job.**

This resolves one question and opens sharper ones, because it means **Slurm
allocates the GPU to vLLM exactly once, and every subsequent scheduling decision
belongs to vLLM's scheduler, which Slurm cannot see.** Slurm gates *job
admission*. vLLM gates *request service*. They are different resources, and some
of this design's throttles were written against the wrong one.

#### What this improves

vLLM does **continuous batching**, so a research run does not *block* chat — it
joins the batch. §8's "one run is effectively an outage for interactive users" is
too strong: degradation is graceful, not binary. The toggle and the concurrency
cap survive, but for a subtler reason than stated.

#### What this breaks

**The Paper fan-out is not free, and an earlier draft said it was.** Concurrent
Paper calls are bounded by **KV cache**: 25 concurrent calls × 15-35k tokens of
prefill each is a large, spiky allocation, and exceeding the cache makes vLLM
preempt and recompute — worse than not fanning out at all.

**But the honest answer is probably that fan-out isn't needed.** Rough math on a
35B-A3B (3B active) model: a 30k-token prefill is seconds, a ~500-token answer
decode is seconds more — call it ~10s per paper. **25 papers sequentially ≈ 4-5
minutes**, comfortably inside a 20-minute budget. Sequential costs wall-clock the
budget can afford, and buys: no KV spike, no chat starvation, no semaphore, no
tuning. Long calls are just queries, one at a time.

→ **Default to sequential.** Treat concurrency as an optimisation to reach for
only if measured wall-clock demands it, and size it from the KV numbers in the
checklist below rather than from the screener's output count.

**Prefill-heavy meets decode-heavy.** Paper calls are enormous prefills; chat is
decode. Without `--enable-chunked-prefill`, a 35k-token prefill stalls the
decodes in flight — the chat user sees their stream freeze. This is the concrete
mechanism behind "research degrades chat," and it has a specific config fix.

**The throttle must be client-side or vLLM-side, not Slurm-side.** `MaxJobs=1`
caps research *jobs* at one. That one job can still fire 25 concurrent requests
at vLLM. Two fixes, and they compose:
- **vLLM request priority** (`--scheduling-policy priority`), chat high, research
  low — the principled version.
- **A client-side semaphore** on the research job's concurrent requests (e.g.
  ≤4), leaving batch headroom for chat — the version that works regardless of
  vLLM's config.

**Decided: one model, one vLLM instance, every call.** vLLM's concurrency exists
precisely so the harness can share the hosted model — a long Paper read is just
another query on the same endpoint. This **closes open decision #8**: there is no
model-per-mode. §5's "smaller model for summary" and §8's "small model for the
screener" are struck.

That removes a lever, so name the ones that replace it. With model size fixed,
cost is **call count × context length**, and every remaining lever is one of:

| Lever | Where |
|---|---|
| **Cache summaries by doc id** | §5 — a repeat summary should be zero LLM calls. Now the *only* summary-cost lever, so it matters more. |
| **Batch the screener** | §8 — ~100 abstracts × ~200 tokens is one ~20k-token call, not 100 calls. |
| **Keep the screener on abstracts** | §8 — the funnel's whole point: short context at the wide end, long context only at the narrow end. |
| **`--enable-prefix-caching`** | If every Paper call shares a system-prompt prefix, vLLM caches it across requests. Free, if it's on. Added to the §4.5 checklist. |

**vLLM's own walltime is a liveness dependency.** If vLLM runs under a Slurm job
with a `--time` limit, the endpoint dies when it expires. A 20-minute research
run straddling that boundary fails mid-flight — which makes §4.2's
checkpoint-and-resume load-bearing rather than a nicety.

#### Open — answer these from the vLLM launch config

Find the `sbatch` script or launch path for vLLM and read the serve arguments:

- **`--max-model-len`** — does it fit the largest paper + prompt? This is the
  *actual* number behind §2's "chunking is an overflow fallback." The overflow
  threshold is not an abstraction; it is this flag.
- **`--enable-prefix-caching`** — on? If every Paper/screener call shares a system
  prompt prefix, this is free cross-request savings, and it is now one of the few
  cost levers left (one model, §4.5).
- **`--gpu-memory-utilization` and `--max-num-seqs`** — together these bound how
  many concurrent long-context requests are servable. Only needed if sequential
  Paper reads prove too slow; compute it, don't guess it.
- **`--enable-chunked-prefill`** — on or off? If off, that is the chat-freeze
  mechanism, and enabling it is a one-line mitigation.
- **`--scheduling-policy`** — is priority scheduling available, or FCFS? If FCFS,
  the client-side semaphore is the only lever.
- **`--max-num-batched-tokens`** — interacts with chunked prefill on the
  prefill/decode tradeoff.
- **Is there a `--time` on vLLM's Slurm job**, and what restarts it?
- **Is it one vLLM instance serving one model?** Determines whether #8 costs a
  GPU.
- **Does the client set request priority today**, or is every call equal? Look for
  the OpenAI-compatible client setup / `base_url` config.

---

## 5. Agent: Paper

**Intent** — read 1..N documents and return a grounded result.

```
paper(refs[], mode: summary | qa | extract | compare,
      question?, focus?, schema?)
```

| refs | mode | returns | replaces |
|---|---|---|---|
| 1 | `summary` | summary + key findings | today's `read_paper` |
| 1 | `qa` | answer + quotes | **the bottleneck fix** |
| N | `summary` | multi-summary / digest | (new) |
| N | `compare` + focus | structured comparison | today's `compare_papers` |
| 1..N | `extract` + schema | data handle | (new — feeds Compute) |

One tool where there are currently two-plus-a-planned-third. The outer model
still picks a mode, but via a **typed, loggable argument** rather than by
choosing between tools — the branching factor doesn't grow. They all share one
interior, which is why the merge is nearly free.

**Interior** — Pinned: `resolve → extract → LLM call`. Full text in one call;
chunking only as an overflow fallback (§2).

**Envelope**

```
{ ref_resolved: {internal_id, doi?, arxiv?, title},
  source: {origin: local_kb | cache | oa_download | web,
           url, extraction_method},
  outcome: resolved | ambiguous(candidates[]) | extraction_failed | not_found,
  trace: {...},
  ...mode-specific:
    summary → {summary, key_findings[]}
    qa      → {answer, supporting_quotes:[{quote, section}], confidence}
    compare → {comparison, failed[]}
    extract → {handle, schema, n_rows, preview[5], provenance} }
```

**The download link is free — it's a field, not a call.** The agent already
resolved the paper in order to read it, so `source.url` always rides along.
This puts v1's "PDF/download-link resolution stays a plain tool" in tension
with Paper owning resolution: resolve it by keeping the standalone primitive
only for the genuine "find me the PDF, don't read it" intent (which is
Search-shaped anyway).

**The four-way outcome is the important part.** With a KB in the loop,
`found=false` overloads three distinct failures and destroys information both
the router and the eval need:

| Outcome | Meaning | Caller should |
|---|---|---|
| `not_found` | read it fine, answer isn't in it | accept the negative |
| `extraction_failed` | resolved but unreadable (scanned PDF, GROBID failure) | flag for ingestion |
| `ambiguous` | fuzzy ref matched several | disambiguate |
| `out_of_scope` | exists consortium-wide, not in the active sub-corpus (§4.3) | tell the user to widen |
| — (resolution miss) | not in KB, not downloadable | *corpus gap* — try another ref |

The corpus-grounded abstention set measures exactly this distinction —
calibrated refusal vs confabulation vs plumbing breakage. As one bool it's
unscoreable and the certification threshold is meaningless.

**Abstain contract** — quote the exact supporting sentence or return
`not_found`. This is the precision guard: the oracle showed raw text can also
mislead (1/11), so quote-or-abstain matters. It doesn't prevent quoting a real
sentence and misreading it.

**Extract mode deserves its own note.** It returns `{handle, schema, n_rows,
preview[5], provenance}` and **not the rows**. That isn't a policy — it's
enforcement by construction: the outer model cannot transcribe values it never
held. Five preview rows let it narrate the schema to the user without being
able to rebuild a 200-row plot. This is also why the mode split earns its keep:
`qa` returns a number in prose because it's human-facing and the router *should*
see it; `extract` is machine-facing and the router *shouldn't*.

**One model, all modes.** (Earlier drafts proposed a smaller model for summary;
struck — there is one vLLM instance serving one model, §4.5.) This was going to
be justified anyway: the evidence says `qwen3.6-35b-a3b` answers correctly given
the text, and there is no evidence a smaller one does, so QA and extract were
never going to move. The summary lever is therefore **caching, not model size** —
cache by document id (a repeat summary should be zero LLM calls; the key is the
id alone, safe because the corpus is consortium-wide, §4.3).

**Eval seam** — re-run the Track D agentic arm on the 20 over-abstention
questions; measure abstain→correct conversion and precision cost. The 0.82
full-text oracle is the ceiling.

**Note:** the current eval hands over a known source, so it tests precision
*given the right paper*. Nothing in the suite currently tests whether the right
paper was fetched — which is why §4.1's DOI failure mode is invisible today.

**Status** — ~80% of parts exist (`read_paper` extraction path,
`document_store.chunk_text`, `database.get_bge`). Essentially PaperQA2's core
move.

---

## 6. Agent: Search

**Intent** — find and rank the evidence relevant to a topic.

```
search(query, filters?(year, corpus), depth?, egress?)
→ { ranked: [{ref, title, snippet, score, source_type}],
    coverage_note, thin_evidence: bool, trace }
```

**Owns** `paper_search` (local, SPECTER/BGE), `semantic_scholar_search`,
web search, multi-query fan-out, dedup-on-alias-set, ranking, and an honest
coverage note ("local corpus thin on X, branched to S2").

**Interior** — Semi-pinned: fixed fan-out + dedup skeleton, one bounded LLM
step to phrase sub-queries.

**`source_type: corpus_paper | oa_paper | web` is load-bearing.** Trust isn't
uniform, so a single similarity score can't rank across tiers — a Nature paper
and an SEO-farmed listicle are not comparable on embedding distance. Consumers
need **tier-aware quotas** (cap web at N, prefer corpus). Without them, web
wins on volume and shallow lexical match.

**Abstain contract** — explicit `thin_evidence` flag rather than padding with
weak hits.

**Eval seam** — LitQA2 retrieval recall@10, already in the suite, becomes this
agent's metric directly.

---

## 7. Agent: Compute

**Intent** — spec + data → verified code + artifacts.

```
compute(spec, data_handle?, language, budget?: quick | compute)
→ { code, language, verify_level: executed | compiled | parsed,
    ran, artifacts: [{type: figure|table|data|stdout, ref}],
    attempts, errors, trace }
```

**Scope, honestly:** mostly plots. Serious users take the code to the API. That
reframe is load-bearing — it's why this is one rung, not a ladder.

**Routing is deterministic, not an LLM judgment.** `language == python` →
sandbox. Everything else → strongest static check that language offers. "Is
this something I need to sandbox?" would be a stochastic call inserted into a
pipeline whose point is auditability, and one the model has an incentive to get
wrong in the lazy direction.

### The verifier ladder

| Rung | Cost | Isolated? | Catches |
|---|---|---|---|
| Parse (`ast.parse`) | ms | no | syntax |
| Lint (ruff/pyflakes) | ~100ms | no | undefined names, bad signatures |
| Import / collect | ~1s | **yes** | — |
| Smoke run | seconds | yes | crashes, and *whether a figure exists* |
| Tests | seconds+ | yes | see trap below |

**The sandbox boundary falls between lint and import, not lint and test.**
`import foo` executes module-level code. There is no worthwhile static import
check.

**For plots, static rungs are nearly worthless.** A script that grabs the wrong
column, mislabels an axis, or plots against the index lints perfectly clean.
The only check that means anything is "did it run and produce a figure."

**Per-language guarantees genuinely differ, and the envelope must say so:**

| Language | Check | Strength |
|---|---|---|
| Python | `ast.parse` + ruff, then sandbox | `executed` |
| C++ | `g++ -fsyntax-only` | **`compiled`** — full type checking, no link, no run |
| R | `parse()` + lintr | `parsed` |

The C++ rung is *stronger* than Python's static rung. A user receiving R must
not read it as carrying the guarantee that the executed Python does.

### Environment

**One broad scientific image**: numpy, scipy, pandas, matplotlib (Agg),
seaborn, sympy, sklearn, statsmodels. Pinned, prebuilt, **no pip install
ever**. The fixed env buys reproducibility, a near-nil security surface, and —
usefully — **a real static gate back**: an import allowlist can reject
`import tensorflow` before spending a subprocess. Impossible in the general
case; the narrow scope makes the cheap check work.

**Multiple images only on a hard dependency conflict.** Splitting by discipline
means N images to drift, N routing decisions. Splitting by runtime class is
unnecessary — one image, two *resource profiles*.

**Budget tiers, not one wall clock.** `quick` (~15s, the plot case, default) /
`compute` (~5min, opt-in). A `scipy.optimize` run or an MCMC legitimately takes
minutes; a hard 10s makes "real scientific computing" into "plots with more
imports installed." Tiering by declared intent keeps it deterministic. Never
unbounded.

**BLAS threading is the GPU-protection lever, not core count.** numpy/scipy
link OpenBLAS/MKL and will grab every visible core — one unbounded
`np.linalg` call eats all 20. Pin `OMP_NUM_THREADS` / `OPENBLAS_NUM_THREADS` /
`MKL_NUM_THREADS`; set a cgroup CPU quota. **This replaces the on/off switch:
throttle to 1-2 cores when the cluster goes LLM-only rather than shutting the
agent down.** Config, not code. (A smoke run is one subprocess on one core with
a timeout — the 20 cores only matter for real test suites or fan-out.)

**Sandbox controls:** network **off** (not hygiene — part of the
no-data-leaves-premises claim; a code sandbox with egress is a hole in RQ-M1),
read-only FS + one scratch dir, memory cap, pid cap, wall clock.

### Repair loop

Bound at 2-3 attempts. **Hash the error signature** (exception type + final
frame); if the identical error recurs, stop immediately — the model is stuck,
not converging. Return `ran=false` with the traceback. A code agent silently
grinding 15 repair attempts is the opacity problem, relocated.

**The self-authored-test trap:** if the agent writes its own tests, the repair
loop optimises against a test the same model authored, and converges on green
by weakening the test. Tests come from the spec or a separate generation step
that never sees the repair loop — otherwise `passed=true` means nothing.

### Returned code

Both figure **and** code come back — they want the script for a repo or a
publication.

- **Byte-identical to what ran.** If the agent tidies the script before
  returning it, the human verifies a figure produced by code they didn't
  receive, and the semantic-verification loop is quietly broken.
- **Standalone-runnable:** own data loading, explicit path, not `df` assumed
  in scope from the harness.
- **Seeded RNG**, or the figure isn't reproducible from the code handed over.

**The data file ships back too — this is a bundle, not a script.** Otherwise
"standalone-runnable" and "materialised into the sandbox scratch dir" contradict
each other: the script that ran points at `/scratch/data.csv`, which evaporates
when the container exits, and it dies on line 1 in their repo — the exact failure
standalone-runnability exists to prevent. So deliver three artifacts —
`plot.py`, `data.csv`, `figure.png` — where the script reads `data.csv` from its
own directory. That's the only version where *byte-identical to what ran* and
*standalone-runnable* are simultaneously true, and it's the reproducible bundle a
publication actually needs.

This retroactively justifies extract mode more than the plotting case did: the
data file landing in their repo carries `value, unit, ref, quote_id` columns.
**Provenance rides into the delivered artifact.** That's a research-integrity
property, and it falls out of a decision made for an unrelated reason.

**Residual, named:** runs ≠ correct. Execution catches crashes, not wrong axes.
Unlike the table-extraction residual, this one has a legitimate out: **the human
is the semantic verifier, and returning the rendered figure via the artifact
system is what lets them be.** A wrong plot is obvious at a glance in a way a
wrong buried number never is. That's why the figure must come back, and why
this agent needs no grounding contract in the paper sense.

### Where data comes from

| Source | Literals OK? | Why |
|---|---|---|
| User typed it in chat / uploaded | **yes** | provenance *is* the conversation |
| Script generates it (`linspace`, simulation) | **yes** | not data |
| **From a document** | **no — handle only** | see below |

A blanket ban is unenforceable (`np.linspace(0,10)` is literals; `sigma = 1.4`
is literals; syntax can't distinguish data from parameters). A blanket allow
leaves the hole. The rule isn't about literals — it's about **origin**.

If the flow is "model reads a paper, model types `y = [1.4, 2.7, 3.1]` into a
script," there is a transcription step happening inside the outer model's head:
not a tool call, not logged, not evaluable — an invisible step in the middle of
a figure that may end up in a publication. Extract mode's handle-only return
(§5) makes it impossible rather than forbidden.

---

## 8. Agent: Deep Research

**Intent** — open question → composite document synthesised from 100+ sources.

```
deep_research(question, egress?, budget?)      [toggled, job-based]
→ job_handle → poll
→ { document, plan[], citations: [{ref, source_type, read_depth}], trace }
```

**There is no "normal research" tier.** Once the router has Search and Paper as
fat tools, "search, screen a few hits, read three papers, answer in the turn"
*is* the outer loop doing its job. A separate agent for it would have the same
interior as the router, dispatched by the router, returning to the router. Pure
ceremony. The boundary that's real isn't depth-of-thought — it's **whether the
work fits in a chat turn.**

**The toggle does more than gate a long wait:**

- It removes a routing decision from the model (same principle as
  `verify_ceiling` and the funnel widths).
- **It's the GPU scheduling lever.** 35+ LLM calls over 20 minutes on a shared
  endpoint degrades chat for everyone. **Concurrency cap 1, queue the rest** —
  though note the cap is a Slurm control while the actual contention is vLLM's
  (§4.5), so it needs a client-side request semaphore to bite. The toggle is what
  makes the degradation acceptable — someone deliberately asked for the expensive
  thing.
- **Off-but-needed:** the router says *"this needs Deep Research, enable it"*
  rather than attempting a bad shallow version. An abstain contract at the UI
  layer — refuse rather than fake.

### The plan is a data structure, not a thought

Decide this first; it determines whether Research is auditable at all.

```
plan: [ {id, sub_question,
         status: open | resolved | unresolvable,
         notes[], evidence_refs[]} ]
```

Loop: pick an open sub-question → Search → screen → Paper fan-out → update
status → maybe spawn new sub-questions.

If the loop is instead "model thinks, calls things, thinks again," you have
rebuilt the flat loop — the exact opacity that started the diagnosis, now
longer and more expensive.

This makes the agent **semi-pinned per iteration**: the free part shrinks to
"what are the sub-questions, and when do I add one" — which is where the genuine
iteration lives anyway (reading paper A changes what you search for in B).
Everything else is fixed skeleton.

Three things fall out for free:

- **Stopping criterion:** every sub-question resolved or unresolvable. Budget
  exhaustion is a hard floor, not the rule.
- **"Surfaces unresolved sub-questions rather than papering over them"** becomes
  implementable — you can only surface them if they're objects.
- **Checkpointing:** plan + notes store *is* the job state. A run that dies at
  minute 18 resumes instead of restarting.

### The funnel

```
~100+ retrieved → screen → ~25-30 full reads → notes → outline → sections
```

**100+ sources cited ≠ 100+ read.** Claude's research doesn't do that either —
many snippets, few full texts. So depth rides in the citation:
**`read_depth: full_text | abstract | snippet`**. A document citing 100 sources
where 70 were only ever abstracts should *say so per citation* — otherwise
"synthesised from 100 sources" overstates what happened and a reader can't tell
which citations to trust. Same instinct as `verify_level`.

It also gives the screener a middle option instead of a binary: a source can be
cited from its abstract without paying for a full read. That's how you reach
100+ citations inside 20 minutes.

**Funnel widths are config, not agent-chosen.** That's what makes cost
predictable (below).

### The screener is asymmetric — and nobody said so in v1

Cheap batched triage over titles/abstracts. Cheap because of **context length and
batching, not model size** — ~100 abstracts at ~200 tokens each is one ~20k-token
call on the same model, not 100 calls on a small one (§4.5).

| Error | Cost | Visible? |
|---|---|---|
| False positive (screen in a bad paper) | one Paper call | yes, recoverable |
| **False negative** (screen out the right paper) | **unrecoverable** | **no** |

A false negative is silent: the paper was in the corpus, it was retrieved, and
the answer simply never appears. It looks exactly like a corpus gap.

→ **Tune for recall, hard. Err permissive. Log every drop and why.** Otherwise
the most consequential decision in the pipeline is the least visible one.

### Grounding is three hops now

`quote → note → section prose → document`. Each hop is lossy; the last two are
inside the synthesiser.

**Rule: the synthesiser drafts from quotes, never from paraphrase-of-
paraphrase.** Notes carry the quote and the ref alongside the claim; every
sentence in the composite traces back to a `ref_resolved`. If a note is a
summary of a summary, the citation is decorative.

**Sections retrieve notes by `sub_question_ids`, not by similarity (§4.2)** — so
the outline is not free-form. Sections map onto the plan; the synthesiser orders
and merges but cannot invent a section with no sub-question behind it. That's
what makes every section traceable, and it makes the outline step semi-pinned
rather than a free-form write.

### Contradiction is a schema question

Thirty papers will disagree, and for a research audience the disagreement is
often *the finding*. A document that silently averages them is worse than one
that names the split.

But you can only surface it if the notes store can represent it. Flat prose
notes make contradiction invisible — the synthesiser would have to happen to
see two of them in the same window, which it won't when drafting section-wise
from retrieved notes. Structured notes make it mechanical:

```
note: {claim, value?, unit?, quote, ref, sub_question_id}
```

Conflicting claims on the same `sub_question_id` are detectable; hand the
synthesiser both with an instruction to report the split.

**Open:** how much structure per note. Too little and contradiction is
undetectable; too much and you re-litigate extract mode's schema for every
sub-question.

### Snowballing (this is where Citation-graph went)

Following references from seed papers is the highest-value deep-research move
and the easiest way to blow the budget — 40 refs each, transitively.

**Depth 1 for v1** (references of seed papers only), routed **back through the
screener**, never read directly. Depth 2 needs measurement first.

### Cost

1 search fan-out + 1 screen (batched, one call) + ~25 Paper calls + outline +
~6 section drafts ≈ **35 LLM calls**. A budget, not a mystery — and only
predictable *because* the funnel widths are config.

**Run the Paper reads sequentially** (§4.5): ~10s each puts 25 papers at ~4-5
minutes, well inside a 20-minute budget, with no KV-cache spike and no risk of
starving chat. Concurrency is an optimisation to reach for only if measured
wall-clock demands it — an earlier draft called parallel fan-out a free win, and
it isn't free and probably isn't needed.

### Eval seam — the weakest in the roster

Every other agent has a clean isolation metric: Paper has the over-abstention
set + 0.82 oracle, Search has recall@10, Compute has "did it run." **Research
has no automatic score** — you can't grade a composite document with exact
match. LLM-judge or human rubric: expensive, noisy.

Prior art before building from scratch: **AutoResearchBench** (frontier agents
<10% on hard splits), **ScholarQA-CS2**, **ArxivDIGESTables** (AstaBench).

**This means v1's §8 promise — "each agent ships with its isolation metric
wired into the certification gate before it goes live" — is one Research cannot
currently keep.** Either the gate takes a different shape for this agent, or
Research ships last, after the pieces underneath it are certified. Which is the
right build order anyway.

---

## 9. Orchestration

The outer model becomes a **router/planner + synthesizer** over 4 fat agents +
2 plain tools, instead of a driver of ~7 low-level primitives. Same shape as
the shipped persona→router migration, one level up.

**Still plain tools** (deterministic, single-shot, no internal reasoning):
`faq`, raw `web_search` for a current fact, standalone link resolution.
Wrapping these adds ceremony with no plumbing payoff.

**Dispatch:**

- **Paper / Search / Compute** — fat MCP tools with typed contracts and isolated
  internal context. Reuses existing tool-dispatch machinery; matches the
  `compare_papers` precedent.
- **Deep Research** — job-based: kick off → poll → handle. The one new
  orchestration path.

---

## 10. Build order

```
1. The store + Paper        ← the measured bottleneck; both spines need the store
2. Search                   ← recall@10 already exists as its metric
3. Compute                  ← independent spine, doesn't touch the literature stack
4. Deep Research            ← depends on 1+2; eval seam weakest, so it ships last
```

---

## 11. Residuals this design does NOT fix

**Table and figure extraction.** The oracle hit 0.82 on GROBID text that was
regex-stripped to flat number-soup, so **access is still the bigger lever than
extraction**. A specialised agent improves access, not extraction. Values
living only in a rendered table or figure panel are where even PaperQA2 tops out
(~0.66). Keep the two levers distinct.

**But there is a cheap first rung sitting right there:** you are regex-stripping
GROBID's TEI into flat text, discarding table structure **GROBID already
parsed**. That's not a new vision pipeline — it's not deleting work that's
already done. Because Paper's interior is pinned, this is a **drop-in swap of
the extract step**: contract unchanged, delta measurable. Ship extract mode with
the current step, *measure* whether residual misses are table-bound, then swap.
That is exactly what pinning bought.

**Plot correctness.** Runs ≠ correct. Mitigated, not fixed, by the human as
semantic verifier (§7).

**Research evaluation.** No automatic metric (§8).

**Retrieval of the wrong paper.** The current eval hands over a known source, so
the confabulated-DOI failure (§4.1) is structurally invisible to the suite
today. The tagged-ref audit makes it *measurable*; it doesn't make it go away.

---

## 12. Open decisions

1. **Scoped abstention** (§4.3) — fifth outcome, or `coverage_note` reporting
   *"0 in /elgeti, 14 consortium-wide"*? Currently the sharpest open item: a
   scoping artifact rendered as `not_found` is a lie the user will act on.
2. **Name: `paper` vs `document`.** Dropped for ELN (not merging next), but web
   sources reintroduce the same pressure from a direction you *do* want —
   `paper(refs[])` reading a blog post is a lie in the signature. Cheap now,
   annoying later.
3. **Chunking.** Must be justified against the full-text baseline on cost, not
   assumed. Overflow fallback regardless.
4. **Note granularity** (§8) — the contradiction/schema tradeoff.
5. **Research's certification gate shape** — different gate, or ships last
   uncertified?
6. **Depth-2 snowballing** — after measurement.
7. **Second sandbox image** — only on a real dependency conflict. Which one
   forces it?
8. **`extract` schema authoring** — who writes the schema, the router or the
   user? Never discussed. If the router writes it, it's inventing structure for
   data it hasn't seen; if the user writes it, extract mode isn't callable
   mid-research-run, which quietly removes it from Deep Research's toolkit.
9. **Is there already a relational DB in the stack?** — *investigation task,
    answer from the code, not from memory.* The §4.2 layout assumes **no**: files
    + DuckDB, no new database. If Postgres/MySQL is already deployed, then
    `jobs/` and `traces/` as tables is arguably tidier and free, and the layout
    should change. **If there isn't one, do not add one for this** — adding a
    database to hold ~250 records per job is exactly the "making it clever"
    failure §4.2 warns against. Where to look:
    - `pyproject.toml` / `requirements*.txt` / `poetry.lock` — `psycopg`,
      `asyncpg`, `sqlalchemy`, `alembic`, `sqlmodel`, `peewee`, `sqlite3` usage
    - `docker-compose*.yml` / `*.env` / helm or k8s manifests — a `db:` service,
      `DATABASE_URL`
    - `alembic/` or `migrations/` directories — presence means a real schema
      already exists
    - **what is actually behind `document_store`** — the only evidence of it in
      these design docs is `document_store.chunk_text`; find the module and see
      whether it's Qdrant-only, or Qdrant + a metadata DB
    - **what `database.get_bge` is a member of** — a module named `database`
      implies something; find out what
    - the Neo4j and Qdrant connection setup, to confirm those are the only two
      stateful services
10. **The vLLM serve config** (§4.5) — *investigation task.* `--max-model-len`,
    `--enable-chunked-prefill`, `--enable-prefix-caching`, `--scheduling-policy`,
    `--max-num-batched-tokens`, `--gpu-memory-utilization`, `--max-num-seqs`, any
    `--time` on vLLM's own Slurm job, and whether the client sets request
    priority. These fix two numbers this design hand-waves — the chunking overflow
    threshold and whether chat freezes during a research run — and confirm whether
    prefix caching (now a primary cost lever) is on.
11. **Is the frontend VPS on-premise?** (§4.4) Scopes RQ-M1's
    no-data-leaves-premises claim. Not a code question — but it needs a sentence
    in the proposal either way.
12. **Interactive queue latency** (§4.4) — does a `quick` Compute job risk
    queuing behind a research run? Wants a small reserved partition if so.

### Decided during design discussion

- **vLLM serves the model and runs as a Slurm job** — so Slurm sees the GPU once,
  and all request-level contention is vLLM's (§4.5).
- **One model, one instance, every call.** No model-per-mode. Cost levers are
  caching, batching, and context length (§4.5).
- **Paper reads run sequentially** — ~4-5 min for 25 papers fits the budget; no KV
  spike, no semaphore (§4.5).
- **One box (Slurm) + a VPS frontend** — the store lives on the box; handles cross
  the network, bytes are served on demand (§4.4).
- **Corpus is consortium-wide**, sub-corpora via slash command are a relevance
  filter, not a security boundary (§4.3).
- **Traces are privileged-read only** (§4.3).
- **`corpus_scope` is a separate control from `egress`** — certification needs
  both (§4.3).
- **Compute delivers a bundle** (code + data + figure), not a script (§7).
- **LRU the regenerable class; never collect the irreproducible class** (§4.2).

---

## 13. One-line summary of the bet

Turn the flat, opaque, context-hungry tool loop into **four named agents that
pass handles instead of payloads and always say which guarantee they're
giving** — starting with Paper, because the eval already proved the answer is in
the text and the only thing missing is handing the model the right passage.
