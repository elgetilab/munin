# Knowledge Browser + `#tag` Composer — Frontend Hand-off

Backend contract for three frontend surfaces:

1. **`#tag` chips in the chat composer** — filter the model's search
   to a specific group / contributor / topic.
2. **Chat sub-tab "Available Knowledge"** — side panel listing the
   tags the user can attach, with a click-to-apply UX.
3. **Header tab "Knowledge"** — full-page browser over contributors,
   topics, and the §15 2D embedding map.

All the endpoints below are live on `http://127.0.0.1:8080` on the
cluster (exposed to the VPS via the existing autossh tunnel at
`127.0.0.1:18080`). Each route uses the standard auth scheme:
`X-Munin-Email` header forwarded by Caddy; `/api/tags*` and
`/api/embedding_map` are **public** (no auth required, tag metadata
is non-sensitive).

---

## 1. Tag taxonomy

Three tag kinds — all AND-combine when the user stacks multiple chips.

| Kind | Example | Filters |
|---|---|---|
| `topic` | `#nmr-studies-of-lipid-bilayers` | Papers in that §15 cluster |
| `group` | `#zeitler` | Papers contributed by that research group |
| `contributor` | `#@alice` | Papers contributed by that individual |

Multiple chips: "papers contributed by Zeitler **AND** in the NMR
cluster" = `[{kind: "group", value: "zeitler"}, {kind: "topic", value: "nmr-studies-of-lipid-bilayers"}]`.

Backend design lives in `docs/TAG-SCOPED-SEARCH.md`.

---

## 2. Endpoints

### 2.1 `GET /api/tags` — catalog for autocomplete

Feeds composer autocomplete and the knowledge browser landing view.
No auth.

**Response (live, smoke-tested 2026-04-20):**

```json
{
  "topics": [
    {
      "slug": "nuclear-magnetic-resonance-spectroscopy",
      "label": "Nuclear magnetic resonance spectroscopy",
      "paper_count": 1566
    },
    { "slug": "rhodopsin-gene-expression-and-characteri",
      "label": "Rhodopsin gene expression and characterization",
      "paper_count": 994 },
    ... ~200 total at corpus size 29,894, sorted desc by paper_count
  ],
  "groups": [
    {
      "slug": "zeitler",
      "display_name": "Zeitler Lab (Leipzig)",
      "paper_count": 1
    },
    { "slug": "corzilius",
      "display_name": "Corzilius Lab (Rostock)",
      "paper_count": 0 },
    { "slug": "deibel",
      "display_name": "Deibel Group (Chemnitz)",
      "paper_count": 0 }
  ],
  "contributors": [
    {
      "username": "zeitler",
      "display_name": "Contributor C",
      "group_slug": "zeitler",
      "paper_count": 1
    },
    ...
  ]
}
```

- Sort within each family: `paper_count` descending.
- `paper_count: 0` means the allowlist knows the group but nothing has
  been ingested yet. Render them but greyed out.
- **Caching**: stale-while-revalidate for 60-300 s is fine. The
  catalog updates only when a new paper is ingested (rare during chat
  sessions).

### 2.2 `GET /api/tags/{kind}/{slug}/papers` — paginated browse

**Shipped 2026-04-20.** Drives the "all papers by Zeitler Lab" and
"all NMR-studies-of-lipid-bilayers papers" pages. This is a browse
view, not a ranked search — no SPECTER, no query required.

- `kind`: `topic` / `group` / `contributor`
- `slug`: the value (topic slug, group slug, or username)
- `?offset=0&limit=50` — pagination
- `?sort=year_desc` (default) / `year_asc` / `upload_desc`
  - `upload_desc` sorts by the newest `contributors[].upload_time`, which
    is what you want for "recently added" views on group pages.

**Response shape:**

```json
{
  "kind": "group",
  "slug": "zeitler",
  "total": 418,
  "offset": 0,
  "limit": 50,
  "sort": "year_desc",
  "papers": [
    {
      "title": "Angioedema: 5 Years' experience...",
      "doi": "10.1288/00005537-199203000-00005",
      "year": 1992,
      "authors": ["Era Van Rijnsoever", "Wilma Kwee-Zuiderwijk", "Johannes Feenstra"],
      "journal": "The Laryngoscope",
      "contributors": [
        {"display_name": "Contributor C",
         "group_slug": "zeitler",
         "group_display_name": "Zeitler Lab (Leipzig)",
         "upload_time": "2026-04-20T13:28:28.622014Z"}
      ],
      "topic": {
        "label": "Renin-angiotensin system in pregnancy",
        "slug": "renin-angiotensin-system-in-pregnancy"
      },
      "download_url": "https://search.muninai.org/paper/10.1288%2F00005537-199203000-00005/pdf"
    }
  ]
}
```

The `topic` field is present once the paper has been clustered by the
nightly §15 run — newly-ingested papers have it missing until the
next rebuild fires (or the operator kicks the timer manually).

- `total` is the full filtered-corpus count — use for "Page 1 of 9"
  and infinite-scroll stop conditions.
- Papers that lack a PDF on disk omit `download_url`. Render a "no
  PDF" badge rather than breaking.
- Within a page the server sorts by `year`. Across pages the same
  ordering holds, so infinite scroll works.
- Max `limit`: 200 per request.

### 2.3 `GET /api/embedding_map` — 2D scatter data

Served by §15. Static JSON (re-emitted by the nightly timer), ~5-10
MB on the current 30k corpus. No auth.

**Response shape:**

```json
{
  "generated_at": "2026-04-20T12:09:03.390034Z",
  "paper_count": 29893,
  "cluster_count": 213,
  "points": [
    {
      "id": "15408098168977121000",
      "doi": "10.1288/00005537-199203000-00005",
      "title": "Angioedema: 5 Years' experience...",
      "year": 1992,
      "x": 1.034,
      "y": 4.697,
      "cluster": 186
    },
    ... 29893 total
  ],
  "clusters": [
    {
      "id": 186,
      "label": "NMR studies of lipid bilayers",
      "slug": "nmr-studies-of-lipid-bilayers",
      "size": 216,
      "centroid": [0.71, -0.58]
    },
    ... 213 total (+ cluster id -1 "Unclustered" for HDBSCAN noise)
  ]
}
```

Cluster `id: -1` / slug `unclustered` contains ~12.6k papers (42% of
corpus, HDBSCAN's honest noise bucket). Decide whether to:
- Hide noise points entirely (clean map, ~17k visible points), or
- Render them in grey at low opacity.

Recommended renderer: [`deck.gl`'s `ScatterplotLayer`](https://deck.gl/docs/api-reference/layers/scatterplot-layer)
or D3 with canvas. SVG won't perform well at 30k+ points.

Hover → show `title`. Click → navigate to the topic's browse page
(`/knowledge/topic/<slug>`) which renders the detail via the browse
endpoint above. Colour by `cluster` id using a repeating palette;
expect 100+ distinct clusters.

### 2.4 `GET /paper/{doi:path}/enriched` — full paper details

Existing endpoint (live). Returns the full enriched paper record —
abstract, Neo4j citation/reference counts, authors with roles, PDF
availability, journal, year, and DOI. Use for the paper detail
modal when the user clicks a specific paper in either the map or
the browse list.

### 2.5 `POST /api/chat/completions` — already accepts `tags`

Existing chat endpoint. Add a `tags` field to the request body:

```json
{
  "persona": "research",
  "messages": [{"role": "user", "content": "find angioedema papers"}],
  "tags": [
    {"kind": "group", "value": "zeitler"},
    {"kind": "topic", "value": "nmr-studies-of-lipid-bilayers"}
  ]
}
```

Backend validates + sanitises (drops malformed entries, unknown
kinds, empty values). The server-side `current_query_tags` ContextVar
makes every `paper_search` tool call automatically scope to the
tags — the model doesn't see a `tags` parameter in its tool schema
and doesn't need to manipulate tags itself.

**Streaming event effects you can surface in the UI:**
- Tool-result events for `paper_search` now carry an `applied_tags`
  field when tags were in effect. Use this to render "Searched the
  Zeitler Lab corpus" above the result list.
- Individual paper result rows now include `contributors` and
  `topic` subfields. Use these to render contributor badges and
  topic chips on each result card.

---

## 3. UX proposals

### 3.1 Composer `#tag` chips

- On `#` keypress, open an autocomplete dropdown populated from
  `GET /api/tags`.
- Fuzzy-match across all three families at once. Show section
  headers (`Topics`, `Groups`, `Contributors`) inside the dropdown.
- Rules for parsing:
  - `#zeitler` → prefer `group` kind (most common intent); if the
    slug doesn't match any group, fall back to `topic` then
    `contributor`.
  - `#@alice` (explicit `@`) → force `contributor` kind.
  - `#topic:nmr-...` (explicit prefix) → optional, for advanced
    users disambiguating.
- Selected chips render inline in the composer input with a remove
  `×` button each. Multiple chips stack.
- Chips are NOT part of the message text — they're extracted before
  submission and sent in the request body's `tags` field. The
  actual user message is the remaining text.
- Pre-fill the composer with chips when arriving from a knowledge
  browser page (`/knowledge/group/zeitler` → composer starts with
  `#zeitler`).

### 3.2 Chat sub-tab "Available Knowledge"

A collapsible side panel next to the chat. Three accordion sections:

- **Topics**: top-10 by `paper_count` visible; "Show all 213" link
  → navigates to `/knowledge/topics`. Clicking any topic chip adds
  it to the composer.
- **Research Groups**: all 3 groups (Corzilius, Deibel, Zeitler).
  Click → adds `#<slug>` chip + injects a hint message ("Scoping
  to Zeitler Lab corpus").
- **Contributors**: individuals; most useful for groups with >1
  member.

Counts come from `GET /api/tags` and should be formatted as
"1,566 papers". Groups with `paper_count: 0` render greyed out with
a tooltip "No papers uploaded yet".

### 3.3 Header "Knowledge" tab (full page)

Route: `/knowledge`. Primary page structure:

1. **Overview bar** (top):
   - Total papers: from `GET /api/embedding_map` → `paper_count`
   - Topics: `cluster_count`
   - Research groups: `groups.length` from `GET /api/tags`
   - Total contributors: `contributors.length`

2. **2D embedding map** (center / main visual):
   - deck.gl scatter, coloured by cluster id, hover → title
   - Sidebar: cluster list sorted by size, filterable by search
   - Click a cluster chip → filter map to that cluster + show
     "View all N papers in [topic]" link
   - Click a point → paper detail modal (from
     `GET /paper/{doi:path}/enriched`)

3. **Research groups** (grid of cards below the map):
   - Each card: `display_name`, `paper_count`, 3 sample paper
     titles (from `GET /api/tags/group/{slug}/papers?limit=3`)
   - "Browse X papers" → `/knowledge/group/<slug>`

4. **Sub-pages** (drilldowns):
   - `/knowledge/group/<slug>` — paginated list via
     `GET /api/tags/group/<slug>/papers`. Secondary nav: year
     filter, topic filter (intersection with cluster data).
   - `/knowledge/topic/<slug>` — same pattern via
     `GET /api/tags/topic/<slug>/papers`.
   - "Discuss in chat" button top-right → opens a new chat with
     that tag pre-applied.

---

## 4. Backend gaps (identified during this hand-off)

**Status: all addressed in this commit or already live.**

| Need | Status | Endpoint |
|---|---|---|
| Tag catalog for autocomplete | ✅ shipped | `GET /api/tags` |
| Paginated browse by tag | ✅ shipped here | `GET /api/tags/{kind}/{slug}/papers` |
| 2D map data | ✅ shipped (§15) | `GET /api/embedding_map` |
| Paper detail lookup | ✅ existing | `GET /paper/{doi:path}/enriched` |
| `#tag` plumbing in chat | ✅ shipped (§28 Sprint B) | `POST /api/chat/completions` (body `tags` field) |

### Nice-to-have additions the frontend may request later

These aren't shipped — flag during implementation if you need them.

- **Tag detail with aggregate stats**
  `GET /api/tags/group/{slug}/stats` → year distribution, top topics
  within the group's corpus, most recent upload date. Cheap; do it
  on-request rather than precomputed.

- **Map-level clustering** to reduce 30k-point download
  If the 5-10 MB `/api/embedding_map` JSON is too heavy on mobile,
  we can add a zoom-level-aware variant that returns cluster
  centroids + top-N papers-per-cluster instead of all points. Not
  urgent — deck.gl handles 30k points fine on desktop.

- **Sparse / keyword hybrid for the knowledge browser search box**
  Currently you can only tag-filter in the browser, not text-search
  within a group. If you want "all Zeitler Lab papers matching
  'angioedema'", pipe it through `paper_search` with the `tags`
  parameter — it already works, just route browser search boxes
  there when a query is present.

- **Cross-user browse permissions**
  Currently all contributors + topics are globally visible to every
  user. If you later want per-user "private corpora" in the browser,
  add a `visibility: "user_only" | "shared"` payload field and
  filter in the browse endpoint. Out of scope for §28.

---

## 5. Smoke tests

Prove the backend works the way this doc says it does:

```bash
# Catalog
curl -s http://127.0.0.1:8080/api/tags \
    | jq '{topics: (.topics|length),
           groups: (.groups|length),
           contributors: (.contributors|length)}'
# Expected: {"topics": 213, "groups": 3, "contributors": 3}

# Browse by group (Zeitler's single ingested paper so far)
curl -s 'http://127.0.0.1:8080/api/tags/group/zeitler/papers?limit=10' | jq .

# Browse by topic
curl -s 'http://127.0.0.1:8080/api/tags/topic/nmr-studies-of-lipid-bilayers/papers?limit=5&sort=year_desc' | jq .

# Map data (stream the paper_count + cluster_count to confirm the file's current)
curl -s http://127.0.0.1:8080/api/embedding_map \
    | jq '{paper_count, cluster_count, generated_at}'

# Tag-scoped chat (end-to-end SSE)
curl -N -s -X POST http://127.0.0.1:8080/api/chat/completions \
    -H "X-Munin-Email: admin@example.org" \
    -H "Content-Type: application/json" \
    -d '{
      "persona": "research",
      "ephemeral": true,
      "messages": [{"role":"user","content":"find papers about angioedema"}],
      "tags": [{"kind":"group","value":"zeitler"}]
    }' | grep -E 'applied_tags|contributors|"topic":'
```

---

## 6. What the frontend must NOT do

A few guardrails to avoid subtle bugs:

- **Do not concatenate tag chips into the message text.** The model
  doesn't see them as text; they flow through the `tags` field
  only. `"find papers #zeitler"` sent verbatim as `user.content`
  with no `tags` field will NOT scope the search.
- **Do not assume tags persist across turns.** Each request's `tags`
  field applies only to that turn. If the user pinned `#zeitler`
  and asks three follow-up questions, re-submit `tags` on every
  POST. (Future enhancement: a conversation-level `default_tags`
  column — not yet wired.)
- **Do not construct download URLs client-side from the DOI alone.**
  Use the server-provided `download_url` from the paper stub. Papers
  without a local PDF omit the field deliberately — don't synthesise.
- **Do not enable tag chips for `semantic_scholar_search` results.**
  S2 is external and doesn't know about our contributors/clusters.
  The model's answer prose should be the source of truth on scope.
