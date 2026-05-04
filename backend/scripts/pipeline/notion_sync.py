#!/usr/bin/env python3
"""
==============================================================================
MUNIN NOTION SYNC SERVICE
==============================================================================
Syncs Notion workspace to Qdrant vector database for RAG.

Features:
    - Fetches all pages from specified Notion databases/pages
    - Converts Notion blocks to markdown
    - Chunks content and generates BGE embeddings
    - Stores in Qdrant 'notion' collection
    - Supports incremental sync (only changed pages)

Requirements:
    pip install notion-client qdrant-client sentence-transformers

Usage:
    # Full sync
    python notion_sync.py sync

    # Sync specific database
    python notion_sync.py sync --database-id <id>

    # Check status
    python notion_sync.py status

Environment Variables:
    NOTION_TOKEN         - Notion integration token
    QDRANT_HOST          - Qdrant server host (default: localhost)
    QDRANT_PORT          - Qdrant server port (default: 6333)
    BGE_MODEL_PATH       - Path to BGE model (optional)
==============================================================================
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

# ==============================================================================
# Configuration
# ==============================================================================
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "__NOTION_TOKEN__")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
BGE_MODEL_PATH = os.getenv("BGE_MODEL_PATH", "")
COLLECTION_NAME = "notion"
CHUNK_SIZE = 512  # tokens
CHUNK_OVERLAP = 50  # tokens

# State file for incremental sync
STATE_FILE = "/opt/munin/knowledge/notion_sync_state.json"


# ==============================================================================
# Lazy Clients
# ==============================================================================
_notion = None
_qdrant = None
_embedder = None


def get_notion():
    """Get or initialize Notion client."""
    global _notion
    if _notion is None:
        if NOTION_TOKEN.startswith("__"):
            print("[ERROR] NOTION_TOKEN not configured")
            print("Set NOTION_TOKEN environment variable with your integration token")
            print("See: https://www.notion.so/my-integrations")
            return None
        try:
            from notion_client import Client
            _notion = Client(auth=NOTION_TOKEN)
            print("[OK] Connected to Notion API")
        except ImportError:
            print("[ERROR] notion-client not installed. Run: pip install notion-client")
            return None
        except Exception as e:
            print(f"[ERROR] Failed to connect to Notion: {e}")
            return None
    return _notion


def get_qdrant():
    """Get or initialize Qdrant client."""
    global _qdrant
    if _qdrant is None:
        try:
            from qdrant_client import QdrantClient
            _qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
            print(f"[OK] Connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
        except Exception as e:
            print(f"[ERROR] Failed to connect to Qdrant: {e}")
            return None
    return _qdrant


def get_embedder():
    """Get or initialize BGE embedder."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            if BGE_MODEL_PATH and os.path.exists(BGE_MODEL_PATH):
                _embedder = SentenceTransformer(BGE_MODEL_PATH)
            else:
                print("Downloading BGE-base model (first time only)...")
                _embedder = SentenceTransformer("BAAI/bge-base-en-v1.5")
            print("[OK] BGE embedder loaded")
        except Exception as e:
            print(f"[ERROR] Failed to load BGE embedder: {e}")
            return None
    return _embedder


# ==============================================================================
# Notion Helpers
# ==============================================================================
def get_page_content(notion, page_id: str) -> str:
    """Fetch and convert page blocks to markdown."""
    blocks = []

    try:
        # Get all blocks
        response = notion.blocks.children.list(block_id=page_id)
        blocks.extend(response.get("results", []))

        # Handle pagination
        while response.get("has_more"):
            response = notion.blocks.children.list(
                block_id=page_id,
                start_cursor=response.get("next_cursor")
            )
            blocks.extend(response.get("results", []))

    except Exception as e:
        print(f"[WARNING] Failed to fetch blocks for {page_id}: {e}")
        return ""

    return blocks_to_markdown(blocks)


def blocks_to_markdown(blocks: list) -> str:
    """Convert Notion blocks to markdown."""
    lines = []

    for block in blocks:
        block_type = block.get("type")

        if block_type == "paragraph":
            text = extract_rich_text(block["paragraph"].get("rich_text", []))
            if text:
                lines.append(text)
                lines.append("")

        elif block_type == "heading_1":
            text = extract_rich_text(block["heading_1"].get("rich_text", []))
            lines.append(f"# {text}")
            lines.append("")

        elif block_type == "heading_2":
            text = extract_rich_text(block["heading_2"].get("rich_text", []))
            lines.append(f"## {text}")
            lines.append("")

        elif block_type == "heading_3":
            text = extract_rich_text(block["heading_3"].get("rich_text", []))
            lines.append(f"### {text}")
            lines.append("")

        elif block_type == "bulleted_list_item":
            text = extract_rich_text(block["bulleted_list_item"].get("rich_text", []))
            lines.append(f"• {text}")

        elif block_type == "numbered_list_item":
            text = extract_rich_text(block["numbered_list_item"].get("rich_text", []))
            lines.append(f"1. {text}")

        elif block_type == "to_do":
            text = extract_rich_text(block["to_do"].get("rich_text", []))
            checked = "x" if block["to_do"].get("checked") else " "
            lines.append(f"[{checked}] {text}")

        elif block_type == "code":
            text = extract_rich_text(block["code"].get("rich_text", []))
            lang = block["code"].get("language", "")
            lines.append(f"```{lang}")
            lines.append(text)
            lines.append("```")
            lines.append("")

        elif block_type == "quote":
            text = extract_rich_text(block["quote"].get("rich_text", []))
            lines.append(f"> {text}")
            lines.append("")

        elif block_type == "callout":
            text = extract_rich_text(block["callout"].get("rich_text", []))
            lines.append(f"> **Note:** {text}")
            lines.append("")

        elif block_type == "divider":
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def extract_rich_text(rich_text: list) -> str:
    """Extract plain text from Notion rich text array."""
    parts = []
    for item in rich_text:
        text = item.get("plain_text", "")
        if item.get("annotations", {}).get("bold"):
            text = f"**{text}**"
        if item.get("annotations", {}).get("italic"):
            text = f"*{text}*"
        if item.get("annotations", {}).get("code"):
            text = f"`{text}`"
        parts.append(text)
    return "".join(parts)


def get_page_title(page: dict) -> str:
    """Extract page title from Notion page object."""
    properties = page.get("properties", {})

    # Try common title property names
    for prop_name in ["Name", "Title", "title", "name"]:
        if prop_name in properties:
            prop = properties[prop_name]
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return extract_rich_text(title_parts)

    # Fallback: use first title property found
    for prop in properties.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            return extract_rich_text(title_parts)

    return "Untitled"


# ==============================================================================
# Chunking
# ==============================================================================
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    # Simple word-based chunking
    words = text.split()

    if len(words) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        if end >= len(words):
            break

        start = end - overlap

    return chunks


# ==============================================================================
# Sync Functions
# ==============================================================================
def ensure_collection(qdrant) -> bool:
    """Ensure Qdrant collection exists."""
    from qdrant_client.models import Distance, VectorParams

    try:
        collections = qdrant.get_collections().collections
        if any(c.name == COLLECTION_NAME for c in collections):
            return True

        # Create collection for BGE-base embeddings (768 dimensions)
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE)
        )
        print(f"[OK] Created collection '{COLLECTION_NAME}'")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to create collection: {e}")
        return False


def load_sync_state() -> dict:
    """Load sync state from file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_sync": None, "page_hashes": {}}


def save_sync_state(state: dict):
    """Save sync state to file."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def content_hash(content: str) -> str:
    """Generate hash of content for change detection."""
    return hashlib.md5(content.encode()).hexdigest()


def sync_page(notion, qdrant, embedder, page: dict, state: dict) -> int:
    """Sync a single page to Qdrant. Returns number of chunks added."""
    from qdrant_client.models import PointStruct

    page_id = page["id"]
    title = get_page_title(page)
    url = page.get("url", "")
    last_edited = page.get("last_edited_time", "")

    # Fetch page content
    content = get_page_content(notion, page_id)
    if not content.strip():
        return 0

    # Check if content changed
    current_hash = content_hash(content)
    if page_id in state["page_hashes"]:
        if state["page_hashes"][page_id] == current_hash:
            return 0  # No changes

    # Chunk content
    chunks = chunk_text(f"{title}\n\n{content}")
    if not chunks:
        return 0

    # Delete old vectors for this page
    try:
        qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector={
                "filter": {
                    "must": [{"key": "page_id", "match": {"value": page_id}}]
                }
            }
        )
    except Exception:
        pass  # Collection might be empty

    # Generate embeddings and store
    points = []
    for i, chunk in enumerate(chunks):
        try:
            vector = embedder.encode(chunk).tolist()
            point_id = abs(hash(f"{page_id}_{i}")) % (2**63)

            points.append(PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "page_id": page_id,
                    "title": title,
                    "url": url,
                    "content": chunk,
                    "chunk_index": i,
                    "last_edited": last_edited,
                    "synced_at": datetime.now().isoformat()
                }
            ))
        except Exception as e:
            print(f"[WARNING] Failed to embed chunk {i} of {title}: {e}")

    if points:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    # Update state
    state["page_hashes"][page_id] = current_hash

    return len(points)


def sync_database(database_id: str, incremental: bool = True) -> dict:
    """Sync all pages from a Notion database."""
    notion = get_notion()
    qdrant = get_qdrant()
    embedder = get_embedder()

    if not all([notion, qdrant, embedder]):
        return {"status": "error", "message": "Failed to initialize clients"}

    if not ensure_collection(qdrant):
        return {"status": "error", "message": "Failed to ensure collection"}

    state = load_sync_state() if incremental else {"last_sync": None, "page_hashes": {}}

    stats = {"pages_processed": 0, "chunks_added": 0, "errors": 0}

    try:
        # Query database
        response = notion.databases.query(database_id=database_id)
        pages = response.get("results", [])

        # Handle pagination
        while response.get("has_more"):
            response = notion.databases.query(
                database_id=database_id,
                start_cursor=response.get("next_cursor")
            )
            pages.extend(response.get("results", []))

        print(f"Found {len(pages)} pages in database")

        for page in pages:
            try:
                chunks = sync_page(notion, qdrant, embedder, page, state)
                stats["pages_processed"] += 1
                stats["chunks_added"] += chunks
                title = get_page_title(page)
                if chunks > 0:
                    print(f"  [+] {title}: {chunks} chunks")
                else:
                    print(f"  [=] {title}: no changes")
            except Exception as e:
                stats["errors"] += 1
                print(f"  [!] Failed: {e}")

    except Exception as e:
        return {"status": "error", "message": str(e)}

    state["last_sync"] = datetime.now().isoformat()
    save_sync_state(state)

    return {"status": "success", **stats}


def sync_all_shared_pages(incremental: bool = True) -> dict:
    """Sync all pages shared with the integration."""
    notion = get_notion()
    qdrant = get_qdrant()
    embedder = get_embedder()

    if not all([notion, qdrant, embedder]):
        return {"status": "error", "message": "Failed to initialize clients"}

    if not ensure_collection(qdrant):
        return {"status": "error", "message": "Failed to ensure collection"}

    state = load_sync_state() if incremental else {"last_sync": None, "page_hashes": {}}

    stats = {"pages_processed": 0, "chunks_added": 0, "errors": 0}

    try:
        # Search for all pages (using empty query)
        response = notion.search(filter={"property": "object", "value": "page"})
        pages = response.get("results", [])

        # Handle pagination
        while response.get("has_more"):
            response = notion.search(
                filter={"property": "object", "value": "page"},
                start_cursor=response.get("next_cursor")
            )
            pages.extend(response.get("results", []))

        print(f"Found {len(pages)} pages shared with integration")

        for page in pages:
            try:
                chunks = sync_page(notion, qdrant, embedder, page, state)
                stats["pages_processed"] += 1
                stats["chunks_added"] += chunks
                title = get_page_title(page)
                if chunks > 0:
                    print(f"  [+] {title}: {chunks} chunks")
            except Exception as e:
                stats["errors"] += 1
                print(f"  [!] Failed: {e}")

    except Exception as e:
        return {"status": "error", "message": str(e)}

    state["last_sync"] = datetime.now().isoformat()
    save_sync_state(state)

    return {"status": "success", **stats}


# ==============================================================================
# CLI Commands
# ==============================================================================
def cmd_sync(args):
    """Sync Notion content to Qdrant."""
    print("=" * 60)
    print("MUNIN NOTION SYNC")
    print("=" * 60)

    if args.database_id:
        print(f"Syncing database: {args.database_id}")
        result = sync_database(args.database_id, incremental=not args.full)
    else:
        print("Syncing all shared pages")
        result = sync_all_shared_pages(incremental=not args.full)

    print()
    print("=" * 60)
    print(f"Status: {result.get('status', 'unknown')}")
    if result.get("status") == "success":
        print(f"Pages processed: {result.get('pages_processed', 0)}")
        print(f"Chunks added: {result.get('chunks_added', 0)}")
        print(f"Errors: {result.get('errors', 0)}")
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    print("=" * 60)


def cmd_status(args):
    """Show sync status."""
    state = load_sync_state()

    print("=" * 60)
    print("NOTION SYNC STATUS")
    print("=" * 60)
    print(f"Last sync: {state.get('last_sync', 'Never')}")
    print(f"Pages tracked: {len(state.get('page_hashes', {}))}")
    print()

    qdrant = get_qdrant()
    if qdrant:
        try:
            collections = qdrant.get_collections().collections
            if any(c.name == COLLECTION_NAME for c in collections):
                info = qdrant.get_collection(COLLECTION_NAME)
                print(f"Qdrant collection '{COLLECTION_NAME}':")
                print(f"  Vectors: {info.points_count}")
                print(f"  Status: {info.status}")
            else:
                print(f"Qdrant collection '{COLLECTION_NAME}': not created")
        except Exception as e:
            print(f"Qdrant error: {e}")
    else:
        print("Qdrant: not connected")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Sync Notion workspace to Qdrant for RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s sync                     # Sync all shared pages
    %(prog)s sync --database-id ABC   # Sync specific database
    %(prog)s sync --full              # Full resync (ignore cache)
    %(prog)s status                   # Show sync status

Environment:
    NOTION_TOKEN    Notion integration token (required)
    QDRANT_HOST     Qdrant server host (default: localhost)
    QDRANT_PORT     Qdrant server port (default: 6333)
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # sync
    sync_parser = subparsers.add_parser("sync", help="Sync Notion to Qdrant")
    sync_parser.add_argument("--database-id", help="Specific database ID to sync")
    sync_parser.add_argument("--full", action="store_true",
                             help="Full resync (ignore incremental cache)")
    sync_parser.set_defaults(func=cmd_sync)

    # status
    status_parser = subparsers.add_parser("status", help="Show sync status")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
