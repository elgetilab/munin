# Tag-Scoped Search — Operator Reference

§28 Sprint B, shipped 2026-04-20. Adds `#tag` chips to the chat
composer that narrow `paper_search` / `deep_research` results to a
research group, individual contributor, or topical cluster. Built on
§15 (topic tags) + §28 Sprint A (contributor tags).

Full design in `docs/future_features.md` §28 (lines 3416-3904).

## Tag taxonomy

Three tag kinds. All AND-combine when multiple are passed.

| Kind | Example chip | Backend filter | Source of truth |
|---|---|---|---|
| `topic` | `#nmr-studies-of-lipid-bilayers` | `payload.topic_slug == <slug>` | §15 `build_embedding_map.py` writes `topic_slug` onto every paper |
| `group` | `#zeitler` | `payload.contributors[].group_slug == <slug>` | §28 Sprint A — set when a paper is ingested via `/api/admin/ingest` with an allowlisted uploader |
| `contributor` | `#@alice` | `payload.contributors[].username == <username>` | Same as `group`; surfaces individual uploaders for the `#@username` shortcut |

Multiple tags → papers must match ALL (Qdrant `must`). Empty tags list
→ unfiltered search (current behaviour).

## Request shape

The frontend parses `#chips` from the chat input and POSTs them as a
structured field on `/api/chat/completions`:

```json
{
  "persona": "research",
  "messages": [{"role": "user", "content": "find papers on angioedema"}],
  "tags": [
    {"kind": "group", "value": "zeitler"},
    {"kind": "topic", "value": "nmr-studies-of-lipid-bilayers"}
  ]
}
```

`chat_service.stream_chat_completion` validates + normalises the list
(drops malformed entries, lowercases values, rejects unknown kinds),
then sets `retrieval.mcp.context.current_query_tags`. Every
`paper_search` call that follows reads the ContextVar and scopes its
Qdrant query. `deep_research` inherits transparently because it calls
`paper_search` internally.

The model itself **does not** manipulate tags. It doesn't see a `tags`
parameter in the tool schema — the tag flow is strictly composer →
request body → ContextVar → tool.

## Tool result additions

`paper_search` results now carry three new fields per paper:

```json
{
  "title": "...",
  "doi": "...",
  "score": 0.87,
  "matched_query": "...",
  "contributors": [
    {"display_name": "Contributor C",
     "group_slug": "zeitler",
     "group_display_name": "Zeitler Lab (Leipzig)"}
  ],
  "topic": {
    "label": "NMR studies of lipid bilayers",
    "slug": "nmr-studies-of-lipid-bilayers"
  },
  "download_url": "https://search.muninai.org/paper/..."
}
```

And at the response level, an `applied_tags` echo when the request
was scoped:

```json
{
  "queries_executed": [...],
  "total_hits": 12,
  "applied_tags": [{"kind": "group", "value": "zeitler"}],
  "results": [...]
}
```

Persona prompts in `personas/research.json` and `personas/chat.json`
now instruct the model to:

1. Credit contributors by `group_display_name` when citing a paper
   that has a `contributors[]` array.
2. Tell the user about any applied scope — "I searched the Zeitler
   Lab corpus" — so the user knows what was in vs. out of scope.

## Catalog endpoint

```
GET /api/tags
```

No auth — tag names are public. Returns:

```json
{
  "topics": [
    {"slug": "nuclear-magnetic-resonance-spectroscopy",
     "label": "Nuclear magnetic resonance spectroscopy",
     "paper_count": 1566},
    ...
  ],
  "groups": [
    {"slug": "zeitler",
     "display_name": "Zeitler Lab (Leipzig)",
     "paper_count": 1},
    ...
  ],
  "contributors": [
    {"username": "zeitler",
     "display_name": "Contributor C",
     "group_slug": "zeitler",
     "paper_count": 1},
    ...
  ]
}
```

Sorted by `paper_count` descending in each family. Topics come from
`/opt/munin/knowledge/embedding_map.json` (the §15 output, excluding
the `unclustered` noise bucket). Groups + contributors come from the
union of `config/contributors.yml` and observed Qdrant payloads.

The frontend fuzzy-matches `#<user input>` against this catalog for
chip autocomplete.

## Payload indexes

Created at retrieval startup (idempotent — `create_payload_index` is
wrapped in try/except so repeated starts are no-ops):

| Key | Type | Purpose |
|---|---|---|
| `contributors[].group_slug` | keyword | `#group` tag filter |
| `contributors[].username` | keyword | `#@user` tag filter |
| `contributors[].email` | keyword | admin queries |
| `topic_slug` | keyword | `#topic` tag filter |
| `cluster_id` | integer | future cluster-level queries |

Verify with:

```bash
curl -s http://127.0.0.1:6333/collections/papers_bge \
    | jq '.result.payload_schema'
```

Without these indexes, tag filters still work, but scale linearly
with corpus size. At 30k papers the difference is ~10ms vs. ~1s.

## Files

| File | Change |
|---|---|
| `retrieval/mcp/context.py` | new `current_query_tags` ContextVar |
| `retrieval/mcp/tools/papers.py` | `_build_tag_filter()`; `_qdrant_search_one` accepts `query_filter`; `paper_search` reads ContextVar, adds `tags` param, surfaces `contributors[]` + `topic` + `applied_tags` in results |
| `retrieval/chat_service.py` | `query_tags` param on `stream_chat_completion`; `_normalize_query_tags` sanitiser; sets `current_query_tags` alongside the other per-request contextvars |
| `retrieval/main.py` | plumbs `body.get("tags")` to chat_service; `GET /api/tags` catalog; startup creates payload indexes on the live paper collection (`PAPERS_COLLECTION`, default `papers_bge`) |
| `personas/research.json`, `personas/chat.json` | attribution + scope-acknowledgement guidance |

## Smoke test

```bash
# Catalog
curl -s http://127.0.0.1:8080/api/tags \
    | jq '{topic_count: (.topics|length),
           group_count: (.groups|length),
           contrib_count: (.contributors|length)}'

# Direct Qdrant filter via the tag filter syntax
curl -s http://127.0.0.1:6333/collections/papers_bge/points/scroll \
    -H 'Content-Type: application/json' \
    -d '{"filter": {"must": [{"key": "contributors[].group_slug",
         "match": {"value": "zeitler"}}]}, "limit": 5,
         "with_payload": ["doi","title","contributors"]}' | jq '.result.points'

# End-to-end tag-scoped chat
curl -N -s -X POST http://127.0.0.1:8080/api/chat/completions \
    -H "X-Munin-Email: you@example.org" \
    -H "Content-Type: application/json" \
    -d '{
      "persona": "research",
      "ephemeral": true,
      "messages": [{"role":"user","content":"find papers about angioedema"}],
      "tags": [{"kind":"group","value":"zeitler"}]
    }' | grep -E '^event: tool_(call|result)' -A 2 | head -40
```

The SSE stream's `tool_result` event for `paper_search` should carry
`"applied_tags": [{"kind": "group", "value": "zeitler"}]` and results
scoped to the Zeitler corpus only.

## Tuning

### Unknown tag values silently ignore

If the model (or a stale frontend) passes
`{"kind": "topic", "value": "nonexistent-slug"}`, the filter still
applies — just matches nothing. The request returns an empty result
set rather than an error. That's honest — "no papers match this
scope" — but watch for it in bug reports if users complain of empty
searches. Check `applied_tags` in the tool result to confirm which
filter was actually applied.

### Tag kinds validation

`_normalize_query_tags` in `chat_service.py` drops any tag whose
`kind` is not in `{"topic", "group", "contributor"}`. Extending to
new kinds (e.g. `year`, `journal`) requires:

1. Adding the kind to `_ALLOWED_TAG_KINDS` in `chat_service.py`.
2. Adding a branch in `_build_tag_filter` in `papers.py` that turns
   the tag into a `qm.FieldCondition` against the right payload key.
3. Creating a payload index in the startup hook of `main.py`.

### Frontend catalog caching

`GET /api/tags` is uncached server-side — every call scans the
allowlist and runs `qdrant.count` per group + contributor. Cheap
enough today (a few dozen ms per call) but worth adding a short TTL
cache if the composer polls it aggressively.

## Known limitations

### `#me` personal notes tag not wired

§28's original design had a third tag kind, `#me`, routing to the
user's personal notes collection. §9 (user memory) shipped separately
and covers most of that use case. If we later want a `#me` tag, the
filter branch would target the `user_notes` collection (not
`papers`), which means `paper_search` is the wrong place for it —
it'd need its own dispatch in `chat_service`. Not in this sprint.

### Tag inheritance across conversation turns

Tags are per-request. If the user opens a chat with `#nmr` then asks
a follow-up question without re-selecting the chip, the follow-up
does NOT inherit the tag (frontend would need to pin the chip across
turns or store `default_tags` on the conversation row). Mentioned in
`future_features.md` §28 open questions; not critical for v1.

### Semantic Scholar does not honour tags

`semantic_scholar_search` is an external HTTP API — tags don't apply.
If the user pins `#zeitler` and the model uses S2, the S2 results
aren't contributor-scoped. This is mentioned implicitly by
`applied_tags` being absent on S2 results, but nothing stops the
model from mixing scoped local hits with unscoped S2 hits. Persona
prompts tell the model to be explicit about scope in its prose so
the user sees what was in vs. out of scope; we rely on that honesty
rather than enforcing in code.
