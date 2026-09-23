# Egress-pair commit audit: Qwen3.8 full vs corpus-only

**Question.** Could anything between the Qwen3.8 full agentic arm
(`2026-08-26_harness-ablation.json`, git `3e0bcfb`) and its corpus-only arm
(`2026-09-15_harness-ablation-agentic-egressoff.json`, git `3be8308`) have
changed the agentic arm's behaviour, independently of egress?

**Answer: yes, and the confound is real, but it has already been measured away.**
Sixteen commits in the range touch code the corpus-only arm runs, two of them
new retrieval capability that did not exist on 08-26. **But the paper no longer
has to rest the egress decomposition on this pair at all**: the 2026-09-17
suite re-run produced a *zero-drift* egress pair on Qwen3.6-35B-A3B, both arms
at git `556b305` on the same day, and a near-clean one on gpt-oss-20b. See
section 4. The Qwen3.8 pair should be reported with its confound named and the
Qwen3.6 pair cited as the controlled measurement.

On the commit count: **75 commits separate the two shas**, of which **26 touch
`backend/retrieval/`**. `RESULTS.md:1103` states the 26 correctly and scoped;
`RESULTS.md:1635` restates it as "26 commits apart", which drops the scope. The
pair is 75 commits apart.

Read-only audit. Nothing was modified.

---

## 1. Range and totals

```
git log 3e0bcfb..3be8308      75 commits, 2026-08-27 .. 2026-09-15
```

| class | N | rule |
|---|---|---|
| documentation-only | 28 | only `*.md`, `docs/`, README, `docs/paper-kit/` |
| evaluation-harness-only | 12 | only `backend/benchmarks/`, `backend/retrieval/evals/`, or `**/tests/` |
| behaviour-affecting | 35 | any production file outside those |
| **of which: touch egress-off paths** | **16** | corpus ladder, source read, evidence re-ranking, abstention, prompts, tool surface, serving profile |

A note on the strict reading of the brief. Classifying "anything under
`backend/retrieval/`" as behaviour-affecting gives **41**, because six commits
touch only `backend/retrieval/tests/`. Test files do not ship, so they are
counted as harness above. Both numbers are given so the choice is visible.

Zero commits touch `requirements*.txt` or a lockfile. Two touch SLURM /vLLM
start scripts, one touches `backend/docker/docker-compose.yml`, two touch
`shared/personas/`.

---

## 2. The 16 commits exercised at egress = off

`search_agent.py` states the gate directly: `egress=off -> local corpus only;
oa_only -> +scholarly; full -> +web`. Everything below is on the local side of
that gate, or upstream of both arms.

| # | commit | date | what it changed | path |
|---|---|---|---|---|
| 1 | `fd559c9` | 08-27 | `executor.py` now coerces model-emitted argument types against the declared schema before validation, generically for **every** tool. | ladder + all tools |
| 2 | `ff713c9` | 08-27 | Raised vLLM to 12 CPUs and bounded the sandbox at 8. | serving profile |
| 3 | `4355fa4` | 08-27 | `_norm_corpus` read `abstract`, a key `paper_search` never sets, so **every corpus hit carried an empty snippet**; also put a relevance floor under `thin_evidence` and added 15 router examples for value lookups. | corpus ladder |
| 4 | `3018a0c` | 08-27 | Cut the research persona's resident tool set to `search`, `source`, `search_user_docs`. | prompts, tool surface |
| 5 | `51f1d5a` | 08-27 | Replaced the parallel fan-out with an **escalating ladder** (corpus alone first), added the `read=N` **grounded read stage** with a groundedness early stop, and fixed `source._parse_qa` reporting an empty completion as resolved rather than abstained. | ladder, read stage, abstention |
| 6 | `b978c0a` | 08-28 | Chunk-index backfill and its sizing probe. | corpus content |
| 7 | `96921b7` | 08-28 | Sliced chunk upserts by point count, not paper count. | corpus content |
| 8 | `993d48e` | 08-28 | Split the chunk build into a daytime extraction pass and a GPU embed pass. | corpus content |
| 9 | `3e2b181` | 08-28 | Marked full-text coverage and recovered missing PDFs instead of deleting entries. | corpus content |
| 10 | `587d93a` | 08-30 | Added `source(mode=evidence)`, **chunk-level retrieval from `papers_chunks`** with citable provenance, wired into `search`. | read stage, re-ranking |
| 11 | `871028f` | 08-31 | Evidence judge answers `[1]` about half the time and `_parse_scores` could not match a leading bracket, so **roughly half of real evidence queries silently discarded the judge call** and fell back to cosine order. | citation re-ranking |
| 12 | `a248cbe` | 08-31 | Made the evidence `scored` flag derive from `n_scored > 0` rather than from the call not erroring; added `n_scored` and a three-valued `judge`. | citation re-ranking |
| 13 | `1bff8a3` | 09-01 | `paper_search`'s merge sorted on variant agreement over similarity, reporting present papers as absent; now ranks on score with a bounded agreement bonus and a reserved slot. Same fix in `research.py::_merge_papers`. | corpus retrieval |
| 14 | `3571bc9` | 09-01 | Added `list_documents` and `browse_tag_papers` to the tool surface. | tool surface |
| 15 | `0137401` | 09-01 | Corrected `capabilities.py` / `faq.yml` self-description injected into the system prompt. | prompts |
| 16 | `4354220` | 09-02 | Added `--shard` to `build_chunk_index` so a rebuild uses both GPUs. | corpus content |

**Two of these are new capability, not repair.** `papers_chunks` does not exist
anywhere in `backend/retrieval/` at `3e0bcfb`:

```
git grep -l papers_chunks 3e0bcfb -- backend/retrieval/   ->  (empty)
git grep -l papers_chunks 3be8308 -- backend/retrieval/   ->  mcp/tools/source.py
```

The chunk index was built between 08-28 and 08-31 (commits 6 to 9) and the
evidence layer that reads it landed 08-30 (commit 10). The corpus-only arm
therefore had a **depth axis the full-egress arm never had**: chunk-level
retrieval into methods sections and tables, exactly where LitQA2 answers live.
`RESULTS.md` says as much for the sibling C2b run: "the chunk-level evidence
layer (`source(mode=evidence)` over `papers_chunks`, 2026-08-30) did not exist
when the 07-27 pair ran."

**Three are corpus-side defects that were live during the 08-26 run.** Commit 3
meant every corpus hit reached the model as a bare title. Commit 1 records that
`search`'s tool error rate went from 0.000 on Qwen3.6 to **0.122 on Qwen3.8**
with no code change, measured 2026-08-26, the day of the full-egress run.
Commits 11 and 12 mean evidence re-ranking was degrading to cosine order about
half the time, though only after the evidence layer existed at all.

Direction of the bias, a priori: every one of these makes the **later,
corpus-only** arm stronger, which would shrink the measured gap. Section 4
tests that prediction against the re-run data and does **not** confirm it.

### Also in the arm's path, outside retrieval

Four commits change SSE transport, which the runner uses (`run_arm.py` streams
the live research chat with `Accept: text/event-stream` against `:8080`):
`3bea54d` (resume 410 consults the client checkpoint), `ee4e52f`, `de11464`,
`3ec1407` (replay buffer sized by bytes then by event cap). These affect resume
and truncation rather than answer content, but `run_arm.py` classifies
`deadline_hit`, so they are not provably inert.

### Behaviour-affecting but outside both arms (15)

`2558350` (deploy.sh banner text only); `7dc7b1d`, `7128bcb`, `7bbfd0e`,
`8f4c619` (vLLM liveness alerting); `c21d44f`, `fbbc75f`, `3823e7c`, `3f977c8`
(user uploads, .docx tables, PDF OCR: LitQA2 uses no uploads); `88c82a1`
(memory-proposal deletion); `852fee2` (smoke script); `18f11d3` (multimodal
turn routing: the eval is text-only); `df29550` (webui typewriter pacing);
`87da6cf` (503 + Retry-After while vLLM is stopped); `cd226aa` (the C2b shadow
compose file, used by the abstention pair on `:8081`, not by this arm).

### Egress-full only

No commit in the range is exclusively egress-full. The closest is the `web.py`
half of `fd559c9` (PMC read through NCBI efetch instead of the blocked page),
but that same commit's `executor.py` half is shared with the corpus ladder.

---

## 3. The two specific checks

**Does the 09-16 recapture (`1380524`) sit inside this range?** **No.** It is a
descendant of `3be8308`, dated 2026-09-16, one day after the range ends:

```
git merge-base --is-ancestor 1380524 3be8308   ->  false
git merge-base --is-ancestor 3be8308 1380524   ->  true
```

It re-captured the agentic arm with the complete retrieval-tool set on Qwen3.8
and gpt-oss-20b. It is after both runs and cannot confound this pair, but it
does mean the recaptured Qwen3.8 numbers sit on a third code state, distinct
from both arms here.

**Was the corpus snapshot identical?** **Cannot be confirmed, and for chunks it
was certainly not.** Contrary to the premise, **neither scorecard carries a
paper count or a chunk count.** Both record only:

```json
"encoder": "bge-large-en-v1.5 / papers_bge"
```

and no `corpus`, `snapshot`, `n_papers` or `n_chunks` field exists in either
file. The only point count on record near these dates is in the C2b section of
`RESULTS.md`, which gives `papers_bge` at **68,913 points on 2026-09-15**;
there is no corresponding figure for 08-26, and the ingest pipeline ran
continuously across the 20-day gap. For `papers_chunks` the answer is
unambiguous: **0 points on 08-26** (the collection did not exist) against
roughly 1.43M on 09-15.

Recommended fix for reproducibility: have the ablation runner stamp
`papers_bge` and `papers_chunks` point counts into the scorecard at capture
time, the way `arm_matching` already stamps the serving config.

---

## 4. What the 2026-09-17 suite re-run settles

The full evaluation suite was re-run on **Qwen3.6-35B-A3B** (git `556b305`,
2026-09-17) and the `egress=off` agentic arm became a standard phase of every
backbone suite (`cb8faa5`). That produced two egress pairs with little or no
code between the arms.

| backbone | full | off | delta | commits between arms | prod commits | touching retrieval / personas / scripts |
|---|---|---|---|---|---|---|
| Qwen3.8-27B | 0.874 | 0.663 | **+0.211** | **75** | 35 | **26** |
| **Qwen3.6-35B-A3B** | 0.869 | 0.704 | **+0.166** [0.101, 0.226] | **0** (same sha, same day) | 0 | **0** |
| gpt-oss-20b | 0.563 | 0.482 | **+0.080** [0.015, 0.146] | 13 | 1 (`c6c56a7`, a SLURM queue wait in `deploy.sh`) | **0** |

`RESULTS.md` already flags this: "Same day, same commit, so unlike the Qwen3.8
pair this full − off delta is egress alone."

**The a priori prediction fails.** The confounded Qwen3.8 delta (+0.211) is
*larger* than the zero-drift Qwen3.6 delta (+0.166), not smaller, so the
intervening commits did not visibly inflate the corpus-only arm at the expense
of the gap. The 0.045 difference is about one CI half-width and sits inside the
~0.035 run-to-run variance the 08-26 scorecard itself records. My section 2
reasoning was sound in the absence of this data; the data does not support it,
and the data wins.

**A second, independent bound.** The 09-16 Qwen3.8 faithfulness recapture
(`1380524`) re-ran the full-egress agentic arm on later code and scored
**0.869**, against **0.874** on 08-26. So 20 days of harness work moved the
Qwen3.8 full arm by **0.005**. `RESULTS.md` draws the same conclusion for the
cross-backbone gap: "the July Qwen3.6 vs Qwen3.8 gap (0.839 vs 0.874) was
harness drift plus the bare-arm budget, not the model."

Two honest limits on that bound. It measures drift on the **full** arm, where
web access can substitute for weaker corpus retrieval; the corpus-only arm is
where the chunk index and evidence mode bite hardest, and no old-code
corpus-only arm exists to compare against, because 09-15 was the first one ever
run. And pairing the recapture (`1380524`) with the 09-15 off arm to get +0.206
is *not* a clean fix: those two are 21 commits apart, 8 of them production,
including `1b79887`, which rewrites the backbone profile and touches
`shared/personas/research.json` and `backend/retrieval/personas.py`.

---

## 5. Recommendation

The Qwen3.8 egress decomposition (57% / 43%) should **not** be presented as a
controlled measurement. Report it with the confound named, and carry the
Qwen3.6 decomposition (69% corpus-only / 31% external tiers, +0.367 / +0.166 of
+0.533) as the controlled one, since it is the only egress pair in the suite
with zero commits between the arms.

**Commit-count fixes, applied 2026-09-23.** `RESULTS.md:1635` now says 75
commits apart, carrying the scope ("26 of them touching `backend/retrieval/`").
`RESULTS-CHAPTER-v6-OUTLINE.md:139` now says 75. `:338` was a *different* range
and a different error: the Qwen3.8 recapture (`1380524`) against the 08-26
headline (`3e0bcfb`) is **96** commits, not 26 and not 75, so it now says 96.
`RESULTS.md:1103` is correct as written and was left alone: 26 commits touch
`backend/retrieval/` over its range too (`3e0bcfb..dfa823b`), and it states the
scope explicitly.

Still worth doing: the runner stamps serving config into `arm_matching` but not
corpus point counts. Neither egress scorecard records a paper or chunk count,
which is why section 3 above cannot answer the corpus question directly.

---

## 6. Sentence for the paper

Option (c), with the resolution attached:

> Sixteen commits between the Qwen3.8 full and corpus-only arms touched code the
> corpus-only arm exercises, including chunk-level evidence retrieval over
> `papers_chunks` and the index it reads, both of which postdate the full-egress
> arm; the decomposition is therefore quoted from the Qwen3.6-35B-A3B pair, whose
> two arms ran the same day on the same commit and which puts the external tiers
> at +0.166 [0.101, 0.226] of the +0.533 harness effect.
