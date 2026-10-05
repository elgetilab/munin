# Munin Paper Crawler

Citation-based paper acquisition system. Starts from seed papers and automatically crawls their references to build a comprehensive research corpus.

## How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PAPER CRAWLER WORKFLOW                             │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────┐
                              │  Seed Papers │
                              │  (DOI/arXiv) │
                              └──────┬───────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DOWNLOAD QUEUE                                  │
│                           (SQLite database)                                  │
│                                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│   │ pending │───▶│downloading│──▶│downloaded│──▶│processed│                 │
│   └─────────┘    └─────────┘    └─────────┘    └─────────┘                 │
│        ▲                              │                                      │
│        │                              │                                      │
│        └──────────────────────────────┘                                      │
│              (extracted citations)                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DOWNLOAD SOURCES                                  │
│                                                                              │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│   │     arXiv       │    │    Sci-Hub      │    │     Manual      │        │
│   │  (open access)  │    │ (opt-in, off)   │    │   (uploaded)    │        │
│   │                 │    │                 │    │                 │        │
│   │ arxiv.org/pdf/  │    │ SCIHUB_ENABLED  │    │ /papers/manual/ │        │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘        │
│          │                       │                      │                   │
│          └───────────────────────┴──────────────────────┘                   │
│                                  │                                          │
│                                  ▼                                          │
│                         /opt/munin/data/papers/pdf/                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CITATION EXTRACTION                                  │
│                                                                              │
│   For each downloaded paper with a DOI:                                      │
│                                                                              │
│   1. Query CrossRef API: api.crossref.org/works/{DOI}                       │
│      └─▶ Extract reference DOIs from metadata                               │
│                                                                              │
│   2. Query Semantic Scholar: api.semanticscholar.org/graph/v1/paper/{DOI}   │
│      └─▶ Extract reference DOIs as backup                                   │
│                                                                              │
│   3. Filter non-research content (enabled by default):                       │
│      └─▶ Skip: components, datasets, peer-reviews                           │
│      └─▶ Skip: "news and views", "editorial", "erratum", etc.               │
│                                                                              │
│   4. Add filtered DOIs to queue as "citation" source                        │
│      └─▶ Crawler will download these in next iteration                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PROCESSING PIPELINE                                  │
│                      (paper_pipeline.py - separate)                          │
│                                                                              │
│   PDF ──▶ GROBID ──▶ CrossRef ──▶ Validate ──▶ BGE-lg. ──▶ Qdrant + Neo4j  │
│           (parse)    (enrich)     (title)      (embed)      (store)         │
│                                                                              │
│   Validation skips papers with:                                              │
│   - Empty titles (metadata extraction failed)                                │
│   - Non-research titles ("news and views", "editorial", etc.)               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Add Seed Papers

Start with papers you know are relevant. The crawler will discover related papers through citations.

```bash
# Add papers by DOI
./paper_crawler.py add-seed 10.1038/s41586-021-03819-2
./paper_crawler.py add-seed 10.48550/arXiv.1706.03762

# Add papers by arXiv ID
./paper_crawler.py add-seed arxiv:1706.03762
./paper_crawler.py add-seed 2312.12345
```

### 2. Start Crawling

```bash
# Basic crawl (5 second delay between downloads)
# Automatically filters out non-research content (news, editorials, etc.)
./paper_crawler.py crawl

# Custom delay (be respectful to servers)
./paper_crawler.py crawl --delay 10

# Limit number of papers
./paper_crawler.py crawl --max 100

# Download only, don't extract citations
./paper_crawler.py crawl --no-citations

# Include all content (disable filtering)
./paper_crawler.py crawl --no-filter
```

### 3. Check Status

```bash
./paper_crawler.py status
```

Output:
```
============================================================
CRAWLER STATUS
============================================================

Papers by Status:
  pending: 1247
  downloaded: 89
  processed: 45
  failed: 3

Papers by Source:
  seed: 5
  citation: 1334

Recent Additions:
  [pending] 10.1234/example.2023 (2024-01-15)
  [downloaded] arxiv:2312.12345 (2024-01-15)
  ...
============================================================
```

### 4. Process Downloaded Papers

After downloading, process PDFs into the knowledge base:

```bash
# Process all downloaded PDFs
./paper_pipeline.py --dir /opt/munin/data/papers/pdf

# Or watch for new PDFs
./paper_pipeline.py --watch
```

## Commands Reference

| Command | Description |
|---------|-------------|
| `add-seed <id>` | Add a seed paper (DOI or arXiv ID) |
| `crawl` | Start the crawl loop |
| `status` | Show queue statistics |
| `process-manual` | Import PDFs from manual upload folder |

### Crawl Options

| Option | Default | Description |
|--------|---------|-------------|
| `--delay` | 5 | Seconds between downloads |
| `--max` | unlimited | Maximum papers to download |
| `--no-citations` | false | Skip citation extraction |
| `--no-filter` | false | Don't filter non-research content |

## Adding Papers Manually

For books or papers you download manually:

1. Copy PDFs to `/opt/munin/data/papers/manual/`
2. Run:
   ```bash
   ./paper_crawler.py process-manual
   ```
3. Files are moved to the main PDF directory and tracked in the database

## Download Sources

### arXiv (Priority 1)
- Open access preprints
- Direct PDF download from `arxiv.org/pdf/{id}.pdf`
- No authentication required
- Works for: arXiv IDs and DOIs pointing to arXiv

### Sci-Hub (off by default)
- The crawler has a Sci-Hub fallback for DOIs arXiv cannot serve. It
  is off unless `SCIHUB_ENABLED=1` is set in the crawler's
  environment; without it, papers not on arXiv are marked failed.
- Whether using it is legal and acceptable is the operator's call.

### Source Selection Logic

```python
if paper.arxiv_id:
    try_arxiv()
elif paper.doi contains "arxiv":
    extract_arxiv_id_and_try_arxiv()

if not downloaded and paper.doi and SCIHUB_ENABLED:
    try_scihub()
```

## Citation Extraction

When a paper is downloaded, the crawler extracts its references:

1. **CrossRef** (primary): Queries `api.crossref.org/works/{DOI}` for structured reference list
2. **Semantic Scholar** (backup): Queries their API if CrossRef fails

Only DOIs are extracted (not titles or URLs) since we need DOIs to download papers.

## Content Filtering

By default, the crawler filters out non-research content before queueing citations.

### Filtered CrossRef Types
- `component` - Figures, tables, supplementary material
- `reference-entry` - Dictionary/encyclopedia entries
- `peer-review` - Peer review reports
- `dataset` - Data files
- `posted-content` - Preprints (we get these from arXiv instead)
- `grant` - Funding information
- `report-component` - Parts of reports

### Filtered Titles (exact match)
- "news and views", "editorial", "erratum", "correction", "retraction"
- "graphical abstract", "notes and references", "cover picture"
- "table of contents", "advertisement", "book review", "corrigendum"
- "front matter", "back matter", "index", "contents", "preface"
- "notes for notes", "in this issue", "issue information", "masthead"

### Sparse Metadata Filter
Papers with **all three** conditions are filtered:
- No abstract
- No authors
- Title shorter than 30 characters

This catches very short generic titles like "Editorial" or "Index" while allowing legitimate papers that may have metadata extraction issues (most real papers have titles > 30 chars).

To disable filtering:
```bash
./paper_crawler.py crawl --no-filter
```

## Database Schema

The crawler uses SQLite (`/opt/munin/data/papers/crawler_queue.db`):

```sql
papers (
    id TEXT PRIMARY KEY,      -- DOI or hash
    doi TEXT,                 -- Paper DOI
    arxiv_id TEXT,            -- arXiv identifier
    title TEXT,               -- Paper title (if known)
    source TEXT,              -- 'seed', 'citation', 'manual'
    parent_doi TEXT,          -- DOI of citing paper
    added_at TEXT,            -- ISO timestamp
    status TEXT,              -- 'pending', 'downloaded', 'failed', 'processed'
    attempts INTEGER,         -- Download attempt count
    error TEXT,               -- Last error message
    pdf_path TEXT,            -- Path to downloaded PDF
    processed_at TEXT         -- When processed by pipeline
)
```

## File Structure

```
/opt/munin/data/papers/
├── pdf/                    # Downloaded PDFs
│   ├── arxiv_1706.03762.pdf
│   ├── doi_10.1038_s41586-021-03819-2.pdf
│   └── ...
├── manual/                 # Drop PDFs here for manual import
├── processed/              # GROBID output (from paper_pipeline.py)
│   ├── arxiv_1706.03762.json
│   └── ...
└── crawler_queue.db        # SQLite database
```

## Rate Limiting

The crawler is designed to be respectful to servers:

| Source | Recommendation |
|--------|---------------|
| arXiv | 5-10 second delay |
| CrossRef | 1 second delay (built-in) |
| Semantic Scholar | 1 second delay (built-in) |

**Default: 5 seconds between downloads**

At 5 seconds delay: ~720 papers/hour, ~17,000 papers/day

## Scheduled Crawling

Set up a cron job for continuous crawling:

```bash
# Edit crontab
crontab -e

# Run crawler every night at 2 AM, max 500 papers
0 2 * * * /opt/cluster/scripts/knowledge/paper_crawler.py crawl --max 500 --delay 10 >> /opt/munin/logs/crawler.log 2>&1
```

Or create a systemd timer for more control.

## Troubleshooting

### Papers Not Downloading

```bash
# Check status for errors
./paper_crawler.py status

# Look at the database directly
sqlite3 /opt/munin/data/papers/crawler_queue.db \
  "SELECT doi, error FROM papers WHERE status='failed' LIMIT 10"
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Not a PDF" | arXiv returned HTML | Paper may not exist or ID is wrong |
| "Connection timeout" | Network issues | Increase delay, check internet |
| "All download sources failed" | Paper not available | May need institutional access |

### Reset Failed Papers

```bash
sqlite3 /opt/munin/data/papers/crawler_queue.db \
  "UPDATE papers SET status='pending', attempts=0 WHERE status='failed'"
```

### Clear Queue

```bash
# Remove all pending papers (keep downloaded)
sqlite3 /opt/munin/data/papers/crawler_queue.db \
  "DELETE FROM papers WHERE status='pending'"
```

## Integration with Paper Pipeline

After crawling, process papers into the knowledge base:

```bash
# One-time processing
./paper_pipeline.py --dir /opt/munin/data/papers/pdf

# Continuous processing (watches for new files)
./paper_pipeline.py --watch
```

The pipeline:
1. Parses PDFs with GROBID
2. Enriches metadata via CrossRef
3. Generates BGE-large embeddings (1024d)
4. Stores in Qdrant (vectors, collection `papers_bge`) and Neo4j (graph)

## Example: Building a Corpus

```bash
# Step 1: Add foundational papers in your field
./paper_crawler.py add-seed 10.48550/arXiv.1706.03762  # Attention Is All You Need
./paper_crawler.py add-seed 10.48550/arXiv.1810.04805  # BERT
./paper_crawler.py add-seed 10.48550/arXiv.2005.14165  # GPT-3

# Step 2: Start crawling (this will discover hundreds of related papers)
./paper_crawler.py crawl --delay 5 --max 1000

# Step 3: Check progress
./paper_crawler.py status

# Step 4: Process into knowledge base
./paper_pipeline.py --dir /opt/munin/data/papers/pdf

# Step 5: Verify in Qdrant
curl http://localhost:6333/collections/papers_bge
```

## Legal Considerations

- **arXiv**: Open access, free to download
- **Sci-Hub**: off unless `SCIHUB_ENABLED=1`; whether it is legal to use is the operator's call.
- **CrossRef/Semantic Scholar APIs**: Free for research, respect rate limits

Always comply with your institution's policies and local laws.
