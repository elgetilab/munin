# Paper Crawler Instructions

## Prerequisites

Install the required packages (run as root since venv is owned by root):

```bash
sudo /opt/munin/services/vllm/venv/bin/pip install beautifulsoup4 arxiv
```

## Quick Start

```bash
# Activate the environment
source /opt/munin/services/vllm/venv/bin/activate

# Navigate to scripts
cd /opt/cluster/scripts/knowledge

# Add a seed paper and start crawling
python paper_crawler.py add-seed arxiv:1706.03762
python paper_crawler.py crawl --max 10
```

---

## Commands

### Add Seed Papers

Seeds are starting points for the crawler. It will download these first, then follow their citations.

```bash
# Add by arXiv ID
python paper_crawler.py add-seed arxiv:1706.03762

# Add by arXiv ID (short form)
python paper_crawler.py add-seed 1706.03762

# Add by DOI
python paper_crawler.py add-seed 10.1038/nature14539
```

**Recommended seed papers for AI/ML research:**

```bash
# Attention Is All You Need (Transformers)
python paper_crawler.py add-seed arxiv:1706.03762

# GPT-3
python paper_crawler.py add-seed arxiv:2005.14165

# LLaMA
python paper_crawler.py add-seed arxiv:2302.13971

# BERT
python paper_crawler.py add-seed arxiv:1810.04805

# ResNet
python paper_crawler.py add-seed arxiv:1512.03385
```

### Check Status

```bash
python paper_crawler.py status
```

Shows:
- Papers by status (pending, downloaded, failed, processed)
- Papers by source (seed, citation, manual)
- Recent additions

### Start Crawling

```bash
# Basic crawl (5 second delay between downloads)
python paper_crawler.py crawl

# Custom delay (be respectful to servers)
python paper_crawler.py crawl --delay 10

# Limit number of papers
python paper_crawler.py crawl --max 50

# Download seeds only (don't follow citations)
python paper_crawler.py crawl --no-citations

# Combined options
python paper_crawler.py crawl --delay 10 --max 100
```

### Process Manual PDFs

If you have PDFs you want to add directly:

```bash
# Copy PDFs to manual folder
cp my-paper.pdf /opt/munin/data/papers/manual/

# Process them into the system
python paper_crawler.py process-manual
```

---

## How It Works

```
┌─────────────────┐
│   Seed Papers   │  ← You add these (arXiv IDs or DOIs)
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│    Download     │────▶│   Save PDF to   │
│  (arXiv/Sci-Hub)│     │  /opt/munin/    │
└────────┬────────┘     │  data/papers/   │
         │              └─────────────────┘
         ▼
┌─────────────────┐
│Extract Citations│  ← Uses CrossRef & Semantic Scholar APIs
│  (DOIs of refs) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Queue cited    │  ← New papers added to download queue
│    papers       │
└────────┬────────┘
         │
         └──────────▶ Repeat until queue is empty
```

### Download Sources

1. **arXiv** (tried first for arXiv IDs) - Open access preprints
2. **Sci-Hub** (fallback for DOIs) - Use responsibly, check legal status in your jurisdiction

---

## File Locations

| Path | Purpose |
|------|---------|
| `/opt/munin/data/papers/pdf/` | Downloaded PDFs |
| `/opt/munin/data/papers/manual/` | Drop PDFs here for manual import |
| `/opt/munin/data/papers/crawler_queue.db` | SQLite database tracking all papers |

---

## Processing Papers into Knowledge Base

After downloading, papers need to be processed into Qdrant/Neo4j:

```bash
# Process all downloaded papers
python paper_pipeline.py process-all

# Process a specific PDF
python paper_pipeline.py process /opt/munin/data/papers/pdf/arxiv_1706.03762.pdf
```

This will:
1. Parse the PDF with GROBID (extracts title, abstract, sections, references)
2. Generate SPECTER embeddings for semantic search
3. Store in Qdrant (vector DB) for similarity search
4. Store in Neo4j (graph DB) for citation network queries

---

## Tips

1. **Start small**: Use `--max 10` to test before doing large crawls

2. **Be respectful**: Default 5-second delay between downloads. Increase with `--delay 10` for large crawls

3. **Seeds matter**: Good seed papers with many citations will yield better results

4. **Check progress**: Run `status` periodically to monitor the queue

5. **Failed downloads**: Papers that fail 3 times are skipped. Check the status output for failures

---

## Troubleshooting

### "Permission denied" errors

The venv is owned by root. Use sudo for pip:
```bash
sudo /opt/munin/services/vllm/venv/bin/pip install <package>
```

### Sci-Hub not working

Sci-Hub mirrors change frequently. If downloads fail, the mirrors in `paper_crawler.py` may need updating.

### GROBID errors during processing

Ensure GROBID is running:
```bash
docker ps | grep grobid
# If not running:
cd /opt/munin/docker && docker compose up -d grobid
```

### Out of disk space

PDFs are stored in `/opt/munin/data/papers/pdf/`. Monitor disk usage:
```bash
du -sh /opt/munin/data/papers/
```
