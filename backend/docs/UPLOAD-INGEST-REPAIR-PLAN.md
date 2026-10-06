# Upload-ingest repair plan (2026-08-18)

Triggered by direct feedback from Contributor D that the PDFs they
uploaded to the knowledge base are damaged or wrong. The complaint is
correct. This document records what was measured, the three
independent defects behind it, the fixes, and the data-repair pass.

Companion to `PAPER-INGEST-AUDIT.md` (2026-05-12), which diagnosed an
overlapping symptom (metadata splices) and shipped the audit fields
and the mismatch guard this plan revises. Two of the five sample DOIs
listed as "affected" in that document are still broken today, so the
2026-05 remediation did not close the loop.

## Measured impact

627 Qdrant points in `papers_bge` carry
`contributors[].email = contributor-d@example.org`. Resolving each
one the way the service does at request time:

| Outcome | Count | Share |
|---|---|---|
| A. No PDF on disk anywhere: download 404s, no full text | 314 | 50.1% |
| B. Serves a different paper's PDF (wrong-DOI collision) | 61 | 9.7% |
| C. PDF exists but the download route 404s (DOI case mismatch) | 22 | 3.5% |
| D. Resolves to the correct PDF | 230 | 36.7% |

Confirmed live against the running service on 127.0.0.1:8080:

```
GET /paper/10.1016%2F0022-2836(82)90515-0/pdf   200, 2.0 MB
```

The record is titled "Transmembrane Protein Structure: Spin Labeling of
Bacteriorhodopsin Mutants". `pdftotext -l 1` on the file it returns
gives Kyte and Doolittle, "A Simple Method for Displaying the
Hydropathic Character of a Protein", J. Mol. Biol. 1982. Same shape for
`10.1126/science.1150577`: the record says "Activation mechanism of the
beta2-adrenergic receptor", the PDF is Cherezov et al., "High-Resolution
Crystal Structure of an Engineered Human beta2-Adrenergic GPCR".

Not specific to one contributor. Corpus-wide, 1142 upload-sourced points
have no PDF on disk, and 243 carry `_crossref_title_rejected`.

Source material is intact: `/mnt/uploads/processed/contributor-d@example.org/`
on the VPS holds 724 original PDFs, 1.9 GB. Nothing is permanently lost.

## Defect 1: uploaded PDFs are moved into the container filesystem

Severity: P0. Cause of outcome A, half the corpus damage.

`paper_pipeline.py:78-84` hardcodes host paths with no environment
override:

```python
PAPERS_DIR     = "/opt/munin/data/papers/pdf"
PROCESSED_DIR  = "/opt/munin/data/papers/processed"
QUARANTINE_DIR = "/opt/munin/data/papers/pdf/quarantine"
OCR_CACHE_DIR  = "/opt/munin/data/papers/ocr_cache"
```

Correct for the host-side watcher (`munin-paper-pipeline.service` runs
as root from `/opt/munin/services/pipeline/venv`). Wrong for the upload
path, which runs the same script inside `munin-retrieval`, where the
corpus is bind-mounted at `/papers` and `/data/papers`:

```
$ docker exec munin-retrieval ls /opt/munin/data/papers/pdf
ls: cannot access '/opt/munin/data/papers/pdf': No such file or directory
```

`_dispose_post_pipeline` then does, at `paper_pipeline.py:374-381`:

```python
if pdf.resolve() != final_pdf.resolve():
    try:
        final_pdf.parent.mkdir(parents=True, exist_ok=True)   # creates it in the container
        if final_pdf.exists():
            ...
        elif pdf.exists():
            shutil.move(str(pdf), str(final_pdf))             # writes to the container layer
```

The `mkdir(parents=True)` is what makes the bug silent: instead of
failing on a missing corpus directory, it materialises the path in the
container's writable layer, moves the PDF there, reports success, and
writes that path into the Qdrant payload. The file dies with the next
`docker compose up --build`. `PROCESSED_DIR` at line 456 goes the same
way, which is why every upload with a missing PDF also has no
processed-marker, while the real mount `/papers-processed` sits unused.

Corroboration: no PDF anywhere under `papers/` has a May or June 2026
mtime, despite 512 uploads on 2026-06-19. The 230 outcome-D survivors
only exist because the crawler had independently downloaded the same
DOI to the host.

### Fix

1. Make every path env-overridable, defaults unchanged so the host
   watcher and manual invocations keep working:

   ```python
   PAPERS_DIR     = os.getenv("PAPERS_PDF_DIR",       "/opt/munin/data/papers/pdf")
   PROCESSED_DIR  = os.getenv("PAPERS_PROCESSED_DIR", "/opt/munin/data/papers/processed")
   QUARANTINE_DIR = os.getenv("PAPERS_QUARANTINE_DIR", os.path.join(PAPERS_DIR, "quarantine"))
   OCR_CACHE_DIR  = os.getenv("PAPERS_OCR_CACHE_DIR", "/opt/munin/data/papers/ocr_cache")
   ```

   `PAPERS_PDF_DIR` and `PAPERS_PROCESSED_DIR` are the names the
   retrieval service already uses, so the container is configured
   correctly the moment the pipeline reads them.

2. Add a startup guard so this can never be silent again. Before any
   work, assert `PAPERS_DIR` and `PROCESSED_DIR` already exist and are
   writable, and exit non-zero with a clear message otherwise. Then
   drop the `parents=True` from the dispose `mkdir` so a missing
   corpus root raises instead of being invented.

3. Add a mount sanity check to the same guard: refuse to run if
   `PAPERS_DIR` is empty while the collection is non-empty. An empty
   corpus directory in production means the mount is missing.

4. No compose change is strictly required, since `PAPERS_PDF_DIR=/papers`
   is already in the retrieval environment block. Add
   `PAPERS_PROCESSED_DIR=/papers-processed` explicitly (it is currently
   consumed only by `main.py`) and
   `PAPERS_OCR_CACHE_DIR=/data/papers/ocr_cache`.

Note that `/data/papers/pdf` inside the container is the same directory
as `/papers` (65839 files in both), because `/opt/munin/data` is mounted
at `/data`. Either value works; `/papers` matches the existing env.

## Defect 2: a DOI proven wrong is kept anyway

Severity: P0. Cause of outcome B, and it damages records other
contributors depend on.

`_merge_metadata` at `paper_pipeline.py:1382-1396` compares the
GROBID-parsed title against the CrossRef title for the extracted DOI.
Below a similarity of 0.3 it concludes GROBID picked up a citation's
DOI rather than the paper's, and then keeps that DOI:

```python
if sim < self._MERGE_TITLE_SIM_THRESHOLD:
    grobid["_crossref_doi_rejected"]   = grobid.get("doi")   # flagged
    grobid["_crossref_title_rejected"] = crossref_title
    return grobid                                            # and kept
```

This implements policy decision 1 from the 2026-05-12 audit ("keep
GROBID metadata and skip Crossref enrichment"), but that policy did not
account for the DOI being load-bearing downstream. It is not just a
display field:

- `_store_vectors` keys the Qdrant point on `sha256(doi.lower())`
  (`paper_pipeline.py:1461-1464`), so the upload **overwrites the
  legitimate record** for the cited paper.
- `_store_graph` does `MERGE (p:Paper {doi: $doi})` and then `SET`s
  title, year, journal and abstract, so Neo4j is clobbered the same way.
- `_dispose_post_pipeline` names the file `doi_<wrong>.pdf`, colliding
  with the crawler's copy of the real paper. Because the collision
  branch drops the newcomer, the wrong PDF is what survives.

That last step is exactly what a user sees: correct title, wrong PDF.

117 of the 627 Elgeti records (18.7%) carry the flag; 61 of those
currently serve a different paper's PDF. That batch was maximally
exposed because the `filename_doi` authority path at
`paper_pipeline.py:999-1006` only fires for `doi_*.pdf` filenames, and
those files are publisher-named (`1-s2.0-S0092867415004997-main.pdf`).

### Fix

Revise the policy: a failed title check is evidence against the DOI,
not just against the enrichment.

1. On mismatch, clear `grobid["doi"]` rather than keeping it. Retain
   `_crossref_doi_rejected` / `_crossref_title_rejected` for audit.

2. Add `_fetch_crossref_by_title(title, authors)` using CrossRef
   `works?query.bibliographic=...&rows=5`, and accept a candidate only
   when its title clears the same 0.3 similarity bar against the GROBID
   title. On acceptance, use that DOI and its metadata. There is
   currently no by-title lookup in the pipeline, only
   `_fetch_crossref(doi)` at line 1273.

3. If no candidate clears the bar, leave the DOI null. The existing
   Stage 1.6 rule in `_dispose_post_pipeline` already routes null-DOI
   papers to `quarantine` with `doi_extraction_failed`, which is the
   right outcome: out of the corpus, PDF preserved, visible to
   `paper_cleanup.py review`.

4. Guard the overwrite independently of the above, because it is the
   step that causes collateral damage. Before upserting onto an
   existing point, compare the incoming title with the stored one. If
   they disagree past the threshold and the stored record was not
   contributed by this uploader, refuse the upsert and quarantine
   instead. This is the backstop for any future path that produces a
   wrong DOI; without it, one bad upload silently destroys a good
   record.

5. Apply the same title check before `_store_graph`'s `MERGE ... SET`.

## Defect 3: the download route cannot resolve a DOI whose case differs

Severity: P2. Cause of outcome C, 22 records.

The pipeline lowercases the DOI on the payload; the crawler preserved
the original case on disk. `main.py:306-318` `get_pdf_path` does a
single exact `os.path.exists` and returns `None` otherwise, so
`GET /paper/{doi}/pdf` 404s on a file that is present:

```
$ curl -o /dev/null -w '%{http_code}' .../paper/10.1017%2Fs0033583506004306/pdf
404
$ ls /opt/munin/data/papers/pdf/doi_10.1017_S0033583506004306.pdf
-rw-rw-r-- 1 vi vi 749615 Jan 13 2026 ...
```

The MCP copy at `mcp/tools/papers.py:127-163` already has a
case-insensitive fallback, which is why `read_paper` works on records
whose download link is broken. The two implementations have drifted.

### Fix

Delete `main.py`'s `get_pdf_path` and import the MCP one, so there is a
single resolver. Replace its `os.listdir` scan (65839 entries per miss)
with a lazily built, mtime-invalidated lowercase index, since the HTTP
route will now hit it on every 404.

## Data repair

Order matters: no re-ingest until defects 1 and 2 are fixed, or the
same damage is reproduced.

### R1. Restore the lost PDFs (outcome A, 314 records)

Pull the archive from the VPS and re-drive it through the fixed
pipeline. `paper_cleanup.py reingest-queue` already exists for this and
paces subprocess invocations, but note its documented v1 limit: it
leaves `pdf_path` pointing into `inbox/`. With defect 1 fixed,
`_dispose_post_pipeline` re-houses the file correctly, so that caveat
no longer applies and the docstring should be updated.

```
rsync from VPS:/mnt/uploads/processed/<email>/ -> staging on hugin
stage into pdf/inbox/ with a regenerated .contributor.json per file
paper_cleanup.py reingest-queue --limit 25 --pace 30 --dry-run   # canary
```

Canary 25 first and diff the resulting records before releasing the
rest. Pace matters: GROBID has ten engine slots and the watcher plus
the detect sweep are both live.

### R2. Repair the clobbered records (outcome B, 61 records)

These need care, because each one is two papers damaged: the cited
paper's record was overwritten, and the uploaded paper has no record of
its own. For each:

1. Recover the real DOI for the uploaded paper via CrossRef by title.
2. Write a new point under the correct DOI with the uploaded PDF.
3. Restore the clobbered record: the crawler's PDF is still on disk
   under the colliding filename, so re-running the pipeline on it
   rebuilds the original record.
4. Reconcile Neo4j, which was clobbered by the same `MERGE ... SET`.

Follow the `repair_authors.py` conventions: dry-run by default,
`--apply`, `--snapshot` before the first write, `--limit` for a canary,
`--resume` for interruption. Write the dry-run and apply logs to
`backend/docs/corpus-quality/` alongside the author-repair records.

### R3. Sweep the rest of the corpus

The 243 corpus-wide `_crossref_title_rejected` records and the 1142
missing-PDF uploads are the same two defects hitting other
contributors. `detect --kinds metadata-mismatch --source upload` plus
the archives in `/mnt/uploads/processed/` cover both. Run per
contributor and report each one's numbers to them.

### R4. Reconcile the count gap

724 archived files against 627 records. Some of the gap is DOI dedup
(one point, several uploads) and some is genuine ingest failure. Once
R1 lands, diff the archive against the corpus by content hash and
report what still has no record.

## Verification

Unit tests, extending `tests/test_paper_pipeline_merge.py`:

- Mismatch clears the DOI rather than keeping it.
- By-title recovery accepts an above-threshold candidate and rejects a
  below-threshold one.
- Null DOI after failed recovery routes to quarantine.
- The overwrite guard refuses a title-mismatched upsert onto an
  existing point.
- Path constants honour the env vars and fall back to the host defaults.

Integration, on a scratch collection:

- Ingest a PDF through `/api/admin/ingest` in the container and assert
  the file lands on the host bind mount, not the container layer. This
  is the regression test that would have caught defect 1 on day one.
- Assert the processed marker appears under `/papers-processed`.
- Ingest a PDF whose GROBID DOI points at a citation and assert the
  pre-existing record for that DOI is untouched.

Post-deploy checks:

- Recount the four outcome categories for the Elgeti set; expect
  A, B and C at zero.
- Assert no PDF exists inside the retrieval container's writable layer:
  `docker exec munin-retrieval find / -xdev -name 'doi_*.pdf'` returns
  nothing.

## Status

All three defects are fixed and committed. Defects 1 and 3 were deployed
and verified live on 2026-08-21 (commits 80c05c2, a294c3a). Defect 2 is
built and verified against the running stack (commit f9dff7a) but is NOT
yet deployed. The data repair (R1 to R4) is still open.

### Deviation from the plan, defect 2 step 4

The plan said the collision guard should refuse "when the stored record
was not contributed by this uploader". That carve-out is wrong and was
not implemented: the guard refuses unconditionally.

The carve-out reopens the exact bug in the single case that produced it.
If GROBID reads the same wrong citation DOI off several PDFs in one
contributor's batch, the first upload takes the point, and every
subsequent one is "contributed by this uploader" and would be allowed to
overwrite it. The batch would eat itself paper by paper. Refusing is
also cheap to undo: the paper is quarantined with its PDF intact for
`paper_cleanup.py review`, whereas an overwrite is unrecoverable without
a snapshot.

### Recovery threshold

`_RECOVERY_TITLE_SIM_THRESHOLD = 0.6`, deliberately double the 0.3
rejection bar. Rejecting a DOI on weak evidence is safe (the paper gets
quarantined); accepting one is not. Since the search query IS the
paper's title, a genuine match returns near 1.0, so 0.6 tolerates markup
and OCR drift while refusing the search engine's near-misses.

Verification evidence for defect 1: a real archived PDF was pushed
through `paper_pipeline.py --single` inside the retrieval container,
against a scratch collection with Neo4j disabled and a scratch corpus
on a host-mounted path. The PDF, both sidecars and the processed marker
all landed on the host mount, byte-identical to the source, and the
inbox drained. `find / -xdev -name 'doi_*.pdf'` inside the container
returns nothing.

That same run reproduced defect 2 unmodified: GROBID read the DOI
`10.1021/bi9714969` off a citation, the title guard fired
(`sim=0.27 < 0.3`) and the wrong DOI was kept regardless. It is one of
the 61 collisions.

Verification for defect 2, same isolation, on that very PDF (Klink et
al., "Pressure Dependence of the Photocycle Kinetics of
Bacteriorhodopsin"):

```
[WARN] GROBID/Crossref title mismatch (sim=0.27 < 0.3); dropping Crossref enrichment AND the DOI
[OK] Recovered DOI by title: 10.1016/s0006-3495(02)75348-4 (sim=1.00)
[DISPOSE] {"state": "live", "final_pdf_path": ".../doi_10.1016_s0006-3495(02)75348-4.pdf", ...}
```

Crossref confirms `10.1016/s0006-3495(02)75348-4` is Klink, Winter,
Engelhard, Chizhov, Biophysical Journal 2002. The paper that previously
overwrote a record now files itself correctly.

The collision backstop was then exercised by seeding that DOI with an
unrelated title and re-ingesting:

```
[WARN] DOI 10.1016/s0006-3495(02)75348-4 already holds a different paper (sim=0.00 < 0.3)
[DISPOSE] {"state": "quarantine", "quarantine_reasons": ["doi_collision_different_paper: ..."], ...}
```

The stored record was left byte-identical, the PDF was preserved in
`quarantine/` with its sidecars, and the inbox drained.

Verification for defect 3: `10.1017/s0033583506004306` returned 404
before the deploy and serves its 749 KB PDF after. All 22
case-mismatch papers were re-checked over HTTP; none still fail. Disk
state is unchanged, as expected for a resolver-only bug.

Recount after the deploy, unchanged except for class C:

| Outcome | Before | After |
|---|---|---|
| A. No PDF on disk | 314 | 314 |
| B. Serves a different paper | 61 | 61 |
| C. Case mismatch, 404 | 22 | 0 |
| D. Resolves | 230 | 252 |

`munin-paper-detect` was stopped for the deploy and restarted after.

## Sequencing

1. ~~Defect 1 fix plus the startup guard.~~ Done 2026-08-21.
2. ~~Defect 3 fix.~~ Done 2026-08-21; recovered 22 records.
3. ~~Defect 2 fix plus tests.~~ Built and verified 2026-08-21; DEPLOY STILL PENDING.
4. R1 canary 25, verify, then the remaining Elgeti backlog.
5. R2 repair script, dry-run, snapshot, canary, apply.
6. R3 and R4 for the other contributors.

Steps 1 to 3 are code and are safe to land together. Steps 4 onward
write to the live corpus and each needs a Qdrant snapshot first.

## Open questions for the maintainer

- Should re-ingest run against the live collection or a shadow one with
  a swap at the end? `docker-compose.shadow.yml` exists; a shadow run
  costs disk but makes the whole repair reversible in one step.
- Tell the contributor before or after the repair? The corpus is visibly wrong
  today, and R1 plus R2 is not a same-day job.
- Should the detect sweep (`munin-paper-detect.service`) be paused for
  the duration? It auto-quarantines, and a large re-ingest will look
  like a spike of new mismatches to it.
