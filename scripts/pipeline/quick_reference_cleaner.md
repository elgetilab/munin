# Paper Cleanup - Quick Reference

## Autonomous Mode (Recommended for Large Batches)

```bash
# Fully autonomous - processes papers one at a time with immediate actions
# Actions: remove bad papers, re-download orphaned PDFs, re-embed missing vectors
./paper_cleanup.py repair-auto --max-papers 1000 --output repair_log.json

# Process papers from processed directory instead of Neo4j
./paper_cleanup.py repair-auto --source processed --max-papers 500
```

## Manual/Inspection Commands

```bash
# Verify a single DOI against all metadata sources
./paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7

# Multi-source repair and clean (inspection mode - no immediate action)
./paper_cleanup.py repair-and-clean --max-check 50 --dry-run

# Repair and clean with auto-remove (batched, not one-by-one)
./paper_cleanup.py repair-and-clean --max-check 500 --auto-remove --limit 50

# Export orphaned DOIs (missing PDF) for re-crawling
./paper_cleanup.py repair-and-clean --max-check 500 --export-orphaned orphaned_dois.txt

# Scan processed directory
./paper_cleanup.py scan-processed --check-pdf --find-orphaned

# Remove single paper
./paper_cleanup.py remove --doi 10.1234/example

# Bulk remove from file
./paper_cleanup.py bulk-remove --file papers_to_remove.txt
```

## Single Paper Operations (for scripts)

```bash
# Download a single paper by DOI (used by repair-auto internally)
./paper_crawler.py download-single 10.1234/example

# Process/embed a single PDF (used by repair-auto internally)
./paper_pipeline.py --single /opt/munin/data/papers/pdf/doi_10.1234_example.pdf
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADMIN_EMAIL` | Yes | Email for API polite pools |
| `OPENALEX_API_KEY` | No | Higher rate limits (100k/day) |
| `SEMANTIC_SCHOLAR_API_KEY` | No | Higher rate limits |

## API Rate Limits

| Source | Without Key | With Key | Delay Used |
|--------|-------------|----------|------------|
| OpenAlex | 100k/day | 100k/day | 0.1s |
| Semantic Scholar | 100/5min | 1/s | 2.0s |
| CrossRef | 50/s (polite) | 50/s | 0.02s |

**Bottleneck:** Semantic Scholar at 2s/request = ~17 min per 500 papers

## repair-auto Decision Flow

```
For each paper:
    │
    ├──▶ Fetch OpenAlex, Semantic Scholar, CrossRef
    │
    └──▶ Decide & Act IMMEDIATELY:
            │
            ├── RETRACTED?        → Remove from all DBs
            ├── SHORT (<3 pages)? → Remove from all DBs
            ├── NO DATA anywhere? → Remove from all DBs
            ├── PDF missing?      → Call paper_crawler.py download-single
            │                        └── Then embed if needed
            ├── Embeddings missing? → Call paper_pipeline.py --single
            └── Has data, all OK?   → Enrich abstract if missing
```

## repair-and-clean Decision Logic (Batch Mode)

```
Paper DOI
    │
    ├──▶ Fetch OpenAlex     → abstract, page_count, refs, is_retracted
    ├──▶ Fetch Semantic Scholar → abstract, citations, refs, year
    ├──▶ Fetch CrossRef     → abstract, title, authors, type
    │
    └──▶ Decision:
            ├── ANY source has data?  → KEEP (enrich if needed)
            ├── ALL sources fail?     → Flag for REMOVAL
            ├── Page count < 3?       → Flag for REMOVAL (short)
            └── Is retracted?         → Flag for REMOVAL
```

## Output Status Codes

| Status | Meaning | repair-auto Action |
|--------|---------|-------------------|
| OK | Paper has data, no action needed | None |
| ENRICHED | Abstract added to Neo4j | Update DB |
| SHORT | Page count < 3 | **Remove** |
| RETRACTED | Paper is retracted | **Remove** |
| NO DATA | No metadata in any source | **Remove** |
| ORPHANED | In database but PDF missing | **Re-download** |
| NO EMBEDDINGS | PDF exists but not in Qdrant | **Re-embed** |

## Get Semantic Scholar API Key

Request at: https://www.semanticscholar.org/product/api#api-key-form

Then set:
```bash
export SEMANTIC_SCHOLAR_API_KEY="your-key-here"
```
