# Chunk-level evidence + LLM-scored relevance

> **Historical (2026-08).** The chunk-level evidence layer was built: `source` mode `evidence` (2026-08-30) over the `papers_chunks` collection, complete on 2026-08-31.

Status: PLAN, not implemented. Drafted 2026-08-28.
Sibling: [`SEARCH-AS-PRIMARY-PLAN.md`](SEARCH-AS-PRIMARY-PLAN.md) (whose changes
C and B were measured and did NOT work; this is why).

---

## 1. Why the previous two attempts failed

Measured on the iLOV reproducer ("what is the extinction coefficient of iLOV at
280nm?"), baseline 26 tool calls / 637,058 turn prompt tokens / 562s:

| attempt | change | result |
|---|---|---|
| C | remove paper_search/web_search from the visible set | 28 tools mean. Model reached web_search via `tool_search` 7/7/11 times |
| A+B | staged tier ladder + opt-in `read=N` | 30 tools, 782k tokens. Model called `search` once, never passed `read` |

Both assumed the model was *choosing* badly. It is not. **Our retrieval indexes
one vector per paper, built from title + abstract** (`papers_bge`: 68,808 points,
one per DOI, no chunk fields). An extinction coefficient lives in a methods
section or a table. No abstract contains it, so no amount of breadth escalation
across corpus/OA/web can surface it, and the model reads 11-14 documents by hand
because that is the only way to get at the text.

**The escalation ladder I built escalates BREADTH (corpus -> OA -> web). What is
missing is DEPTH (paper -> chunks -> scored evidence).** PaperQA2's agent has
exactly this as a distinct middle step, `gather_evidence`, between finding
papers and answering; it retrieves chunks, not documents, and LLM-scores each
chunk in the context of the question before it is used.

## 2. What already exists in-tree (this is not greenfield)

- `document_store.chunk_text()`: ~512-token chunks, ~50-token overlap,
  paragraph-aware with sentence fallback. Reusable as-is.
- `user_docs` Qdrant collection: already chunk-indexed, payload
  `{document_id, chunk_index, chunk_text, total_chunks, filename, ...}`. The
  shape to mirror.
- `document_store._extract_pdf()`: GROBID first, pypdf fallback.
- `source` already returns the citation spine: `ref_resolved`, `read_depth`, and
  `source: {origin, url, extraction_method}`.

## 3. Design: a `papers_chunks` collection

One point per chunk, payload carrying everything a citation needs:

```
{
  paper_id, doi, title, year, authors,      # paper identity (denormalised
  journal,                                   #   so a hit cites without a join)
  chunk_index, total_chunks, chunk_text,
  section,            # GROBID <div type=...> when available, else null
  page,               # when the extractor gives it, else null
  char_start, char_end,
  contributors, topic_slug, cluster_id      # carried for tag scoping
}
```

Denormalising paper identity onto every chunk is deliberate: an evidence hit
must be citable on its own, without a second lookup that could fail or drift.

Tag scoping (`_build_tag_filter`) must work against this collection too, so
`contributors` / `topic_slug` are carried through.

## 4. Citation provenance (the hard requirement)

Every returned piece of evidence carries, and `source` re-emits unchanged:

```
{
  "quote":  "<verbatim chunk text, or the sentence span within it>",
  "ref":    {doi, title, authors, year, journal},
  "locator": {section, page, chunk_index, char_start, char_end},
  "origin": "local_kb" | "oa" | "web",
  "extraction_method": "grobid" | "pypdf" | "abstract"
}
```

Two rules, both learned from real failures in this repo:

- **A quote is copied, never generated.** `chunk_text` is stored verbatim, and
  the quote must be a substring of it. Assert this, do not trust the model.
  (`_QA_SYSTEM` explicitly permits answering from the model's own knowledge,
  which is how a UV-microscopy paper returned an iLOV coefficient with
  `abstained=False` on 2026-08-27.)
- **Evidence with no locator is not evidence.** If a chunk cannot be tied to a
  paper and an offset, it is dropped rather than returned unattributed. The
  `bibref.py` work exists because invented author names reached users once.

## 5. Integration: `search` keeps the entry point, `source` owns the reading

Per the operator decision (2026-08-28): the capability stays reachable through
`search`, but the implementation folds into `source` so provenance has ONE
owner.

- New `source` mode: `mode="evidence"`. Takes a question, retrieves top-N chunks
  from `papers_chunks` (optionally scoped to given refs/tags), LLM-scores them
  against the question, returns ranked evidence with the provenance block above.
  No free-text answer: this returns *evidence*, and the caller composes.
- `search` calls it internally when the query is answer-shaped, and returns the
  evidence under `answers`, exactly where the current `read` bolt-on puts it.
  The model still makes ONE call.
- The current `read=N` whole-paper path is retired in favour of this. It costs
  ~40s per document and re-parses the PDF each time.

## 6. LLM-scored relevance

Replace the embedding-cosine floor (`OA_RELEVANCE_FLOOR = 0.62`) as the sole
gate. Embedding cosine is why two ruthenium dye-sensitised solar-cell papers
scored 0.66 and passed for an iLOV query.

**Batch the scoring, do not score one call per candidate.** One LLM call takes
the question plus N candidates (title + snippet, or chunk text) and returns a
0-10 relevance score with a one-line reason for each. At `top_k=10` that is ONE
extra call per search, not ten. Retain the cosine as the cheap prefilter that
decides what enters the batch.

Applies at two levels: paper-level over `ranked` (cheap, one call), and
chunk-level inside `mode="evidence"` (the RCS analogue).

## 7. Build cost: the part to size before committing

**Full text is NOT cached.** `/opt/munin/data/papers/processed/` holds metadata
records only (277 MB of `{doi, paper_id, state, ingested_at}` JSON); every
`source` read re-parses the PDF through GROBID today.

So building the index means extracting **69,025 PDFs / 142 GB** through GROBID,
chunking, and embedding. Rough shape, to be replaced by a measured probe on ~200
papers before any commitment:

- ~20 chunks/paper at 512 tokens -> **~1.4M chunks**
- 1.4M x 1024 dims x 4 bytes = **~5.7 GB of vectors**, plus chunk text in payload
- GROBID is the bottleneck, not the GPU. At a few seconds per paper this is
  tens of hours and it competes with the live service.

**Cache the extracted text while doing it.** It removes the per-read GROBID cost
for every future `source` call, which is a second, independent win.

## 8. Risks

- **A 1.4M-point collection changes retrieval characteristics.** Chunk hits will
  crowd out paper-level diversity; expect to need per-paper capping (at most K
  chunks per document in the returned set).
- **GROBID failures are silent today** (`extraction_failed` falls back to
  abstract). At 69k papers a few percent failures is thousands of papers with no
  chunk coverage. Needs a coverage report, not a silent partial index.
- **Storage.** ~6 GB vectors plus payload text on a box already holding 142 GB
  of PDFs and a 68k-point collection.
- **Paper numbers move.** `papers_bge` is the collection every committed
  retrieval scorecard was measured against. A chunk index is a NEW collection
  and must not replace it silently; the retrieval benchmarks stay pinned to
  `papers_bge` unless deliberately re-baselined.
- **LLM scoring adds a call to every search.** One batched call is acceptable;
  watch that it does not become per-candidate under refactoring.

## 9. Test plan

- `chunk_text` boundary tests already exist for user_docs; extend for the
  paper payload shape.
- Provenance: assert every returned quote is a verbatim substring of the stored
  `chunk_text`; assert evidence without a locator is dropped.
- Tag scoping works against `papers_chunks` (a `/group` query must not return
  out-of-scope chunks).
- Per-paper chunk cap is enforced.
- Batched relevance scorer: N candidates in, N scores out, order preserved,
  malformed model output degrades to the cosine ordering rather than raising.
- The reproducer: `--scenario lov`. Target is the answer found with `search`
  called once and total tool calls in single digits.

## 10. Open questions for the operator

1. **Backfill scope.** All 68,808 papers, or start with the tagged/contributor
   sub-corpora (the group KBs people actually pin) and widen later?
2. **Embedding model.** `papers_bge` is BGE-large (1024d); `user_docs` is 768d.
   Chunks at 1024d cost ~5.7 GB and stay on one axis with the paper index.
   Confirm 1024d.
3. **When does the backfill run?** It is tens of hours of GROBID and competes
   with the live service and the nightly batch window.
4. **Retire `read=N`** (whole-paper reads) once evidence mode lands, or keep it
   as a fallback for documents with no chunk coverage?
5. **Scope of LLM relevance.** Paper-level only for now (one batched call per
   search), or chunk-level too from the start (a second batched call)?
