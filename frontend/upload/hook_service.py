import asyncio
import os
import re
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, BackgroundTasks

app = FastAPI()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hook-service")

STAGING_DIR = Path("/mnt/uploads/staging")
COMPLETE_DIR = Path("/mnt/uploads/complete")
PROCESSED_DIR = Path("/mnt/uploads/processed")

CLUSTER_INGEST_URL = os.getenv("CLUSTER_INGEST_URL", "http://127.0.0.1:18080/api/admin/ingest")
ADMIN_INGEST_TOKEN = os.getenv("ADMIN_INGEST_TOKEN", "")
PIPELINE_TIMEOUT = int(os.getenv("PIPELINE_TIMEOUT", "900"))


def sanitize_email(email: str) -> str:
    """Turn an email into a safe directory name."""
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", email.lower())


def sanitize_filename(name: str) -> str:
    """Strip unsafe characters from a filename, preserve extension."""
    return re.sub(r"[^a-zA-Z0-9._\- ]", "_", name)


def unique_dest(dest_dir: Path, filename: str) -> Path:
    """Return a non-colliding destination path."""
    dest = dest_dir / filename
    counter = 1
    while dest.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        dest = dest_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


def mtime_iso(path: Path) -> str:
    """Return file mtime as ISO 8601 UTC timestamp."""
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


async def push_to_cluster(pdf_path: Path, email: str, filename: str):
    """POST a PDF to the cluster ingest endpoint. On success, move to processed/.
    On failure, leave in complete/ for the cron retry sweep."""
    if not ADMIN_INGEST_TOKEN:
        log.warning("ADMIN_INGEST_TOKEN not set — skipping cluster push for %s", filename)
        return

    try:
        async with httpx.AsyncClient(timeout=PIPELINE_TIMEOUT) as client:
            with pdf_path.open("rb") as f:
                resp = await client.post(
                    CLUSTER_INGEST_URL,
                    headers={"Authorization": f"Bearer {ADMIN_INGEST_TOKEN}"},
                    files={"file": (filename, f, "application/pdf")},
                    data={
                        "email": email,
                        "filename": filename,
                        "upload_time": mtime_iso(pdf_path),
                    },
                )

        if resp.status_code == 200:
            body = resp.json()
            status = body.get("status", "?")
            doi = body.get("doi", "")
            log.info("[ingest] %s %s <- %s (doi=%s)", status.upper(), email, filename, doi)
            # Move to processed/ — cluster has seen it
            dest_dir = PROCESSED_DIR / sanitize_email(email)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = unique_dest(dest_dir, pdf_path.name)
            shutil.move(str(pdf_path), str(dest))
        elif resp.status_code == 503:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            log.warning(
                "[ingest] 503 saturated, backing off %ds — %s <- %s",
                retry_after, email, filename,
            )
            await asyncio.sleep(retry_after)
            # Leave in complete/ for cron retry
        else:
            log.warning(
                "[ingest] FAIL HTTP %d %s <- %s: %s",
                resp.status_code, email, filename, resp.text[:200],
            )
            # Leave in complete/ for cron retry

    except Exception as e:
        log.error("[ingest] ERROR %s <- %s: %s", email, filename, e)
        # Leave in complete/ for cron retry


@app.post("/hooks")
async def tusd_hook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    # tusd v2+ uses {"Type": "...", "Event": {...}} structure
    # tusd v1 uses {"Upload": {...}, "HTTPRequest": {...}} with Hook-Name header
    hook_name = request.headers.get("Hook-Name", "") or payload.get("Type", "")

    if hook_name != "post-finish":
        return {"ok": True}

    try:
        # Handle both tusd v1 and v2 payload structures
        event = payload.get("Event", payload)
        http_request = event.get("HTTPRequest", {})
        http_headers = http_request.get("Header", {})
        upload = event.get("Upload", payload.get("Upload", {}))

        email_list = http_headers.get("X-Munin-Email", [])
        email = email_list[0] if email_list else None

        if not email:
            log.warning(f"post-finish hook with no Remote-Email — headers: {dict(http_headers)}")
            return {"ok": True}

        upload_id = upload["ID"]
        meta = upload.get("MetaData", {})
        original_name = meta.get("filename", f"{upload_id}.bin")

        safe_name = sanitize_filename(original_name)
        safe_email = sanitize_email(email)

        src = STAGING_DIR / upload_id
        info_file = STAGING_DIR / f"{upload_id}.info"

        if not src.exists():
            log.error(f"Source file not found: {src}")
            return {"ok": False, "error": "source not found"}

        dest_dir = COMPLETE_DIR / safe_email
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = unique_dest(dest_dir, safe_name)
        shutil.move(str(src), str(dest))

        if info_file.exists():
            info_file.unlink()

        log.info(f"[{email}] {original_name} → {dest.relative_to(COMPLETE_DIR)}")

        # Fire-and-forget: push to cluster in background
        if dest.suffix.lower() == ".pdf":
            background_tasks.add_task(push_to_cluster, dest, email, original_name)

        return {"ok": True}

    except Exception as e:
        log.error(f"Hook error: {e} | payload: {payload}")
        return {"ok": False, "error": str(e)}


@app.get("/health")
def health():
    return {"ok": True}
