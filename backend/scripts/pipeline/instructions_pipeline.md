# Paper Pipeline Instructions

The paper pipeline processes downloaded PDFs into the knowledge base (Qdrant + Neo4j).

## Prerequisites

Ensure the following services are running:

```bash
# Check services
docker ps | grep -E "(qdrant|neo4j|grobid)"

# If not running, start them
cd /opt/munin/docker && docker compose up -d qdrant neo4j grobid
```

## Quick Start

```bash
# Process all unprocessed PDFs (recommended command)
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py'
```

The `set -a` command exports all variables to child processes (Python needs this for Neo4j authentication). Note: `set -a` must be added externally when sourcing, not in the file itself (to maintain Docker .env compatibility).

---

## Commands

All commands should be run with the cluster.env sourced to ensure Neo4j authentication works.

### Process All PDFs in Directory

```bash
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py'
```

This processes all unprocessed PDFs. Already-processed papers are skipped.

### Process a Single PDF

```bash
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --single /opt/munin/data/papers/pdf/doi_10.1234_example.pdf'
```

Outputs the extracted metadata as JSON when complete.

### Watch Mode (Auto-Process New PDFs)

```bash
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --watch'
```

Monitors the directory and automatically processes new PDFs as they appear. Press `Ctrl+C` to stop.

---

## Reprocessing & Fast Mode

### Reprocess All Papers (Full Database Rebuild)

Use this when you need to rebuild the entire knowledge base (e.g., after fixing bugs, changing DOI extraction, etc.):

```bash
# Step 1: Clear Neo4j graph (optional - only if you want a clean graph)
source /opt/hugin/config/cluster.env
sudo docker exec munin-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (n) DETACH DELETE n"

# Step 2: Reprocess all papers with fast mode (parallel processing)
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --reprocess --fast --workers 2'
```

**Flags:**
- `--reprocess`: Clears all processed markers and re-runs the pipeline on all PDFs
- `--fast`: Enables parallel GROBID processing and batch embeddings
- `--workers N`: Number of parallel workers (default: 4, recommended: 2 to avoid overloading GROBID)

### Reprocess Without Clearing Neo4j

If you just want to re-run processing without clearing the graph:

```bash
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --reprocess --fast --workers 2'
```

This will update existing paper nodes (matched by DOI) rather than creating duplicates.

### Sequential Reprocessing (Slower but Safer)

If GROBID is having issues with parallel requests:

```bash
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --reprocess'
```

---

## What the Pipeline Does

```
┌─────────────────┐
│    PDF File     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  [1/4] GROBID   │  ← Extracts title, abstract, authors, references
│   PDF Parser    │    from the PDF structure
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ [2/4] CrossRef  │  ← Enriches metadata using DOI lookup
│   Enrichment    │    (journal name, publication year, etc.)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ [3/4] SPECTER   │  ← Generates 768-dimensional embeddings
│   Embeddings    │    for semantic search
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  [4/4] Store    │
│  ┌───────────┐  │
│  │  Qdrant   │  │  ← Vector DB for semantic search
│  │ (vectors) │  │
│  └───────────┘  │
│  ┌───────────┐  │
│  │  Neo4j    │  │  ← Graph DB for citations & authors
│  │  (graph)  │  │
│  └───────────┘  │
└─────────────────┘
```

### Data Stored

**Qdrant (Vector Database):**
- Paper ID
- Title
- Abstract (first 2000 chars)
- DOI
- Year
- Authors
- Journal
- PDF path
- SPECTER embedding (768 dimensions)

**Neo4j (Graph Database):**
- Paper nodes (id, title, doi, year, journal, abstract)
- Author nodes (id, name)
- AUTHORED relationships (Author → Paper)
- CITES relationships (Paper → Paper)

---

## File Locations

| Path | Purpose |
|------|---------|
| `/opt/munin/data/papers/pdf/` | Input PDFs (from crawler) |
| `/opt/munin/data/papers/processed/` | JSON markers for processed papers |
| `/opt/munin/data/models/specter/` | SPECTER embedding model |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GROBID_URL` | `http://localhost:8070` | GROBID service URL |
| `QDRANT_HOST` | `localhost` | Qdrant host |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | (required) | Neo4j password |

---

## Verifying Results

### Check Qdrant

```bash
# Count papers in collection
curl -s http://localhost:6333/collections/papers | jq '.result.points_count'

# Search for a paper
curl -s -X POST http://localhost:6333/collections/papers/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit": 5}' | jq '.result.points[].payload.title'
```

### Check Neo4j

Open http://localhost:7474 in browser, login with `neo4j` / your password, then:

```cypher
// Count papers
MATCH (p:Paper) RETURN count(p)

// Count authors
MATCH (a:Author) RETURN count(a)

// Find papers with citations
MATCH (p:Paper)-[:CITES]->(cited:Paper)
RETURN p.title, count(cited) as citations
ORDER BY citations DESC
LIMIT 10

// Find prolific authors
MATCH (a:Author)-[:AUTHORED]->(p:Paper)
RETURN a.name, count(p) as papers
ORDER BY papers DESC
LIMIT 10
```

---

## Troubleshooting

### "Cannot connect to GROBID"

```bash
# Check if GROBID is running
curl http://localhost:8070/api/isalive

# If not, start it
cd /opt/munin/docker && docker compose up -d grobid

# GROBID takes ~30 seconds to start. Check logs:
docker logs munin-grobid
```

### "Qdrant connection failed"

```bash
# Check if Qdrant is running
curl http://localhost:6333/collections

# If not, start it
cd /opt/munin/docker && docker compose up -d qdrant
```

### "Neo4j connection failed"

```bash
# Check if Neo4j is running
curl http://localhost:7474

# If not, start it
cd /opt/munin/docker && docker compose up -d neo4j

# Verify password is being exported correctly
sudo bash -c 'source /opt/hugin/config/cluster.env && echo $NEO4J_PASSWORD'

# Test connection
source /opt/hugin/config/cluster.env
sudo docker exec munin-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "RETURN 1"
```

**Note:** The `cluster.env` file must have `set -a` at the beginning to export variables to child processes.

### "Embedder failed to load"

```bash
# Check if SPECTER model exists
ls /opt/munin/data/models/specter/

# If missing, run the knowledge base setup again
sudo bash HuginSLURM/phase2-munin/05-knowledge-base.sh
```

### Permission Denied Errors

```bash
# Fix ownership of papers directories
sudo chown -R $USER:$USER /opt/munin/data/papers/
```

### Re-process a Single Paper

Delete its marker file and run again:

```bash
# Remove the processed marker
sudo rm /opt/munin/data/papers/processed/doi_10.1234_example.json

# Re-process the paper
sudo bash -c 'source /opt/hugin/config/cluster.env && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py --single /opt/munin/data/papers/pdf/doi_10.1234_example.pdf'
```

---

## Complete Workflow

The typical workflow combines the crawler and pipeline:

```bash
# 1. Add seed papers and crawl
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_crawler.py add-seed arxiv:1706.03762'
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_crawler.py crawl --max 20'

# 2. Process downloaded papers
sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && /opt/munin/services/vllm/venv/bin/python /opt/cluster/scripts/knowledge/paper_pipeline.py'

# 3. Verify
curl -s http://localhost:6333/collections/papers | jq '.result.points_count'

# 4. Check Neo4j stats
source /opt/hugin/config/cluster.env
sudo docker exec munin-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (p:Paper) RETURN count(p) as papers"
```

---

## Tips

1. **Run crawler first**: The pipeline processes PDFs from `/opt/munin/data/papers/pdf/` which are downloaded by the crawler.

2. **GROBID is slow**: Processing each PDF takes 10-30 seconds depending on length.

3. **Idempotent**: Re-running the pipeline skips already-processed papers.

4. **Watch mode for automation**: Use `--watch` if you want continuous processing as new papers are crawled.

5. **Check GROBID health**: If many papers fail, GROBID might be overloaded. Restart it:
   ```bash
   docker restart munin-grobid
   ```
