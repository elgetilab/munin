# Author metadata audit - `papers_bge`

Generated 2026-08-03T11:06:44.728138+00:00. Read-only.

| State | Records | Share |
|---|---:|---:|
| Clean | 54951 | 80.3% |
| Damaged | 7647 | 11.2% |
| No authors | 5828 | 8.5% |
| **Total** | **68426** | |

In repair scope (damaged or empty, DOI present): **13438**. Out of scope for lack of a DOI: 37.

## Damage by kind

| Kind | Author strings |
|---|---:|
| `needs_repair` | 7647 |
| `artefact_char` | 3240 |
| `digit` | 2650 |
| `affiliation` | 330 |
| `conjunction` | 294 |
| `too_short` | 270 |
| `exploded_caps` | 253 |
| `too_long` | 136 |
| `email` | 31 |

70 affected records also carry `_crossref_title_rejected`, i.e. the ingest guard already flagged their metadata as belonging to a different paper. These are report-only: the backfill must not rewrite authors on a record whose identity is in doubt.

## Crossref yield (sampled)

- sampled: 40
- Crossref returned authors: 40
- would adopt Crossref: 23
- would keep stored names, sanitized (already fuller): 17
- failed / no authors: 0

### Examples

- `10.1021/bi00383a005`
  - stored: `['G Ranadive', 'Ani1 Lala', 'C 5 -C G D O U B L E B O N D O F C H O L E S T E R O L R O L E I N M E M B R A N E S V']`
  - crossref: `['G. N. Ranadive', 'Anil K. Lala']`
- `10.1126/science.3465038`
  - stored: `[]`
  - crossref: `['John J. Letterio', 'Shaun R. Coughlin', 'Lewis T. Williams']`
- `10.1016/0042-6822(77)90076-9`
  - stored: `['Garry Lund', "Barry Ziola,'", 'Aim0 Salmi', 'Douglas Scraba2']`
  - crossref: `['Garry A. Lund', 'Barry R. Ziola', 'Aimo Salmi', 'Douglas G. Scraba']`
- `10.1016/0968-0004(87)90146-0`
  - stored: `[]`
  - crossref: `['Alan R. Fersht']`
- `10.1016/S0005-2728(05)80144-6`
  - stored: `['Herv6 Bottin', 'Pierre S6tif']`
  - crossref: `['Hervé Bottin', 'Pierre Sétif']`
- `10.1126/science.278.5346.2123`
  - stored: `[]`
  - crossref: `['Roland Beckmann', 'Doryen Bubeck', 'Robert Grassucci', 'Pawel Penczek', 'Adriana Verschoor', 'Günter Blobel']`
- `10.1126/science.7863329`
  - stored: `[]`
  - crossref: `['Susan B. Parker', 'Gregor Eichele', 'Pumin Zhang', 'Alan Rawls', 'Arthur T. Sands', 'Allan Bradley']`
- `10.1016/0006-2952(78)90100-4`
  - stored: `['John Bilezikian', 'Alan Dornfeld', 'I~nald Gammon']`
  - crossref: `['John P. Bilezikian', 'Alan M. Dornfeld', 'Donald E. Gammon']`
