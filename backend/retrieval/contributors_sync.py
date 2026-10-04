"""Periodic sync of contributors.yml from the VPS auth service.

The auth service (auth.<domain>) owns the user/contributor database. This
module fetches its YAML projection every N seconds and writes the result to a
local file path that the rest of retrieval already watches via mtime.

Wire-up: `start_sync_task(loop)` is called from main.py's startup hook. It
spawns a fire-and-forget asyncio task; nothing blocks ingest if the sync
fails. If `CONTRIBUTORS_SYNC_URL` is unset the module is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx


logger = logging.getLogger(__name__)


CONTRIBUTORS_SYNC_URL = os.getenv("CONTRIBUTORS_SYNC_URL", "")
CONTRIBUTORS_SYNC_TOKEN = os.getenv("CONTRIBUTORS_SYNC_TOKEN", "")
CONTRIBUTORS_SYNC_INTERVAL_SECS = int(os.getenv("CONTRIBUTORS_SYNC_INTERVAL_SECS", "300"))
CONTRIBUTORS_CONFIG_PATH = os.getenv(
    "CONTRIBUTORS_CONFIG", "/data/contributors.yml"
)
# Failure backoff: when a fetch fails we retry sooner so the first
# successful sync after a transient outage doesn't take 5 minutes.
CONTRIBUTORS_SYNC_BACKOFF_SECS = int(os.getenv("CONTRIBUTORS_SYNC_BACKOFF_SECS", "60"))


def _atomic_write(target: Path, body: str) -> bool:
    """Write `body` to `target` atomically. Returns True if the file changed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    if existing == body:
        return False
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(target.parent),
        prefix=".contributors.", suffix=".tmp", delete=False,
    ) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    tmp_path.replace(target)
    return True


async def fetch_once(client: httpx.AsyncClient) -> bool:
    """Single fetch + atomic-write-on-diff. Returns True if file changed."""
    headers = {"Accept": "text/yaml"}
    if CONTRIBUTORS_SYNC_TOKEN:
        headers["Authorization"] = f"Bearer {CONTRIBUTORS_SYNC_TOKEN}"
    resp = await client.get(CONTRIBUTORS_SYNC_URL, headers=headers, timeout=15.0)
    resp.raise_for_status()
    return _atomic_write(Path(CONTRIBUTORS_CONFIG_PATH), resp.text)


async def _sync_loop():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                changed = await fetch_once(client)
                if changed:
                    logger.info(
                        "contributors_sync: refreshed %s",
                        CONTRIBUTORS_CONFIG_PATH,
                    )
                delay = CONTRIBUTORS_SYNC_INTERVAL_SECS
            except Exception as e:
                logger.warning("contributors_sync: fetch failed: %s", e)
                delay = CONTRIBUTORS_SYNC_BACKOFF_SECS
            await asyncio.sleep(delay)


def start_sync_task() -> asyncio.Task | None:
    """Launch the background sync loop. No-op if URL is unset."""
    if not CONTRIBUTORS_SYNC_URL:
        logger.info("contributors_sync: CONTRIBUTORS_SYNC_URL unset, sync disabled")
        return None
    logger.info(
        "contributors_sync: polling %s every %ds -> %s",
        CONTRIBUTORS_SYNC_URL,
        CONTRIBUTORS_SYNC_INTERVAL_SECS,
        CONTRIBUTORS_CONFIG_PATH,
    )
    return asyncio.create_task(_sync_loop(), name="contributors_sync")
