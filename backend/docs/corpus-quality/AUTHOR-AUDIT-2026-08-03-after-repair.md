# Author metadata audit - `papers_bge`

Generated 2026-08-03T17:39:36.817841+00:00. Read-only.

| State | Records | Share |
|---|---:|---:|
| Clean | 67574 | 98.7% |
| Damaged | 115 | 0.2% |
| No authors | 747 | 1.1% |
| **Total** | **68436** | |

In repair scope (damaged or empty, DOI present): **825**. Out of scope for lack of a DOI: 37.

## Damage by kind

| Kind | Author strings |
|---|---:|
| `needs_repair` | 115 |
| `artefact_char` | 39 |
| `digit` | 32 |
| `exploded_caps` | 15 |
| `affiliation` | 12 |
| `too_short` | 8 |
| `conjunction` | 1 |
| `email` | 1 |

70 affected records also carry `_crossref_title_rejected`, i.e. the ingest guard already flagged their metadata as belonging to a different paper. These are report-only: the backfill must not rewrite authors on a record whose identity is in doubt.
