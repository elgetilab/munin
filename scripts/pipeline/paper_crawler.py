#!/usr/bin/env python3
"""
==============================================================================
MUNIN PAPER CRAWLER
==============================================================================
Citation-based paper acquisition system. Starts from seed papers and crawls
citations to build a comprehensive paper corpus.

Sources:
    - arXiv: Open access preprints
    - Sci-Hub: Research papers (use responsibly)
    - Manual: Admin-uploaded PDFs

Pipeline:
    Seed DOIs → Extract citations → Queue downloads → Process PDFs → Qdrant/Neo4j

Usage:
    # Add seed papers
    ./paper_crawler.py add-seed 10.1234/example.paper
    ./paper_crawler.py add-seed arxiv:2312.12345

    # List all seeds
    ./paper_crawler.py list-seeds

    # Remove a seed (and optionally its citations)
    ./paper_crawler.py remove-seed 10.1234/example.paper
    ./paper_crawler.py remove-seed 10.1234/example.paper --cascade

    # Clear pending papers
    ./paper_crawler.py clear-pending              # All pending
    ./paper_crawler.py clear-pending --source citation  # Only citations

    # Reset entire queue (requires confirmation)
    ./paper_crawler.py reset --confirm

    # Start crawler
    ./paper_crawler.py crawl --delay 5

    # Check status
    ./paper_crawler.py status

    # Process manually added PDFs
    ./paper_crawler.py process-manual

Requirements:
    pip install requests beautifulsoup4 arxiv

==============================================================================
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import requests

# ==============================================================================
# Configuration
# ==============================================================================
DATA_DIR = Path(os.getenv("MUNIN_DATA_DIR", "/opt/munin/data"))
PAPERS_DIR = DATA_DIR / "papers"
PDF_DIR = PAPERS_DIR / "pdf"
MANUAL_DIR = PAPERS_DIR / "manual"  # For manually added PDFs
QUEUE_DB = PAPERS_DIR / "crawler_queue.db"

# Delay between downloads (seconds) - be respectful to servers
DEFAULT_DELAY = 5

# User agent for requests (polite crawling with contact email)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
USER_AGENT = f"MuninCrawler/1.0 (Research Cluster; mailto:{ADMIN_EMAIL})"

# Sci-Hub mirrors (use responsibly, check legal status in your jurisdiction)
# These domains change frequently - update as needed
SCIHUB_MIRRORS = [
    "https://www.wellesu.com",
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.ee",
    "https://sci-hub.wf",
]

# Quality filtering configuration
MIN_PAGE_COUNT = int(os.getenv("MIN_PAGE_COUNT", "3"))
REQUIRE_ABSTRACT_OR_REFS = os.getenv("REQUIRE_ABSTRACT_OR_REFS", "true").lower() == "true"

# Blocklist file for DOIs to never download
BLOCKLIST_FILE = PAPERS_DIR / "blocklist.txt"


# ==============================================================================
# Blocklist Management
# ==============================================================================
def load_blocklist() -> Set[str]:
    """Load DOI blocklist from file."""
    if not BLOCKLIST_FILE.exists():
        return set()
    with open(BLOCKLIST_FILE) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def is_blocked(doi: str) -> bool:
    """Check if DOI is in blocklist."""
    blocklist = load_blocklist()
    return doi in blocklist


# ==============================================================================
# OpenAlex API
# ==============================================================================
def fetch_openalex_quick(doi: str) -> Optional[dict]:
    """
    Fetch minimal metadata from OpenAlex for pre-download filtering.

    Returns dict with: biblio, abstract_inverted_index, referenced_works, is_retracted
    """
    api_key = os.getenv("OPENALEX_API_KEY", "")
    try:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        params = {"api_key": api_key} if api_key else {}
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


# ==============================================================================
# Data Classes
# ==============================================================================
@dataclass
class PaperInfo:
    """Information about a paper to download."""
    doi: Optional[str]
    arxiv_id: Optional[str]
    title: Optional[str]
    source: str  # 'seed', 'citation', 'manual'
    parent_doi: Optional[str]  # DOI of paper that cited this one
    added_at: str
    status: str  # 'pending', 'downloaded', 'failed', 'processed'
    attempts: int = 0
    error: Optional[str] = None


# ==============================================================================
# Database Management
# ==============================================================================
def init_db():
    """Initialize the crawler database."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(QUEUE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            doi TEXT,
            arxiv_id TEXT,
            title TEXT,
            source TEXT,
            parent_doi TEXT,
            added_at TEXT,
            status TEXT,
            attempts INTEGER DEFAULT 0,
            error TEXT,
            pdf_path TEXT,
            processed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON papers(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_doi ON papers(doi)")
    conn.commit()
    conn.close()


def get_db():
    """Get database connection."""
    return sqlite3.connect(QUEUE_DB)


def paper_exists(doi: str = None, arxiv_id: str = None) -> bool:
    """Check if paper already exists in database."""
    conn = get_db()
    if doi:
        result = conn.execute(
            "SELECT 1 FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
    elif arxiv_id:
        result = conn.execute(
            "SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
    else:
        result = None
    conn.close()
    return result is not None


def add_paper(paper: PaperInfo):
    """Add paper to the download queue."""
    paper_id = paper.doi or paper.arxiv_id or hashlib.sha256(
        (paper.title or str(time.time())).encode()
    ).hexdigest()[:16]

    conn = get_db()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO papers
            (id, doi, arxiv_id, title, source, parent_doi, added_at, status, attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id, paper.doi, paper.arxiv_id, paper.title,
            paper.source, paper.parent_doi, paper.added_at, paper.status, 0
        ))
        conn.commit()
    finally:
        conn.close()


def update_paper_status(paper_id: str, status: str, error: str = None, pdf_path: str = None):
    """Update paper status in database."""
    conn = get_db()
    if error:
        conn.execute("""
            UPDATE papers SET status = ?, error = ?, attempts = attempts + 1
            WHERE id = ?
        """, (status, error, paper_id))
    elif pdf_path:
        conn.execute("""
            UPDATE papers SET status = ?, pdf_path = ?, attempts = attempts + 1
            WHERE id = ?
        """, (status, pdf_path, paper_id))
    else:
        conn.execute("""
            UPDATE papers SET status = ?, attempts = attempts + 1
            WHERE id = ?
        """, (status, paper_id))
    conn.commit()
    conn.close()


def get_pending_papers(limit: int = 100) -> List[dict]:
    """Get papers pending download."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    results = conn.execute("""
        SELECT * FROM papers
        WHERE status = 'pending' AND attempts < 3
        ORDER BY added_at
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in results]


# ==============================================================================
# arXiv Downloader
# ==============================================================================
def download_arxiv(arxiv_id: str, output_path: Path) -> bool:
    """Download paper from arXiv."""
    # Clean arxiv ID (remove arxiv: prefix if present)
    arxiv_id = arxiv_id.replace("arxiv:", "").replace("arXiv:", "")

    # arXiv PDF URL
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    try:
        response = requests.get(
            pdf_url,
            headers={"User-Agent": USER_AGENT},
            timeout=60,
            stream=True
        )
        response.raise_for_status()

        # Check if we got a PDF
        content_type = response.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and not response.content[:4] == b"%PDF":
            print(f"    [ERROR] Not a PDF: {content_type}")
            return False

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"    [OK] Downloaded from arXiv: {arxiv_id}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"    [ERROR] arXiv download failed: {e}")
        return False


# ==============================================================================
# Sci-Hub Downloader
# ==============================================================================
def download_scihub(doi: str, output_path: Path) -> bool:
    """Download paper from Sci-Hub."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("    [ERROR] BeautifulSoup not installed: pip install beautifulsoup4")
        return False

    for mirror in SCIHUB_MIRRORS:
        try:
            # Request the Sci-Hub page
            url = f"{mirror}/{doi}"
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=30
            )

            if response.status_code != 200:
                print(f"    [WARNING] Sci-Hub mirror {mirror} returned status {response.status_code}")
                continue

            # Parse the page to find the PDF iframe
            soup = BeautifulSoup(response.text, "html.parser")

            # Find PDF URL in iframe or embed
            pdf_url = None
            iframe = soup.find("iframe", {"id": "pdf"})
            if iframe and iframe.get("src"):
                pdf_url = iframe["src"]
            else:
                embed = soup.find("embed", {"type": "application/pdf"})
                if embed and embed.get("src"):
                    pdf_url = embed["src"]

            if not pdf_url:
                # Try to find any PDF link
                for a in soup.find_all("a", href=True):
                    if ".pdf" in a["href"]:
                        pdf_url = a["href"]
                        break

            if not pdf_url:
                print(f"    [WARNING] Sci-Hub mirror {mirror} returned page but no PDF link found")
                continue

            # Handle relative URLs
            if pdf_url.startswith("//"):
                pdf_url = "https:" + pdf_url
            elif pdf_url.startswith("/"):
                pdf_url = mirror + pdf_url

            # Download the PDF
            pdf_response = requests.get(
                pdf_url,
                headers={"User-Agent": USER_AGENT},
                timeout=60,
                stream=True
            )
            pdf_response.raise_for_status()

            with open(output_path, "wb") as f:
                for chunk in pdf_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"    [OK] Downloaded from Sci-Hub: {doi}")
            return True

        except requests.exceptions.RequestException as e:
            print(f"    [WARNING] Sci-Hub mirror {mirror} failed: {e}")
            continue

    return False


# ==============================================================================
# Content Type Filtering
# ==============================================================================
# CrossRef types that are NOT research papers
NON_RESEARCH_TYPES = {
    "component",           # Figures, tables, supplementary material
    "reference-entry",     # Dictionary/encyclopedia entries
    "peer-review",         # Peer review reports
    "dataset",             # Data files
    "posted-content",      # Preprints (we get these from arXiv instead)
    "grant",               # Funding information
    "report-component",    # Parts of reports
}

# Titles that indicate non-research content (exact match, case-insensitive)
NON_RESEARCH_TITLES = {
    "news and views", "editorial", "erratum", "correction", "retraction",
    "graphical abstract", "notes and references", "cover picture",
    "table of contents", "advertisement", "book review", "corrigendum",
    "front matter", "back matter", "index", "contents", "author index",
    "subject index", "acknowledgements", "preface", "foreword",
    "notes for notes", "in this issue", "issue information", "masthead",
}

# Minimum title length for papers with no abstract AND no authors
# (catches generic titles like "Notes for Notes" while allowing legitimate papers
# that may have metadata extraction issues but have descriptive titles)
MIN_TITLE_LENGTH_NO_METADATA = 30


def is_research_paper(doi: str) -> tuple[bool, str]:
    """
    Check if a DOI is a research paper (not news, editorial, etc.).
    Returns (is_research, reason).

    Performs checks in order:
    1. CrossRef: content type, title blocklist, sparse metadata
    2. OpenAlex: page count, abstract/refs, retraction status
    """
    # Check blocklist first
    if is_blocked(doi):
        return False, "blocklisted"

    # --- CrossRef checks ---
    try:
        url = f"https://api.crossref.org/works/{doi}"
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json().get("message", {})

            # Check CrossRef type
            cr_type = data.get("type", "")
            if cr_type in NON_RESEARCH_TYPES:
                return False, f"type:{cr_type}"

            # Check title
            title = data.get("title", [""])[0] if data.get("title") else ""
            if title.lower().strip() in NON_RESEARCH_TITLES:
                return False, f"title:{title}"

            # Check for empty metadata + short title (likely non-research)
            abstract = data.get("abstract", "")
            authors = data.get("author", [])
            if not abstract and not authors and len(title) < MIN_TITLE_LENGTH_NO_METADATA:
                return False, f"sparse_metadata:no_abstract+no_authors+short_title({len(title)})"

    except Exception:
        pass  # CrossRef check failed, continue with OpenAlex

    # --- OpenAlex checks ---
    openalex = fetch_openalex_quick(doi)
    if openalex:
        # Check if retracted
        if openalex.get("is_retracted"):
            return False, "retracted"

        # Check page count
        biblio = openalex.get("biblio", {})
        first_page = biblio.get("first_page")
        last_page = biblio.get("last_page")
        if first_page and last_page:
            try:
                pages = int(last_page) - int(first_page) + 1
                if pages < MIN_PAGE_COUNT:
                    return False, f"too_few_pages:{pages}"
            except ValueError:
                pass  # Non-numeric pages, allow

        # Check for abstract or references
        if REQUIRE_ABSTRACT_OR_REFS:
            has_abstract = bool(openalex.get("abstract_inverted_index"))
            has_refs = len(openalex.get("referenced_works", [])) > 0
            if not has_abstract and not has_refs:
                return False, "no_abstract_or_refs"

    return True, "ok"


# ==============================================================================
# Citation Extraction
# ==============================================================================
def extract_citations_from_crossref(doi: str) -> List[str]:
    """Extract citation DOIs from CrossRef."""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30
        )

        if response.status_code != 200:
            return []

        data = response.json().get("message", {})
        references = data.get("reference", [])

        citation_dois = []
        for ref in references:
            ref_doi = ref.get("DOI")
            if ref_doi:
                citation_dois.append(ref_doi)

        return citation_dois

    except Exception as e:
        print(f"    [WARNING] CrossRef citation extraction failed: {e}")
        return []


def extract_citations_from_semantic_scholar(doi: str) -> List[str]:
    """Extract citation DOIs from Semantic Scholar."""
    try:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
        params = {"fields": "references.externalIds"}

        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=30
        )

        if response.status_code != 200:
            return []

        data = response.json()
        references = data.get("references", [])

        citation_dois = []
        for ref in references:
            external_ids = ref.get("externalIds", {})
            ref_doi = external_ids.get("DOI")
            if ref_doi:
                citation_dois.append(ref_doi)

        return citation_dois

    except Exception as e:
        print(f"    [WARNING] Semantic Scholar citation extraction failed: {e}")
        return []


def queue_citations(parent_doi: str, filter_content: bool = True):
    """Extract and queue citations from a paper.

    Args:
        parent_doi: DOI of the paper to extract citations from
        filter_content: If True, filter out non-research content (editorials, etc.)
    """
    print(f"  Extracting citations from {parent_doi}...")

    # Try CrossRef first, then Semantic Scholar
    citations = extract_citations_from_crossref(parent_doi)
    if not citations:
        citations = extract_citations_from_semantic_scholar(parent_doi)

    new_count = 0
    skipped_count = 0
    for doi in citations:
        if paper_exists(doi=doi):
            continue

        # Filter non-research content before queueing
        if filter_content:
            is_research, reason = is_research_paper(doi)
            if not is_research:
                skipped_count += 1
                continue  # Skip silently to avoid spam

        paper = PaperInfo(
            doi=doi,
            arxiv_id=None,
            title=None,
            source="citation",
            parent_doi=parent_doi,
            added_at=datetime.now().isoformat(),
            status="pending"
        )
        add_paper(paper)
        new_count += 1

    msg = f"    Found {len(citations)} citations, {new_count} new papers queued"
    if skipped_count > 0:
        msg += f", {skipped_count} non-research skipped"
    print(msg)


# ==============================================================================
# Paper Downloader
# ==============================================================================
def download_paper(paper: dict) -> bool:
    """Download a single paper from available sources."""
    doi = paper.get("doi")
    arxiv_id = paper.get("arxiv_id")
    paper_id = paper["id"]

    # Generate filename
    if arxiv_id:
        filename = f"arxiv_{arxiv_id.replace('/', '_')}.pdf"
    elif doi:
        filename = f"doi_{doi.replace('/', '_').replace(':', '_')}.pdf"
    else:
        filename = f"paper_{paper_id}.pdf"

    output_path = PDF_DIR / filename

    # Skip if already downloaded
    if output_path.exists():
        print(f"    [SKIP] Already exists: {filename}")
        update_paper_status(paper_id, "downloaded", pdf_path=str(output_path))
        return True

    print(f"  Downloading: {doi or arxiv_id}")

    # Try arXiv first (if it's an arXiv paper)
    if arxiv_id:
        if download_arxiv(arxiv_id, output_path):
            update_paper_status(paper_id, "downloaded", pdf_path=str(output_path))
            return True

    # Check if DOI points to arXiv
    if doi and ("arxiv" in doi.lower() or "48550" in doi):
        # Extract arXiv ID from DOI
        match = re.search(r"(\d{4}\.\d{4,5})", doi)
        if match:
            arxiv_from_doi = match.group(1)
            if download_arxiv(arxiv_from_doi, output_path):
                update_paper_status(paper_id, "downloaded", pdf_path=str(output_path))
                return True

    # Try Sci-Hub
    if doi:
        if download_scihub(doi, output_path):
            update_paper_status(paper_id, "downloaded", pdf_path=str(output_path))
            return True

    # All sources failed
    update_paper_status(paper_id, "failed", error="All download sources failed")
    return False


def download_single(doi: str, quiet: bool = False) -> bool:
    """
    Download a single paper by DOI (for repair/cleanup scripts).

    This function is designed to be called by external scripts like paper_cleanup.py
    to re-download orphaned papers (papers in DB but missing PDF).

    Args:
        doi: The DOI to download
        quiet: Minimal output

    Returns:
        True if download succeeded, False otherwise
    """
    if not quiet:
        print(f"[download-single] Downloading: {doi}")

    # Check if PDF already exists
    filename = f"doi_{doi.replace('/', '_').replace(':', '_')}.pdf"
    output_path = PDF_DIR / filename

    if output_path.exists():
        if not quiet:
            print(f"  [OK] Already exists: {output_path}")
        return True

    # Check if paper is in database
    conn = get_db()
    existing = conn.execute(
        "SELECT id, status FROM papers WHERE doi = ?", (doi,)
    ).fetchone()
    conn.close()

    if existing:
        paper_id, status = existing
        # Paper exists in DB, try to download
        paper = {"id": paper_id, "doi": doi, "arxiv_id": None}
        success = download_paper(paper)
        if success and not quiet:
            print(f"  [OK] Downloaded: {output_path}")
        elif not success and not quiet:
            print(f"  [FAILED] Could not download: {doi}")
        return success
    else:
        # Paper not in DB, add it first
        paper = PaperInfo(
            doi=doi,
            arxiv_id=None,
            title=None,
            source="repair",
            parent_doi=None,
            added_at=datetime.now().isoformat(),
            status="pending"
        )
        add_paper(paper)

        # Get the paper ID
        conn = get_db()
        paper_id = conn.execute(
            "SELECT id FROM papers WHERE doi = ?", (doi,)
        ).fetchone()[0]
        conn.close()

        paper_dict = {"id": paper_id, "doi": doi, "arxiv_id": None}
        success = download_paper(paper_dict)
        if success and not quiet:
            print(f"  [OK] Downloaded: {output_path}")
        elif not success and not quiet:
            print(f"  [FAILED] Could not download: {doi}")
        return success


# ==============================================================================
# Manual Paper Processing
# ==============================================================================
def process_manual_papers():
    """Process manually added PDFs from the manual directory."""
    if not MANUAL_DIR.exists():
        print(f"Manual directory not found: {MANUAL_DIR}")
        return

    pdf_files = list(MANUAL_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDFs in manual directory")

    for pdf_path in pdf_files:
        # Check if already in database
        paper_id = hashlib.sha256(pdf_path.name.encode()).hexdigest()[:16]

        conn = get_db()
        exists = conn.execute(
            "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        conn.close()

        if exists:
            print(f"  [SKIP] Already tracked: {pdf_path.name}")
            continue

        # Move to main PDF directory
        dest_path = PDF_DIR / pdf_path.name
        if dest_path.exists():
            dest_path = PDF_DIR / f"manual_{pdf_path.name}"

        pdf_path.rename(dest_path)

        # Add to database
        paper = PaperInfo(
            doi=None,
            arxiv_id=None,
            title=pdf_path.stem,
            source="manual",
            parent_doi=None,
            added_at=datetime.now().isoformat(),
            status="downloaded"
        )

        conn = get_db()
        conn.execute("""
            INSERT INTO papers
            (id, doi, arxiv_id, title, source, parent_doi, added_at, status, pdf_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id, None, None, pdf_path.stem, "manual",
            None, paper.added_at, "downloaded", str(dest_path)
        ))
        conn.commit()
        conn.close()

        print(f"  [OK] Added manual paper: {pdf_path.name}")


# ==============================================================================
# Crawl Command
# ==============================================================================
def crawl(delay: int = DEFAULT_DELAY, max_papers: int = None,
          extract_citations: bool = True, filter_content: bool = True):
    """Main crawl loop."""
    print("=" * 60)
    print("MUNIN PAPER CRAWLER")
    print("=" * 60)
    print(f"Delay between downloads: {delay}s")
    print(f"Max papers: {max_papers or 'unlimited'}")
    print(f"Extract citations: {extract_citations}")
    print(f"Filter non-research: {filter_content}")
    print("=" * 60)

    downloaded = 0
    failed = 0

    while True:
        # Get pending papers
        pending = get_pending_papers(limit=10)

        if not pending:
            print("\n[DONE] No more papers to download")
            break

        if max_papers and downloaded >= max_papers:
            print(f"\n[DONE] Reached max papers limit: {max_papers}")
            break

        for paper in pending:
            if max_papers and downloaded >= max_papers:
                break

            print(f"\n[{downloaded + 1}] Processing: {paper['doi'] or paper['arxiv_id']}")

            success = download_paper(paper)

            if success:
                downloaded += 1

                # Extract citations if enabled and it's a DOI
                if extract_citations and paper.get("doi"):
                    queue_citations(paper["doi"], filter_content=filter_content)
            else:
                failed += 1

            # Respect rate limits
            print(f"    Waiting {delay}s before next download...")
            time.sleep(delay)

    print("\n" + "=" * 60)
    print(f"Crawl complete: {downloaded} downloaded, {failed} failed")
    print("=" * 60)


# ==============================================================================
# Status Command
# ==============================================================================
def show_status():
    """Show crawler status."""
    conn = get_db()

    # Count by status
    stats = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM papers
        GROUP BY status
    """).fetchall()

    # Count by source
    sources = conn.execute("""
        SELECT source, COUNT(*) as count
        FROM papers
        GROUP BY source
    """).fetchall()

    # Recent additions
    recent = conn.execute("""
        SELECT doi, arxiv_id, title, status, added_at
        FROM papers
        ORDER BY added_at DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    print("=" * 60)
    print("CRAWLER STATUS")
    print("=" * 60)

    print("\nPapers by Status:")
    for status, count in stats:
        print(f"  {status}: {count}")

    print("\nPapers by Source:")
    for source, count in sources:
        print(f"  {source}: {count}")

    print("\nRecent Additions:")
    for doi, arxiv_id, title, status, added_at in recent:
        identifier = doi or arxiv_id or title[:30]
        print(f"  [{status}] {identifier} ({added_at[:10]})")

    print("=" * 60)


# ==============================================================================
# Add Seed Command
# ==============================================================================
def add_seed(identifier: str):
    """Add a seed paper to start crawling from."""
    # Determine if it's arXiv or DOI
    arxiv_id = None
    doi = None

    if identifier.startswith("arxiv:") or identifier.startswith("arXiv:"):
        arxiv_id = identifier.split(":", 1)[1]
    elif re.match(r"^\d{4}\.\d{4,5}", identifier):
        arxiv_id = identifier
    else:
        doi = identifier

    # Check if exists
    if paper_exists(doi=doi, arxiv_id=arxiv_id):
        print(f"Paper already in database: {identifier}")
        return

    paper = PaperInfo(
        doi=doi,
        arxiv_id=arxiv_id,
        title=None,
        source="seed",
        parent_doi=None,
        added_at=datetime.now().isoformat(),
        status="pending"
    )

    add_paper(paper)
    print(f"Added seed paper: {identifier}")


# ==============================================================================
# Remove Seed Command
# ==============================================================================
def remove_seed(identifier: str, cascade: bool = False):
    """Remove a seed paper and optionally its citations from the queue."""
    conn = get_db()

    # Find the paper
    if identifier.startswith("arxiv:") or identifier.startswith("arXiv:"):
        arxiv_id = identifier.split(":", 1)[1]
        paper = conn.execute(
            "SELECT id, doi, arxiv_id FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
    elif re.match(r"^\d{4}\.\d{4,5}", identifier):
        paper = conn.execute(
            "SELECT id, doi, arxiv_id FROM papers WHERE arxiv_id = ?", (identifier,)
        ).fetchone()
    else:
        paper = conn.execute(
            "SELECT id, doi, arxiv_id FROM papers WHERE doi = ?", (identifier,)
        ).fetchone()

    if not paper:
        print(f"Paper not found: {identifier}")
        conn.close()
        return

    paper_id, doi, arxiv_id = paper

    # Count citations that would be removed
    if cascade and doi:
        citation_count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE parent_doi = ?", (doi,)
        ).fetchone()[0]
    else:
        citation_count = 0

    # Remove the paper
    conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))

    # Remove cascaded citations if requested
    if cascade and doi:
        conn.execute("DELETE FROM papers WHERE parent_doi = ?", (doi,))

    conn.commit()
    conn.close()

    print(f"Removed: {identifier}")
    if cascade and citation_count > 0:
        print(f"  Also removed {citation_count} citations from this paper")


# ==============================================================================
# List Seeds Command
# ==============================================================================
def list_seeds():
    """List all seed papers in the queue."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    seeds = conn.execute("""
        SELECT doi, arxiv_id, title, status, added_at, pdf_path
        FROM papers
        WHERE source = 'seed'
        ORDER BY added_at DESC
    """).fetchall()

    conn.close()

    if not seeds:
        print("No seed papers in the queue.")
        return

    print("=" * 70)
    print("SEED PAPERS")
    print("=" * 70)
    print(f"{'Identifier':<40} {'Status':<12} {'Added':<12}")
    print("-" * 70)

    for seed in seeds:
        identifier = seed['doi'] or seed['arxiv_id'] or seed['title'][:35]
        if len(identifier) > 38:
            identifier = identifier[:35] + "..."
        status = seed['status']
        added = seed['added_at'][:10] if seed['added_at'] else 'N/A'
        print(f"{identifier:<40} {status:<12} {added:<12}")

    print("-" * 70)
    print(f"Total: {len(seeds)} seeds")
    print("=" * 70)


# ==============================================================================
# Clear Pending Command
# ==============================================================================
def clear_pending(source_filter: str = None):
    """Remove all pending papers from the queue."""
    conn = get_db()

    if source_filter:
        count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status = 'pending' AND source = ?",
            (source_filter,)
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM papers WHERE status = 'pending' AND source = ?",
            (source_filter,)
        )
    else:
        count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status = 'pending'"
        ).fetchone()[0]
        conn.execute("DELETE FROM papers WHERE status = 'pending'")

    conn.commit()
    conn.close()

    if source_filter:
        print(f"Removed {count} pending papers (source: {source_filter})")
    else:
        print(f"Removed {count} pending papers")


# ==============================================================================
# Reset Command
# ==============================================================================
def reset_queue(confirm: bool = False):
    """Clear the entire paper queue."""
    if not confirm:
        print("This will delete ALL papers from the queue!")
        print("Run with --confirm to proceed.")
        return

    conn = get_db()

    # Get counts before deletion
    stats = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM papers
        GROUP BY status
    """).fetchall()

    total = sum(count for _, count in stats)

    # Clear the table
    conn.execute("DELETE FROM papers")
    conn.commit()
    conn.close()

    print("=" * 50)
    print("QUEUE RESET COMPLETE")
    print("=" * 50)
    print(f"Removed {total} papers:")
    for status, count in stats:
        print(f"  - {status}: {count}")
    print("=" * 50)
    print("\nNote: Downloaded PDF files were NOT deleted.")
    print(f"To also remove PDFs: rm -rf {PDF_DIR}/*")


# ==============================================================================
# Main
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Munin Paper Crawler - Citation-based paper acquisition"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # add-seed command
    seed_parser = subparsers.add_parser("add-seed", help="Add a seed paper")
    seed_parser.add_argument("identifier", help="DOI or arXiv ID (e.g., 10.1234/paper or arxiv:2312.12345)")

    # remove-seed command
    remove_parser = subparsers.add_parser("remove-seed", help="Remove a paper from the queue")
    remove_parser.add_argument("identifier", help="DOI or arXiv ID to remove")
    remove_parser.add_argument("--cascade", action="store_true", help="Also remove citations discovered from this paper")

    # list-seeds command
    subparsers.add_parser("list-seeds", help="List all seed papers")

    # clear-pending command
    clear_parser = subparsers.add_parser("clear-pending", help="Remove all pending papers")
    clear_parser.add_argument("--source", choices=["seed", "citation", "manual"], help="Only clear papers from this source")

    # reset command
    reset_parser = subparsers.add_parser("reset", help="Clear the entire paper queue")
    reset_parser.add_argument("--confirm", action="store_true", help="Confirm queue reset")

    # crawl command
    crawl_parser = subparsers.add_parser("crawl", help="Start crawling")
    crawl_parser.add_argument("--delay", type=int, default=DEFAULT_DELAY, help="Seconds between downloads")
    crawl_parser.add_argument("--max", type=int, default=None, help="Maximum papers to download")
    crawl_parser.add_argument("--no-citations", action="store_true", help="Don't extract citations")
    crawl_parser.add_argument("--no-filter", action="store_true", help="Don't filter out non-research content (editorials, news, etc.)")

    # status command
    subparsers.add_parser("status", help="Show crawler status")

    # process-manual command
    subparsers.add_parser("process-manual", help="Process manually added PDFs")

    # download-single command (for repair/cleanup scripts)
    single_parser = subparsers.add_parser("download-single", help="Download a single paper by DOI (for repair scripts)")
    single_parser.add_argument("doi", help="DOI to download")
    single_parser.add_argument("--quiet", action="store_true", help="Minimal output")

    args = parser.parse_args()

    # Initialize database
    init_db()

    if args.command == "add-seed":
        add_seed(args.identifier)
    elif args.command == "remove-seed":
        remove_seed(args.identifier, cascade=args.cascade)
    elif args.command == "list-seeds":
        list_seeds()
    elif args.command == "clear-pending":
        clear_pending(source_filter=args.source)
    elif args.command == "reset":
        reset_queue(confirm=args.confirm)
    elif args.command == "crawl":
        crawl(
            delay=args.delay,
            max_papers=args.max,
            extract_citations=not args.no_citations,
            filter_content=not args.no_filter
        )
    elif args.command == "status":
        show_status()
    elif args.command == "process-manual":
        process_manual_papers()
    elif args.command == "download-single":
        success = download_single(args.doi, quiet=args.quiet)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
