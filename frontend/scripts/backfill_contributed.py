#!/usr/bin/env python3
"""
backfill_contributed.py — one-time §28 migration on the VPS.

Walks /mnt/uploads/complete/<email>/ and POSTs each PDF to the
cluster's /api/admin/ingest endpoint over the existing autossh
tunnel. Files that ingest successfully get moved to
/mnt/uploads/processed/<email>/ so the script is resumable.

Runs on the VPS (host <vps-host>), NOT on the cluster.

Deployment:
    # From your workstation, copy this file + start the run
    scp scripts/vps/backfill_contributed.py <user>@<vps-host>:/tmp/
    ssh <user>@<vps-host>
    # On the VPS:
    sudo cp /tmp/backfill_contributed.py /usr/local/bin/
    sudo chmod +x /usr/local/bin/backfill_contributed.py
    # Export the same token that lives in the cluster's
    # /opt/hugin/config/cluster.env on the SAME MACHINE as hook_service
    export ADMIN_INGEST_TOKEN=<64-char hex>

    # Dress rehearsal — smallest user first (418 files, a few hours)
    sudo -E /usr/local/bin/backfill_contributed.py \\
         --email contributor-c@example.org

    # Once that looks clean, the two bigger groups
    sudo -E /usr/local/bin/backfill_contributed.py --all

    # Preview without doing anything
    sudo -E /usr/local/bin/backfill_contributed.py --all --dry-run

Config knobs (environment variables):
    ADMIN_INGEST_TOKEN   (required) 64-char hex bearer token
    CLUSTER_INGEST_URL   default http://127.0.0.1:18080/api/admin/ingest
    PIPELINE_TIMEOUT     per-paper HTTP timeout in seconds, default 900
    COMPLETE_DIR         default /mnt/uploads/complete
    PROCESSED_DIR        default /mnt/uploads/processed
    REJECTED_DIR         default /mnt/uploads/rejected
    LOG_PATH             default /var/log/backfill-uploads.log

CLI flags:
    --email <addr>   ingest only this uploader's files
    --all            ingest every non-admin user in COMPLETE_DIR
    --dry-run        walk + log what would be POSTed, don't actually POST
    --limit N        stop after ingesting N successful papers (useful
                     for a tiny test run before the full rehearsal)

Design decisions (agreed 2026-04-20):
    - Concurrency: 1. Sequential POSTs. The cluster's GROBID +
      SPECTER are the bottleneck, and earlier runs had issues at
      higher parallelism.
    - Admin skip: admin@example.org is hard-coded in ADMIN_SKIPLIST
      (just the one test file). All other mailboxes get ingested.
    - Upload-time preservation: os.path.getmtime(pdf) → ISO 8601
      UTC → passed to endpoint's `upload_time` form field. Cluster
      stores this in the contributors[] payload so the history
      reflects when the user originally uploaded, not "now".
    - Resume: on 200 OK the PDF moves to /mnt/uploads/processed/.
      Re-running the script only sees what's still in complete/, so
      interruption + restart is safe and idempotent.
    - Failure policy: skip-and-log. Timeouts, 5xx, auth errors and
      network errors leave the file in complete/ for the next run.
    - Permanent rejections (400/413/415/422, e.g. "File is empty or not
      a PDF") move the file to /mnt/uploads/rejected/<email>/ with a
      .reason.txt beside it. Until 2026-10 they stayed in complete/ and
      were re-POSTed every 30 minutes forever: 12 uploads (empty files,
      saved HTML pages, a JPEG) had failed every run since April.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import Iterator

import requests


# ---- config --------------------------------------------------------------

COMPLETE_DIR = pathlib.Path(os.getenv("COMPLETE_DIR", "/mnt/uploads/complete"))
PROCESSED_DIR = pathlib.Path(os.getenv("PROCESSED_DIR", "/mnt/uploads/processed"))
REJECTED_DIR = pathlib.Path(os.getenv("REJECTED_DIR", "/mnt/uploads/rejected"))
# Client errors the cluster will give again for the same file.
PERMANENT_REJECT_CODES = {400, 413, 415, 422}
CLUSTER_INGEST_URL = os.getenv(
    "CLUSTER_INGEST_URL", "http://127.0.0.1:18080/api/admin/ingest"
)
PIPELINE_TIMEOUT = int(os.getenv("PIPELINE_TIMEOUT", "900"))
LOG_PATH = pathlib.Path(os.getenv("LOG_PATH", "/var/log/backfill-uploads.log"))

ADMIN_SKIPLIST = {"admin@example.org"}


# ---- logging setup -------------------------------------------------------

def _setup_logging() -> logging.Logger:
    log = logging.getLogger("backfill")
    log.setLevel(logging.INFO)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s")
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(fmt)
    log.addHandler(stdout)

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        print(f"[WARN] could not open {LOG_PATH}: {e}", file=sys.stderr)
    return log


# ---- work discovery ------------------------------------------------------

def iter_pending(email_filter: str | None) -> Iterator[tuple[pathlib.Path, str]]:
    """Yield (pdf_path, email) for every pending upload under COMPLETE_DIR,
    in a stable order so dress-rehearsal subsets are reproducible."""
    if not COMPLETE_DIR.is_dir():
        return
    for email_dir in sorted(COMPLETE_DIR.iterdir()):
        if not email_dir.is_dir():
            continue
        email = email_dir.name
        if email in ADMIN_SKIPLIST:
            continue
        if email_filter and email != email_filter:
            continue
        for pdf in sorted(email_dir.iterdir()):
            if not pdf.is_file():
                continue
            if pdf.suffix.lower() != ".pdf":
                continue
            yield pdf, email


def count_pending(email_filter: str | None) -> int:
    return sum(1 for _ in iter_pending(email_filter))


# ---- HTTP ---------------------------------------------------------------

def mtime_iso(path: pathlib.Path) -> str:
    """Return the file's mtime as an ISO 8601 UTC timestamp. Preserves
    the original upload date on VPS files — avoids every backfilled
    paper showing a single lumped 'uploaded_at'."""
    ts = path.stat().st_mtime
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def post_paper(
    pdf: pathlib.Path, email: str, token: str, log: logging.Logger
) -> tuple[bool, str, dict | None]:
    """Single synchronous POST. Returns (success, short_status, body_json).

    Success means the cluster returned 200 AND status != "skipped".
    Skipped papers (quality filter, non-research content) are moved to
    processed/ anyway — they've been seen and shouldn't be retried.

    HTTP 503 with `Retry-After` is the cluster's saturation signal
    (the /api/admin/ingest concurrency cap was hit). We sleep
    `Retry-After` seconds in-place before returning False so the next
    paper in the loop is held off too. The current paper stays in
    complete/ and gets retried on the next pass.
    """
    try:
        with pdf.open("rb") as f:
            r = requests.post(
                CLUSTER_INGEST_URL,
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (pdf.name, f, "application/pdf")},
                data={
                    "email": email,
                    "filename": pdf.name,
                    "upload_time": mtime_iso(pdf),
                },
                timeout=PIPELINE_TIMEOUT,
            )
    except requests.Timeout:
        return False, "TIMEOUT", None
    except requests.RequestException as e:
        return False, f"NETERR:{e.__class__.__name__}", None

    # 503 = cluster is saturated. Honour Retry-After so we don't
    # spin uselessly against a wedged endpoint.
    if r.status_code == 503:
        try:
            retry_after = int(r.headers.get("Retry-After", "60"))
        except (TypeError, ValueError):
            retry_after = 60
        retry_after = max(5, min(retry_after, 600))
        log.info(
            "503 saturated for %s — sleeping %ds (Retry-After) before next paper",
            pdf.name, retry_after,
        )
        time.sleep(retry_after)
        return False, "503:saturated", None

    if r.status_code != 200:
        tail = r.text[:300].replace("\n", " ")
        log.warning("HTTP %d for %s: %s", r.status_code, pdf.name, tail)
        if r.status_code in PERMANENT_REJECT_CODES:
            return False, f"REJECTED:HTTP{r.status_code}", {"detail": tail}
        return False, f"HTTP{r.status_code}", None

    try:
        body = r.json()
    except ValueError:
        return False, "BAD_JSON", None

    status = body.get("status", "?")
    # Both "ingested" and "skipped" mean "cluster has seen it". Move
    # the file so we don't retry next run.
    return status in {"ingested", "skipped"}, status, body


def unique_dest(dest_dir: pathlib.Path, filename: str) -> pathlib.Path:
    """A non-colliding path in dest_dir: a second upload with the same name
    must not overwrite the first one already moved there."""
    dest = dest_dir / filename
    stem, suffix = pathlib.Path(filename).stem, pathlib.Path(filename).suffix
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


def move_to_processed(pdf: pathlib.Path, email: str) -> pathlib.Path:
    dst_dir = PROCESSED_DIR / email
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = unique_dest(dst_dir, pdf.name)
    shutil.move(str(pdf), str(dst))
    return dst


def move_to_rejected(pdf: pathlib.Path, email: str, reason: str) -> pathlib.Path:
    dst_dir = REJECTED_DIR / email
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = unique_dest(dst_dir, pdf.name)
    shutil.move(str(pdf), str(dst))
    dst.with_name(dst.name + ".reason.txt").write_text(
        f"{datetime.now(timezone.utc).isoformat()} {reason}\n")
    return dst


# ---- main loop ----------------------------------------------------------

def run(email_filter: str | None, dry_run: bool, limit: int | None) -> int:
    log = _setup_logging()

    token = os.environ.get("ADMIN_INGEST_TOKEN", "").strip()
    if not token and not dry_run:
        log.error("ADMIN_INGEST_TOKEN not set; export it before running.")
        return 2

    total = count_pending(email_filter)
    if total == 0:
        log.info(
            "No pending files under %s (filter=%r). Nothing to do.",
            COMPLETE_DIR,
            email_filter,
        )
        return 0

    log.info(
        "backfill starting: %d pending, concurrency=1, timeout=%ds, dry_run=%s, limit=%s",
        total,
        PIPELINE_TIMEOUT,
        dry_run,
        limit,
    )
    if email_filter:
        log.info("filter: email=%s", email_filter)

    ok = 0
    skipped = 0
    failed = 0
    rejected = 0
    started = time.time()

    for i, (pdf, email) in enumerate(iter_pending(email_filter), start=1):
        elapsed = time.time() - started
        rate = ok / elapsed if elapsed and ok else 0.0
        remaining = total - (i - 1)
        eta_min = (remaining / rate) / 60.0 if rate else float("nan")

        prefix = f"[{i}/{total}] rate={rate:.2f}/s eta={eta_min:.1f}min"

        if dry_run:
            log.info("%s DRYRUN %s <- %s", prefix, email, pdf.name)
            continue

        t0 = time.time()
        success, status, body = post_paper(pdf, email, token, log)
        dt = time.time() - t0

        if success:
            if status == "ingested":
                ok += 1
                doi = (body or {}).get("doi")
                log.info(
                    "%s OK %s <- %s (%.1fs, doi=%s)",
                    prefix, email, pdf.name, dt, doi,
                )
            else:  # status == "skipped"
                skipped += 1
                reason = (body or {}).get("reason", "skipped")
                log.info(
                    "%s SKIP %s <- %s (%.1fs, %s)",
                    prefix, email, pdf.name, dt, reason[:80],
                )
            try:
                move_to_processed(pdf, email)
            except OSError as e:
                log.error("move to processed failed for %s: %s", pdf.name, e)
        elif status.startswith("REJECTED:"):
            rejected += 1
            try:
                dst = move_to_rejected(pdf, email, f"{status} {(body or {}).get('detail', '')}")
                log.warning("%s REJECT %s <- %s (%s) -> %s", prefix, email, pdf.name, status, dst)
            except OSError as e:
                failed += 1
                log.error("move to rejected failed for %s: %s", pdf.name, e)
        else:
            failed += 1
            log.warning(
                "%s FAIL %s <- %s (%.1fs, %s)",
                prefix, email, pdf.name, dt, status,
            )

        if limit is not None and ok >= limit:
            log.info("hit --limit %d, stopping early", limit)
            break

    elapsed = time.time() - started
    log.info(
        "done. ingested=%d skipped=%d rejected=%d failed=%d elapsed=%.1fmin",
        ok, skipped, rejected, failed, elapsed / 60.0,
    )
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--email", help="Ingest only this uploader's files")
    g.add_argument("--all", action="store_true", help="Ingest every non-admin user")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N successful ingests (useful for smoke tests)",
    )
    args = parser.parse_args()

    return run(
        email_filter=args.email if not args.all else None,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
