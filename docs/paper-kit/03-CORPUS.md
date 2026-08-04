# 03 Corpus

How the ~68k-paper corpus was built and kept honest. This section matters more
than it looks: three separate results in the evaluation turned out to be corpus
artifacts rather than model or retrieval effects, so corpus quality is a
first-class methodological concern, not housekeeping.

---

## 1. Scale and composition

| Snapshot | Papers | Note |
|---|---|---|
| 2026-05-12 (audit) | 67,171 | 3,922 contributor uploads (5.8%), 63,249 crawler downloads (94.2%) |
| 2026-07 (headline runs) | 68,462 | the number quoted alongside every Track C/D result |
| 2026-08-03 (author audit) | 68,426 | pre-repair |
| 2026-08-03 (after repair) | 68,436 | post-repair |

Domain: chemistry / biophysics / membrane biology, matching the host group's
research. This is important context for external validity: the two public
retrieval benchmarks used (SciFact, LitSearch) are **not** in this domain, and
the paper should say so rather than implying transfer.

The corpus is **consortium-wide**, and sub-corpora (selected with a slash
command such as `/elgeti`) are a **relevance filter, not a security boundary**.
Everything is visible to everyone; scoping is about signal. One consequence
worth stating: because there is no access boundary, the summary cache can key
on document id alone. Under a per-group corpus that same cache would have been
an exfiltration channel (ask for a summary of a paper you cannot see, get one
back instantly from cache). Sub-corpus membership is a **label set, not a
partition**: a paper can be relevant to several groups, applied at ingestion.

---

## 2. Ingest pipeline

```
PDF -> GROBID (parse to TEI) -> Crossref (enrich) -> BGE-large (embed)
    -> Qdrant (vectors + payload) + Neo4j (citation graph) + SQLite (state)
```

Runs as an always-on systemd service watching a drop directory every 60
seconds. Supports single-file, batch, reprocess, and a parallel fast mode
(parallel GROBID plus batched embeddings).

Embeddings are built from **title + abstract**. Note the consequence, because
it makes one later result trivially safe: author metadata is never part of the
vector, so author repair cannot move any retrieval metric.

### The ingest-time identity guard

Crossref enrichment is **rejected** when the Crossref title does not match the
GROBID-extracted title (token-set Jaccard, threshold 0.3). On rejection the
pipeline keeps GROBID metadata, drops the Crossref enrichment, and preserves
the rejected Crossref title on the payload as `_crossref_title_rejected` so
remediation tooling can surface it later.

This guard exists because of a specific failure: GROBID sometimes extracts a
**reference's** DOI rather than the paper's own, so the paper gets keyed under
a cited work's identifier and then enriched with that other paper's metadata.
The result is a Qdrant point with two papers' metadata spliced together. The
guard converts a silent corruption into a flagged record.

**The guard is not a fix.** The correct repair for a mis-keyed paper is
CrossRef-by-title lookup, not lookup by the suspect DOI, and that remains open
work.

### Acquisition: the crawler

Citation-based expansion from seed papers, driven by a SQLite queue with a
`pending -> downloading -> downloaded -> processed` state machine, where
extracted citations feed back into `pending`. Sources in order: arXiv (open
access), Sci-Hub (paywalled), manual upload.

**The Sci-Hub path needs a decision before publication.** It is in the code and
it accounts for part of the 94% crawler share. Options are to describe it
factually, to describe acquisition generically ("open-access resolvers and
institutional access"), or to remove it from the published configuration. This
is a judgement call the authors should make deliberately rather than by
omission. See `08-LIMITATIONS.md` §6.

---

## 3. Continuous quality control

Three always-on services plus one nightly timer, with operator escape hatches.
The corpus is not curated by hand; it is swept.

| Mechanism | Cadence | What it does |
|---|---|---|
| `munin-paper-detect.service` | every 15 min | Paced detection sweep with auto-quarantine, slowly walking the whole corpus. |
| `paper_cleanup.py review` | operator, weekly | Triage of what the sweep quarantined: keep / reject / skip per record. |
| `munin-paper-reattribute.timer` | 04:30 daily | Backfills group attribution for records whose uploader joined the contributor allowlist after ingest. |
| `munin-embedding-map.timer` | nightly | Rebuilds the 2D map and topical clusters. |

Detection kinds: `metadata-mismatch`, `low-quality`, `short`,
`metadata-unverifiable`, `orphan`.

**Quarantine is a soft action.** A quarantined paper leaves the searchable
corpus but keeps its PDF (moved to a quarantine directory, not deleted) and
keeps its Neo4j node so the citation graph stays intact. Removal is a separate,
explicit operator command.

### The embedding map

A nightly pipeline that is both a user-facing feature and a corpus diagnostic:

1. Scroll the Qdrant collection with vectors.
2. **L2-normalise** every vector. Without this the downstream Euclidean HDBSCAN
   produces one mega-cluster, because the encoder's outputs are not guaranteed
   unit-norm.
3. UMAP to 10d (`n_neighbors=15`, `min_dist=0`, cosine) for clustering; 2D is
   too lossy on a dense corpus.
4. HDBSCAN (`min_cluster_size=15`, Euclidean). Points that fit no cluster land
   in `cluster_id = -1`, labelled `unclustered`: **honest noise rather than
   forced assignment**.
5. UMAP to 2d separately for map coordinates.
6. Label each cluster with the LLM from the 10 titles nearest the cluster
   centroid (thinking disabled, temperature 0.2, 20 max tokens).
7. Write `cluster_id` / `topic_label` / `topic_slug` back to every point,
   grouped by cluster, so the full corpus costs `O(cluster_count)` HTTP calls
   rather than `O(paper_count)`.

At the 2026-04 build: 29,893 papers, 213 clusters.

---

## 4. The metadata audits (a reportable result in their own right)

### 4.1 Title/metadata audit, 2026-05-12

Triggered by a real failure: the assistant conflated two researchers sharing a
surname (different institutions, different fields) while answering a question
scoped to a research-group tag. Two layers contributed, and separating them is
the useful part:

- **A model-side regression** that ignored scoped tool results in favour of
  pretraining priors. Fixed by the Qwen 3.6 upgrade; replay reproduces the
  correct behaviour on every repetition.
- **A corpus-side ingest issue**, where multiple papers' metadata had been
  spliced onto the same Qdrant point.

Measured, PDF first page versus stored title, n=300 per source, deterministic
stride, naive normalised word-stem match:

| Source | good | bad | skipped | bad rate (of decidable) |
|---|---|---|---|---|
| Contributor uploads | 222 | 64 | 14 | **22.4%** |
| Crawler downloads | 250 | 37 | 13 | **12.9%** |

Also found: 2,168 records with HTML tags in the title, 184 null DOIs (uploads
only).

**Roughly 30 to 40 percent of the flagged cases were false positives**, from
Greek letters mangled in PDF text extraction (`β` becoming `p`, `α` becoming
`a`), MathML/JATS markup in stored titles versus plain text in the PDF, and
first pages that are a journal masthead or table-of-contents header with the
real title on page 2. This is worth reporting because it is a general lesson
about automated corpus audits: the naive detector's raw rate overstates damage
by a large factor, and reporting the raw rate would have been wrong.

### 4.2 Author audit and repair, 2026-08-03

**Root cause:** until 2026-08 the Crossref merge enriched title, journal, and
year but **never authors**, so every stored author list was GROBID's parse of
the PDF text layer.

Before repair (68,426 records):

| State | Records | Share |
|---|---:|---:|
| Clean | 54,951 | 80.3% |
| Damaged | 7,647 | 11.2% |
| No authors | 5,828 | 8.5% |

Damage taxonomy (author strings): `artefact_char` 3,240, `digit` 2,650,
`affiliation` 330, `conjunction` 294, `too_short` 270, `exploded_caps` 253,
`too_long` 136, `email` 31. A representative case: a stored list containing
`'C 5 -C G D O U B L E B O N D O F C H O L E S T E R O L ...'`, which is
running-header text from the PDF that the parser read as a name.

After repair (68,436 records):

| State | Records | Share |
|---|---:|---:|
| Clean | 67,574 | **98.7%** |
| Damaged | 115 | 0.2% |
| No authors | 747 | 1.1% |

**Five safety properties of the repair, in the order they matter:**

1. **Identity guard.** A record is rewritten only when the Crossref title
   matches the stored title, using the same token-set Jaccard test and the same
   0.3 threshold as the ingest path. A record whose identity is in doubt is
   never given someone else's authors: it is skipped and reported.
2. **Quarantine.** Records already carrying `_crossref_title_rejected` are
   skipped outright. They belong to the separate GROBID mis-keying problem, and
   the run produces their worklist rather than papering over it (70 records).
3. **Reversible.** Every write stamps `_authors_previous` with the exact prior
   value plus `_authors_source` and `_authors_repaired_at`, so a record can be
   restored from its own payload without a snapshot.
4. **Additive.** Only `authors` and three provenance keys are written; vectors
   are never touched. Since embeddings come from title and abstract, author
   repair provably cannot move any retrieval metric.
5. **Resumable.** Processed point ids append to a state file, so an interrupted
   run continues rather than re-querying Crossref for 13k DOIs.

Neo4j's `:Author` graph was reconciled in a follow-up pass, so the two stores
were knowingly out of step between runs.

**A negative result from this work that belongs in the paper:** Semantic
Scholar is **not safe as an author backfill source** for this purpose. In a
validation sample it returned wrong authors for 3 of 16 records. Crossref was
used instead. The general point is that "authoritative metadata API" is not one
category, and a repair pipeline that trusts the wrong one silently manufactures
new errors at scale while appearing to fix them.

---

## 5. Why this section belongs in the paper

Three evaluation results were shaped by the corpus rather than by the model or
the retriever, and none would have been interpretable without the audits:

1. **Citation re-rank collapsed to near-zero Recall on LitQA2**, originally
   attributed to cold-start (the LitQA2 sources were backfilled with zero
   citations, so re-ranking demoted them below older cited papers). That
   explanation was **incomplete**: the same collapse reproduced on a corpus
   with a dense citation graph, and the true cause was a score-scale mismatch.
   The corpus artifact was real but was masking a production bug. See
   `07-FINDINGS.md` §5.
2. **The researcher-conflation failure** looked like a model hallucination and
   was half a corpus corruption.
3. **The fabricated-author incident** looked like a model hallucination and was
   entirely a metadata hole in the web tier. See `02-ARCHITECTURE.md` §6.

The transferable claim: in a private-corpus RAG system, **a substantial
fraction of apparent model failures are corpus failures**, and you cannot tell
which is which without construct-time ground truth and standing audits.
