# Paper Crawler Quick Reference

## Setup

```bash
source /opt/munin/services/vllm/venv/bin/activate
cd /opt/munin/scripts/knowledge
```

## Seed Management

```bash
# Add a seed paper (DOI or arXiv)
./paper_crawler.py add-seed 10.1234/example.paper
./paper_crawler.py add-seed arxiv:2312.12345

# List all seeds
./paper_crawler.py list-seeds

# Remove a seed
./paper_crawler.py remove-seed 10.1234/example.paper

# Remove a seed AND all citations discovered from it
./paper_crawler.py remove-seed 10.1234/example.paper --cascade
```

## Crawling

```bash
# Start crawling (default 5s delay between downloads)
# Automatically filters out non-research content (news, editorials, etc.)
./paper_crawler.py crawl

# Custom delay (be respectful to servers)
./paper_crawler.py crawl --delay 10

# Limit number of papers
./paper_crawler.py crawl --max 100

# Download only, don't extract citations
./paper_crawler.py crawl --no-citations

# Disable content filtering (include everything)
./paper_crawler.py crawl --no-filter
```

## Queue Management

```bash
# Show status (counts by status and source)
./paper_crawler.py status

# Clear all pending papers
./paper_crawler.py clear-pending

# Clear only pending citations
./paper_crawler.py clear-pending --source citation

# Clear only pending seeds
./paper_crawler.py clear-pending --source seed

# Reset entire queue (requires confirmation)
./paper_crawler.py reset --confirm
```

## Manual Papers

```bash
# Process PDFs from /opt/munin/data/papers/manual/
./paper_crawler.py process-manual
```

## Paper Pipeline (Processing)

```bash
# Process all unprocessed PDFs
# Automatically skips non-research content (empty titles, editorials, etc.)
./paper_pipeline.py

# Process a single PDF
./paper_pipeline.py --single /path/to/paper.pdf

# Reprocess all PDFs (clears markers, re-runs pipeline)
./paper_pipeline.py --reprocess

# Fast mode (parallel processing)
./paper_pipeline.py --fast --workers 8

# Reprocess all in fast mode
./paper_pipeline.py --reprocess --fast --workers 8

# Watch for new PDFs
./paper_pipeline.py --watch
```

## Directories

| Path | Description |
|------|-------------|
| `/opt/munin/data/papers/pdf/` | Downloaded PDFs |
| `/opt/munin/data/papers/manual/` | Drop PDFs here for manual processing |
| `/opt/munin/data/papers/processed/` | Processing markers (JSON) |
| `/opt/munin/data/papers/skipped/` | Skipped PDFs (with reasons) |
| `/opt/munin/data/papers/ocr_cache/` | OCR'd versions of scanned PDFs |
| `/opt/munin/data/papers/crawler_queue.db` | SQLite queue database |

## Full Reset

```bash
# Show what would be deleted (dry run)
./reset_knowledge_base.sh --dry-run

# Interactive reset (asks for confirmation)
./reset_knowledge_base.sh

# Non-interactive reset (skips confirmation)
./reset_knowledge_base.sh --confirm
```

Wipes: Qdrant papers collection, Neo4j Paper/Author nodes, crawler_queue.db, PDFs, processed markers, skipped markers, OCR cache.

## Direct Database Access

```bash
sqlite3 /opt/munin/data/papers/crawler_queue.db

# Useful queries:
SELECT doi, status, source FROM papers WHERE status = 'pending';
SELECT COUNT(*) FROM papers GROUP BY status;
SELECT * FROM papers WHERE source = 'seed';
```
