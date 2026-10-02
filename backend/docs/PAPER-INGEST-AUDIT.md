# Paper-ingest audit (2026-05-12)

Triggered by a chat where the
assistant mixed up the group's contributor
and a different scientist with the same surname while answering a question
scoped to the `#elgeti` research-group tag.

Two layers contributed to the failure: a model-side regression that
ignored scoped tool results in favour of pretraining priors (fixed by
the Qwen 3.6 upgrade — replay reproduces "the contributor only" on every
rep), and a corpus-side ingest issue where multiple papers' metadata
have been spliced together on the same Qdrant point. This document
covers the second layer.

## Corpus snapshot

```
total papers           67 171
contributor uploads     3 922   (5.8%)
crawler downloads      63 249   (94.2%)
HTML tags in title      2 168   (93 uploaded, 2 075 crawled)
null DOI                  184   uploaded only
```

PDF first-page vs stored title sample (n=300 per source, deterministic
stride, naive normalised word-stem match):

```
uploads:  good=222  BAD=64  skipped=14   bad_rate = 22.4% (of decidable)
crawler:  good=250  BAD=37  skipped=13   bad_rate = 12.9% (of decidable)
```

Spot-reading the BAD samples, roughly 30 to 40 percent are false
positives caused by:

- Greek letters mangled in PDF text extraction (`β` → `p`, `α` → `a`),
- MathML/JATS markup in titles vs plain text in PDFs,
- First page being just a journal masthead / TOC header (the real
  title appears on page 2).

After discounting those, realistic real-mismatch rates are roughly
**~15 percent on uploads, ~6 to 8 percent on crawler downloads** — so
the user's hypothesis that uploads are worse holds, but the crawler
path is **not** clean.

## The smoking-gun record

The chat surfaced one specific bad point:

```
qdrant.papers / doi=10.2307/411791  (contributor: Contributor D, 2026-04-22)
  title    : "Language and information... by Bar-Hillel" (JSTOR review, 1965)
  journal  : Language
  year     : 1965
  authors  : Wallace, Laskowski, Thornton
  abstract : "The LIGPLOT program automatically generates..."
  pdf_path : /papers/doi_10.2307_411791.pdf
```

The PDF on disk is **Wallace/Laskowski/Thornton, "LIGPLOT" (Protein
Engineering 1995)**, confirmed via `pdftotext -l 1`. Three different
papers are fused into one record:

- `doi / title / journal / year` came from a Crossref enrichment of
  the JSTOR Bar-Hillel DOI.
- `authors / abstract` came from GROBID parsing the actual PDF.
- `pdf_path` is named after the wrong DOI because the final-move step
  uses the post-enrichment DOI to name the file.

## Root causes (with code references)

### A. Upload path has no filename-DOI authority

`backend/retrieval/main.py:1832` `_api_admin_ingest_inner` stores
uploads at `inbox/<uuid>.pdf`. `paper_pipeline._extract_doi_from_filename`
only matches filenames starting with `doi_`, so uploads always return
`None` and the pipeline falls through to GROBID for the DOI.

The pipeline itself acknowledges GROBID DOI extraction can be wrong
(`paper_pipeline.py:564`):

```python
# Use filename DOI as authoritative source if available
# GROBID can mistakenly extract DOIs from citations instead of the paper itself
if filename_doi:
    grobid_data["doi"] = filename_doi
```

That guard only fires for the crawler path. Uploads inherit any
GROBID DOI mistake silently. This is the most likely path the
LIGPLOT/JSTOR splice took.

### B. `_merge_metadata` overwrites without sanity check

`paper_pipeline.py:901`:

```python
def _merge_metadata(self, grobid: Dict, crossref: Dict) -> Dict:
    if crossref.get("title"):
        title = crossref["title"]
        if isinstance(title, list):
            title = title[0]
        grobid["title"] = title           # overwrite
    if crossref.get("container-title"):
        grobid["journal"] = crossref["container-title"][0]
    if crossref.get("published-print", {}).get("date-parts"):
        grobid["year"] = ...               # overwrite
    return grobid
```

Title / journal / year are clobbered with the Crossref record for
whatever DOI was supplied. Authors and abstract are **not**
overwritten, so we get the GROBID-parsed values for those fields
even when the DOI is wrong. There is no check that the GROBID title
and the Crossref title describe the same paper.

### C. Crossref HTML/JATS in titles is not stripped

2 168 records have `<b>`, `<i>`, `<sup>`, `<sub>`, or `<mml:math …>`
in the title field. Crawler dominates this set (95.7 percent) because
every Crossref-enriched paper is exposed. Polluted titles degrade
SPECTER embeddings (the 0.803 score for a Bar-Hillel essay against
"Elgeti lab research" plausibly reflects this) and look broken in the
UI.

### D. Sci-Hub returns wrong PDFs (crawler side)

The crawler downloads by DOI from arXiv / Sci-Hub. Sci-Hub
occasionally returns the wrong file for a given DOI. The filename-DOI
authority code path then trusts the filename, so the mismatch is
silent. This is the dominant mechanism for the crawler-side
~6 to 8 percent real mismatch rate.

### E. `_extract_doi_from_filename` brittleness

`paper_pipeline.py:326` walks digits after `10.` until the first
underscore. Works for most DOIs but is brittle for registrant codes
that contain non-digits or unusual suffix shapes. Not the headline
issue, but worth a unit test.

## Policy decisions (recorded for future maintainers)

Set 2026-05-12 in conversation with the maintainer:

1. **Mismatch behaviour at ingest time**: when GROBID-parsed title
   and Crossref title disagree past a similarity threshold, keep
   GROBID metadata and **skip Crossref enrichment** entirely. The
   suspicious Crossref DOI is preserved as `_crossref_doi_rejected`
   for audit.

2. **New audit fields**: `_grobid_title`, `_grobid_doi`,
   `_ingest_source` (`upload` / `crawler` / `unknown`).

3. **Backfill strategy**: forward-only for new ingests; for the
   existing 67 k records, the remediation tool backfills lazily —
   records it inspects get the audit fields written back. Corpus
   cleans piece by piece.

4. **Remediation tool**: detect + report + queue-for-reingest. PDFs
   are never destroyed; bad Qdrant points are deleted, PDF is moved
   back to `inbox/` for the pipeline to re-process under hardened
   rules. Reversible.

## Affected sample DOIs (for spot-check during remediation)

Uploads with real-looking mismatches:

- `10.5703/1288284314998` (elgeti) — claims Purdue conference paper,
  PDF is a rhodopsin photolysis study from Biochemistry 1990.
- `10.2307/411791` (elgeti) — JSTOR Bar-Hillel review, PDF is LIGPLOT
  1995 (the originating case for this audit).
- `10.1080/14786442508628538` (corzilius) — claims 1925 Heisenberg
  paper, PDF is 1961 Physics Today on Goudsmit / Pauli.
- `10.1103/physrevb.16.3887` (corzilius) — MathML-corrupted title,
  PDF is Phys. Chem. Solids 1990.
- `10.1021/bi00612a001` (elgeti) — claims 1978 Pober et al.
  rhodopsin paper, PDF is a Biochemistry 1982 ESR paper.

Crawler with real-looking mismatches:

- `10.1038/19525` — claims primate orbitofrontal cortex paper, PDF
  is about microfungi.
- `10.1038/47534` — claims NhaA transport protein, PDF is on telomere
  image analysis.

## Operating the remediation tool

```bash
# Inspect 100 records (default), GROBID paced 30s/call, write CSV.
# Resume-friendly: subsequent runs skip records that already carry
# `_inspected_at`.
sudo /opt/munin/services/vllm/venv/bin/python \
    /opt/cluster/scripts/pipeline/paper_cleanup.py \
    find-metadata-mismatch --limit 100 --report-out /var/log/cluster-admin/mm.csv

# Same, but also queue every high-severity record for reingest.
sudo ... find-metadata-mismatch --limit 100 --queue-for-reingest \
    --reingest-log /var/log/cluster-admin/reingest.csv

# Drive the queue (paced subprocesses against the hardened pipeline).
sudo ... reingest-queue --limit 50 --pace 30
```

`--source upload` / `--source crawler` restrict the pass. `--no-grobid`
falls back to pdftotext as a fast-and-noisy proxy — does not mark
`_inspected_at` so a real GROBID pass picks the record up later.

## Monitoring

The ingest-time guard logs a unique marker line on every rejected
Crossref enrichment:

```
[WARN] GROBID/Crossref title mismatch (sim=X.XX < 0.3); keeping GROBID metadata, dropping Crossref enrichment
```

Greppable from per-job logs:

```bash
grep -c "GROBID/Crossref title mismatch" /opt/munin/logs/vllm-service-*.out
# Or across the pipeline daemon:
journalctl -u munin-paper-pipeline.service | grep -c "GROBID/Crossref title mismatch"
```

A sustained zero count after a week of new ingests would suggest the
0.3 threshold is too permissive (or every new paper has perfect
metadata, less likely). A flood would suggest it's too strict —
recalibrate by re-running `/tmp/calibrate_jaccard_grobid.py` on a
fresh sample.

## Unit tests

```bash
/opt/munin/services/vllm/venv/bin/python \
    backend/scripts/pipeline/tests/test_paper_pipeline_merge.py
/opt/munin/services/vllm/venv/bin/python \
    backend/scripts/pipeline/tests/test_paper_cleanup_mismatch.py
```

Pure-function, no DB / network. Cover the title-similarity guard,
JATS stripping, the LIGPLOT-style merge rejection, the
running-header severity downgrade, DOI filename parsing, and the
threshold-drift detector (cleanup vs pipeline constants must match).

## Files

- Audit script (read-only): `/tmp/audit_papers.py`. Env vars
  `AUDIT_PDF_LIMIT` (samples per source, default 300) and
  `AUDIT_SKIP` (offset into deterministic stride for different
  draws).
- Calibration script (read-only, GROBID-paced):
  `/tmp/calibrate_jaccard_grobid.py`.
- Audit run output: `/tmp/audit_papers.out`.
- Unit tests: `backend/scripts/pipeline/tests/`.

## Status

- Stage 1 (pipeline hardening): SHIPPED. New ingests are guarded;
  `_grobid_title` / `_grobid_doi` / `_ingest_source` / `_ingest_at`
  fields land on every new Qdrant point.
- Stage 2 (remediation tool): SHIPPED.
  `paper_cleanup.py find-metadata-mismatch` + `reingest-queue`.
- Stage 3 (tests + monitoring): SHIPPED. 49 unit tests across two
  files; greppable log marker for production monitoring.
