# Paper Quality Filtering - Quick Start

## Overview

The paper crawler now includes quality filtering to prevent low-quality papers (single-page introductions, editorials without content, etc.) from entering the knowledge base.

**Filters applied:**
- Minimum page count (default: 3 pages)
- Requires abstract OR references
- Blocks retracted papers
- DOI blocklist for manual exclusions

## Configuration

Environment variables (set in `/opt/hugin/config/cluster.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENALEX_API_KEY` | (empty) | OpenAlex API key for higher rate limits |
| `MIN_PAGE_COUNT` | `3` | Minimum pages required |
| `REQUIRE_ABSTRACT_OR_REFS` | `true` | Require abstract OR references |
| `ADMIN_EMAIL` | `admin@example.com` | Email for API User-Agent headers |

## Cleanup Script Usage

```bash
cd /opt/cluster/scripts/knowledge
```

### Remove a specific paper

Removes from Qdrant, Neo4j, SQLite queue, and deletes the PDF:

```bash
./paper_cleanup.py remove --doi 10.1111/j.1749-6632.1978.tb22009.x
```

Preview without making changes:

```bash
./paper_cleanup.py remove --doi 10.1111/j.1749-6632.1978.tb22009.x --dry-run
```

Remove without adding to blocklist:

```bash
./paper_cleanup.py remove --doi 10.1234/example --no-blocklist
```

### Find low-quality papers

Find papers in Neo4j with no abstract AND no references:

```bash
# Just find and display
./paper_cleanup.py find-low-quality

# Write DOIs to a file for review
./paper_cleanup.py find-low-quality --output low_quality.txt

# Auto-remove up to 10 papers (from all databases: Qdrant, Neo4j, SQLite, filesystem)
./paper_cleanup.py find-low-quality --auto-remove --limit 10

# Auto-remove ALL found papers (requires --confirm for safety)
./paper_cleanup.py find-low-quality --auto-remove --confirm
```

### Find short papers

Find papers with fewer than N pages (queries OpenAlex):

```bash
# Just find and display
./paper_cleanup.py find-short --min-pages 3

# Write DOIs to a file for review
./paper_cleanup.py find-short --min-pages 3 --output short_papers.txt

# Auto-remove up to 5 papers
./paper_cleanup.py find-short --min-pages 3 --auto-remove --limit 5

# Auto-remove ALL found papers (requires --confirm)
./paper_cleanup.py find-short --min-pages 3 --auto-remove --confirm
```

### Bulk remove

Remove multiple papers from a file (one DOI per line):

```bash
./paper_cleanup.py bulk-remove --file papers_to_remove.txt
```

### What gets removed with --auto-remove

When using `--auto-remove`, papers are removed from **all knowledge bases**:
- **Qdrant** - Vector embeddings deleted
- **Neo4j** - Paper nodes and relationships deleted
- **SQLite** - Removed from crawler queue
- **Filesystem** - PDF and processed markers deleted
- **Blocklist** - DOI added to prevent re-download

## Handling Queued Papers

### Option A: Clear pending citations and re-crawl

If many bad papers are queued, clear them and let the new filters handle re-queueing:

```bash
./paper_crawler.py clear-pending --source citation
```

### Option B: Let the crawler skip them

The pre-download filter checks OpenAlex before downloading. Bad papers in the queue will be automatically skipped:

```bash
./paper_crawler.py crawl --max 10
```

### Check queue status

```bash
./paper_crawler.py status
```

## Blocklist

Manually blocked DOIs are stored in `/opt/munin/data/papers/blocklist.txt`.

Format (one DOI per line, # for comments):
```
# DOIs to never download
10.1111/j.1749-6632.1978.tb22009.x
10.1234/another.bad.paper
```

Papers removed via `paper_cleanup.py` are automatically added to the blocklist.

## Verification

Test that a problematic paper is filtered:

```bash
python3 -c "
from paper_crawler import is_research_paper
is_ok, reason = is_research_paper('10.1111/j.1749-6632.1978.tb22009.x')
print(f'Passes filter: {is_ok}')
print(f'Reason: {reason}')
"
```

Expected output:
```
Passes filter: False
Reason: too_few_pages:1
```

## Where Filters Are Applied

1. **Pre-download** (`paper_crawler.py`): Checks before downloading PDF
   - Blocklist check
   - CrossRef content type
   - OpenAlex: page count, abstract/refs, retraction status

2. **Post-processing** (`paper_pipeline.py`): Checks after GROBID parsing
   - OpenAlex quality filters (page count, abstract/refs, retraction)
   - Title blocklist (editorials, errata, etc.)
   - Sparse metadata detection
