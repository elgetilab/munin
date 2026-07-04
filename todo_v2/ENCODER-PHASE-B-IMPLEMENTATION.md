# Encoder migration - Phase B implementation (DRAFT for review)

Production cutover SPECTER-v1 (768d, `papers`) -> BGE-large-en-v1.5 (1024d,
`papers_bge`). Phase A validated it (RESULTS.md: AgentRetriever Recall@10
0.44->0.73, p~0). Status: **DRAFT, no code changed yet.** Parent:
`ENCODER-MIGRATION-PLAN.md`.

## Design principle: flag-gated, deploy is a no-op

Everything is driven by two env vars that DEFAULT to today's behaviour:

```
PAPER_ENCODER=specter          # specter | bge-large
PAPERS_COLLECTION=papers       # papers  | papers_bge
```

So: deploy the code -> nothing changes (still SPECTER + `papers`). Cutover =
flip the two vars in `munin.env` + restart retrieval & pipeline. Rollback = flip
them back + restart. `papers` (SPECTER) is kept live throughout for instant
rollback. This decouples the risky behaviour change from the code deploy.

CRITICAL: `get_specter()` is shared by the `papers` AND `notion` collections
(both 768d). We must NOT globally swap it, or notion search breaks. Add a
SEPARATE paper-encoder path; notion keeps `get_specter()`.

## 1. `retrieval/database.py` - new paper-encoder abstraction

Add config + three functions (full code):

```python
# --- paper encoder selection (encoder migration) ---
PAPER_ENCODER      = os.getenv("PAPER_ENCODER", "specter")        # specter|bge-large
PAPERS_COLLECTION  = os.getenv("PAPERS_COLLECTION", "papers")     # papers|papers_bge
BGE_LARGE_MODEL_PATH = os.getenv("BGE_LARGE_MODEL_PATH", "/models/bge-large")
# BGE query instruction (queries ONLY; docs raw). Empty for SPECTER.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
PAPER_QUERY_PREFIX = _BGE_QUERY_INSTRUCTION if PAPER_ENCODER == "bge-large" else ""

_paper_encoder = None

def get_paper_encoder():
    """The encoder for the PAPERS corpus (SPECTER or BGE-large per PAPER_ENCODER).
    Distinct from get_specter() (which also serves the 768d notion collection)."""
    global _paper_encoder
    if _paper_encoder is None:
        if PAPER_ENCODER == "bge-large":
            from sentence_transformers import SentenceTransformer
            _paper_encoder = SentenceTransformer(BGE_LARGE_MODEL_PATH)  # 1024d
            logger.info("Paper encoder: BGE-large (%s)", BGE_LARGE_MODEL_PATH)
        else:
            _paper_encoder = get_specter()                              # 768d
            logger.info("Paper encoder: SPECTER-v1")
    return _paper_encoder

def encode_paper_query(text: str) -> list[float]:
    """Embed a QUERY against the papers corpus (applies the BGE prefix if any)."""
    return get_paper_encoder().encode(PAPER_QUERY_PREFIX + text).tolist()

def encode_paper_doc(text: str) -> list[float]:
    """Embed a DOCUMENT (title\\n\\nabstract) for the papers corpus (raw, no prefix)."""
    return get_paper_encoder().encode(text).tolist()
```

Note: normalization is not needed (Qdrant Cosine normalizes internally; ranking
is identical), so we match `build_papers_bge.py` behaviour without extra flags.

## 2. Vector-search sites -> `encode_paper_query` + `PAPERS_COLLECTION`

Each currently does `vec = get_specter().encode(q)` + `collection_name="papers"`.
Replace with `encode_paper_query(q)` + `database.PAPERS_COLLECTION`.

| file:line | context | change |
|---|---|---|
| `mcp/tools/papers.py:201,203` (+286) | `_qdrant_search_one` (paper_search MCP) | encode_paper_query + PAPERS_COLLECTION |
| `main.py:320,332,336` | `search_papers()` | same |
| `main.py:3386,3429,3435` | `/search/hybrid` endpoint | same |

Do NOT touch `main.py:385-390` (the `notion` search - stays `get_specter()` +
`collection_name="notion"`).

## 3. Metadata sites -> `PAPERS_COLLECTION` (collection name only, no encoder)

These scroll/retrieve/count paper payloads by DOI/filter. After cutover new
papers live in `papers_bge`, so these must read the active collection.

`main.py`: lines 1595, 1611, 1824, 1843, 1860, 3632, 4002.
`mcp/tools/papers.py`: line 528.
Change `collection_name="papers"` -> `collection_name=database.PAPERS_COLLECTION`.

## 4. `scripts/pipeline/paper_pipeline.py` - new papers embed with the active encoder

- `COLLECTION_NAME = os.getenv("PAPERS_COLLECTION", "papers")` (was hardcoded).
- Embedder: load per `PAPER_ENCODER` (BGE-large 1024d or SPECTER 768d).
- `create_collection` dim: 1024 if bge-large else 768 (only fires if the
  collection is missing; `papers_bge` already exists from Phase A, so it won't).
- Embed doc RAW: `main_text = f"{title}\n\n{abstract}"` (unchanged; no query
  prefix on docs). Lines ~1392, 636-637.
- Result: after cutover, newly-ingested papers land in `papers_bge` with BGE.

## 5. Deploy-side

- **Model:** place `BAAI/bge-large-en-v1.5` at
  `/opt/munin/data/models/bge-large` (download once). Mount it in
  `docker/docker-compose.yml` retrieval service:
  `- /opt/munin/data/models/bge-large:/models/bge-large:ro` (mirrors the specter
  mount). Pipeline runs on the host venv, so it reads the host path directly
  (set `BGE_LARGE_MODEL_PATH` for the pipeline unit if needed).
- **munin.env.template:** add the three vars defaulted to today's values
  (`PAPER_ENCODER=specter`, `PAPERS_COLLECTION=papers`,
  `BGE_LARGE_MODEL_PATH=/models/bge-large`) + a comment pointing here.
- **docker-compose.yml** retrieval `environment:` already uses `${VAR:-default}`
  substitution; add the three with specter/papers defaults so they flow in.
- Deploy: `sudo ./deploy.sh compose retrieval pipeline` (code + mount, no
  behaviour change while defaults hold).

## 6. Cutover runbook (varghele/root)

1. Confirm `papers_bge` is complete (68,121 pts, 1024d) - already true from
   Phase A. Confirm the BGE model is at `/opt/munin/data/models/bge-large`.
2. Deploy the flag-gated code (no-op): `sudo ./deploy.sh compose retrieval pipeline`.
3. Flip in `munin.env` (or cluster.env): `PAPER_ENCODER=bge-large`,
   `PAPERS_COLLECTION=papers_bge`.
4. Restart retrieval (`docker compose ... up -d --force-recreate retrieval`) and
   the pipeline unit (`systemctl restart munin-paper-pipeline`).
5. Smoke test: a paper_search / `/search/hybrid` query returns sensible hits;
   check the retrieval log says "Paper encoder: BGE-large".

**Rollback:** set `PAPER_ENCODER=specter`, `PAPERS_COLLECTION=papers`, restart.
Instant; `papers`+SPECTER untouched.

## 7. Phase C (measure) + retire

- Re-run the answer track on the live stack: `run_all --track litqa2-answer`
  (concurrency 1) -> confirm accuracy rises toward ~0.73 (Phase A projection).
  Commit the post-cutover scorecard; `compare` vs baseline is the paper's
  headline before/after.
- Soak (watch chat retrieval quality / user reports). Retire `papers` (768d)
  once confident (frees ~210 MB).

## Open items / risks to check BEFORE cutover

- **Knowledge/embedding-map service** (`scripts/knowledge/build_embedding_map.py`,
  nightly) reads paper vectors - if it assumes 768d/SPECTER it must switch to
  `papers_bge`/1024d too, or it will break / mix encoders. VERIFY before cutover.
- Any other consumer of the `papers` collection vectors (not just payloads) must
  move to `papers_bge`. Payload-only consumers are dim-agnostic.
- `papers_bge` must stay in sync with `papers` until cutover: papers ingested
  AFTER the Phase A re-embed (2026-07-03) exist only in `papers` until a
  top-up re-embed. Run `build_papers_bge.py` once more just before cutover to
  catch any new papers (it is resumable / idempotent).
- Eval provenance: scorecards already stamp `encoder`; keep tagging runs.
