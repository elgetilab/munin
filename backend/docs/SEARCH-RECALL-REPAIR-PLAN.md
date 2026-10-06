# Search recall repair plan (2026-09-01)

Triggered by a chat export from a user (2026-08-20). The
export itself was removed from the repo once this work landed: it is
one user's conversation and it does not belong in a tree being prepared
for public release. Everything it evidenced is quoted below. They asked
whether four named papers were in the corpus and whether they could list
their own uploads. Munin told them one paper could not be found
"in any scholarly database or on the web". That paper is in the corpus,
inside the very group scope they had attached.

Three independent defects sit behind that conversation. Only the first
is a retrieval bug; the other two are missing capabilities that made
the retrieval bug unrecoverable, because the model had no way to browse
what it could not find by searching.

## Measured impact

The paper is `10.1002/aenm.202505525`, "Device Performance of Emerging
Photovoltaic Materials (Version 6)", 2026. It carries
`contributors[].group_slug = deibel`, one of 1572 papers in that scope.
Dense retrieval ranks it first at cosine 1.0 for its own title, so
neither the encoder, the collection nor the tag filter is at fault.

Measured against the live index, three full runs of
`backend/retrieval/tests/test_paper_search_merge.py`:

| Path | Recall of the paper the user named |
|---|---|
| `paper_search(query=title, top_k=5)` | 12/18 runs, at rank 4-5 when present |
| the same retrieval, re-sorted by score alone | rank 1 in 3 of 3 runs |
| `paper_search(query=title, top_k=50)` | absent, then rank 36, then rank 11 |
| `search(query=<the user's real phrasing>)` | 6/6, and chunk evidence 6/6 |

The other three titles they asked about are genuinely absent from the
corpus, so those answers were correct.

Two facts shape the fix. First, the failure is intermittent: the query
expander runs at temperature 0.5, so the same question flips between
"here it is" and "this paper does not exist" between attempts. A
confident false absence is the worst shape a retrieval failure can
take, because nothing downstream can tell it from a true one. Second,
asking for MORE results makes recall worse, since `per_query =
max(top_k, 5)` widens the fan-out along with the window and recruits
more agreement hits to crowd the front. At `top_k=50` all 50 returned
rows had `matched_by > 1`.

## Defect 1: the fan-out merge ranks on agreement, not similarity

Severity: P1. Cause of the false absence.

`paper_search` (`mcp/tools/papers.py`) expands one query into up to five
variants, runs them in parallel, dedupes, and merges:

```python
merged = sorted(seen.values(), key=lambda r: (-r.get("matched_by", 1), -r.get("score", 0)))
```

`matched_by` counts how many variants surfaced the paper. As the
primary key it is a frequency prior that overrides cosine outright. An
exact-title query matches its own paper on the base query alone, while
the generic paraphrases the expander invents agree with each other on
survey papers, which then win on count. In a live merge the exact title
carried the highest cosine of the whole candidate set (0.821) and
sorted fifth behind four lower-scoring papers.

`search()` is immune because `_dedup_and_rank` re-scores every tier on
one shared relevance axis and explicitly demotes the legacy per-tier
score to a tie-break (`search_agent.py:302-304`, 2026-07-24). That
lesson was applied inside `search_agent` and nowhere else.

The same frequency-first rule appears at two more sites:

- `semantic_scholar_search` (`papers.py`), sorting
  `(-matched_by, -citation_count)`. S2 returns results in its own
  relevance order and we discard that order entirely.
- `_merge_papers` (`research.py`), the Deep Research candidate
  merge, sorting `(-matched_by, -citation_count)`. Worse: its `_add`
  helper never copies `score` into the merged row, so the corpus
  similarity is thrown away before ranking and relevance plays no part
  in Deep Research's candidate order at all.

`web.py` sorts frequency-first too, but that is rank fusion across
search engines with no comparable per-hit score, and `search_agent`
already demotes it downstream. Left alone deliberately.

## Defect 2: the model cannot enumerate anything

Severity: P1 for trust, and the reason defect 1 was unrecoverable.

They asked twice for a list and got four apologies. That answer was
honest about the tools but wrong about the system: both capabilities
exist server-side and are used by the web UI today.

- `GET /api/documents` (`main.py:2739`) wraps
  `document_store.list_documents` and returns every uploaded document.
- `GET /api/tags/{kind}/{slug}/papers` (`main.py:1661`) is a paginated
  browse of a group corpus ordered by year, built for the Knowledge
  page (`KnowledgePage.tsx:319`). Their request "list the first 10
  documents in the Available knowledge / Deibel group" maps onto
  `/api/tags/group/deibel/papers?limit=10` exactly.

Neither is registered in `mcp/schemas.py`. A browse path also removes
the ranking question from this class of question entirely: "what is in
this collection" should never be answered by a similarity search.

## Defect 3: the scope block overstates what tags scope

Severity: low, but it fed the confusion in this transcript.

`build_active_tags_block` (`chat_service.py:1758-1762`) tells the model
that tags scope "every paper_search / deep_research call". The model
generalised that to uploaded documents and told the user its `#deibel`
scope "should scope this automatically" while searching their personal
document store. It does not: `search_user_docs` filters on user email
and project only. The FAQ also has no entry for the Knowledge page, so
the model could only tell them vaguely to check the interface.

## Not a defect: the user's empty document store

Checked for a silent indexing gap and did not find a systemic one.
Every user directory holding text files has matching points in
`user_docs`; the 15 directories with zero embeddings hold only pasted
PNGs, which are correctly never embedded. Email casing is normalised at
both `main.py:604` and `document_store.py:54`, and the index holds no
casing anomalies. Their four empty searches most likely reflect an
account with no text uploads.

One residue did turn out to be a real bug, though it belongs to a
different user and did not affect this transcript: user hash
`d77e6cc01c9b8228` has 9 text files on disk (7 `.docx`, 1 `.md`,
1 `.pdf`) and 3 embedded documents. Diagnosed and fixed in phase 4
below; the six missing files are table-shaped `.docx` that the
paragraph-only extractor read as empty.

## Phases

### Phase 1: rank on similarity (defect 1)  — DONE (1bff8a3)

`papers.py`, `research.py`. Tests:
`tests/test_paper_search_merge.py`, 11 passing. Written before the fix
and red on exactly the four assertions this phase had to flip.

1. `paper_search`: rank on `score`, with agreement as a bounded
   additive bonus (`+0.002` per extra variant, capped at four) that can
   break a near-tie but never overturn a real gap. `matched_by` stays
   in the payload: it is genuine signal and the model reads it.
2. `paper_search`: reserve one slot in the window for the top hit of
   `query_list[0]`, which `expand_queries` guarantees is the caller's
   literal wording. The expander's opinion may reorder the tail, never
   evict the literal query's best hit.
3. `semantic_scholar_search`: rank on S2's own returned position (best
   rank across variants), with agreement and citations as tie-breaks,
   and the same reserved slot. S2's relevance order is the only score
   that tier has, so stop discarding it.
4. `research.py::_merge_papers`: carry `score` through the
   merge, and rank by each upstream tool's own position rather than
   re-ranking on a frequency prior. Local before S2 at equal position,
   matching the existing trust order. Deep Research then inherits
   phase 1.1 automatically.

Measured after: live recall of the named paper 12/18 -> 6/6, at rank 1
in every run, and `top_k=50` from absent/36/11 -> rank 2. No
regressions across test_search_expansion, test_search_ranking,
test_search_relevance, test_search_agent_hygiene, test_source_evidence,
test_dispatch_registry, test_tool_result.

### Phase 2: give the model a browse path (defect 2)  — DONE

New `tag_browse.py`, new `mcp/tools/knowledge.py`,
`mcp/tools/documents.py`, `mcp/schemas.py`, `mcp/dispatchers.py`,
`main.py`. Tests: `tests/test_browse_tools.py`, 17 passing.

1. `tag_browse.py` holds the browse implementation, extracted from the
   `/api/tags/{kind}/{slug}/papers` route so the endpoint and the tool
   run the same code and the UI and the model can never disagree about
   what a collection contains. Verified byte-identical to the live
   endpoint's response on `group/deibel` before and after.
2. `list_documents`: wraps `document_store.list_documents`, reads
   `current_user_email` from the MCP context the way `search_user_docs`
   does. Returns filename, chunk count, upload time and `status` per
   document, plus a true `total`.
3. `browse_tag_papers`: wraps `tag_browse` with `offset` / `limit` /
   `sort`, defaulting `kind` and `slug` from the active scope tags, and
   refusing to guess when two tags are attached (they AND-combine in
   search, but a browse takes one collection).
4. Neither goes in `CORE_TOOLS`: both are occasional-use, and the
   schema is kept small on purpose. They are reachable through
   `tool_search`, whose IDF scorer drops "list" as a stopword, so the
   descriptions carry "enumerate", "browse", "catalog" and "inventory"
   instead. A test asserts discovery for the phrasings the model in the
   transcript actually tried and failed with.
5. `BACKEND-API.md` unchanged: the endpoint's contract, including its
   error shapes and statuses, is preserved exactly.

Coverage honesty is the load-bearing property in both tools, not the
listing. Each reports the collection's real `total` and says when a
page is partial, because "here are the papers" over page one of 1572 is
the same failure this work exists to fix, wearing a different costume.
An empty result is self-describing for the same reason: zero hits from
a SEARCH are ambiguous, zero from an ENUMERATION are not, and the tool
says which it is so the model stops hedging about vocabulary mismatch.
`list_documents` also surfaces per-document `status`, so a file that is
stored but not embedded is never reported as searchable (that is also
the signal phase 4 needs).

### Phase 3: tell the truth about scope (defect 3)  — DONE

`chat_service.py::build_active_tags_block`, `capabilities.py`,
`config/faq.yml`. Tests: `tests/test_self_description.py` (10) and two
new cases in `tests/test_query_tags.py`.

Investigating this one moved the centre of gravity. The scope block was
the smaller half; the six user-facing features the model recited at the
user, almost verbatim, come from `capabilities._STATIC_FEATURES`, and
neither the Knowledge page nor tag scoping was in that tuple. That is
why the answer was "check your interface directly" rather than a
pointer: from where the model sat, those features did not exist.

1. `_STATIC_FEATURES` gains the two missing entries, with a comment
   recording that this list is read out as an ANSWER, so an omission
   tells a user a feature is absent rather than merely underdocumented.
2. `build_active_tags_block` now says the tags apply to CORPUS SEARCH
   ONLY, names the four tools that honour them, states that
   `search_user_docs` is scoped by account and project and never by
   tag, and routes listing requests to `browse_tag_papers` on the
   grounds that a similarity search returns a sample.
3. `faq.yml` gains a `knowledge_scope` topic covering what tags scope,
   what they do not, and where to browse; `upload_documents` now points
   at `list_documents` for inventory questions and says uploads are
   never tag-scoped. `deploy.sh agents` already syncs this file, so no
   deploy change is needed.

### Phase 4: the docx indexing residue  — DONE

`document_store.py`, new `scripts/maintenance/backfill_user_docs.py`,
`BACKEND-API.md`, `frontend/webui/src/lib/api.ts`. Tests:
`tests/test_docx_extraction.py`, 11 passing.

It was a real parser bug, and a silent one.

**Diagnosis.** All six unembedded files are valid OOXML. Every one has
ZERO paragraph text and exactly one table carrying the whole document,
187 to 3636 characters. The seventh, the one that worked, is
paragraph-shaped: 358 paragraphs, no tables. `_extract_docx` read only
`document.paragraphs`, so a table-shaped document extracted to the
empty string, chunked to nothing, and embedded nothing. A form, a
questionnaire or a lab record exported to Word is table-shaped more
often than not.

**Why it survived three months.** `upload_document` returns
`status: "stored"` for a zero-chunk document, which is exactly what it
returns for an image, and logged nothing at all. So a `.docx` the
parser could not read was indistinguishable from a screenshot, at the
API, in the logs, and in the UI. The user was told the upload
succeeded.

**Fix.**

1. `_extract_docx` walks the body in document ORDER, collecting
   paragraphs and table rows (cells joined with `|`, merged cells
   deduped on the underlying XML element). Order matters so a table
   stays next to the prose that introduces it. Still uncovered, and
   noted in the docstring: headers/footers, text boxes, and tables
   nested inside a cell. None appeared in the corpus.
2. `upload_document` now returns a `reason` on a text-type `stored`
   response (`no_text_extracted` / `no_chunks` / `index_unavailable`)
   and logs a warning naming the file, so the next shape the parser
   cannot read announces itself on the first upload rather than on a
   complaint. Images still carry no reason: storing without embedding
   is correct for them, and a reason string would read as a defect.
3. `BACKEND-API.md` 4.9 documents the field; the frontend
   `UploadedDocument` type carries it as optional (`tsc` clean, no UI
   change).

**Blast radius, measured corpus-wide.** Exactly 8 uploaded text
documents on disk have no points in `user_docs`. Six are the `.docx`
above, all one user, all now recoverable. The other two are PDFs from a
different user, 5.7 MB and 11.5 MB, produced by "Skia/PDF" (printed
from a browser) and "Microsoft: Print To PDF"; `pdftotext` gets zero
characters from either. They have no text layer, so this is not a
parser gap but a missing OCR path, addressed in phase 5.

**Backfill.** `scripts/maintenance/backfill_user_docs.py` re-extracts
and embeds on-disk documents that are missing from the index.
Idempotent, `--dry-run` first. Two inherent limits it documents rather
than papers over: `conversation_id` and `project_id` were only ever
written to the Qdrant payload, so a recovered document comes back
user-global (still found, via search_user_docs' existing fallback, just
not preferred inside its project); and a user email is recovered by
hashing indexed emails against the one-way directory name, so a user
whose every document failed is reported rather than guessed
(`--email` handles that). Recovered points carry `backfilled: true`.

### Phase 5: OCR for uploaded PDFs with no text layer

`retrieval/Dockerfile`, `document_store.py`, `main.py`,
`scripts/maintenance/backfill_user_docs.py`, `BACKEND-API.md`,
`frontend/webui/src/lib/api.ts`. Tests: `tests/test_pdf_ocr.py`, 11
passing.

Phase 4 found two uploads that are page images with no text layer, so
no parser can read them. The papers pipeline had OCR'd scanned papers
for months; user uploads had no such path.

**Where OCR runs.** In the retrieval container, using the same
`ocrmypdf --skip-text` invocation `paper_pipeline.py` already uses, so
there is one OCR behaviour to reason about rather than two. That costs
~360 MB on an 8.7 GB image, about 4%, which is cheaper than the
alternative of a host-side sweep: ocrmypdf lives on the cluster head
but the BGE encoder and the Qdrant client live in the container, so a
host-side design has to straddle both and ends up shelling back into
the container to embed.

**When it runs.** NOT during the upload request. Measured on the two
real documents, OCR takes 8.4 s and 38.3 s, and a 50 MB scan would take
longer, so holding the request open is not acceptable.
`upload_document` returns immediately with `reason="no_text_layer"` and
an internal `ocr_pending` marker; the route turns that into a FastAPI
`BackgroundTask` calling `ocr_and_embed`. The document flips from
stored to embedded a minute or so later and `list_documents` reflects
it. `ocr_and_embed` is idempotent (it checks for existing points), so a
retry after a restart, a duplicate upload, or a concurrent sweep cannot
double-embed.

**Recovering what already exists.** `backfill_user_docs.py --ocr`
sweeps PDFs that predate this path or whose OCR was interrupted, going
through the same `ocr_and_embed`. Results are cached by content hash,
so a re-run costs a disk read rather than another minute of tesseract.

**Measured on the two real files:** 28,604 characters / 17 chunks in
8.4 s, and 53,699 characters / 35 chunks in 38.3 s. Both are real
scientific papers that were previously unreachable.

**Honest limits.** Only the English tesseract pack is installed, since
the corpus is English and each extra language is ~15 MB. An OCR pass
that still yields nothing (a blank scan, a photograph, an unsupported
language) is logged and left as `stored` rather than retried forever.

### Phase 5a: deleting a document must take its OCR'd copy with it

`document_store.py`, `scripts/maintenance/backfill_user_docs.py`.
Tests: 3 more in `tests/test_pdf_ocr.py`, 14 passing.

Found by smoke-testing phase 5 end to end rather than by review. A real
scanned upload OCR'd and embedded correctly in about a second; deleting
it through `DELETE /api/documents/{id}` removed the Qdrant points and
the file tree and left `ocr_cache/<hash>_ocr.pdf` behind, which is the
document's full content as a searchable-text PDF. Same shape as
KNOWN-BUGS #1, where deleting a conversation orphans its proposed
memories: a user who deletes a document expects the derived copy to go
too.

- `ocr_cache_path()` now derives the key in ONE place, because the
  writer and the evictor have to agree and a second copy of
  `sha256(...)[:16]` is how an eviction silently stops matching.
- `delete_document` evicts BEFORE `rmtree`: the key is the hash of the
  file's bytes, so afterwards there is nothing left to derive it from
  and the cached copy is orphaned permanently.
- Eviction is unconditional even though the cache is content-addressed
  and two users could share an entry. The cache is derived data, the
  surviving document is already embedded so nothing reads it, and
  keeping a deleted user's content to save a future minute of tesseract
  is the wrong trade.
- `--prune-ocr-cache` clears what earlier deletions left behind. Run
  live: 1 orphan found and removed, from the smoke test itself.
