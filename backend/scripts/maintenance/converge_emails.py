#!/usr/bin/env python3
"""
converge_emails.py - move data stored under a user's alias addresses onto
the user's identity address.

WHY. Every service stores user data under the X-Munin-Email header. Until
2026-11 that header carried the address the user typed at login, so a user
with two addresses had two separate accounts' worth of chats, uploads, API
keys and usage. munin-auth now sends a frozen users.identity_email instead
(migrate_identity_email_v1), which stops new splits. This tool folds the
data that was already split onto the identity. Once per deployment; a
second run finds nothing to do.

Stdlib only, so it runs unchanged on a plain host python or piped into a
container: `docker exec -i <c> python3 - <sub> ... < converge_emails.py`.
Every write subcommand is a DRY RUN unless `--apply` is given, and prints a
JSON report either way. Reports carry counts and sha256(email)[:16] hashes
(the same hash that names user_docs directories), never addresses or text.
The mapping file from `plan` does carry addresses: keep it off shared disks.

SUBCOMMANDS

  plan AUTH_DB             Read munin-auth's sessions.db (run after the auth
                           migration) and print the {alias: identity} map as
                           JSON. Every user_emails row whose address is not
                           its user's identity_email is an alias.

  chats DB --map F         Rewrite user_email in every chats.db table that has
                           one (found by schema, so tables added later are
                           covered). Where user_email is part of a primary key
                           (user_profiles, user_memory, rejected_memory_keys)
                           and the identity already has the row, the row with
                           the later updated_at/created_at wins, the identity's
                           on a tie; the other is deleted.

  gateway DB --map F       Same rules for gateway.db (api_keys, usage_log,
                           user_activity, which keys on `email`).

  files DIR --map F        Move user_docs/<hash(alias)>/<doc_id> trees into
                           user_docs/<hash(identity)>/. Refuses a doc_id that
                           exists on both sides. Never overwrites.

  qdrant --map F           Set payload user_email=identity on every point of
                           the user_docs collection whose user_email is an
                           alias. QDRANT_HOST/QDRANT_PORT as the retrieval
                           service uses them.

ORDER. Deploy the auth change first (new writes then land on the identity),
then run plan, then chats/files/qdrant on the backend host and gateway on
the frontend host. Stop nothing: each DB step is one transaction, and
--apply backs the DB up with the SQLite backup API first.

Exit status: 0 done or nothing to do, 2 refused (see report), 1 error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import urllib.request

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
KEY_COLUMNS = ("user_email", "email")
TIME_COLUMNS = ("updated_at", "last_seen_at", "created_at", "timestamp")
# Tables whose `email` column is not a user key (auth-side tables are
# handled by the auth migration; these never appear in chats/gateway DBs,
# listed so a future table named like them is not rewritten by accident).
SKIP_TABLES = {"sessions", "user_emails", "display_names"}


def email_hash(email: str) -> str:
    """Same function as document_store._email_hash."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def load_map(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for alias, identity in raw.items():
        a, i = alias.strip().lower(), identity.strip().lower()
        if a and i and a != i:
            out[a] = i
    identities = set(out.values())
    clash = identities & set(out)
    if clash:
        raise SystemExit(f"map is not flat: {len(clash)} identity is also an alias")
    return out


def emit(report: dict) -> None:
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


# =============================================================================
# plan
# =============================================================================

def cmd_plan(args) -> int:
    conn = sqlite3.connect(f"file:{args.auth_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "identity_email" not in cols:
        print("users.identity_email missing: deploy the auth change first",
              file=sys.stderr)
        return EXIT_ERROR
    rows = conn.execute(
        """
        SELECT ue.email AS alias, u.identity_email AS identity
          FROM user_emails ue JOIN users u ON u.id = ue.user_id
         WHERE u.identity_email IS NOT NULL AND ue.email != u.identity_email
         ORDER BY u.id, ue.email
        """
    ).fetchall()
    unset = conn.execute(
        "SELECT COUNT(*) FROM users WHERE identity_email IS NULL"
    ).fetchone()[0]
    if unset:
        print(f"warning: {unset} user(s) without identity_email; they are "
              "set on the next login or auth restart", file=sys.stderr)
    emit({r["alias"]: r["identity"] for r in rows})
    return EXIT_OK


# =============================================================================
# SQLite rewrite (chats, gateway)
# =============================================================================

def _key_column(conn, table) -> str | None:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    for k in KEY_COLUMNS:
        if k in cols:
            return k
    return None


def _unique_sets(conn, table) -> list[list[str]]:
    """Every column set that must be unique: the primary key plus each
    unique index."""
    sets = []
    pk = [r[1] for r in sorted(conn.execute(f'PRAGMA table_info("{table}")'),
                               key=lambda r: r[5]) if r[5]]
    if pk:
        sets.append(pk)
    for idx in conn.execute(f'PRAGMA index_list("{table}")'):
        if idx[2]:  # unique
            sets.append([r[2] for r in conn.execute(f'PRAGMA index_info("{idx[1]}")')])
    return sets


def _newer(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """True when row a is strictly newer than row b."""
    for c in TIME_COLUMNS:
        if c in a.keys() and a[c] is not None and b[c] is not None:
            return str(a[c]) > str(b[c])
    return False


def rewrite_db(path: str, mapping: dict[str, str], apply: bool) -> tuple[dict, int]:
    report = {"db": os.path.basename(path), "apply": apply, "tables": {}}
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = f"{path}.pre-converge-{stamp}"
            dst = sqlite3.connect(backup)
            conn.backup(dst)
            dst.close()
            report["backup"] = os.path.basename(backup)
        conn.execute("BEGIN IMMEDIATE")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table in SKIP_TABLES:
                continue
            key = _key_column(conn, table)
            if not key:
                continue
            uniques = [u for u in _unique_sets(conn, table) if key in u]
            moved = replaced = dropped = 0
            users = set()
            for alias, identity in mapping.items():
                rows = conn.execute(
                    f'SELECT rowid AS _rid, * FROM "{table}" WHERE "{key}" = ?',
                    (alias,)).fetchall()
                for row in rows:
                    users.add(email_hash(identity))
                    clash = None
                    for u in uniques:
                        where = " AND ".join(f'"{c}" = ?' for c in u)
                        vals = [identity if c == key else row[c] for c in u]
                        clash = conn.execute(
                            f'SELECT rowid AS _rid, * FROM "{table}" WHERE {where}',
                            vals).fetchone()
                        if clash:
                            break
                    if clash is None:
                        conn.execute(f'UPDATE "{table}" SET "{key}" = ? WHERE rowid = ?',
                                     (identity, row["_rid"]))
                        moved += 1
                    elif _newer(row, clash):
                        conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (clash["_rid"],))
                        conn.execute(f'UPDATE "{table}" SET "{key}" = ? WHERE rowid = ?',
                                     (identity, row["_rid"]))
                        replaced += 1
                    else:
                        conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (row["_rid"],))
                        dropped += 1
            if moved or replaced or dropped:
                report["tables"][table] = {
                    "moved": moved, "alias_row_won": replaced,
                    "identity_row_kept": dropped, "users": sorted(users)}
        left = 0
        for table in tables:
            key = None if table in SKIP_TABLES else _key_column(conn, table)
            if key:
                q = ",".join("?" * len(mapping))
                left += conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{key}" IN ({q})',
                    list(mapping)).fetchone()[0] if mapping else 0
        if left:
            conn.execute("ROLLBACK")
            report["error"] = f"{left} alias rows remain after rewrite; rolled back"
            return report, EXIT_ERROR
        if apply:
            conn.execute("COMMIT")
        else:
            conn.execute("ROLLBACK")
    finally:
        conn.close()
    return report, EXIT_OK


def cmd_db(args) -> int:
    report, rc = rewrite_db(args.db, load_map(args.map), args.apply)
    emit(report)
    return rc


# =============================================================================
# files (user_docs)
# =============================================================================

def cmd_files(args) -> int:
    mapping = load_map(args.map)
    report = {"apply": args.apply, "moved_docs": 0, "users": [], "refused": []}
    plan = []
    for alias, identity in mapping.items():
        src = os.path.join(args.dir, email_hash(alias))
        if not os.path.isdir(src):
            continue
        dst = os.path.join(args.dir, email_hash(identity))
        for doc_id in sorted(os.listdir(src)):
            target = os.path.join(dst, doc_id)
            if os.path.exists(target):
                report["refused"].append(
                    {"user": email_hash(identity), "doc_id": doc_id})
            else:
                plan.append((src, dst, doc_id))
        report["users"].append(email_hash(identity))
    if report["refused"]:
        emit(report)
        return EXIT_REFUSED
    for src, dst, doc_id in plan:
        if args.apply:
            os.makedirs(dst, exist_ok=True)
            shutil.move(os.path.join(src, doc_id), os.path.join(dst, doc_id))
        report["moved_docs"] += 1
    if args.apply:
        for src in {p[0] for p in plan}:
            if not os.listdir(src):
                os.rmdir(src)
    emit(report)
    return EXIT_OK


# =============================================================================
# qdrant
# =============================================================================

def _qdrant(method: str, path: str, body: dict) -> dict:
    host = os.environ.get("QDRANT_HOST", "localhost")
    port = os.environ.get("QDRANT_PORT", "6333")
    req = urllib.request.Request(
        f"http://{host}:{port}{path}", method=method,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def cmd_qdrant(args) -> int:
    mapping = load_map(args.map)
    coll = args.collection
    report = {"apply": args.apply, "collection": coll, "points": {}}
    for alias, identity in mapping.items():
        flt = {"must": [{"key": "user_email", "match": {"value": alias}}]}
        n = _qdrant("POST", f"/collections/{coll}/points/count",
                    {"filter": flt, "exact": True})["result"]["count"]
        if not n:
            continue
        report["points"][email_hash(identity)] = n
        if args.apply:
            _qdrant("POST", f"/collections/{coll}/points/payload?wait=true",
                    {"payload": {"user_email": identity}, "filter": flt})
            left = _qdrant("POST", f"/collections/{coll}/points/count",
                           {"filter": flt, "exact": True})["result"]["count"]
            if left:
                report["error"] = f"{left} points still on an alias"
                emit(report)
                return EXIT_ERROR
    emit(report)
    return EXIT_OK


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("auth_db"); s.set_defaults(fn=cmd_plan)
    for name in ("chats", "gateway"):
        s = sub.add_parser(name); s.add_argument("db")
        s.add_argument("--map", required=True); s.add_argument("--apply", action="store_true")
        s.set_defaults(fn=cmd_db)
    s = sub.add_parser("files"); s.add_argument("dir")
    s.add_argument("--map", required=True); s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_files)
    s = sub.add_parser("qdrant"); s.add_argument("--collection", default="user_docs")
    s.add_argument("--map", required=True); s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_qdrant)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
