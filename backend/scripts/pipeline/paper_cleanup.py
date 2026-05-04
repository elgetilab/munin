#!/usr/bin/env python3
"""
==============================================================================
MUNIN Paper Cleanup Script
==============================================================================
Remove papers from Qdrant, Neo4j, SQLite, and filesystem by DOI or criteria.
Multi-source metadata enrichment and repair-first cleanup logic.

Commands:
    # Verify single DOI against all sources (debugging)
    ./paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7

    # Multi-source repair and clean - enriches from all sources, only removes
    # papers if ALL metadata sources (OpenAlex, Semantic Scholar, CrossRef) fail
    ./paper_cleanup.py repair-and-clean --max-check 500 --dry-run
    ./paper_cleanup.py repair-and-clean --max-check 500 --auto-remove --limit 50

    # Scan processed papers directory for orphaned papers
    ./paper_cleanup.py scan-processed --check-pdf --check-metadata

    # Remove a single paper by DOI
    ./paper_cleanup.py remove --doi 10.1111/j.1749-6632.1978.tb22009.x

    # Find papers with no abstract AND no references (OpenAlex only)
    ./paper_cleanup.py find-low-quality

    # Find papers with less than 3 pages
    ./paper_cleanup.py find-short --min-pages 3

    # Bulk remove papers from a file (one DOI per line)
    ./paper_cleanup.py bulk-remove --file papers_to_remove.txt

    # Dry run (show what would be removed without actually removing)
    ./paper_cleanup.py remove --doi 10.1234/example --dry-run

Environment Variables:
    OPENALEX_API_KEY          - OpenAlex API key (optional, for higher limits)
    SEMANTIC_SCHOLAR_API_KEY  - Semantic Scholar API key (optional)
    ADMIN_EMAIL               - Email for API polite pools (required)

Requirements:
    pip install qdrant-client neo4j requests
==============================================================================
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

# ==============================================================================
# Configuration
# ==============================================================================
DATA_DIR = Path(os.getenv("MUNIN_DATA_DIR", "/opt/munin/data"))
PAPERS_DIR = DATA_DIR / "papers"
PDF_DIR = PAPERS_DIR / "pdf"
PROCESSED_DIR = PAPERS_DIR / "processed"
SKIPPED_DIR = PAPERS_DIR / "skipped"
QUEUE_DB = PAPERS_DIR / "crawler_queue.db"
BLOCKLIST_FILE = PAPERS_DIR / "blocklist.txt"
FAILED_DOWNLOADS_FILE = PAPERS_DIR / "failed_downloads.txt"
LOGS_DIR = PAPERS_DIR / "logs"

# User agent email for API requests
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "munin-neo4j-password")
COLLECTION_NAME = "papers"

# API rate limit delays (seconds)
OPENALEX_DELAY = 0.1      # 100k/day with key
SEMANTIC_SCHOLAR_DELAY = 2.0  # Conservative for unauthenticated access
CROSSREF_DELAY = 0.02     # 50/s polite pool


# ==============================================================================
# Dataclasses for Multi-Source Metadata
# ==============================================================================
@dataclass
class MetadataResult:
    """Result from a single metadata source."""
    source: str  # 'openalex', 'semantic_scholar', 'crossref'
    found: bool
    title: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    reference_count: Optional[int] = None
    citation_count: Optional[int] = None
    is_retracted: bool = False
    authors: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AggregatedMetadata:
    """Merged metadata from all sources."""
    doi: str
    sources_checked: List[str] = field(default_factory=list)
    sources_with_data: List[str] = field(default_factory=list)

    # Best available data (prioritized across sources)
    title: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    reference_count: Optional[int] = None
    citation_count: Optional[int] = None
    authors: List[str] = field(default_factory=list)

    is_retracted: bool = False
    pdf_exists: bool = False
    pdf_page_count: Optional[int] = None  # Page count from actual PDF file

    # Track which source provided each field
    abstract_source: Optional[str] = None
    title_source: Optional[str] = None

    @property
    def has_any_data(self) -> bool:
        """Returns True if any source had data for this paper."""
        return len(self.sources_with_data) > 0

    @property
    def effective_page_count(self) -> Optional[int]:
        """Returns page count from PDF if available, otherwise from API."""
        return self.pdf_page_count if self.pdf_page_count is not None else self.page_count

    @property
    def is_short(self) -> bool:
        """Returns True if page count < 3 (from PDF or API)."""
        pages = self.effective_page_count
        return pages is not None and pages < 3

    @property
    def should_remove(self) -> bool:
        """Returns True if paper should be removed: retracted, short, or no data anywhere."""
        return self.is_retracted or self.is_short or not self.has_any_data

    @property
    def status(self) -> str:
        """Human-readable status string."""
        if self.is_retracted:
            return "RETRACTED"
        if self.is_short:
            return "SHORT"
        if not self.has_any_data:
            return "NO DATA"
        return "OK"


# ==============================================================================
# Database Connections
# ==============================================================================
def get_qdrant_client():
    """Get Qdrant client."""
    try:
        from qdrant_client import QdrantClient
        return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    except Exception as e:
        print(f"[WARNING] Could not connect to Qdrant: {e}")
        return None


def get_neo4j_driver():
    """Get Neo4j driver."""
    try:
        from neo4j import GraphDatabase
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except Exception as e:
        print(f"[WARNING] Could not connect to Neo4j: {e}")
        return None


def get_sqlite_conn():
    """Get SQLite connection for crawler queue."""
    if not QUEUE_DB.exists():
        print(f"[WARNING] Crawler queue database not found: {QUEUE_DB}")
        return None
    return sqlite3.connect(QUEUE_DB)


# ==============================================================================
# Blocklist Management
# ==============================================================================
def load_blocklist() -> Set[str]:
    """Load DOI blocklist from file."""
    if not BLOCKLIST_FILE.exists():
        return set()
    with open(BLOCKLIST_FILE) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def add_to_blocklist(doi: str):
    """Add a DOI to the blocklist."""
    blocklist = load_blocklist()
    if doi in blocklist:
        return  # Already blocked

    # Ensure parent directory exists
    BLOCKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(BLOCKLIST_FILE, "a") as f:
        f.write(f"{doi}\n")
    print(f"  Added to blocklist: {doi}")


def load_failed_downloads() -> Set[str]:
    """Load failed downloads list from file."""
    if not FAILED_DOWNLOADS_FILE.exists():
        return set()
    with open(FAILED_DOWNLOADS_FILE) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def add_to_failed_downloads(doi: str):
    """Add a DOI to the failed downloads list."""
    failed = load_failed_downloads()
    if doi in failed:
        return  # Already in list

    FAILED_DOWNLOADS_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(FAILED_DOWNLOADS_FILE, "a") as f:
        f.write(f"{doi}\n")


def clear_failed_downloads():
    """Clear the failed downloads list."""
    if FAILED_DOWNLOADS_FILE.exists():
        FAILED_DOWNLOADS_FILE.unlink()
        print(f"[OK] Cleared failed downloads list: {FAILED_DOWNLOADS_FILE}")


# ==============================================================================
# Paper Removal Functions
# ==============================================================================
def find_paper_in_qdrant(qdrant, doi: str) -> List[int]:
    """Find paper point IDs in Qdrant by DOI."""
    if not qdrant:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Search by DOI in payload
        results = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="doi", match=MatchValue(value=doi))]
            ),
            limit=100
        )

        points = results[0] if results else []
        return [p.id for p in points]
    except Exception as e:
        print(f"  [WARNING] Qdrant search failed: {e}")
        return []


def remove_from_qdrant(qdrant, doi: str, dry_run: bool = False) -> bool:
    """Remove paper from Qdrant by DOI."""
    if not qdrant:
        return False

    point_ids = find_paper_in_qdrant(qdrant, doi)
    if not point_ids:
        print(f"  [INFO] Paper not found in Qdrant: {doi}")
        return False

    if dry_run:
        print(f"  [DRY-RUN] Would remove {len(point_ids)} point(s) from Qdrant")
        return True

    try:
        qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector=point_ids
        )
        print(f"  [OK] Removed {len(point_ids)} point(s) from Qdrant")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove from Qdrant: {e}")
        return False


def remove_from_neo4j(driver, doi: str, dry_run: bool = False) -> tuple[bool, bool]:
    """
    Remove paper from Neo4j by DOI.

    Returns:
        Tuple of (was_removed, was_stub_node)
        - was_removed: True if paper was found and removed
        - was_stub_node: True if it was a stub (citation target, never processed)
    """
    if not driver:
        return False, False

    try:
        with driver.session() as session:
            # First check if paper exists and get details
            result = session.run("""
                MATCH (p:Paper {doi: $doi})
                RETURN p.title as title, p.abstract as abstract, p.paper_id as paper_id
            """, doi=doi).single()

            if not result:
                print(f"  [INFO] Paper not found in Neo4j: {doi}")
                return False, False

            title = result['title']
            has_abstract = bool(result['abstract'])
            has_paper_id = bool(result['paper_id'])

            # Stub nodes are citation targets that were never downloaded/processed
            # They have a DOI but no paper_id and usually no title/abstract
            is_stub = not has_paper_id and not has_abstract

            if dry_run:
                node_type = "stub node (citation target)" if is_stub else "paper"
                print(f"  [DRY-RUN] Would remove {node_type} from Neo4j: {title or doi}")
                return True, is_stub

            # Delete paper and all relationships
            session.run("""
                MATCH (p:Paper {doi: $doi})
                DETACH DELETE p
            """, doi=doi)

            node_type = "stub node" if is_stub else "paper"
            print(f"  [OK] Removed {node_type} from Neo4j: {title or doi}")
            return True, is_stub
    except Exception as e:
        print(f"  [ERROR] Failed to remove from Neo4j: {e}")
        return False, False


def remove_from_sqlite(doi: str, dry_run: bool = False) -> bool:
    """Remove paper from SQLite crawler queue by DOI."""
    conn = get_sqlite_conn()
    if not conn:
        return False

    try:
        # Check if paper exists
        result = conn.execute(
            "SELECT id, pdf_path FROM papers WHERE doi = ?", (doi,)
        ).fetchone()

        if not result:
            print(f"  [INFO] Paper not found in SQLite: {doi}")
            conn.close()
            return False

        paper_id, pdf_path = result

        if dry_run:
            print(f"  [DRY-RUN] Would remove paper from SQLite: {paper_id}")
            conn.close()
            return True

        conn.execute("DELETE FROM papers WHERE doi = ?", (doi,))
        conn.commit()
        conn.close()

        print(f"  [OK] Removed paper from SQLite: {doi}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove from SQLite: {e}")
        if conn:
            conn.close()
        return False


def doi_to_filename(doi: str) -> str:
    """Convert DOI to safe filename (matches paper_crawler.py logic)."""
    # Same logic as paper_crawler.py line 613
    return f"doi_{doi.replace('/', '_').replace(':', '_')}.pdf"


def get_pdf_page_count(pdf_path: Path) -> Optional[int]:
    """Get page count from a PDF file."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except ImportError:
        # Try pdfinfo as fallback
        import subprocess
        try:
            result = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Pages:"):
                    return int(line.split(":")[1].strip())
        except Exception:
            pass
    except Exception:
        pass
    return None


def find_pdf_by_doi(doi: str) -> Optional[Path]:
    """Find PDF file by DOI."""
    # Generate expected filename (same as paper_crawler.py)
    filename = doi_to_filename(doi)
    pdf_path = PDF_DIR / filename

    if pdf_path.exists():
        return pdf_path

    # Try case-insensitive search if exact match fails
    if PDF_DIR.exists():
        filename_lower = filename.lower()
        for f in PDF_DIR.iterdir():
            if f.name.lower() == filename_lower:
                return f

    # Also check processed markers
    marker_name = filename.replace('.pdf', '.json')
    marker_path = PROCESSED_DIR / marker_name
    if marker_path.exists():
        return marker_path

    return None


def remove_pdf_file(doi: str, dry_run: bool = False) -> bool:
    """Remove PDF file and processed marker by DOI."""
    pdf_path = find_pdf_by_doi(doi)
    if not pdf_path:
        print(f"  [INFO] PDF file not found for: {doi}")
        return False

    if dry_run:
        print(f"  [DRY-RUN] Would remove file: {pdf_path}")
        return True

    try:
        pdf_path.unlink()
        print(f"  [OK] Removed file: {pdf_path}")

        # Also remove processed marker
        marker_name = f"{pdf_path.stem}.json"
        marker_path = PROCESSED_DIR / marker_name
        if marker_path.exists():
            marker_path.unlink()
            print(f"  [OK] Removed marker: {marker_path}")

        # Also remove skipped marker if exists
        skipped_path = SKIPPED_DIR / marker_name
        if skipped_path.exists():
            skipped_path.unlink()
            print(f"  [OK] Removed skipped marker: {skipped_path}")

        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove file: {e}")
        return False


def remove_paper_by_doi(doi: str, dry_run: bool = False, add_blocklist: bool = True):
    """Remove a paper from all databases by DOI."""
    print(f"\n{'='*60}")
    print(f"Removing paper: {doi}")
    print(f"{'='*60}")

    if dry_run:
        print("[DRY-RUN MODE - No changes will be made]")

    # Connect to databases
    qdrant = get_qdrant_client()
    neo4j = get_neo4j_driver()

    # Track what was found/removed
    removed_from_any = False
    was_stub_node = False

    if remove_from_qdrant(qdrant, doi, dry_run):
        removed_from_any = True

    neo4j_removed, was_stub = remove_from_neo4j(neo4j, doi, dry_run)
    if neo4j_removed:
        removed_from_any = True
        was_stub_node = was_stub

    if remove_from_sqlite(doi, dry_run):
        removed_from_any = True

    if remove_pdf_file(doi, dry_run):
        removed_from_any = True

    # Add to blocklist
    if add_blocklist and not dry_run:
        add_to_blocklist(doi)
    elif add_blocklist and dry_run:
        print(f"  [DRY-RUN] Would add to blocklist: {doi}")

    # Cleanup connections
    if neo4j:
        neo4j.close()

    if removed_from_any:
        if was_stub_node:
            print(f"\n[OK] Stub node removal complete: {doi}")
            print(f"     (This was a citation target that was never downloaded)")
        else:
            print(f"\n[OK] Paper removal complete: {doi}")
    else:
        print(f"\n[INFO] Paper not found in any store: {doi}")


# ==============================================================================
# Finding Low-Quality Papers
# ==============================================================================
def fetch_openalex_metadata(doi: str) -> Optional[Dict]:
    """Fetch metadata from OpenAlex API."""
    api_key = os.getenv("OPENALEX_API_KEY", "")
    try:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        params = {"api_key": api_key} if api_key else {}
        headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{ADMIN_EMAIL})"}
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def enrich_paper_from_openalex(driver, doi: str) -> Dict:
    """
    Fetch metadata from OpenAlex and update Neo4j if data is available.

    Returns dict with:
        - enriched: bool - whether paper was enriched
        - has_abstract: bool
        - has_refs: bool
        - page_count: int or None
        - is_retracted: bool
    """
    result = {
        "enriched": False,
        "has_abstract": False,
        "has_refs": False,
        "page_count": None,
        "is_retracted": False,
        "title": None
    }

    metadata = fetch_openalex_metadata(doi)
    if not metadata:
        return result

    # Extract data from OpenAlex
    result["is_retracted"] = metadata.get("is_retracted", False)
    result["title"] = metadata.get("title")

    # Check abstract (OpenAlex uses inverted index format)
    abstract_inv = metadata.get("abstract_inverted_index")
    if abstract_inv:
        result["has_abstract"] = True
        # Reconstruct abstract from inverted index
        try:
            word_positions = []
            for word, positions in abstract_inv.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            abstract_text = " ".join(word for _, word in word_positions)
        except Exception:
            abstract_text = None
    else:
        abstract_text = None

    # Check references
    referenced_works = metadata.get("referenced_works", [])
    result["has_refs"] = len(referenced_works) > 0

    # Check page count
    biblio = metadata.get("biblio", {})
    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    if first_page and last_page:
        try:
            result["page_count"] = int(last_page) - int(first_page) + 1
        except ValueError:
            pass

    # Enrich Neo4j if we have new data
    if driver and (abstract_text or result["has_refs"]):
        try:
            with driver.session() as session:
                # Update abstract if we have one and Neo4j doesn't
                if abstract_text:
                    session.run("""
                        MATCH (p:Paper {doi: $doi})
                        WHERE p.abstract IS NULL OR p.abstract = ''
                        SET p.abstract = $abstract
                    """, doi=doi, abstract=abstract_text[:2000])
                    result["enriched"] = True

                # We could also add citation relationships here if needed
                # For now, just mark that refs exist

            if result["enriched"]:
                print(f"    [ENRICHED] Updated abstract from OpenAlex")
        except Exception as e:
            print(f"    [WARNING] Failed to enrich Neo4j: {e}")

    return result


# ==============================================================================
# Multi-Source Metadata Fetcher
# ==============================================================================
class MultiSourceMetadataFetcher:
    """Fetches and aggregates metadata from OpenAlex, Semantic Scholar, and CrossRef."""

    def __init__(self):
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY", "")
        self.semantic_scholar_api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")

    def fetch_openalex(self, doi: str) -> MetadataResult:
        """Fetch metadata from OpenAlex."""
        result = MetadataResult(source="openalex", found=False)

        try:
            url = f"https://api.openalex.org/works/https://doi.org/{doi}"
            params = {"api_key": self.openalex_api_key} if self.openalex_api_key else {}
            headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{self.admin_email})"}
            response = requests.get(url, params=params, headers=headers, timeout=15)

            time.sleep(OPENALEX_DELAY)

            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json()
            result.found = True
            result.title = data.get("title")
            result.year = data.get("publication_year")
            result.is_retracted = data.get("is_retracted", False)

            # Extract abstract from inverted index
            abstract_inv = data.get("abstract_inverted_index")
            if abstract_inv:
                try:
                    word_positions = []
                    for word, positions in abstract_inv.items():
                        for pos in positions:
                            word_positions.append((pos, word))
                    word_positions.sort()
                    result.abstract = " ".join(word for _, word in word_positions)
                except Exception:
                    pass

            # Reference count
            referenced_works = data.get("referenced_works", [])
            result.reference_count = len(referenced_works) if referenced_works else None

            # Citation count
            cited_by_count = data.get("cited_by_count")
            if cited_by_count is not None:
                result.citation_count = cited_by_count

            # Page count from biblio
            biblio = data.get("biblio", {})
            first_page = biblio.get("first_page")
            last_page = biblio.get("last_page")
            if first_page and last_page:
                try:
                    result.page_count = int(last_page) - int(first_page) + 1
                except ValueError:
                    pass

            # Authors
            authorships = data.get("authorships", [])
            result.authors = [
                a.get("author", {}).get("display_name", "")
                for a in authorships if a.get("author", {}).get("display_name")
            ]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_semantic_scholar(self, doi: str) -> MetadataResult:
        """Fetch metadata from Semantic Scholar."""
        result = MetadataResult(source="semantic_scholar", found=False)

        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            params = {"fields": "title,abstract,year,citationCount,referenceCount,authors,isOpenAccess"}
            headers = {}
            if self.semantic_scholar_api_key:
                headers["x-api-key"] = self.semantic_scholar_api_key

            response = requests.get(url, params=params, headers=headers, timeout=15)

            time.sleep(SEMANTIC_SCHOLAR_DELAY)

            if response.status_code == 404:
                result.error = "Not found"
                return result
            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json()
            result.found = True
            result.title = data.get("title")
            result.abstract = data.get("abstract")
            result.year = data.get("year")
            result.citation_count = data.get("citationCount")
            result.reference_count = data.get("referenceCount")

            # Authors
            authors = data.get("authors", [])
            result.authors = [a.get("name", "") for a in authors if a.get("name")]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_crossref(self, doi: str) -> MetadataResult:
        """Fetch metadata from CrossRef."""
        result = MetadataResult(source="crossref", found=False)

        try:
            url = f"https://api.crossref.org/works/{doi}"
            headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{self.admin_email})"}
            response = requests.get(url, headers=headers, timeout=15)

            time.sleep(CROSSREF_DELAY)

            if response.status_code == 404:
                result.error = "Not found"
                return result
            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json().get("message", {})
            result.found = True
            result.title = data.get("title", [None])[0] if data.get("title") else None
            result.abstract = data.get("abstract")

            # Year from published-print or published-online
            for date_field in ["published-print", "published-online", "issued"]:
                if date_field in data:
                    date_parts = data[date_field].get("date-parts", [[None]])
                    if date_parts and date_parts[0] and date_parts[0][0]:
                        result.year = date_parts[0][0]
                        break

            # Reference count
            result.reference_count = data.get("references-count")

            # Citation count (CrossRef calls it "is-referenced-by-count")
            result.citation_count = data.get("is-referenced-by-count")

            # Authors
            authors = data.get("author", [])
            result.authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in authors
            ]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_all(self, doi: str, check_pdf: bool = False) -> AggregatedMetadata:
        """
        Fetch metadata from all sources and aggregate.

        Args:
            doi: The DOI to look up
            check_pdf: Whether to check if PDF exists on filesystem

        Returns:
            AggregatedMetadata with best available data from all sources
        """
        aggregated = AggregatedMetadata(doi=doi)

        # Fetch from all sources
        results = {
            "openalex": self.fetch_openalex(doi),
            "semantic_scholar": self.fetch_semantic_scholar(doi),
            "crossref": self.fetch_crossref(doi),
        }

        for source, result in results.items():
            aggregated.sources_checked.append(source)
            if result.found:
                aggregated.sources_with_data.append(source)

        # Merge results with priority
        # Title: prefer CrossRef (authoritative), then S2, then OpenAlex
        for source in ["crossref", "semantic_scholar", "openalex"]:
            if results[source].found and results[source].title and not aggregated.title:
                aggregated.title = results[source].title
                aggregated.title_source = source

        # Abstract: prefer longest non-empty abstract
        best_abstract = ""
        best_abstract_source = None
        for source, result in results.items():
            if result.found and result.abstract and len(result.abstract) > len(best_abstract):
                best_abstract = result.abstract
                best_abstract_source = source
        if best_abstract:
            aggregated.abstract = best_abstract
            aggregated.abstract_source = best_abstract_source

        # Year: prefer any available
        for source in ["openalex", "semantic_scholar", "crossref"]:
            if results[source].found and results[source].year and not aggregated.year:
                aggregated.year = results[source].year

        # Page count: only OpenAlex provides this reliably
        if results["openalex"].found and results["openalex"].page_count:
            aggregated.page_count = results["openalex"].page_count

        # Citation count: prefer Semantic Scholar (most accurate)
        for source in ["semantic_scholar", "openalex", "crossref"]:
            if results[source].found and results[source].citation_count is not None and aggregated.citation_count is None:
                aggregated.citation_count = results[source].citation_count

        # Reference count: prefer Semantic Scholar
        for source in ["semantic_scholar", "openalex", "crossref"]:
            if results[source].found and results[source].reference_count is not None and aggregated.reference_count is None:
                aggregated.reference_count = results[source].reference_count

        # Authors: prefer CrossRef
        for source in ["crossref", "semantic_scholar", "openalex"]:
            if results[source].found and results[source].authors and not aggregated.authors:
                aggregated.authors = results[source].authors

        # Retraction: any source reporting retracted = retracted
        aggregated.is_retracted = results["openalex"].is_retracted

        # Check PDF if requested
        if check_pdf:
            pdf_path = find_pdf_by_doi(doi)
            aggregated.pdf_exists = pdf_path is not None
            # Get page count from actual PDF file
            if pdf_path and pdf_path.suffix.lower() == '.pdf':
                aggregated.pdf_page_count = get_pdf_page_count(pdf_path)

        return aggregated


def verify_doi(doi: str, verbose: bool = True) -> AggregatedMetadata:
    """
    Verify a single DOI against all metadata sources.

    Args:
        doi: The DOI to verify
        verbose: Whether to print detailed output

    Returns:
        AggregatedMetadata for the DOI
    """
    fetcher = MultiSourceMetadataFetcher()
    metadata = fetcher.fetch_all(doi, check_pdf=True)

    if verbose:
        print(f"\n{'='*60}")
        print(f"DOI Verification: {doi}")
        print(f"{'='*60}")
        print()

        # Source status
        print("Metadata Sources:")
        for source in ["openalex", "semantic_scholar", "crossref"]:
            status = "Y" if source in metadata.sources_with_data else "N"
            print(f"  {source:<20} {status}")

        print()
        print("Aggregated Data:")
        print(f"  Title:           {metadata.title or 'N/A'}")
        print(f"  Year:            {metadata.year or 'N/A'}")
        print(f"  Authors:         {', '.join(metadata.authors[:3]) if metadata.authors else 'N/A'}")
        print(f"  Abstract:        {('Yes (' + metadata.abstract_source + ')') if metadata.abstract else 'N/A'}")
        print(f"  Page count (API):{metadata.page_count or 'N/A'}")
        print(f"  Page count (PDF):{metadata.pdf_page_count or 'N/A'}")
        print(f"  Citations:       {metadata.citation_count if metadata.citation_count is not None else 'N/A'}")
        print(f"  References:      {metadata.reference_count if metadata.reference_count is not None else 'N/A'}")
        print(f"  PDF exists:      {'Yes' if metadata.pdf_exists else 'No'}")

        print()
        print("Decision:")
        print(f"  Is retracted:    {'Yes' if metadata.is_retracted else 'No'}")
        print(f"  Is short (<3p):  {'Yes' if metadata.is_short else 'No'}")
        print(f"  Has any data:    {'Yes' if metadata.has_any_data else 'No'}")
        print(f"  Status:          {metadata.status}")
        print(f"  Should remove:   {'Yes' if metadata.should_remove else 'No'}")
        print()

    return metadata


def repair_and_clean(
    source: str = "both",
    max_check: int = 500,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False,
    output_file: Optional[str] = None,
    dry_run: bool = False,
    check_pdf: bool = True,
    export_orphaned: Optional[str] = None
) -> Dict:
    """
    Multi-source metadata enrichment and cleanup.

    Process:
    1. Get papers from Neo4j and/or processed directory
    2. Check each against OpenAlex, Semantic Scholar, and CrossRef
    3. If ANY source has data: enrich the paper
    4. If ALL sources fail: flag for removal

    Args:
        source: 'neo4j', 'processed', or 'both' (default: both)
        max_check: Maximum papers to check (default: 500)
        auto_remove: Automatically remove flagged papers
        limit: Maximum papers to auto-remove
        confirm: Required for auto-remove without limit
        output_file: Save results to JSON file
        dry_run: Don't modify databases
        check_pdf: Check if PDFs exist for papers

    Returns:
        Dict with summary statistics
    """
    driver = get_neo4j_driver()
    fetcher = MultiSourceMetadataFetcher()

    print("=" * 70)
    print("Repair and Clean - Multi-Source Metadata Enrichment")
    print("=" * 70)
    print()
    print("This tool checks papers against multiple sources and only removes")
    print("papers that have NO DATA in any source.")
    print()

    # Collect DOIs to check
    dois_to_check = set()

    if source in ("neo4j", "both"):
        if driver:
            try:
                with driver.session() as session:
                    result = session.run("""
                        MATCH (p:Paper)
                        WHERE p.doi IS NOT NULL
                        RETURN p.doi as doi
                        ORDER BY rand()
                        LIMIT $max_check
                    """, max_check=max_check)
                    neo4j_dois = [r["doi"] for r in result]
                    dois_to_check.update(neo4j_dois)
                    print(f"Found {len(neo4j_dois)} papers from Neo4j")
            except Exception as e:
                print(f"[WARNING] Failed to query Neo4j: {e}")

    if source in ("processed", "both"):
        if PROCESSED_DIR.exists():
            processed_files = list(PROCESSED_DIR.glob("*.json"))[:max_check]
            for pf in processed_files:
                try:
                    with open(pf) as f:
                        data = json.load(f)
                        if "doi" in data:
                            dois_to_check.add(data["doi"])
                except Exception:
                    pass
            print(f"Found {len(processed_files)} processed files")

    dois_to_check = list(dois_to_check)[:max_check]
    print(f"\nTotal unique DOIs to check: {len(dois_to_check)}")
    print()

    if not dois_to_check:
        print("No papers to check.")
        return {"checked": 0}

    # Process papers
    results = {
        "checked": 0,
        "ok": 0,
        "enriched": 0,
        "no_data": 0,
        "retracted": 0,
        "short": 0,
        "orphaned": 0,  # In DB but no PDF
        "to_remove": [],
        "to_enrich": [],
        "orphaned_dois": [],
    }

    # Print header
    print(f"{'DOI':<45} {'OA':<4} {'S2':<4} {'CR':<4} {'PDF':<5} {'Status'}")
    print("-" * 80)

    for i, doi in enumerate(dois_to_check):
        metadata = fetcher.fetch_all(doi, check_pdf=check_pdf)
        results["checked"] += 1

        # Source indicators
        oa = "Y" if "openalex" in metadata.sources_with_data else "N"
        s2 = "Y" if "semantic_scholar" in metadata.sources_with_data else "N"
        cr = "Y" if "crossref" in metadata.sources_with_data else "N"
        pdf = "OK" if metadata.pdf_exists else "MISS" if check_pdf else "?"

        # Determine status and action
        if metadata.is_retracted:
            status = "RETRACTED"
            results["retracted"] += 1
            results["to_remove"].append(doi)
        elif metadata.is_short:
            status = "SHORT"
            results["short"] += 1
            results["to_remove"].append(doi)
        elif not metadata.has_any_data:
            status = "NO DATA"
            results["no_data"] += 1
            results["to_remove"].append(doi)
        elif metadata.has_any_data and check_pdf and not metadata.pdf_exists:
            status = "ORPHANED"
            results["orphaned"] += 1
            results["orphaned_dois"].append(doi)
        else:
            # Has data - check if we should enrich
            if metadata.abstract and driver and not dry_run:
                # Try to enrich Neo4j with abstract if missing
                try:
                    with driver.session() as session:
                        update_result = session.run("""
                            MATCH (p:Paper {doi: $doi})
                            WHERE p.abstract IS NULL OR p.abstract = ''
                            SET p.abstract = $abstract
                            RETURN count(p) as updated
                        """, doi=doi, abstract=metadata.abstract[:2000])
                        if update_result.single()["updated"] > 0:
                            status = "ENRICHED"
                            results["enriched"] += 1
                            results["to_enrich"].append(doi)
                        else:
                            status = "OK"
                            results["ok"] += 1
                except Exception:
                    status = "OK"
                    results["ok"] += 1
            else:
                status = "OK"
                results["ok"] += 1

        print(f"{doi:<45} {oa:<4} {s2:<4} {cr:<4} {pdf:<5} {status}")

        # Progress update
        if (i + 1) % 50 == 0:
            print(f"... processed {i + 1}/{len(dois_to_check)} ...")

    # Print summary
    print("-" * 80)
    print()
    print("Summary:")
    print(f"  Papers checked:        {results['checked']}")
    print(f"  Already OK:            {results['ok']}")
    print(f"  Enriched:              {results['enriched']}  (metadata added)")
    print(f"  Removal candidates:    {results['no_data']}  (no data from any source)")
    print(f"  Retracted:             {results['retracted']}")
    print(f"  Short (<3 pages):      {results['short']}")
    if check_pdf:
        print(f"  Orphaned (no PDF):     {results['orphaned']}")

    # Export orphaned DOIs for re-crawling
    if export_orphaned and results["orphaned_dois"]:
        with open(export_orphaned, "w") as f:
            f.write("# Orphaned papers - in database but PDF missing\n")
            f.write("# Re-crawl these DOIs to restore PDFs\n")
            for doi in results["orphaned_dois"]:
                f.write(f"{doi}\n")
        print(f"\n[OK] Exported {len(results['orphaned_dois'])} orphaned DOIs to: {export_orphaned}")

    # Save results to file
    if output_file:
        with open(output_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "max_check": max_check,
                "results": results
            }, f, indent=2)
        print(f"\n[OK] Results saved to: {output_file}")

    # Auto-remove if requested
    if auto_remove and results["to_remove"]:
        _auto_remove_papers(results["to_remove"], limit, confirm)

    if driver:
        driver.close()

    return results


def repair_auto(
    source: str = "neo4j",
    max_papers: int = 100,
    output_file: Optional[str] = None,
    scripts_dir: Optional[str] = None,
    retry_failed: bool = False,
    single_doi: Optional[str] = None
) -> Dict:
    """
    Autonomous paper repair - works one paper at a time with immediate actions.

    For each paper:
    - If no data in any source → REMOVE immediately
    - If retracted → REMOVE immediately
    - If short (<3 pages) → REMOVE immediately
    - If orphaned (missing PDF) → RE-DOWNLOAD via paper_crawler.py
    - If missing embeddings → RE-EMBED via paper_pipeline.py
    - Otherwise → ENRICH metadata if possible

    Args:
        source: 'neo4j' or 'processed' (where to get paper list)
        max_papers: Maximum papers to process
        output_file: Save log to JSON file
        scripts_dir: Directory containing paper_crawler.py and paper_pipeline.py

    Returns:
        Dict with processing statistics
    """
    import subprocess

    # Find scripts directory
    if scripts_dir is None:
        scripts_dir = Path(__file__).parent
    else:
        scripts_dir = Path(scripts_dir)

    crawler_script = scripts_dir / "paper_crawler.py"
    pipeline_script = scripts_dir / "paper_pipeline.py"

    if not crawler_script.exists():
        print(f"[ERROR] paper_crawler.py not found at: {crawler_script}")
        return {"error": "crawler_script_not_found"}

    if not pipeline_script.exists():
        print(f"[ERROR] paper_pipeline.py not found at: {pipeline_script}")
        return {"error": "pipeline_script_not_found"}

    driver = get_neo4j_driver()
    qdrant = get_qdrant_client()
    fetcher = MultiSourceMetadataFetcher()

    print("=" * 70)
    print("Autonomous Paper Repair")
    print("=" * 70)
    print()
    print("Actions:")
    print("  - NO DATA in any source  → Remove from all databases")
    print("  - RETRACTED              → Remove from all databases")
    print("  - SHORT (<3 pages)       → Remove from all databases")
    print("  - ORPHANED (no PDF)      → Re-download via paper_crawler.py")
    print("  - NO EMBEDDINGS          → Re-embed via paper_pipeline.py")
    print()

    # Handle failed downloads list
    if retry_failed:
        clear_failed_downloads()
        failed_downloads = set()
    else:
        failed_downloads = load_failed_downloads()
        if failed_downloads:
            print(f"Skipping {len(failed_downloads)} previously failed downloads")
            print(f"  (use --retry-failed to retry them)")
            print()

    # Collect DOIs to check
    dois_to_check = []

    if single_doi:
        # Process a single DOI
        dois_to_check = [single_doi]
        print(f"Processing single DOI: {single_doi}")
    elif source == "neo4j" and driver:
        try:
            with driver.session() as session:
                result = session.run("""
                    MATCH (p:Paper)
                    WHERE p.doi IS NOT NULL
                    RETURN p.doi as doi
                    ORDER BY rand()
                    LIMIT $max_papers
                """, max_papers=max_papers)
                dois_to_check = [r["doi"] for r in result]
                print(f"Found {len(dois_to_check)} papers from Neo4j")
        except Exception as e:
            print(f"[ERROR] Failed to query Neo4j: {e}")
            return {"error": str(e)}

    elif source == "processed":
        if PROCESSED_DIR.exists():
            processed_files = list(PROCESSED_DIR.glob("*.json"))[:max_papers]
            for pf in processed_files:
                try:
                    with open(pf) as f:
                        data = json.load(f)
                        if "doi" in data:
                            dois_to_check.append(data["doi"])
                except Exception:
                    pass
            print(f"Found {len(dois_to_check)} papers from processed directory")

    if not dois_to_check:
        print("No papers to process.")
        return {"checked": 0}

    # Stats
    stats = {
        "checked": 0,
        "ok": 0,
        "enriched": 0,
        "removed": 0,
        "redownloaded": 0,
        "redownload_failed": 0,
        "reembedded": 0,
        "reembed_failed": 0,
        "errors": [],
        "log": []
    }

    print(f"\nProcessing {len(dois_to_check)} papers...\n")
    print(f"{'#':<5} {'DOI':<40} {'OA':<3} {'S2':<3} {'CR':<3} {'Action':<20} {'Result'}")
    print("-" * 100)

    for i, doi in enumerate(dois_to_check):
        stats["checked"] += 1
        log_entry = {"doi": doi, "index": i + 1}

        try:
            # Check metadata from all sources
            metadata = fetcher.fetch_all(doi, check_pdf=True)

            oa = "Y" if "openalex" in metadata.sources_with_data else "N"
            s2 = "Y" if "semantic_scholar" in metadata.sources_with_data else "N"
            cr = "Y" if "crossref" in metadata.sources_with_data else "N"

            # Decide action
            if metadata.is_retracted:
                action = "REMOVE (retracted)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "retracted"

            elif metadata.is_short:
                action = "REMOVE (short)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "short"

            elif not metadata.has_any_data:
                action = "REMOVE (no data)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "no_data"

            elif not metadata.pdf_exists:
                # Check if this DOI previously failed to download
                if doi in failed_downloads:
                    action = "SKIP (prev failed)"
                    stats["skipped_failed"] = stats.get("skipped_failed", 0) + 1
                    result = "Skipped"
                    log_entry["action"] = "skipped_previous_failure"
                else:
                    action = "RE-DOWNLOAD"
                    # Call paper_crawler.py download-single
                    proc = subprocess.run(
                        ["python3", str(crawler_script), "download-single", doi, "--quiet"],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if proc.returncode == 0:
                        stats["redownloaded"] += 1
                        result = "Downloaded"
                        log_entry["action"] = "redownloaded"

                        # Now check if we need to embed
                        pdf_path = find_pdf_by_doi(doi)
                        if pdf_path and qdrant:
                            # Check if embeddings exist
                            has_embeddings = bool(find_paper_in_qdrant(qdrant, doi))
                            if not has_embeddings:
                                # Re-embed
                                proc2 = subprocess.run(
                                    ["python3", str(pipeline_script), "--single", str(pdf_path)],
                                    capture_output=True,
                                    text=True,
                                    timeout=300
                                )
                                if proc2.returncode == 0:
                                    stats["reembedded"] += 1
                                    result = "Downloaded+Embedded"
                                    log_entry["action"] = "redownloaded_and_embedded"
                                else:
                                    stats["reembed_failed"] += 1
                                    result = "Downloaded (embed failed)"
                                    log_entry["embed_error"] = proc2.stderr[:200]
                    else:
                        stats["redownload_failed"] += 1
                        result = "Download failed"
                        log_entry["action"] = "redownload_failed"
                        log_entry["error"] = proc.stderr[:200]
                        # Add to failed downloads list
                        add_to_failed_downloads(doi)

            else:
                # PDF exists, check if embeddings exist
                has_embeddings = bool(find_paper_in_qdrant(qdrant, doi)) if qdrant else True

                if not has_embeddings:
                    action = "RE-EMBED"
                    pdf_path = find_pdf_by_doi(doi)
                    if pdf_path:
                        proc = subprocess.run(
                            ["python3", str(pipeline_script), "--single", str(pdf_path)],
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        if proc.returncode == 0:
                            stats["reembedded"] += 1
                            result = "Embedded"
                            log_entry["action"] = "reembedded"
                        else:
                            stats["reembed_failed"] += 1
                            result = "Embed failed"
                            log_entry["action"] = "reembed_failed"
                            log_entry["error"] = proc.stderr[:200]
                    else:
                        result = "PDF not found"
                        log_entry["action"] = "error"
                        log_entry["error"] = "pdf_not_found"
                else:
                    # Check if we can enrich with abstract
                    if metadata.abstract and driver:
                        try:
                            with driver.session() as session:
                                update_result = session.run("""
                                    MATCH (p:Paper {doi: $doi})
                                    WHERE p.abstract IS NULL OR p.abstract = ''
                                    SET p.abstract = $abstract
                                    RETURN count(p) as updated
                                """, doi=doi, abstract=metadata.abstract[:2000])
                                if update_result.single()["updated"] > 0:
                                    action = "ENRICH"
                                    stats["enriched"] += 1
                                    result = "Enriched"
                                    log_entry["action"] = "enriched"
                                else:
                                    action = "OK"
                                    stats["ok"] += 1
                                    result = "OK"
                                    log_entry["action"] = "ok"
                        except Exception:
                            action = "OK"
                            stats["ok"] += 1
                            result = "OK"
                            log_entry["action"] = "ok"
                    else:
                        action = "OK"
                        stats["ok"] += 1
                        result = "OK"
                        log_entry["action"] = "ok"

            print(f"{i+1:<5} {doi:<40} {oa:<3} {s2:<3} {cr:<3} {action:<20} {result}")

        except Exception as e:
            print(f"{i+1:<5} {doi:<40} {'?':<3} {'?':<3} {'?':<3} {'ERROR':<20} {str(e)[:20]}")
            stats["errors"].append({"doi": doi, "error": str(e)})
            log_entry["action"] = "error"
            log_entry["error"] = str(e)

        stats["log"].append(log_entry)

    # Print summary
    print("-" * 100)
    print()
    print("Summary:")
    print(f"  Papers checked:      {stats['checked']}")
    print(f"  OK (no action):      {stats['ok']}")
    print(f"  Enriched:            {stats['enriched']}")
    print(f"  Removed:             {stats['removed']}")
    print(f"  Re-downloaded:       {stats['redownloaded']}")
    print(f"  Re-download failed:  {stats['redownload_failed']}")
    print(f"  Re-embedded:         {stats['reembedded']}")
    print(f"  Re-embed failed:     {stats['reembed_failed']}")
    if stats.get("skipped_failed", 0) > 0:
        print(f"  Skipped (prev fail): {stats['skipped_failed']}")
    print(f"  Errors:              {len(stats['errors'])}")

    if stats['redownload_failed'] > 0:
        print(f"\n  Failed downloads logged to: {FAILED_DOWNLOADS_FILE}")

    # Save log
    if output_file:
        # If just a filename (no path), save to logs directory
        output_path = Path(output_file)
        if not output_path.is_absolute() and "/" not in output_file:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = LOGS_DIR / output_file

        try:
            with open(output_path, "w") as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "source": source,
                    "max_papers": max_papers,
                    "stats": {k: v for k, v in stats.items() if k != "log"},
                    "log": stats["log"]
                }, f, indent=2)
            print(f"\n[OK] Log saved to: {output_path}")
        except PermissionError:
            print(f"\n[WARNING] Could not write log to {output_path} (permission denied)")

    if driver:
        driver.close()

    return stats


def scan_processed(
    check_metadata: bool = False,
    check_pdf: bool = False,
    find_orphaned: bool = False,
    max_scan: int = 1000,
    output_file: Optional[str] = None
) -> Dict:
    """
    Scan the processed papers directory for issues.

    Args:
        check_metadata: Check each paper against metadata sources
        check_pdf: Check if PDFs exist for processed papers
        find_orphaned: Find papers in database but missing PDF
        max_scan: Maximum files to scan
        output_file: Save results to JSON file

    Returns:
        Dict with scan results
    """
    print("=" * 70)
    print("Scan Processed Papers Directory")
    print("=" * 70)
    print()

    if not PROCESSED_DIR.exists():
        print(f"[ERROR] Processed directory not found: {PROCESSED_DIR}")
        return {"error": "directory_not_found"}

    results = {
        "total_files": 0,
        "valid_json": 0,
        "with_doi": 0,
        "pdf_missing": [],
        "metadata_issues": [],
    }

    processed_files = list(PROCESSED_DIR.glob("*.json"))
    results["total_files"] = len(processed_files)
    print(f"Found {len(processed_files)} processed marker files")

    if max_scan < len(processed_files):
        processed_files = processed_files[:max_scan]
        print(f"Scanning first {max_scan} files...")

    fetcher = MultiSourceMetadataFetcher() if check_metadata else None

    for i, pf in enumerate(processed_files):
        try:
            with open(pf) as f:
                data = json.load(f)
                results["valid_json"] += 1

                doi = data.get("doi")
                if doi:
                    results["with_doi"] += 1

                    # Check PDF existence
                    if check_pdf or find_orphaned:
                        pdf_path = find_pdf_by_doi(doi)
                        if not pdf_path:
                            results["pdf_missing"].append({
                                "doi": doi,
                                "marker_file": str(pf)
                            })

                    # Check metadata sources
                    if check_metadata and fetcher:
                        metadata = fetcher.fetch_all(doi)
                        if not metadata.has_any_data:
                            results["metadata_issues"].append({
                                "doi": doi,
                                "reason": "no_data_in_any_source"
                            })

        except json.JSONDecodeError:
            pass
        except Exception as e:
            pass

        if (i + 1) % 100 == 0:
            print(f"  Scanned {i + 1}/{len(processed_files)}...")

    # Print summary
    print()
    print("Scan Results:")
    print(f"  Total files:       {results['total_files']}")
    print(f"  Valid JSON:        {results['valid_json']}")
    print(f"  With DOI:          {results['with_doi']}")

    if check_pdf or find_orphaned:
        print(f"  Missing PDF:       {len(results['pdf_missing'])}")
        if results["pdf_missing"][:5]:
            print("    Examples:")
            for item in results["pdf_missing"][:5]:
                print(f"      - {item['doi']}")

    if check_metadata:
        print(f"  Metadata issues:   {len(results['metadata_issues'])}")
        if results["metadata_issues"][:5]:
            print("    Examples:")
            for item in results["metadata_issues"][:5]:
                print(f"      - {item['doi']}: {item['reason']}")

    # Save results
    if output_file:
        with open(output_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "results": results
            }, f, indent=2)
        print(f"\n[OK] Results saved to: {output_file}")

    return results


def find_low_quality_papers(
    output_file: Optional[str] = None,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False,
    enrich: bool = True,
    max_check: int = 500
) -> List[str]:
    """
    Find truly low-quality papers using smart filtering.

    Process:
    1. Find papers with missing metadata in Neo4j
    2. Check OpenAlex for each paper:
       - If page count < 3: mark for removal (definitely low quality)
       - If has abstract/refs in OpenAlex: enrich Neo4j (not low quality, just missing data)
       - If no data in OpenAlex either: mark for removal (truly low quality)
    3. Only remove papers that are truly low quality

    Args:
        output_file: Write found DOIs to this file
        auto_remove: Automatically remove found papers from all knowledge bases
        limit: Maximum number of papers to auto-remove
        confirm: Required for auto-remove without limit
        enrich: Try to enrich papers from OpenAlex before deciding to remove
        max_check: Maximum number of papers to check from Neo4j

    Returns:
        List of DOIs marked for removal
    """
    driver = get_neo4j_driver()
    if not driver:
        print("[ERROR] Cannot connect to Neo4j")
        return []

    print("=" * 60)
    print("Smart Low-Quality Paper Detection")
    print("=" * 60)
    print("\nProcess:")
    print("  1. Find papers with missing metadata in Neo4j")
    print("  2. Check OpenAlex for actual quality signals")
    print("  3. Enrich papers that have data in OpenAlex")
    print("  4. Only remove truly low-quality papers")
    print("")

    candidates = []
    to_remove = []
    enriched_count = 0
    short_count = 0
    retracted_count = 0
    no_data_count = 0

    try:
        with driver.session() as session:
            # Find papers with missing abstract or no citations in Neo4j
            result = session.run("""
                MATCH (p:Paper)
                WHERE p.doi IS NOT NULL
                  AND ((p.abstract IS NULL OR p.abstract = '')
                       OR NOT EXISTS { MATCH (p)-[:CITES]->() })
                RETURN p.doi as doi, p.title as title, p.year as year,
                       p.abstract as abstract
                ORDER BY p.year DESC
                LIMIT $max_check
            """, max_check=max_check)

            candidates = list(result)

        if not candidates:
            print("\nNo papers with missing metadata found.")
            return []

        print(f"Found {len(candidates)} papers with incomplete metadata in Neo4j")
        print("Checking against OpenAlex...\n")

        print(f"{'DOI':<45} {'Status':<15} {'Pages':<6} {'Title':<30}")
        print("-" * 100)

        for i, record in enumerate(candidates):
            doi = record['doi']
            title = (record['title'] or 'N/A')[:28]
            neo4j_has_abstract = bool(record['abstract'])

            if (i + 1) % 20 == 0:
                print(f"... checked {i + 1}/{len(candidates)} ...")

            # Check OpenAlex
            oa_result = enrich_paper_from_openalex(driver if enrich else None, doi)

            pages = oa_result["page_count"]
            pages_str = str(pages) if pages else "?"

            # Determine status
            if oa_result["is_retracted"]:
                status = "RETRACTED"
                to_remove.append(doi)
                retracted_count += 1
            elif pages and pages < 3:
                status = "SHORT"
                to_remove.append(doi)
                short_count += 1
            elif oa_result["enriched"]:
                status = "ENRICHED"
                enriched_count += 1
            elif oa_result["has_abstract"] or oa_result["has_refs"]:
                status = "OK (OA data)"
                # Has data in OpenAlex, just wasn't enriched (maybe enrich=False)
            elif neo4j_has_abstract:
                status = "OK (Neo4j)"
                # Has abstract in Neo4j already
            else:
                status = "NO DATA"
                to_remove.append(doi)
                no_data_count += 1

            print(f"{doi:<45} {status:<15} {pages_str:<6} {title:<30}")

        print("-" * 100)
        print(f"\nSummary:")
        print(f"  Papers checked:    {len(candidates)}")
        print(f"  Enriched:          {enriched_count} (metadata added from OpenAlex)")
        print(f"  Short (<3 pages):  {short_count} (to remove)")
        print(f"  Retracted:         {retracted_count} (to remove)")
        print(f"  No data anywhere:  {no_data_count} (to remove)")
        print(f"  Total to remove:   {len(to_remove)}")

    finally:
        driver.close()

    # Write to output file if specified
    if output_file and to_remove:
        with open(output_file, "w") as f:
            f.write("# Truly low-quality papers (short, retracted, or no data anywhere)\n")
            for doi in to_remove:
                f.write(f"{doi}\n")
        print(f"\n[OK] Wrote {len(to_remove)} DOIs to: {output_file}")

    # Auto-remove if requested
    if auto_remove and to_remove:
        _auto_remove_papers(to_remove, limit, confirm)

    return to_remove


def find_short_papers(
    min_pages: int = 3,
    output_file: Optional[str] = None,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False
) -> List[str]:
    """
    Find papers with fewer than min_pages pages using OpenAlex.

    Args:
        min_pages: Minimum page count threshold
        output_file: Write found DOIs to this file
        auto_remove: Automatically remove found papers from all knowledge bases
        limit: Maximum number of papers to auto-remove
        confirm: Required for auto-remove without limit

    Returns:
        List of DOIs found
    """
    conn = get_sqlite_conn()
    if not conn:
        print("[ERROR] Cannot connect to SQLite")
        return []

    print("=" * 60)
    print(f"Finding papers with fewer than {min_pages} pages")
    print("=" * 60)

    # Get all DOIs from database
    results = conn.execute(
        "SELECT doi FROM papers WHERE doi IS NOT NULL AND status = 'downloaded'"
    ).fetchall()
    conn.close()

    if not results:
        print("\nNo downloaded papers found.")
        return []

    print(f"\nChecking {len(results)} papers against OpenAlex...")

    short_papers = []
    checked = 0

    for (doi,) in results:
        checked += 1
        if checked % 10 == 0:
            print(f"  Checked {checked}/{len(results)}...")

        metadata = fetch_openalex_metadata(doi)
        if not metadata:
            continue

        biblio = metadata.get("biblio", {})
        first_page = biblio.get("first_page")
        last_page = biblio.get("last_page")

        if first_page and last_page:
            try:
                page_count = int(last_page) - int(first_page) + 1
                if page_count < min_pages:
                    title = metadata.get("title", "N/A")
                    short_papers.append({
                        "doi": doi,
                        "title": title,
                        "pages": page_count
                    })
            except ValueError:
                pass  # Non-numeric pages

    if not short_papers:
        print(f"\nNo papers with fewer than {min_pages} pages found.")
        return []

    print(f"\nFound {len(short_papers)} papers with fewer than {min_pages} pages:\n")
    print(f"{'DOI':<50} {'Pages':<6} {'Title':<40}")
    print("-" * 100)

    for paper in short_papers:
        doi = paper['doi'][:48]
        title = (paper['title'] or 'N/A')[:38]
        pages = paper['pages']
        print(f"{doi:<50} {pages:<6} {title:<40}")

    print("-" * 100)
    print(f"\nTotal: {len(short_papers)} papers")

    found_dois = [p['doi'] for p in short_papers]

    # Write to output file if specified
    if output_file and found_dois:
        with open(output_file, "w") as f:
            f.write(f"# Short papers (fewer than {min_pages} pages)\n")
            for doi in found_dois:
                f.write(f"{doi}\n")
        print(f"\n[OK] Wrote {len(found_dois)} DOIs to: {output_file}")

    # Auto-remove if requested
    if auto_remove and found_dois:
        _auto_remove_papers(found_dois, limit, confirm)

    return found_dois


def _auto_remove_papers(dois: List[str], limit: Optional[int], confirm: bool):
    """
    Auto-remove papers from all knowledge bases.

    Args:
        dois: List of DOIs to remove
        limit: Maximum number to remove (None = all)
        confirm: Required if limit is None
    """
    # Safety check: require --confirm if no limit
    if limit is None and not confirm:
        print("\n[ERROR] Auto-remove without --limit requires --confirm flag")
        print("        This is a safety measure to prevent accidental mass deletion.")
        print(f"        Would remove {len(dois)} papers.")
        print("\n        Use: --auto-remove --confirm")
        print("        Or:  --auto-remove --limit N")
        return

    to_remove = dois[:limit] if limit else dois

    print(f"\n{'='*60}")
    print(f"AUTO-REMOVING {len(to_remove)} papers from all knowledge bases")
    print(f"{'='*60}")
    print("This will remove from: Qdrant, Neo4j, SQLite, filesystem")
    print("Papers will be added to blocklist to prevent re-download")
    print("")

    removed_count = 0
    for i, doi in enumerate(to_remove, 1):
        print(f"\n[{i}/{len(to_remove)}] Removing: {doi}")
        remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
        removed_count += 1

    print(f"\n{'='*60}")
    print(f"Auto-removal complete: {removed_count} papers removed")
    print(f"{'='*60}")


def bulk_remove(filepath: str, dry_run: bool = False, add_blocklist: bool = True):
    """Remove multiple papers from a file containing DOIs."""
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] File not found: {filepath}")
        return

    # Read DOIs from file
    with open(path) as f:
        dois = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not dois:
        print("[ERROR] No DOIs found in file")
        return

    print(f"{'='*60}")
    print(f"Bulk removing {len(dois)} papers")
    print(f"{'='*60}")

    for doi in dois:
        remove_paper_by_doi(doi, dry_run=dry_run, add_blocklist=add_blocklist)

    print(f"\n{'='*60}")
    print(f"Bulk removal complete: {len(dois)} papers processed")
    print(f"{'='*60}")


# ==============================================================================
# Main
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Munin Paper Cleanup - Remove papers from all databases"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a single paper by DOI")
    remove_parser.add_argument("--doi", required=True, help="DOI of paper to remove")
    remove_parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without actually removing")
    remove_parser.add_argument("--no-blocklist", action="store_true", help="Don't add DOI to blocklist")

    # find-low-quality command
    low_quality_parser = subparsers.add_parser("find-low-quality", help="Smart detection: checks OpenAlex, enriches good papers, removes truly bad ones")
    low_quality_parser.add_argument("--output", help="Write found DOIs to this file")
    low_quality_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove found papers from all databases")
    low_quality_parser.add_argument("--limit", type=int, help="Maximum number of papers to auto-remove")
    low_quality_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")
    low_quality_parser.add_argument("--no-enrich", action="store_true", help="Don't enrich papers from OpenAlex (just detect)")
    low_quality_parser.add_argument("--max-check", type=int, default=500, help="Maximum papers to check from Neo4j (default: 500)")

    # find-short command
    short_parser = subparsers.add_parser("find-short", help="Find papers with few pages")
    short_parser.add_argument("--min-pages", type=int, default=3, help="Minimum pages required (default: 3)")
    short_parser.add_argument("--output", help="Write found DOIs to this file")
    short_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove found papers from all databases")
    short_parser.add_argument("--limit", type=int, help="Maximum number of papers to auto-remove")
    short_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")

    # bulk-remove command
    bulk_parser = subparsers.add_parser("bulk-remove", help="Remove multiple papers from a file")
    bulk_parser.add_argument("--file", required=True, help="File containing DOIs (one per line)")
    bulk_parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without actually removing")
    bulk_parser.add_argument("--no-blocklist", action="store_true", help="Don't add DOIs to blocklist")

    # verify-doi command
    verify_parser = subparsers.add_parser("verify-doi", help="Verify a single DOI against all metadata sources")
    verify_parser.add_argument("doi", help="DOI to verify (e.g., 10.1016/0021-9991(77)90112-7)")

    # repair-and-clean command
    repair_parser = subparsers.add_parser("repair-and-clean", help="Multi-source metadata enrichment and cleanup")
    repair_parser.add_argument("--source", choices=["neo4j", "processed", "both"], default="both",
                               help="Which papers to scan (default: both)")
    repair_parser.add_argument("--max-check", type=int, default=500, help="Maximum papers to check (default: 500)")
    repair_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove flagged papers")
    repair_parser.add_argument("--limit", type=int, help="Maximum papers to auto-remove")
    repair_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")
    repair_parser.add_argument("--output", help="Save results to JSON file")
    repair_parser.add_argument("--dry-run", action="store_true", help="Don't modify databases")
    repair_parser.add_argument("--no-pdf-check", action="store_true", help="Don't check if PDFs exist")
    repair_parser.add_argument("--export-orphaned", metavar="FILE", help="Export orphaned DOIs (missing PDF) to file for re-crawling")

    # scan-processed command
    scan_parser = subparsers.add_parser("scan-processed", help="Scan processed papers directory for issues")
    scan_parser.add_argument("--check-metadata", action="store_true", help="Check each paper against metadata sources")
    scan_parser.add_argument("--check-pdf", action="store_true", help="Check if PDFs exist for processed papers")
    scan_parser.add_argument("--find-orphaned", action="store_true", help="Find papers in database but missing PDF")
    scan_parser.add_argument("--max-scan", type=int, default=1000, help="Maximum files to scan (default: 1000)")
    scan_parser.add_argument("--output", help="Save results to JSON file")

    # repair-auto command (autonomous mode)
    auto_parser = subparsers.add_parser("repair-auto", help="Autonomous repair - processes papers one at a time with immediate actions")
    auto_parser.add_argument("--doi", help="Process a single DOI instead of bulk")
    auto_parser.add_argument("--source", choices=["neo4j", "processed"], default="neo4j",
                             help="Where to get paper list (default: neo4j)")
    auto_parser.add_argument("--max-papers", type=int, default=100, help="Maximum papers to process (default: 100)")
    auto_parser.add_argument("--output", help="Save log to JSON file")
    auto_parser.add_argument("--scripts-dir", help="Directory containing paper_crawler.py and paper_pipeline.py")
    auto_parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed downloads")

    args = parser.parse_args()

    if args.command == "remove":
        remove_paper_by_doi(
            args.doi,
            dry_run=args.dry_run,
            add_blocklist=not args.no_blocklist
        )
    elif args.command == "find-low-quality":
        find_low_quality_papers(
            output_file=args.output,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm,
            enrich=not args.no_enrich,
            max_check=args.max_check
        )
    elif args.command == "find-short":
        find_short_papers(
            min_pages=args.min_pages,
            output_file=args.output,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm
        )
    elif args.command == "bulk-remove":
        bulk_remove(
            args.file,
            dry_run=args.dry_run,
            add_blocklist=not args.no_blocklist
        )
    elif args.command == "verify-doi":
        verify_doi(args.doi)
    elif args.command == "repair-and-clean":
        repair_and_clean(
            source=args.source,
            max_check=args.max_check,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm,
            output_file=args.output,
            dry_run=args.dry_run,
            check_pdf=not args.no_pdf_check,
            export_orphaned=args.export_orphaned
        )
    elif args.command == "scan-processed":
        scan_processed(
            check_metadata=args.check_metadata,
            check_pdf=args.check_pdf,
            find_orphaned=args.find_orphaned,
            max_scan=args.max_scan,
            output_file=args.output
        )
    elif args.command == "repair-auto":
        repair_auto(
            source=args.source,
            max_papers=args.max_papers,
            output_file=args.output,
            scripts_dir=args.scripts_dir,
            retry_failed=args.retry_failed,
            single_doi=args.doi
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
