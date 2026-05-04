#!/bin/bash
# ==============================================================================
# MUNIN KNOWLEDGE BASE - FULL RESET SCRIPT
# ==============================================================================
# Wipes all data and starts fresh:
#   - Qdrant: papers collection
#   - Neo4j: Paper/Author nodes and relationships
#   - SQLite: crawler_queue.db
#   - Files: PDFs, processed markers, skipped markers, OCR cache
#
# Usage:
#   ./reset_knowledge_base.sh              # Interactive mode (asks for confirmation)
#   ./reset_knowledge_base.sh --confirm    # Non-interactive (skips confirmation)
#   ./reset_knowledge_base.sh --dry-run    # Show what would be deleted
#
# ==============================================================================

set -e

# Configuration
DATA_DIR="${MUNIN_DATA_DIR:-/opt/munin/data}"
PAPERS_DIR="$DATA_DIR/papers"
PDF_DIR="$PAPERS_DIR/pdf"
MANUAL_DIR="$PAPERS_DIR/manual"
PROCESSED_DIR="$PAPERS_DIR/processed"
SKIPPED_DIR="$PAPERS_DIR/skipped"
OCR_CACHE_DIR="$PAPERS_DIR/ocr_cache"
QUEUE_DB="$PAPERS_DIR/crawler_queue.db"

QDRANT_HOST="${QDRANT_HOST:-localhost}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-munin-neo4j-password}"

# Parse arguments
DRY_RUN=false
CONFIRMED=false

for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            ;;
        --confirm)
            CONFIRMED=true
            ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "============================================================"
echo "MUNIN KNOWLEDGE BASE - FULL RESET"
echo "============================================================"
echo ""

# Count what will be deleted
echo "Scanning data to be deleted..."
echo ""

PDF_COUNT=$(find "$PDF_DIR" -name "*.pdf" 2>/dev/null | wc -l || echo "0")
PROCESSED_COUNT=$(find "$PROCESSED_DIR" -name "*.json" 2>/dev/null | wc -l || echo "0")
SKIPPED_COUNT=$(find "$SKIPPED_DIR" -name "*.json" 2>/dev/null | wc -l || echo "0")
OCR_COUNT=$(find "$OCR_CACHE_DIR" -type f 2>/dev/null | wc -l || echo "0")

# Check Qdrant
QDRANT_COUNT=$(curl -s "http://$QDRANT_HOST:$QDRANT_PORT/collections/papers" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result',{}).get('points_count',0))" 2>/dev/null || echo "0")

# Check SQLite
if [ -f "$QUEUE_DB" ]; then
    QUEUE_COUNT=$(python3 -c "import sqlite3; c=sqlite3.connect('$QUEUE_DB'); print(c.execute('SELECT COUNT(*) FROM papers').fetchone()[0])" 2>/dev/null || echo "0")
else
    QUEUE_COUNT="0"
fi

echo "Data to be deleted:"
echo "  - PDFs:              $PDF_COUNT files"
echo "  - Processed markers: $PROCESSED_COUNT files"
echo "  - Skipped markers:   $SKIPPED_COUNT files"
echo "  - OCR cache:         $OCR_COUNT files"
echo "  - Qdrant vectors:    $QDRANT_COUNT points"
echo "  - Crawler queue:     $QUEUE_COUNT entries"
echo ""

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}[DRY RUN] No changes made.${NC}"
    exit 0
fi

# Confirmation
if [ "$CONFIRMED" != true ]; then
    echo -e "${RED}WARNING: This will permanently delete all paper data!${NC}"
    echo ""
    read -p "Type 'DELETE' to confirm: " CONFIRM
    if [ "$CONFIRM" != "DELETE" ]; then
        echo "Aborted."
        exit 1
    fi
fi

echo ""
echo "Starting reset..."
echo ""

# 1. Delete Qdrant collection
echo -n "[1/6] Deleting Qdrant 'papers' collection... "
QDRANT_RESULT=$(curl -s -X DELETE "http://$QDRANT_HOST:$QDRANT_PORT/collections/papers" 2>/dev/null || echo '{"error": true}')
if echo "$QDRANT_RESULT" | grep -q '"status":"ok"' || echo "$QDRANT_RESULT" | grep -q '"result":true'; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${YELLOW}SKIPPED (collection may not exist)${NC}"
fi

# 2. Clear Neo4j (Paper and Author nodes)
echo -n "[2/6] Clearing Neo4j graph data... "
if command -v cypher-shell &> /dev/null; then
    cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" -a "$NEO4J_URI" \
        "MATCH (n) WHERE n:Paper OR n:Author DETACH DELETE n" 2>/dev/null && \
        echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}SKIPPED (connection failed)${NC}"
else
    # Try with Python
    python3 << EOF 2>/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}SKIPPED (connection failed)${NC}"
from neo4j import GraphDatabase
driver = GraphDatabase.driver("$NEO4J_URI", auth=("$NEO4J_USER", "$NEO4J_PASSWORD"))
with driver.session() as session:
    session.run("MATCH (n) WHERE n:Paper OR n:Author DETACH DELETE n")
driver.close()
EOF
fi

# 3. Delete SQLite queue database
echo -n "[3/6] Deleting crawler queue database... "
if [ -f "$QUEUE_DB" ]; then
    rm -f "$QUEUE_DB"
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${YELLOW}SKIPPED (not found)${NC}"
fi

# 4. Delete PDF files
echo -n "[4/6] Deleting PDF files... "
if [ -d "$PDF_DIR" ] && [ "$(ls -A $PDF_DIR 2>/dev/null)" ]; then
    rm -f "$PDF_DIR"/*.pdf 2>/dev/null || true
    echo -e "${GREEN}OK ($PDF_COUNT files)${NC}"
else
    echo -e "${YELLOW}SKIPPED (empty)${NC}"
fi

# 5. Delete processed/skipped markers
echo -n "[5/6] Deleting processing markers... "
DELETED=0
if [ -d "$PROCESSED_DIR" ]; then
    rm -f "$PROCESSED_DIR"/*.json 2>/dev/null && DELETED=$((DELETED + PROCESSED_COUNT))
fi
if [ -d "$SKIPPED_DIR" ]; then
    rm -f "$SKIPPED_DIR"/*.json 2>/dev/null && DELETED=$((DELETED + SKIPPED_COUNT))
fi
echo -e "${GREEN}OK ($DELETED files)${NC}"

# 6. Delete OCR cache
echo -n "[6/6] Deleting OCR cache... "
if [ -d "$OCR_CACHE_DIR" ] && [ "$(ls -A $OCR_CACHE_DIR 2>/dev/null)" ]; then
    rm -rf "$OCR_CACHE_DIR"/*
    echo -e "${GREEN}OK ($OCR_COUNT files)${NC}"
else
    echo -e "${YELLOW}SKIPPED (empty)${NC}"
fi

echo ""
echo "============================================================"
echo -e "${GREEN}RESET COMPLETE${NC}"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Add seed papers:    ./paper_crawler.py add-seed <DOI>"
echo "  2. Start crawling:     ./paper_crawler.py crawl"
echo "  3. Process papers:     ./paper_pipeline.py"
echo ""
echo "Or run the full pipeline:"
echo "  ./paper_crawler.py crawl --max 100 && ./paper_pipeline.py"
echo ""
