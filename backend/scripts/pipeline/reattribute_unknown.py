#!/usr/bin/env python3
"""
reattribute_unknown.py — retroactively assign group attribution to
papers that were ingested before their uploader was added to
`config/contributors.yml`.

Background
----------
When a user uploads via upload.muninai.org and their email isn't in
the allowlist yet, /api/admin/ingest still ingests the paper but
stamps the contributor as `group_slug: "unknown"`. Later, when the
admin adds the uploader to contributors.yml, those past papers
remain stuck under "unknown" — there's no automatic backfill.

This script walks the Qdrant `papers` collection, finds points whose
`contributors[]` list contains an entry with `group_slug: "unknown"`
whose email IS now in the allowlist, and rewrites that contributor
entry with the now-known fields (display_name, username, group_slug,
group_display_name). Optionally also updates the matching Neo4j
:Contributor node.

Idempotent. Safe to re-run after every contributors.yml change.

Usage:
    sudo /opt/munin/services/pipeline/venv/bin/python3 \\
         /opt/cluster/scripts/pipeline/reattribute_unknown.py --dry-run
    sudo /opt/munin/services/pipeline/venv/bin/python3 \\
         /opt/cluster/scripts/pipeline/reattribute_unknown.py

Environment:
    QDRANT_HOST              default 127.0.0.1
    QDRANT_PORT              default 6333
    QDRANT_COLLECTION        default papers
    NEO4J_URI                default bolt://127.0.0.1:7687
    NEO4J_USER               default neo4j
    NEO4J_PASSWORD           required for Neo4j updates (skipped if unset)
    CONTRIBUTORS_CONFIG_PATH default /opt/munin/config/contributors.yml
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Optional

import yaml


QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
CONTRIBUTORS_CONFIG_PATH = os.getenv(
    "CONTRIBUTORS_CONFIG_PATH", "/opt/munin/config/contributors.yml"
)


def _load_contributors() -> dict[str, dict]:
    """Mirror of retrieval/main.py:_load_contributors — same shape, no
    mtime cache (this script is short-lived). Supports both `email:`
    single and `emails:` list forms."""
    try:
        with open(CONTRIBUTORS_CONFIG_PATH, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"[ERROR] contributors.yml unreadable: {e}", file=sys.stderr)
        return {}
    out: dict[str, dict] = {}
    for entry in doc.get("contributors", []) or []:
        addrs: list[str] = []
        single = entry.get("email")
        if isinstance(single, str) and single.strip():
            addrs.append(single.strip().lower())
        listed = entry.get("emails")
        if isinstance(listed, list):
            for a in listed:
                if isinstance(a, str) and a.strip():
                    addrs.append(a.strip().lower())
        for addr in addrs:
            out[addr] = entry
    return out


def _rebuild_contributor(c: dict, allowlist: dict[str, dict]) -> Optional[dict]:
    """If `c` is an unknown entry whose email is now allowlisted,
    return the rebuilt entry. Otherwise return None."""
    if not isinstance(c, dict):
        return None
    if (c.get("group_slug") or "") != "unknown":
        return None
    email = (c.get("email") or "").strip().lower()
    if not email:
        return None
    known = allowlist.get(email)
    if known is None:
        return None
    return {
        "email": email,
        "username": known.get("username"),
        "display_name": known.get("display_name"),
        "group_slug": known.get("research_group") or "unknown",
        "group_display_name": known.get("research_group_display_name"),
        "upload_time": c.get("upload_time"),
    }


def _scroll_unknown(client) -> list:
    """Yield every paper point whose contributors[] contains a
    group_slug=='unknown' element. Qdrant filter does the cheap
    pre-screening server-side."""
    from qdrant_client.http import models as qm
    flt = qm.Filter(
        must=[
            qm.FieldCondition(
                key="contributors[].group_slug",
                match=qm.MatchValue(value="unknown"),
            )
        ]
    )
    out = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=flt,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        out.extend(points)
        if offset is None:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview re-attributions, write nothing")
    parser.add_argument("--no-neo4j", action="store_true",
                        help="Skip Neo4j updates (Qdrant only)")
    args = parser.parse_args()

    allowlist = _load_contributors()
    if not allowlist:
        print("[ERROR] No contributors loaded; nothing to do.", file=sys.stderr)
        return 2

    print(f"[INFO] Allowlist has {len(allowlist)} email(s).")

    from qdrant_client import QdrantClient
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    print(f"[INFO] Scrolling {QDRANT_COLLECTION} for unknown contributors...")
    t0 = time.time()
    points = _scroll_unknown(client)
    print(f"[INFO] {len(points)} candidate point(s) in {time.time() - t0:.1f}s")

    neo4j_driver = None
    if not args.no_neo4j and NEO4J_PASSWORD:
        try:
            from neo4j import GraphDatabase
            neo4j_driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            with neo4j_driver.session() as s:
                s.run("RETURN 1")
        except Exception as e:
            print(f"[WARN] Neo4j unavailable, skipping graph updates: {e}")
            neo4j_driver = None

    updated = 0
    skipped = 0
    by_group: dict[str, int] = {}

    for p in points:
        payload = p.payload or {}
        contribs: list = payload.get("contributors") or []
        new_contribs: list[dict] = []
        any_change = False
        for c in contribs:
            rebuilt = _rebuild_contributor(c, allowlist)
            if rebuilt is not None:
                new_contribs.append(rebuilt)
                any_change = True
                by_group[rebuilt["group_slug"]] = by_group.get(rebuilt["group_slug"], 0) + 1
            else:
                # Either already attributed, or still unknown (uploader
                # not in allowlist yet). Pass through unchanged.
                new_contribs.append(c if isinstance(c, dict) else {})
        if not any_change:
            skipped += 1
            continue

        if args.dry_run:
            updated += 1
            continue

        try:
            client.set_payload(
                collection_name=QDRANT_COLLECTION,
                payload={"contributors": new_contribs},
                points=[p.id],
                wait=False,
            )
        except Exception as e:
            print(f"[WARN] Qdrant set_payload failed for {p.id}: {e}")
            continue

        # Update Neo4j: for each newly-attributed contributor, MERGE
        # the proper :Contributor node and CONTRIBUTED edge to this
        # paper. The old "unknown" relationship from before is
        # implicit via the Contributor node email — same email gets
        # its display_name/group_slug updated, no new node needed.
        if neo4j_driver is not None:
            doi = payload.get("doi")
            for c in new_contribs:
                if not isinstance(c, dict) or not c.get("email"):
                    continue
                if c.get("group_slug") == "unknown":
                    continue
                try:
                    with neo4j_driver.session() as s:
                        if doi:
                            s.run(
                                """
                                MERGE (co:Contributor {email: $email})
                                SET co.username = coalesce($username, co.username),
                                    co.display_name = coalesce($display_name, co.display_name),
                                    co.group_slug = $group_slug,
                                    co.group_display_name = coalesce($group_display_name, co.group_display_name)
                                WITH co
                                MATCH (p:Paper {doi: $doi})
                                MERGE (co)-[r:CONTRIBUTED]->(p)
                                """,
                                email=c["email"],
                                username=c.get("username"),
                                display_name=c.get("display_name"),
                                group_slug=c["group_slug"],
                                group_display_name=c.get("group_display_name"),
                                doi=doi,
                            )
                except Exception as e:
                    print(f"[WARN] Neo4j update failed for {p.id}: {e}")

        updated += 1
        if updated % 100 == 0:
            print(f"[INFO]   updated {updated} so far...", flush=True)

    if neo4j_driver is not None:
        neo4j_driver.close()

    print(
        f"\n[DONE] {('DRY RUN' if args.dry_run else '')}\n"
        f"  candidates with group_slug=unknown: {len(points)}\n"
        f"  re-attributed: {updated}\n"
        f"  unchanged (still unknown / not in allowlist): {skipped}\n"
        f"  by group:"
    )
    for slug, n in sorted(by_group.items(), key=lambda x: -x[1]):
        print(f"    {slug}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
