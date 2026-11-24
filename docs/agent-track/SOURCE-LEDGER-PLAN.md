# Source ledger: cite by id, render on the server

Status: PLAN (2026-11-24). Phase B of CITATION-GROUNDING-PLAN.md, approved to
start; plus the context-budget findings the owner asked for.

## Problem (recap)

Wrong references come mostly from model memory (15 of 25 traced), then from
miscopying (6) and from wrong corpus records (4). When a tool supplies a DOI
the model copies it (74 of 80); it invents what the tools do not show and
anything cited after the tool result has left the context. The model writes
every DOI, author list, journal and artifact URL as free text.

## Design

### 1. Ledger (`backend/retrieval/source_ledger.py`, new)

A persisted per-conversation table in chats.db:

    sources(conversation_id, sid, kind, key, record_json, first_turn, PRIMARY KEY(conversation_id, sid))

- `kind`: paper | web | artifact | upload.
- `key`: normalised DOI, else canonical URL, else corpus paper id, else
  artifact id. The same work keeps the same id for the whole conversation.
- `sid`: `S1, S2, ...` for papers, web pages and uploads; `A1, A2, ...` for
  artifacts.
- `record`: what the tools returned (title, authors, year, journal, DOI, URL,
  paper id, snippet), filled out with `bibref` (journal, year, full author
  list) when the id is first cited, not when it is first seen.

### 2. Harvest (where tool results enter the loop)

At `chat_service.py` where each result becomes a `role: tool` message
(`truncate_tool_result(res["result"])`), a generic harvester walks the result
and gives every item that looks like a source (a dict with a DOI, or a URL and
a title, or a corpus paper id and a title) an `sid` field in the model's view.
No per-tool code: search, paper_search, source, read_paper, citations,
s2_citations, compare_papers, web_search and web_fetch are all covered.
Artifacts get an `A` id from the `artifact_created` path, with their real
`/api/artifacts/...` URL in the record. DOIs the user typed in the message
become sources too.

### 3. Prompt

The research persona's citing rules change to: cite sources only as `[S7]`
(several: `[S3, S7]`), artifacts as `[A2]`; never type a DOI, a paper URL, an
artifact URL or a reference list; if no retrieved source supports a claim, say
so instead of citing. The other personas that cite (router, deep research
hand-off) get the same rule.

### 4. Render (post-turn, where citecheck runs)

- `[S7]` becomes a numbered link `[1](https://doi.org/...)` (or the URL), in
  order of first use, and a **References** list built from the records is
  appended: authors, year, title, journal, DOI.
- `[A2]` becomes the artifact's real link.
- An id that is not in the ledger is removed and counted (`notes.unknown_ids`).
- Delivered with the existing `answer_revised` event; the saved answer is the
  rendered one. The stream shows raw ids until the swap; the frontend renders
  `[S7]` as a small neutral chip so the stream does not look broken.
- citecheck still runs afterwards, on everything, as the backstop for DOIs the
  model still types and for wrong corpus records.

### 5. Later turns

Prior tool results are not replayed today. Instead, each later turn gets a
compact **sources block** in the system context: `S7: Sali A, 1991, Folding
of a small protein (J Mol Biol)`, newest and most-cited first, capped at
about 1,500 tokens (about 40 entries). Earlier sources stay citable by id
without replaying raw results.

### 6. Context budget (the research the owner asked for)

Verified: the KV pool holds about 211K tokens (`vllm-service` log: "GPU KV
cache size: 210,993 tokens"; 10 of 40 layers keep KV, FP8). The 64K limit is
`VLLM_MAX_MODEL_LEN=65536` in `config/models/sage-a1.5.env`, a setting. The
model declares 262,144 positions. With `max_num_seqs=2`, 98,304 per sequence
fits two full sequences without preemption.

Literature (sources in the research notes): masking old observations matches
or beats LLM summarisation at half the cost (The Complexity Trap, 2025);
paged viewers beat dumping whole outputs (SWE-agent); Anthropic's context
editing clears old tool results above a trigger in batches.

Changes, in order:

1. **Token-based, rank-aware tool views** replace `truncate_tool_result`:
   evidence passages first, each capped (about 350 tokens around the match)
   instead of dropped; lower-ranked hits and fields already in the ledger go
   first. Each view ends with `{shown: k/n, handle: R12}`.
2. **`expand_result(handle, item, offset)`**: one new tool that pages the full
   stored result, so trimming loses nothing permanently.
3. **Elision by ledger**: an elided result becomes
   `{"_elided": true, "sources": ["S7", "S9"], "handle": "R12"}` instead of
   "re-run the tool", cleared in batches so the prefix cache is not broken on
   every step.
4. **Window**: measure 64K vs 96K on the eval instance (needle tests below plus
   the user-failure slice); raise production only if 96K is not worse.

### 7. Tests

Hermetic unit tests (`tests/test_source_ledger.py`): harvest across every tool
shape, stable ids across turns, dedupe by DOI and URL, render (numbering,
reference list, unknown ids, artifacts), the sources block budget, and the
tool-view budget (top evidence always survives; the old "all 8 passages
dropped" envelope as a regression case).

A new benchmark track, **grounding** (`munin_bench/grounding`, synthetic, no
user data, committed with its cases):

- citation by id: a question answered from search results; every rendered
  reference must be a ledger record, no typed DOIs, no unknown ids
- cross-turn: turn 3 cites a paper found in turn 1
- artifact reuse: turn 2 links an artifact made in turn 1
- needle in a tool result: a fact planted in passage k of a large search
  envelope, k = 1..8, at 64K and 96K
- long session: 15 to 25 tool calls, then a recall question about the first
- mechanical checks only (exact ids, links resolve, the needle value), so no
  judge is needed; the user-failure slice keeps the Opus judge

### 8. Measurement

On the eval instance (free GPU, `deploy.sh instance up`), never routed to users:

- the 30-case citation slice and the full 111 cases, 3 replicates each,
  baseline (current main) vs ledger, Opus 5.5 judge, replicate-averaged sign
  test on rubric points
- the grounding track, both arms
- share of references rendered from the ledger vs typed by the model
- unregistered DOIs per answer

Done when: rubric points on the slice up with p < 0.05, unregistered DOIs down
by at least half, no significant regression on the full run, grounding track
at least 90% on citation by id.

## Order of work

1. Ledger, harvest, render, persona rule, frontend chip; unit tests.
2. Grounding track; baseline it on the eval instance.
3. Ledger arm on the eval instance; compare.
4. Context-budget changes 1 to 3, measured the same way; then the 64K/96K arm.
5. Deploy what passes.

## Open questions (defaults in bold)

1. An id the model invents (not in the ledger):
   - **remove it and count it**
   - replace it with a visible `[source?]`
2. A DOI the model still types that is in no retrieved source:
   - **leave it to citecheck (it flags it only if it fails a check), count it**
   - mark every such DOI "not from a retrieved source"
3. Reference style:
   - **numbered `[1]` in the text, a References list at the end**
   - author-year in the text
4. Window:
   - **measure 96K on the eval instance first, then decide for production**
   - raise production to 96K now
