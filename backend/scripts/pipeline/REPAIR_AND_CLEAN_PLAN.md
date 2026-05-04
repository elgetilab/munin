# Multi-Source Paper Repair and Clean Pipeline

## Problem Statement

The current `paper_cleanup.py` only uses OpenAlex for verification, causing legitimate papers to be incorrectly flagged for removal. Example: `10.1016/0021-9991(77)90112-7` (a highly-cited 1977 paper) has no data in OpenAlex but exists in Semantic Scholar and CrossRef.

**User Requirements:**
1. Multi-source metadata enrichment (OpenAlex, Semantic Scholar, CrossRef)
2. "Repair and Clean" approach - enrich metadata before deciding to remove
3. Only remove papers if ALL metadata sources fail
4. Scan processed papers directory too (7,171 JSON files)
5. Better verification pipeline before deletion
6. Ensure papers are available via MCP download tool after processing

## Solution: Multi-Source Fetcher with Repair-First Logic

### Decision Flow

```
Paper DOI
    │
    ├──▶ Fetch OpenAlex → abstract, page_count, refs, is_retracted
    ├──▶ Fetch Semantic Scholar → abstract, citations, refs, year
    ├──▶ Fetch CrossRef → abstract, title, authors, type
    │
    └──▶ Decision:
            ├── ANY source has data? → REPAIR (enrich Neo4j/Qdrant)
            ├── ALL sources fail? → Flag for REMOVAL
            └── Short/Retracted? → Flag for REMOVAL
```

## Implementation

### 1. New Classes in `paper_cleanup.py`

**MetadataResult** - Single source result:
```python
@dataclass
class MetadataResult:
    source: str  # 'openalex', 'semantic_scholar', 'crossref'
    found: bool
    title: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    reference_count: Optional[int] = None
    citation_count: Optional[int] = None
    is_retracted: bool = False
    error: Optional[str] = None
```

**AggregatedMetadata** - Merged results from all sources:
```python
@dataclass
class AggregatedMetadata:
    doi: str
    sources_checked: List[str]
    sources_with_data: List[str]
    # Best available data (prioritized across sources)
    title, abstract, year, page_count, reference_count, citation_count
    is_retracted: bool
    is_short: bool  # page_count < 3

    @property
    def has_any_data(self) -> bool

    @property
    def should_remove(self) -> bool  # retracted OR short OR no data anywhere
```

### 2. Multi-Source Fetcher

**API Endpoints:**
- OpenAlex: `https://api.openalex.org/works/https://doi.org/{doi}`
- Semantic Scholar: `https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title,abstract,year,citationCount,referenceCount,authors`
- CrossRef: `https://api.crossref.org/works/{doi}`

**Rate Limits:**
| Source | Limit | Delay |
|--------|-------|-------|
| OpenAlex | 100k/day (with key) | 0.1s |
| Semantic Scholar | 5k/5min (no key) | 1.0s |
| CrossRef | 50/s (polite pool) | 0.02s |

**Bottleneck:** Semantic Scholar at 1 req/s ≈ 17 min per 1000 papers

### 3. New CLI Commands

```bash
# Main repair command - enriches from all sources, only removes if all fail
./paper_cleanup.py repair-and-clean --max-check 500 --auto-remove --limit 50

# Verify single DOI against all sources (debugging)
./paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7

# Scan processed directory for orphaned papers
./paper_cleanup.py scan-processed --check-metadata
```

### 4. repair-and-clean Command Options

| Option | Description |
|--------|-------------|
| `--source neo4j\|processed\|both` | Which papers to scan (default: both) |
| `--max-check N` | Maximum papers to check (default: 500) |
| `--auto-remove` | Automatically remove flagged papers |
| `--limit N` | Max papers to auto-remove |
| `--confirm` | Required for auto-remove without limit |
| `--output FILE` | Save results to JSON |
| `--dry-run` | Don't modify databases |

### 5. Output Format

```
Repair and Clean - Multi-Source Metadata Enrichment
============================================================
DOI                                           OpenAlex   S2         CrossRef   PDF    Status
-----------------------------------------------------------------------------------------------
10.1016/0021-9991(77)90112-7                  N          Y          Y          OK     ENRICHED
10.1111/j.1749-6632.1978.tb22009.x            Y          Y          Y          OK     SHORT
10.1234/totally.fake.doi                      N          N          N          MISS   NO DATA
...

Summary:
  Papers checked:        500
  Already OK:            320
  Enriched:              150  (metadata added)
  Removal candidates:     20  (no data from any source)
  Retracted:               5
  Short (<3 pages):        5
```

## Files to Modify

| File | Changes |
|------|---------|
| `scripts/knowledge/paper_cleanup.py` | Add `MultiSourceMetadataFetcher`, `MetadataResult`, `AggregatedMetadata`, `repair_and_clean()`, `verify_doi()`, `scan_processed()` |
| `phase2-munin/retrieval/mcp/tools/papers.py` | Fix `get_pdf_path()` to use correct "doi_" prefix |

## New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SEMANTIC_SCHOLAR_API_KEY` | (empty) | Optional API key for higher rate limits |

(Existing: `OPENALEX_API_KEY`, `ADMIN_EMAIL`)

## Verification

1. **Test verify-doi with problem paper:**
   ```bash
   ./paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7
   # Should show: OpenAlex=N, S2=Y, CrossRef=Y, Status=Should NOT remove
   ```

2. **Test repair-and-clean dry run:**
   ```bash
   ./paper_cleanup.py repair-and-clean --max-check 50 --dry-run
   # Should show enrichment from multiple sources
   ```

3. **Test with known bad paper:**
   ```bash
   ./paper_cleanup.py verify-doi 10.1111/j.1749-6632.1978.tb22009.x
   # Should show: has data but SHORT (1 page), Status=Should remove
   ```

4. **Test MCP download after fix:**
   ```bash
   curl "http://localhost:8000/paper/10.1038%2Fnature12373/pdf"
   # Should return PDF or proper redirect
   ```

## Key Implementation Details

### Semantic Scholar API Call
```python
def fetch_semantic_scholar(self, doi: str) -> MetadataResult:
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": "title,abstract,year,citationCount,referenceCount,authors"}
    headers = {"x-api-key": self.api_key} if self.api_key else {}
    response = requests.get(url, params=params, headers=headers, timeout=15)
    # Parse response...
```

### Metadata Merging Priority
1. **Abstract:** Prefer longest non-empty abstract from any source
2. **Page count:** OpenAlex only (others don't provide)
3. **Citation count:** Prefer Semantic Scholar (most accurate)
4. **Title/Authors:** Prefer CrossRef (authoritative)
5. **Retraction:** Any source reporting retracted = retracted

### Update Processed Markers
When enriching, update `/opt/munin/data/papers/processed/*.json`:
```python
existing['enrichment_info'] = {
    'enriched_at': datetime.now().isoformat(),
    'sources_used': ['semantic_scholar', 'crossref'],
    'abstract_source': 'semantic_scholar',
    'fields_added': ['abstract']
}
```

---

## Fix: MCP Paper Download Tool

### Problem

The MCP `get_pdf_path()` function has a bug - it looks for PDFs with the wrong filename pattern:

**Current (broken):**
```python
# In phase2-munin/retrieval/mcp/tools/papers.py line 73
safe_doi = doi.replace("/", "_").replace(":", "_")
pdf_path = os.path.join(PAPERS_PDF_DIR, f"{safe_doi}.pdf")
# Looks for: 10.1234_example.pdf
```

**Paper crawler saves as:**
```python
# In scripts/knowledge/paper_crawler.py line 613
filename = f"doi_{doi.replace('/', '_').replace(':', '_')}.pdf"
# Saves as: doi_10.1234_example.pdf
```

### Fix

Update `phase2-munin/retrieval/mcp/tools/papers.py`:

```python
def get_pdf_path(doi: str) -> str | None:
    if not doi:
        return None

    # DOI sanitization - match paper_crawler.py logic
    safe_doi = doi.replace("/", "_").replace(":", "_")

    # Primary format: doi_10.1234_example.pdf (from paper_crawler.py)
    pdf_path = os.path.join(PAPERS_PDF_DIR, f"doi_{safe_doi}.pdf")
    if os.path.exists(pdf_path):
        return pdf_path

    # Legacy format: 10.1234_example.pdf (without prefix)
    legacy_path = os.path.join(PAPERS_PDF_DIR, f"{safe_doi}.pdf")
    if os.path.exists(legacy_path):
        return legacy_path

    # Case-insensitive fallback
    if os.path.isdir(PAPERS_PDF_DIR):
        target_lower = f"doi_{safe_doi}.pdf".lower()
        for f in os.listdir(PAPERS_PDF_DIR):
            if f.lower() == target_lower:
                return os.path.join(PAPERS_PDF_DIR, f)

    return None
```
