# Search recall repair plan (2026-09-01)

Triggered by a chat export from a user (2026-08-20, kept
at the repo root as `Munin Chat Export - Document Listing & Sharing.md`).
He asked whether four named papers were in the corpus and whether he
could list his own uploads. Munin told him one paper could not be found
"in any scholarly database or on the web". That paper is in the corpus,
inside the very group scope he had attached.

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
| `search(query=<his real phrasing>)` | 6/6, and chunk evidence 6/6 |

The other three titles he asked about are genuinely absent from the
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

He asked twice for a list and got four apologies. That answer was
honest about the tools but wrong about the system: both capabilities
exist server-side and are used by the web UI today.

- `GET /api/documents` (`main.py:2739`) wraps
  `document_store.list_documents` and returns every uploaded document.
- `GET /api/tags/{kind}/{slug}/papers` (`main.py:1661`) is a paginated
  browse of a group corpus ordered by year, built for the Knowledge
  page (`KnowledgePage.tsx:319`). His request "list the first 10
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
scope "should scope this automatically" while searching his personal
document store. It does not: `search_user_docs` filters on user email
and project only. The FAQ also has no entry for the Knowledge page, so
the model could only tell him vaguely to check the interface.

## Not a defect: his empty document store

Checked for a silent indexing gap and did not find a systemic one.
Every user directory holding text files has matching points in
`user_docs`; the 15 directories with zero embeddings hold only pasted
PNGs, which are correctly never embedded. Email casing is normalised at
both `main.py:604` and `document_store.py:54`, and the index holds no
casing anomalies. His four empty searches most likely reflect an
account with no text uploads.

One unexplained residue, tracked as phase 4 below: user hash
`d77e6cc01c9b8228` has 9 text files on disk (7 `.docx`, 1 `.md`,
1 `.pdf`) and 3 embedded documents.

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

### Phase 3: tell the truth about scope (defect 3)

`chat_service.py::build_active_tags_block`, `mcp/tools/faq.py`. Name
the tools tags actually scope, state that uploaded documents are not
tag-scoped, and add the Knowledge page to the FAQ so the model can
point at a real place.

### Phase 4: investigate the docx indexing residue

`document_store.py::_extract_docx`. Reproduce against the affected
files, establish whether extraction fails silently, whether the failure
is logged, and whether `list_documents` reports such a document as
embedded when it is not. Fix or write up depending on what it turns
out to be. Scoped as an investigation, not a known fix.
