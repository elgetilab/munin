# 07 Findings

The intellectual spine of the paper: the diagnostic arc, and the results that
cut against the obvious narrative. Numbers here are all sourced from
`05-RESULTS.md`.

Each finding is stated as a claim, its evidence, its mechanism, and what it
generalises to. The mechanism matters most: a number without a mechanism is a
data point, and a number with one is a finding.

---

## 1. The arc, in one paragraph

Retrieval was the first bottleneck (SPECTER answer accuracy 0.427 tracked
Recall@10 0.442 almost exactly). Fixing it with the BGE-large migration lifted
Recall@10 from 0.44 to 0.73 but answer accuracy only from 0.42 to 0.50,
falsifying the naive recall-implies-accuracy projection and revealing a second
bottleneck. That second bottleneck was **not the model**: it was `read_paper`
summarising and discarding the text that held the answer. Replacing it with
full-text reading (`source(mode=qa)`) collapsed over-abstention from ~42% to 6%
and took accuracy 0.497 → 0.864. With the architecture finished, the harness
ablation closed the loop, on the production backbone Qwen3.8-27B: agentic
0.874 vs bare 0.387 vs naive RAG 0.211, harness value **+0.487 [0.407, 0.568],
p < 0.001**, with tool-failure recovery 1.000. The same three arms on the
retired Qwen3.6-35B-A3B a month earlier gave 0.839 / 0.302 / 0.171 and +0.538,
so the ordering and rough magnitude hold across a dense 27B and a 3B-active
MoE (a suggestive, not controlled, replication; `08-LIMITATIONS.md`
section 3), and a third backbone from a different lab, gpt-oss-20b, gave
0.563 / 0.407 / 0.101 and +0.156, replicating the ordering at a third of the
size (finding 8c). Faithfulness closed last: a null that had held on two
backbones turned out to be the judge never seeing the passages the harness
read, and the corrected measurement has the harness roughly doubling
supported claims (finding 4).

**The generalisable shape:** two successive bottlenecks were both diagnosed by
measurement, and the larger of the two was in plumbing rather than in the model
or the retriever. Neither would have been found by prompt iteration.

---

## 2. The bottleneck was the reading path, not the model or the retriever

**Claim.** Given the right ~2k tokens the 35B model already answers correctly.
What was missing was plumbing.

**Evidence.** Of 20 questions where the agentic arm abstained despite the
source paper being retrievable, **16 of 20 had actually read the paper** and
still abstained. A full-text oracle flipped **9 of 11** over-abstentions to
correct, establishing a 0.82 ceiling. Building that oracle into production
(`source(mode=qa)`) took accuracy 0.497 → 0.864 and over-abstention 42% → 6%,
**clearing the oracle ceiling**.

**Mechanism.** `read_paper` extracted the full PDF text, compressed it to a 3-5
sentence summary plus bullets, and threw the raw text away. LitQA2 answers
frequently turn on a buried numeric (a surface area, a fold-change, a table
cell) which is exactly what summarisation removes. The model then correctly
reported it could not find the answer, which looked like over-conservatism and
was actually honest reporting about information it had been denied.

**Generalises to.** Any agentic RAG system with a summarise-then-reason step.
The summarisation is usually justified as context economy, and its cost is
invisible in aggregate metrics because it manifests as abstention rather than
as error. Measure it by giving the model the raw text on the abstention cases
and seeing what flips.

**Corollary that reframes the retrieval work.** Retrieval quality still bounds
**breadth** (how many distinct sources a deep-research report can cite), but on
single-source question answering the reading path, not retrieval, was the
ceiling.

---

## 3. Naive RAG is worse than no retrieval at all

**Claim.** Retrieve-then-answer with a fixed top-5 context scores **below the
bare model** on the same questions.

**Evidence.** Replicated three times, on two backbones: pilot n=100, Qwen3.6
(RAG − bare = −0.170 [−0.26, −0.08], p ≈ 0); clean run n=199, Qwen3.6
(−0.131 [−0.196, −0.070], p < 0.001); and the headline n=199, Qwen3.8
(**−0.176 [−0.251, −0.096], p < 0.001**). The Qwen3.8 figure is the strongest
form of the finding, because it holds against a correctly budgeted bare arm
(the Qwen3.6 bare arm lost 33 answers to truncation, which if anything
flattered RAG). Naive RAG abstains on **0.498** of questions against the bare
model's 0.040 on Qwen3.8 (0.749 vs 0.201 on Qwen3.6).

**Mechanism.** Verified from the answers themselves: imperfect top-5 retrieval
makes the model **anchor on the retrieved abstracts** and refuse ("not enough
information") instead of falling back on correct parametric knowledge. The
retrieved context does not merely fail to help, it actively suppresses a
capability the model already had. The risk-coverage view makes this precise:
RAG buys low selective risk (0.292) only by collapsing coverage to 0.241.

**Generalises to.** Any evaluation that reports RAG-versus-bare as if
retrieval were monotonically helpful. It is not, and the failure mode is
coverage collapse rather than increased error, so accuracy-only reporting will
attribute it to the wrong cause.

**Caveat to carry.** RAG accuracy is prompt-sensitive. The *anchoring effect*
is robust across three runs and two Qwen backbones; the exact magnitude is not
a property of retrieval in general. On gpt-oss-20b RAG is again far below bare
(0.101 vs 0.407, −0.306 [−0.377, −0.231]), but the mechanism there is
different: 162 of 199 RAG turns end with no final message rather than an
explicit refusal (finding 8c), so the ordering replicates on a third
backbone while the anchoring mechanism is only shown on the two Qwen ones.

**What this makes the harness's contribution.** The value is not retrieval per
se. It is the **agentic loop's iterative, multi-source retrieval**, which can
recognise that the first context was insufficient and go get more. The
`egress=off` arm (R1) puts a number on the two halves: the corpus-only loop
is +0.276 over bare (0.663, precision 0.917, abstaining on 28% rather than
guessing), and letting it reach the web and Semantic Scholar adds +0.211 by
turning 41 of those abstentions into correct answers.

---

## 4. Grounding roughly doubles with the harness, and a two-backbone null hid it for seven weeks

**Claim.** The agentic arm's answer claims are supported by its own retrieved
evidence about twice as often as the RAG arm's, once the evidence the model
actually read is in the judge's context. Until 2026-09-16 the same
measurement read as a null on two backbones and three runs, and the null was
a capture defect.

**Evidence.** On Qwen3.8, paired on all 199 questions, agentic arm
re-captured 2026-09-16 with the complete retrieval-tool set and judged against
the same RAG arm as before: RAG **0.282 [0.248, 0.316]**, agentic **0.540
[0.503, 0.578]**, **delta +0.258 [0.206, 0.311], p < 0.001**. The recaptured
arm's accuracy is the 08-26 arm's within noise (0.869 vs 0.874, paired
−0.005, p = 0.89), so this is the same system measured properly, not a
different one. On gpt-oss-20b, agentic 0.392 [0.330, 0.448] (n=159); the RAG
arm answers 37 of 199 questions, so the paired comparison has n=21: +0.253
[−0.009, +0.502], p = 0.056. Same sign and size, underpowered.

**What the null was.** The faithfulness capture listened for a fixed list of
retrieval tools and extracted grounding text from their results. The list
predated the corpus search ladder (`search`) and the grounded read stage
(`source`), the two tools through which the agentic arm reads full-text
passages before answering; on Qwen3.8 they were 446 of its 1,380 calls, on
gpt-oss 97% of all calls. Their results never became contexts. The RAG arm's
evidence (top-5 abstracts) was captured completely. So the agentic arm was
judged against web, Semantic Scholar and abstract snippets while its answers
drew on passages the judge never saw, and 36 of 199 rows were unscoreable
(15 abstentions, 21 answers with no captured context at all). The bias could only run one way, and the previous text's "genuine null,
not an underpowered one" was true of the numbers and false of the
measurement. Contexts are extracted at capture time, so the arm had to be
re-captured; both scorecards are kept.

**Mechanism, revised.** The earlier reading ("retrieving more is not the same
as grounding more; the harness buys correctness, not literal grounding") does
not survive. With 26 passages per question in the evidence set, the agentic
arm's claims are supported at 0.54; the RAG arm's, against 5 abstracts, at
0.28. The harness reads the paragraph it cites. What remains true from the
earlier reading: the un-supported ~46% is not fabrication (C1: 0 confabulated
local citations on three backbones), and a sentence-level entailment judge
under-counts claims supported by two passages jointly, so 0.54 is a floor on
grounding rather than an estimate of it.

**What it cost.** A null that "replicated" across two backbones and three
runs. The replication was real: every run had the same gap. Replication
across backbones does not protect against a shared measurement defect, and
the thing that exposed it was a *third* backbone whose tool mix left 13
scoreable rows, which was too few to ignore. `08-LIMITATIONS.md` carries the
lesson; `05-RESULTS.md` R2 carries both generations of the number.

**Generalises to.** Any RAG-style faithfulness evaluation whose retrieval
surface grows after the capture code is written. The capture should derive
its tool list from the tool registry, not from a hand-written set; ours still
does not (`08-LIMITATIONS.md`).

---

## 5. A benchmark found a production bug, and the first explanation was wrong

**Claim.** Citation re-ranking scored −0.368 nDCG@10 against dense on
LitSearch, a 76% relative drop, and the cause was a **score-scale mismatch**
present in production, not a property of the citation signal.

**Evidence and the measurement that identified it.** Over 40 queries on the
top-100 dense pool:

| Quantity | Value |
|---|---|
| Dense cosine spread within pool | **0.087** |
| Citation score spread within pool | **1.000** |
| Weighted influence, dense (x0.7) | 0.061 |
| Weighted influence, citation (x0.3) | 0.300 |
| **Citation / dense ranking influence** | **4.95x** |

`compute_citation_score` log-normalises citation counts to a full [0, 1] range,
while BGE cosine similarities inside a top-100 pool span only ~0.087. Combining
them raw means a nominal "70% relevance / 30% citations" formula behaves as
roughly **83% citations / 17% relevance**. At the class-default 0.8/0.2 it is
still ~2.9x. The re-ranker effectively sorted by citation count and used
relevance as a tiebreak.

**It affected production**: the same formula was the `/search/hybrid` path, that
is, the user-facing search page.

**Two controls confirm the diagnosis.** Recall@100 was *identical* to dense
(0.829), proving the re-ranker only reordered the fetched pool and that all
damage was in the ordering. And it changed the top-10 on **597 of 597**
queries, so it was genuinely exercised rather than inert.

**The fix and the A/B.** Min-max normalise the vector score across the fetched
pool before mixing, so both terms span [0, 1] and the weights mean what they
say. The raw `vector_score` is still what the API reports; only ranking uses
the normalised value, so the response contract is unchanged. A degenerate
all-equal pool gives `vector_norm = 1.0`, collapsing to the citation term
rather than dividing by zero.

| Citation re-rank 0.7/0.3 | nDCG@10 | R@10 | MRR | vs BGE-dense |
|---|---|---|---|---|
| Before | 0.117 | 0.195 | 0.116 | −0.368 [−0.405, −0.331], p = 0.0000 |
| **After** | **0.469** | **0.609** | **0.442** | **−0.016 [−0.030, −0.003], p = 0.014** |

**+0.352 nDCG@10, recovering 96% of the gap to dense.** BM25, BGE-dense, and
RRF are bit-identical across the two runs, a control confirming the change
touched only the re-ranker.

**Read the residual honestly.** Citation re-rank is now *statistically* still a
hair below dense (−0.016, p = 0.014) though practically at parity. The fix
**removes active harm; it does not turn citations into a win on this
benchmark.** That is the expected result rather than a disappointment:
LitSearch is near-single-target retrieval (mean 1.07 relevant papers per
query), so a popularity prior has almost nothing to contribute and the best it
can do is not get in the way.

**The first explanation was wrong, and saying so is part of the finding.**
Phase 5 saw citation re-rank collapse to ~0 on LitQA2 and attributed it to
cold-start: the LitQA2 sources were backfilled with zero citations, so
re-ranking demoted them below older cited papers. Cold-start was real but was
not the whole story. On LitSearch the graph is **dense** (344,703 in-corpus
edges over 35,978 papers, maximum in-degree 4,964) and it collapses just as
badly. The scale mismatch is present regardless of graph density, and it would
have been **invisible on BEIR**, where the graph is empty and citation re-rank
degenerates harmlessly to dense-only.

**The regression test encoded the defect.** The existing unit tests asserted
the *unnormalised* formula, so they failed on the fix and had to be rewritten
against hand-derived expectations. A pinned-fixture regression test now
captures the ranking that inverted under the bug.

**Generalises to.** Any weighted linear combination of scores from different
distributions. The stated weights are meaningless unless both terms are
normalised over the same pool, and the error is invisible on benchmarks where
one of the signals is absent.

---

## 6. Corpus-grounded abstention: the system declines rather than recites

**Claim.** Removing the supporting paper from the corpus makes the system
abstain rather than fall back on what the base model happens to remember.

**Evidence.** At matched `egress=off`, on the answerable subset, correct
abstention on source removal is **0.889** (Wilson [0.719, 0.961], n=27) on
the production backbone Qwen3.8 (2026-09-15): overall accuracy drops
**0.540 → 0.040** when the source is removed, abstention rises 0.420 → 0.920,
and of the 27 answerable questions 24 are correctly refused, 2 still answered
correctly, 1 answered wrong. On Qwen3.6 the same pair gave 0.667 [0.481,
0.852] and a 0.540 → 0.080 drop, which was itself the move from the old flat
loop's 0.200 [0.050, 0.350] (n=20): because every run uses the same 50 frozen
questions, both steps are question-paired bootstraps, **+0.467 [0.232,
0.697], p < 0.001** for the harness rewrite (controlled, one backbone) and
**+0.222 [0.040, 0.420], p = 0.015** for the backbone swap (suggestive:
backbone and seven weeks of harness moved together).

**Mechanism.** The old harness answered 12 of 20 from parametric memory when
the local source was gone. The agent architecture on Qwen3.6 answered 4 of 27
and correctly abstained on 18; on Qwen3.8 it answers 3 of 27 and abstains on
24. On the 24 questions answerable in both of the last two runs, 5 of the
absent-arm verdicts went from wrong-answer to refusal and 2 from
right-answer-without-the-source to refusal, against 1 the other way.

**The re-run had to close a leak the original design did not face.** The
chunk-level evidence layer (2026-08-30) reads a second collection,
`papers_chunks`, so a shadow that removed the papers from `papers_bge` alone
would have left their full text reachable. The 09-15 absent arm ran against
shadows of both collections (leakage verified 0 in each). This is worth a
sentence in the paper because it is the general form of the C2 hazard: every
retrieval path the harness has must be shadowed, and a harness that grows a
new path silently invalidates an old shadow.

**This reverses a prior conclusion.** The 2026-07-10 reading declared the C2
design "fatally confounded", on the grounds that LitQA2 questions are answerable
without the local source. That was true of the harness of the day and is false
of the current one. Reporting the reversal, rather than quietly replacing the
old text, is the honest form.

**The independence of the two abstention behaviours is the informative null.**
Across the same rewrite, C1 abstention on fabricated papers was **unchanged**
(0.980 → 0.970, overlapping CIs, 0/100 confabulations both times) while C2
over-abstention on answerable questions moved substantially. The harness became
**less trigger-happy about refusing real questions without becoming credulous
about fake ones.** These are separable properties, and a system can move one
without the other.

**C1 survives the backbone swap, where it was most at risk.** Qwen3.8 is
the backbone that guesses more freely on its own (R1: bare abstention 0.040,
precision of attempted falling), which is the disposition under which a
fabricated paper should be most tempting to answer. Inside the harness it
refused all 100 (Wilson [0.963, 1.000]), 0 confabulated local citations, with
about half the tool calls of Qwen3.6 (5.6 vs 10.8 per item): corpus, Semantic
Scholar, Crossref 404, one web search, refuse, naming the sources checked.
The change in behaviour is in the refusal itself: 13 refusals (4 on Qwen3.6)
also offer the nearest real corpus paper, spot-checked as "unrelated" or
"possibly what you meant", not as a substitute.

**Caveats that must travel with this claim.** One of 27 still answered wrong
on removal and two answered correctly without the source, so this is strong
calibration and not perfect. n = 27 is small: quote the Wilson interval
[0.719, 0.961], and note the bootstrap touches 1.0. The cross-backbone gain
is suggestive only. C1 remains the cleanest signal because it does not depend
on a shadow-corpus construction; C2b is the one that shows the harness
declines *answerable* questions when the corpus loses the source, which C1
cannot.

---

## 7. Egress is a first-class experimental variable

**Claim.** Outbound network access is not an implementation detail; it changes
the sign of results.

**Evidence.** The same C2 experiment at `egress=full` shows present 0.820 /
absent 0.740, that is, removing the local source costs almost nothing, because
the model **re-fetches the removed papers from the web**: measured, 17 of 49.
At `egress=off` the same experiment shows 0.540 / 0.080.

**Decomposition on the present arm:** old harness corpus-only 0.400; new
harness corpus-only 0.540 (harness gain +0.14); new harness corpus + web 0.820
(web tier adds +0.28). **The web tier contributes more than the harness
upgrade on this set.**

**Generalises to.** Every private-corpus or sovereignty claim in the RAG
literature. If the system can reach the open web, a corpus-grounding
measurement is not measuring corpus grounding. And `egress=off` is *necessary
but not sufficient*: a cached open-access paper on local disk answers the
question without touching the network, so provenance needs a separate control
(`corpus_scope`) from the network control.

**Engineering consequence.** The scorer records capture date and egress per
operating point and refuses to bless a figure that mixes them, emitting
`mixed_generations` / `mixed_egress` markers. Discipline was not trusted; the
tool enforces it.

---

## 8. Tool failures are absorbed, not propagated

**Claim.** Failures in the tool layer do not become failures in the answer.

**Evidence.** Over 1,380 tool calls on the Qwen3.8 headline run: error rate
0.139, degraded rate 0.379, 102 of 199 queries hit at least one failure, and
**every one still produced a final answer (recovery rate 1.000)**. On Qwen3.6
(1,714 calls, 86 queries with a failure) recovery was also 1.000, and two
independent fault-injection probes show the same. Mean calls per query fell
8.61 → 6.93 across the swap. The error-rate rise between the runs is **not**
a clean comparison: `web_fetch`'s failure definition changed between them
(`05-RESULTS.md` R6), and the one genuinely comparable rise, `search` 0.000 →
0.122, is a backbone behaviour covered in section 10.

**Mechanism.** Under degradation the harness works harder rather than failing:
call counts rise from 8.6 to 13.9 and 16.1 per query in the two probes. Combined
with the transport-level retry wrapper (exponential backoff on 5xx / 429 /
pre-first-byte stream drops, honouring `Retry-After`), transient failures are
recovered below the agent layer and persistent ones are compensated above it.

**The weak link is named, not hidden.** `web_fetch` has a 68% error rate on
the headline run (45% on Qwen3.6 under a looser definition that counted
anti-bot interstitials as content), dominated by publisher datacenter-IP walls
(one major publisher is a hard block that is unfixable at the fetch layer) and
burst rate-limiting. Retry-with-backoff recovers the transient share; reading
PMC through NCBI's efetch API instead of the blocking page (`fd559c9`, after
the run) lifted the ok rate on 24 real search URLs 0.50 → 0.71. Corpus and
Semantic Scholar retrieval are effectively error-free (0.000 over 332 calls on
Qwen3.8, 781 on Qwen3.6).

---

## 8b. The harness, not the backbone, keeps attempted answers trustworthy

**Claim.** Swapping the backbone changed how often the *bare* model attempts an
answer and how often it is wrong when it does; inside the harness neither
moved, and precision rose.

**Evidence.** Outside the harness, Qwen3.8 abstains far less than Qwen3.6
(bare 0.201 → 0.040, RAG 0.749 → 0.498) and its precision of attempted falls
with it (bare 0.476 → 0.403, RAG 0.708 → 0.420): it attempts many more
questions and is wrong more often when it does. Inside the harness, abstention
is **identical** at 0.075 on both backbones and precision of attempted
**rises** 0.908 → 0.946.

**Mechanism.** The harness's abstention behaviour is a property of the loop
(a full-text `source` read before answering, and an explicit not-in-corpus
path), not of the backbone's disposition to answer. A backbone
that guesses more freely on its own is held to the same evidence bar once it
has to read before answering.

**Caveat to carry.** Cross-backbone, so suggestive rather than controlled (a
month of retrieval commits sits between the runs). The within-run contrast
(bare/RAG precision falling while agentic precision rises, on the same
questions on the same day) is the clean part.

**Generalises to.** Any claim that agent reliability is mostly a model
property. Here the model got more willing to guess and the system did not.

**Third backbone, same reading, different magnitude (gpt-oss-20b,
2026-09-16).** A model from a different lab behaves the same way in kind:
bare precision 0.482, agentic precision 0.896; the harness again raises
precision of attempted by holding the model to the evidence bar. What differs
is how much it holds back: the agentic arm abstains on 0.342 of answerable
questions (Qwen: 0.075), so the harness value is +0.156 rather than +0.487.
The harness keeps attempted answers trustworthy on three backbones; how many
answers it attempts is a backbone property. See 8c.

---

## 8c. The harness effect replicates across labs, and its size is a backbone property

**Claim.** The arm ordering, the sign of every paired delta and the
significance of the harness value hold on a backbone from a different lab
with a different tokenizer, architecture and training recipe; the size of the
effect does not.

**Evidence.** gpt-oss-20b (21B MoE, 3.6B active, native MXFP4), same 199
questions, arms, budgets and deadline, run as an instance beside production on
its own GPU: agentic **0.563** vs bare 0.407 vs RAG 0.101, agentic − bare
**+0.156 [0.075, 0.241], p = 0.004**, agentic − RAG +0.462, RAG − bare −0.306.
On Qwen3.8 the same three numbers are +0.487, +0.663, −0.176. The bare arms
are within 0.02 of each other; the agentic arms are 0.31 apart; the abstention
rates inside the harness are 0.342 and 0.075.

**Mechanism.** Two behaviours of this model, neither seen on the Qwen family.
First, inside the harness it declines a third of answerable questions after
reading, at high precision when it does answer: the harness makes it careful
rather than correct. Second, **without tools it frequently ends its turn
without answering**: on 25 of 199 bare and 162 of 199 RAG prompts it reasons
"we need to search" and stops with no final message, no tool call and no
truncation. The frozen protocol scores that as wrong, so RAG's 0.101 is
mostly refusal by silence, not the anchoring failure of finding 3, and the
bare/RAG precision-of-attempted numbers (0.48, 0.80) are the fairer read of
what the model knows. The same behaviour appears as 20 empty answers in C1
and 11 in the standalone answer track.

**What it cost to find out.** The first pass at this run scored agentic 0.467.
vLLM's harmony parser had glued channel tokens to 3% of tool names, which the
executor rejected as unknown tools, and the scorer did not read this model's
habitual `**Answer:** A`. Both were repaired before the headline re-run (the
repairs change none of the stored Qwen verdicts) and the pre-repair number is
kept. A new model family costs the harness something before it costs the
model anything; the harness now carries one gpt-oss-shaped accommodation
beside its three Qwen-shaped ones, and says so.

**Caveat to carry.** One non-Qwen point, three variables moving at once (lab,
size class, sampling), an underpowered faithfulness comparison (finding 4),
and the routing deploy gate at 0.647 saying this backbone would not ship
behind the router as-is. The claim is "replicates in kind, not in size", and
the paper should resist drawing a curve through two labs.

**Generalises to.** Any harness paper measured on one model family: the
ordering may survive the swap while the headline number halves, and the
place to look for the reason is the abstention column, not accuracy.

---

## 9. Harness improvements are not prompt improvements

**Claim.** Two of the three behaviours worth fixing were not fixable by
prompting, and one prompt intervention made things twice as bad.

**Evidence.** See `06-ABLATIONS.md` §4. Softening the research fragment from
`deep` to `medium` raised the paired over-tooling median from 10.5 to 20.5
calls. A "at most 3-4 follow-ups" instruction did nothing. A 30-call code cap
fixed it immediately (p90 40 → 30, tail above 30 calls 9/40 → 2/40) with
grounding held and zero failures.

**A second instance of the same lesson, from production.** The fabricated-author
incident happened under a persona prompt that already said "Do NOT fabricate
paper titles, authors, abstracts or DOIs". *That rule is what failed.* The fix
therefore had to be mechanical (structural metadata resolution with no LLM in
the path, plus a post-turn audit) rather than another instruction.

**Mechanism for the backfire.** A thinner default response induces
*compensatory* search: the model perceives its baseline answer as inadequate
and works around it with more tool calls. Prompt-level nudges toward brevity
can therefore increase cost.

**Generalises to.** Agent cost control and instruction-following guardrails
generally. If an instruction has already failed once in production, adding a
stronger version of the same instruction is not a fix. Prefer a mechanism that
makes the failure structurally impossible or that detects it after the fact.

---

## 10. Secondary findings worth one sentence each

- **Multi-query fan-out did not beat single-query dense** on LitQA2 retrieval
  (Recall@10, p = 0.71). Complexity in the retrieval loop was not what helped.
- **The agentic arm produced zero unparseable answers** on both Qwen
  backbones, so its accuracy is not inflated by lenient parsing. The Qwen3.6
  bare arm's 33 unparseable answers were a token-budget defect in the harness,
  since fixed; on Qwen3.8 every arm returns 0. On gpt-oss-20b the agentic arm
  returns 6 (3 empty, 3 without a letter) and the bare/RAG arms 25 and 162,
  all of the no-final-message kind, none truncated (finding 8c).
- **A tool-trained model without tools may simply not answer.** gpt-oss-20b
  reasons "use search" and ends its turn on 13% of bare and 81% of RAG
  prompts. Report precision of attempted beside accuracy whenever the bare
  arm is a model trained for tool use, and record `finish_reason` and the
  reasoning tail per row so the two failure kinds can be told apart.
- **The tool mix is a backbone fingerprint.** gpt-oss-20b made 85% of its
  1,742 calls through the corpus `search` ladder and left the corpus 17 times
  in 199 questions; Qwen3.8 made 331 web searches and 175 Semantic Scholar
  calls. Recovery from tool failure was 1.000 on all three backbones.
- **Cost is real and should be reported**: ~20x bare wall-clock at 6.9 tool
  calls per query on Qwen3.8 (a dense 27B; ~5.4x at 8.6 calls on the 3B-active
  MoE). But the pilot ran at 16 calls per query for a lower score, so the
  tool-retirement work bought accuracy *and* cost.
- **Qwen3.8 emits mistyped tool arguments where Qwen3.6 did not.** `search`
  went from 0 errors in 90 calls to 11 in 90, every one an argument *type*
  (`top_k="5"`, `top_k=5.0`, `filters="year:2023"`). The executor now coerces
  arguments against the declared schema (`fd559c9`); a backbone swap is a
  tool-contract test as much as an accuracy test.
- **A harness bug once faked a null result.** A naive answer parser dropped
  ~17% of BGE answers and ~10% of SPECTER's as unparseable, turning a real
  +0.075 (p = 0.028) into an apparent +0.05 n.s. The recovered cases became
  *abstentions*, not correct answers, so accuracy barely moved but the
  comparison became honest. Fixing an eval harness can leave the headline
  number nearly unchanged while making it mean something different.
- **Deep-research abstention that looked like a defect was correct behaviour.**
  A run resolving 1 of 3 sub-questions abstained on 14 of 18 reads; reproduction
  showed the abstentions were on topically-adjacent papers that genuinely did
  not address the question, and the same papers resolved 5/5 on their real
  topic. The real limit was upstream: retrieval relevance and corpus depth.
