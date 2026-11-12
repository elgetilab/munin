#!/usr/bin/env python3
"""
merge_state.py - merge one Munin backend's user state into another.

WHY. When the cluster is down, users are served by a Lite backend on another
host (docs/install/lite.md). Chats, artifacts, projects, memories and uploads
made there live in that host's chats.db, user_docs directory and Qdrant
`user_docs` collection. Returning to the cluster without merging them throws
that work away; failing over again later needs the same merge in the other
direction (cluster -> standby Lite). This tool is that merge, written once and
generic in direction: SOURCE -> TARGET, with an optional `--since` cutoff.

Stdlib only, so it runs unchanged in the retrieval container on either side or
on a plain host python. Every write subcommand is a DRY RUN unless `--apply` is
given, and prints a JSON report either way. Nothing user-authored is printed:
the report carries counts, ids and sha256(email)[:16] user hashes (the same
hash that names user_docs directories), never message text or addresses.

SUBCOMMANDS

  snapshot SRC OUT        Copy a live SQLite DB with the backup API (consistent
                          even while the service writes). OUT "-" streams the
                          snapshot to stdout, so a remote host needs no temp
                          file: `docker exec -i ... python3 - snapshot ... -`.

  db                      Merge chats.db rows. Rules:
                          * the source is always re-snapshotted first and the
                            target backed up with the backup API before writing;
                          * one transaction (BEGIN IMMEDIATE), inserts and
                            updates by column NAME (column order differs between
                            deployments), never INSERT OR REPLACE (a REPLACE
                            delete skips the FTS delete trigger);
                          * refuses on a schema mismatch, a primary-key
                            collision whose rows differ, a (conversation_id,
                            index_in_conversation) slot taken by another
                            message, or a row whose parent exists on neither
                            side, unless a policy flag says otherwise;
                          * a row identical on both sides is skipped, which is
                            what makes a second run a no-op;
                          * after writing, inside the same transaction, checks
                            FTS5 integrity, messages/messages_fts parity, that
                            every merged message is in the index with its text,
                            that a word from each merged message MATCHes it, and
                            that a re-plan finds nothing left to do. Any failure
                            rolls the whole merge back.

  files                   Copy user_docs/<user-hash>/<doc_id>/<file> trees with
                          collision checks (sha256 per file). Never overwrites.

  qdrant-export           Scroll a collection (vectors + payloads) to JSONL.
  qdrant                  Upsert an export into a target collection after
                          checking point-id collisions and documents already
                          indexed under other point ids (which would duplicate
                          chunks). Talks REST via urllib; QDRANT_HOST/QDRANT_PORT
                          as the retrieval service uses them.

POLICIES (db: --on-conflict; files and qdrant: --on-conflict)

  refuse        (default) any differing collision stops the run, nothing written
  keep-target   leave the target row/file/point as is, skip the source one
  source-wins   overwrite the target row (db) or point (qdrant) with the source
  newer-wins    db only: take whichever row has the later timestamp column

  --skip-orphans          drop source rows whose parent is on neither side
  --allow-schema-drift    merge the intersection of columns when they differ,
                          provided every NOT NULL target column without a
                          default is present in the source
  --create-missing-tables create a table the target lacks from the source DDL

LIMITS (inherent to an additive merge)

  * Deletions do not propagate. A conversation deleted on the target after the
    source copy was taken comes back if it is in scope; use --since to keep the
    scope to what was written during the failover window.
  * Sandbox-generated artifact files and Deep Research checkpoints are not
    part of this merge (the rows are; the sandbox scratch they point at is
    ephemeral on both sides). The report counts them.

Exit status: 0 done or nothing to do, 2 refused (see report), 1 error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
POLICIES_DB = ("refuse", "keep-target", "source-wins", "newer-wins")
POLICIES_FILES = ("refuse", "keep-target")
POLICIES_QDRANT = ("refuse", "keep-target", "source-wins")


# =============================================================================
# Shared helpers
# =============================================================================

def email_hash(email: Optional[str]) -> Optional[str]:
    """Same function as document_store._email_hash, so report keys match the
    user_docs directory names."""
    if not email:
        return None
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def parse_since(value: Optional[str]) -> Optional[_dt.datetime]:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def ts_to_dt(value: Any, kind: str) -> Optional[_dt.datetime]:
    """Timestamp column -> aware UTC datetime. ISO text for chat tables, epoch
    seconds (REAL) for research tables. Unparseable -> None (always in scope:
    a row we cannot date is safer merged and reported than silently dropped)."""
    if value is None or value == "":
        return None
    try:
        if kind == "epoch":
            return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
        d = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def emit(report: dict, path: Optional[str]) -> None:
    text = json.dumps(report, indent=2, sort_keys=False, default=str)
    if path:
        with open(path, "w") as f:
            f.write(text + "\n")
    print(text)


def note(msg: str) -> None:
    print(msg, file=sys.stderr)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# =============================================================================
# snapshot
# =============================================================================

def snapshot_db(src_path: str, out_path: str) -> dict:
    """Backup-API copy of a (possibly live, WAL-mode) SQLite DB. The copy is
    switched to a rollback journal so it is one self-contained file."""
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        if out_path == "-":
            mem = sqlite3.connect(":memory:")
            src.backup(mem)
            if hasattr(mem, "serialize"):
                data = mem.serialize()
            else:  # Python < 3.11: go through a private temp file.
                fd, tmp = tempfile.mkstemp(suffix=".db")
                os.close(fd)
                try:
                    disk = sqlite3.connect(tmp)
                    mem.backup(disk)
                    disk.close()
                    with open(tmp, "rb") as f:
                        data = f.read()
                finally:
                    os.unlink(tmp)
            mem.close()
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            return {"bytes": len(data)}
        dst = sqlite3.connect(out_path)
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        return {"path": out_path, "bytes": os.path.getsize(out_path), "integrity": ok}
    finally:
        src.close()


# =============================================================================
# db merge
# =============================================================================

@dataclass(frozen=True)
class TableSpec:
    name: str
    ts_col: str
    ts_kind: str = "iso"  # "iso" text or "epoch" REAL
    # (column, parent_table, parent_column): a non-NULL value must resolve on
    # one side or the row is an orphan. Mirrors the FOREIGN KEYs in chat_store
    # plus the two that are enforced in code rather than DDL (proposals are
    # deleted at startup when their conversation is gone; research events are
    # unreachable without their job).
    parents: tuple = ()
    # Soft references: reported when dangling, never block.
    soft: tuple = ()


# Insert order satisfies every parent before its children.
TABLES: tuple[TableSpec, ...] = (
    TableSpec("projects", "updated_at"),
    TableSpec("conversations", "updated_at", soft=(("project_id", "projects", "id"),)),
    TableSpec("messages", "created_at", parents=(("conversation_id", "conversations", "id"),)),
    TableSpec("conversation_plans", "updated_at", parents=(("conversation_id", "conversations", "id"),)),
    TableSpec("artifacts", "updated_at", parents=(("conversation_id", "conversations", "id"),)),
    TableSpec("artifact_versions", "created_at", parents=(("artifact_id", "artifacts", "id"),)),
    TableSpec("proposed_memories", "proposed_at", parents=(("conversation_id", "conversations", "id"),)),
    TableSpec("rejected_memory_keys", "rejected_at"),
    TableSpec("user_memory", "updated_at"),
    TableSpec("user_profiles", "updated_at"),
    TableSpec("research_jobs", "updated_at", "epoch",
              soft=(("conversation_id", "conversations", "id"), ("artifact_id", "artifacts", "id"))),
    TableSpec("research_events", "t", "epoch", parents=(("job_id", "research_jobs", "job_id"),)),
)
SPEC = {t.name: t for t in TABLES}
FTS_TABLE = "messages_fts"
FTS_TRIGGERS = ("messages_fts_insert", "messages_fts_delete", "messages_fts_update")
TERMINAL_RESEARCH = {"done", "complete", "completed", "failed", "error", "cancelled", "canceled", "stopped"}


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    """Ordinary tables, excluding SQLite internals and FTS5 shadow tables."""
    out = []
    for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith("sqlite_") or name.startswith(FTS_TABLE):
            continue
        if sql and sql.upper().startswith("CREATE VIRTUAL"):
            continue
        out.append(name)
    return out


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    cols = {}
    for cid, name, ctype, notnull, dflt, pk in conn.execute(f'PRAGMA table_info("{table}")'):
        cols[name] = {"type": (ctype or "").upper(), "notnull": bool(notnull),
                      "default": dflt, "pk": pk}
    return cols


def _pk_cols(cols: dict[str, dict]) -> list[str]:
    pk = sorted(((c["pk"], n) for n, c in cols.items() if c["pk"]), key=lambda x: x[0])
    return [n for _, n in pk]


def check_schema(src: sqlite3.Connection, tgt: sqlite3.Connection, allow_drift: bool,
                 create_missing: bool) -> tuple[dict, list[str], dict[str, list[str]]]:
    """Compare tables by column NAME (order is irrelevant: messages columns are
    ordered differently on deployments migrated at different times).

    Returns (schema_report, blocking_problems, merge_columns_per_table)."""
    rep: dict[str, Any] = {"tables": {}, "fts": {}}
    problems: list[str] = []
    merge_cols: dict[str, list[str]] = {}
    src_tables = _user_tables(src)
    tgt_tables = set(_user_tables(tgt))

    for t in src_tables:
        entry: dict[str, Any] = {}
        rep["tables"][t] = entry
        if t not in SPEC:
            n = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            entry["unknown_table"] = True
            entry["source_rows"] = n
            if n:
                problems.append(f"{t}: table unknown to merge_state.py with {n} rows "
                                "(add a TableSpec so its timestamp and parents are known)")
            continue
        scols = _columns(src, t)
        if t not in tgt_tables:
            entry["missing_in_target"] = True
            if not create_missing:
                n = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if n:
                    problems.append(f"{t}: missing in target ({n} source rows); "
                                    "pass --create-missing-tables")
                continue
            merge_cols[t] = list(scols)
            continue
        tcols = _columns(tgt, t)
        only_src = sorted(set(scols) - set(tcols))
        only_tgt = sorted(set(tcols) - set(scols))
        differ = sorted(c for c in set(scols) & set(tcols)
                        if (scols[c]["type"], scols[c]["notnull"], bool(scols[c]["pk"]))
                        != (tcols[c]["type"], tcols[c]["notnull"], bool(tcols[c]["pk"])))
        if _pk_cols(scols) != _pk_cols(tcols):
            problems.append(f"{t}: primary key differs ({_pk_cols(scols)} vs {_pk_cols(tcols)})")
        if only_src or only_tgt or differ:
            entry.update({"only_in_source": only_src, "only_in_target": only_tgt,
                          "definition_differs": differ})
            if not allow_drift:
                problems.append(f"{t}: columns differ (source-only {only_src}, target-only "
                                f"{only_tgt}, differing {differ}); pass --allow-schema-drift")
            else:
                required = [c for c in only_tgt
                            if tcols[c]["notnull"] and tcols[c]["default"] is None]
                if required:
                    problems.append(f"{t}: target requires {required}, absent in source")
        else:
            entry["identical_columns"] = True
        merge_cols[t] = [c for c in tcols if c in scols]

    # FTS plumbing on the target: without the virtual table and all three
    # triggers, inserted messages would be invisible to search.
    tgt_objs = {(r[0], r[1]) for r in tgt.execute("SELECT type, name FROM sqlite_master")}
    rep["fts"]["target_table"] = ("table", FTS_TABLE) in tgt_objs
    rep["fts"]["target_triggers"] = {t: ("trigger", t) in tgt_objs for t in FTS_TRIGGERS}
    if "messages" in merge_cols and not (rep["fts"]["target_table"]
                                         and all(rep["fts"]["target_triggers"].values())):
        problems.append("target lacks messages_fts or one of its triggers")
    return rep, problems, merge_cols


def _rows(conn: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    q = ", ".join(f'"{c}"' for c in cols)
    cur = conn.execute(f'SELECT {q} FROM "{table}"')
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@dataclass
class TablePlan:
    name: str
    cols: list[str]
    pk: list[str]
    source_rows: int = 0
    in_scope: int = 0
    pulled_as_parent: int = 0
    identical: int = 0
    inserts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    kept_target: int = 0
    conflicts: list[dict] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)
    soft_dangling: int = 0
    source_duplicate_slots: int = 0

    def summary(self) -> dict:
        return {
            "source_rows": self.source_rows,
            "in_scope": self.in_scope,
            "pulled_in_as_parent": self.pulled_as_parent,
            "identical_skipped": self.identical,
            "insert": len(self.inserts),
            "update": len(self.updates),
            "kept_target": self.kept_target,
            "conflicts": len(self.conflicts),
            "orphans": len(self.orphans),
            "soft_references_dangling": self.soft_dangling,
            "source_duplicate_index_slots": self.source_duplicate_slots,
        }


def _key(row: dict, pk: list[str]) -> tuple:
    return tuple(row[c] for c in pk)


def _ident(key: tuple, pk: list[str]) -> str:
    """Printable key. Natural-key parts (user_email, a memory key) are hashed:
    reports go to terminals and tickets, user data does not."""
    parts = []
    for col, v in zip(pk, key):
        if v is None:
            parts.append("")
        elif col == "id" or col.endswith("_id") or col in ("version", "seq"):
            parts.append(str(v))
        else:
            parts.append("h:" + hashlib.sha256(str(v).strip().lower().encode()).hexdigest()[:16])
    return "|".join(parts)


def plan_db(src: sqlite3.Connection, tgt: sqlite3.Connection, merge_cols: dict[str, list[str]],
            since: Optional[_dt.datetime], policy: str) -> dict[str, TablePlan]:
    """Work out, per table, what a merge would do. Pure reads."""
    tgt_tables = set(_user_tables(tgt))
    src_rows: dict[str, dict[tuple, dict]] = {}
    tgt_rows: dict[str, dict[tuple, dict]] = {}
    plans: dict[str, TablePlan] = {}
    scope: dict[str, set] = {}

    for spec in TABLES:
        t = spec.name
        if t not in merge_cols:
            continue
        cols = merge_cols[t]
        pk = _pk_cols(_columns(src, t))
        plans[t] = TablePlan(t, cols, pk)
        rows = _rows(src, t, cols)
        src_rows[t] = {_key(r, pk): r for r in rows}
        plans[t].source_rows = len(rows)
        if t in tgt_tables:
            tgt_rows[t] = {_key(r, pk): r for r in _rows(tgt, t, cols)}
        else:
            tgt_rows[t] = {}
        scope[t] = set()
        for k, r in src_rows[t].items():
            d = ts_to_dt(r.get(spec.ts_col), spec.ts_kind) if since else None
            if since is None or d is None or d >= since:
                scope[t].add(k)
        plans[t].in_scope = len(scope[t])

    # Parent closure: a child in scope drags in a parent that exists only in
    # the source, even if the parent itself predates --since (a conversation
    # started before the failover that got new messages during it).
    changed = True
    while changed:
        changed = False
        for spec in TABLES:
            if spec.name not in scope:
                continue
            for col, ptable, pcol in spec.parents:
                if ptable not in scope:
                    continue
                for k in list(scope[spec.name]):
                    v = src_rows[spec.name][k].get(col)
                    if v is None:
                        continue
                    pk = (v,)
                    if pk in src_rows[ptable] and pk not in scope[ptable] \
                            and pk not in tgt_rows[ptable]:
                        scope[ptable].add(pk)
                        plans[ptable].pulled_as_parent += 1
                        changed = True

    for spec in TABLES:
        t = spec.name
        if t not in plans:
            continue
        p = plans[t]
        # Slots already used in the TARGET, for the message ordering check. A
        # source that itself repeats a slot (the 2026-08 double-persist bug
        # left three assistant rows at one index) is copied as it is and
        # counted, not refused: that is the source's history, not a collision.
        slots: dict[tuple, str] = {}
        if t == "messages":
            for r in tgt_rows[t].values():
                slots[(r["conversation_id"], r["index_in_conversation"])] = r["id"]
            seen: dict[tuple, int] = {}
            for r in src_rows[t].values():
                sl = (r["conversation_id"], r["index_in_conversation"])
                seen[sl] = seen.get(sl, 0) + 1
            p.source_duplicate_slots = sum(1 for n in seen.values() if n > 1)
        for k in sorted(scope[t], key=lambda x: tuple("" if v is None else str(v) for v in x)):
            row = src_rows[t][k]
            # Orphans: parent on neither side (after this merge).
            orphan = None
            for col, ptable, pcol in spec.parents:
                v = row.get(col)
                if v is None:
                    continue
                pk = (v,)
                present = pk in tgt_rows.get(ptable, {}) or pk in scope.get(ptable, set())
                if not present:
                    orphan = {"key": _ident(k, p.pk), "missing_parent": f"{ptable}.{pcol}"}
                    break
            if orphan:
                p.orphans.append(orphan)
                continue
            for col, ptable, pcol in spec.soft:
                v = row.get(col)
                if v is not None and (v,) not in tgt_rows.get(ptable, {}) \
                        and (v,) not in scope.get(ptable, set()):
                    p.soft_dangling += 1
            existing = tgt_rows[t].get(k)
            if existing is None:
                if t == "messages":
                    slot = (row["conversation_id"], row["index_in_conversation"])
                    taken = slots.get(slot)
                    if taken is not None and taken != row["id"]:
                        p.conflicts.append({"key": _ident(k, p.pk), "kind": "index_taken",
                                            "by": taken})
                        continue
                p.inserts.append(row)
                continue
            diff = sorted(c for c in p.cols if existing.get(c) != row.get(c))
            if not diff:
                p.identical += 1
                continue
            take_source = False
            if policy == "source-wins":
                take_source = True
            elif policy == "newer-wins":
                sd = ts_to_dt(row.get(spec.ts_col), spec.ts_kind)
                td = ts_to_dt(existing.get(spec.ts_col), spec.ts_kind)
                take_source = sd is not None and (td is None or sd > td)
            if policy == "refuse":
                p.conflicts.append({"key": _ident(k, p.pk), "kind": "differs", "columns": diff})
            elif take_source:
                p.updates.append(row)
            else:
                p.kept_target += 1
    return plans


def _verify_fts(conn: sqlite3.Connection, message_ids: list[str]) -> dict:
    """Check the FTS index after writing. Raises on any inconsistency."""
    out: dict[str, Any] = {}
    conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('integrity-check')")
    out["fts5_integrity_check"] = "ok"
    n_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    n_fts = conn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
    out["messages"] = n_msg
    out["messages_fts"] = n_fts
    bad_content = 0
    match_ok = match_tried = 0
    # A whole unicode61 token: ASCII letters bounded by non-alphanumerics on
    # both sides. A bare [A-Za-z]{5,} can pick the letters out of "abcdef123",
    # which is one token, and the MATCH then rightly finds nothing.
    word = re.compile(r"(?<![^\W_])[A-Za-z]{5,}(?![^\W_])")
    for mid in message_ids:
        r = conn.execute(
            "SELECT m.rowid, m.content, m.conversation_id, f.content, f.conversation_id "
            f"FROM messages m LEFT JOIN {FTS_TABLE} f ON f.rowid = m.rowid WHERE m.id = ?",
            (mid,)).fetchone()
        if r is None or r[3] != r[1] or r[4] != r[2]:
            bad_content += 1
            continue
        m = word.search(r[1] or "")
        if m:
            match_tried += 1
            hit = conn.execute(
                f"SELECT 1 FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH ? AND rowid = ?",
                ('"' + m.group(0) + '"', r[0])).fetchone()
            match_ok += 1 if hit else 0
    out["merged_messages_checked"] = len(message_ids)
    out["merged_messages_index_mismatch"] = bad_content
    out["merged_messages_match_found"] = f"{match_ok}/{match_tried}"
    if n_msg != n_fts or bad_content or match_ok != match_tried:
        raise RuntimeError(f"FTS verification failed: {out}")
    return out


def _fts_parity(conn: sqlite3.Connection) -> dict:
    try:
        n_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        n_fts = conn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
        return {"messages": n_msg, "messages_fts": n_fts, "parity": n_msg == n_fts}
    except sqlite3.Error as e:
        return {"error": str(e)}


def _extras(src: sqlite3.Connection, plans: dict[str, TablePlan], docs_dir: Optional[str]) -> dict:
    """Facts about merged rows that point outside chats.db."""
    out: dict[str, Any] = {}
    conv_user = {}
    if "conversations" in plans:
        for r in src.execute("SELECT id, user_email FROM conversations"):
            conv_user[r[0]] = r[1]
    msgs = plans.get("messages")
    if msgs is not None:
        refs = []
        for row in msgs.inserts + msgs.updates:
            raw = row.get("attachments")
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for it in items or []:
                if isinstance(it, dict) and it.get("document_id"):
                    refs.append((conv_user.get(row["conversation_id"]), it["document_id"]))
        out["attachment_refs"] = len(refs)
        if docs_dir:
            missing = [d for (u, d) in refs
                       if not os.path.isdir(os.path.join(docs_dir, email_hash(u) or "_", d))]
            out["attachment_refs_without_file_in_docs_dir"] = len(missing)
    arts = plans.get("artifacts")
    if arts is not None:
        out["sandbox_generated_artifacts"] = sum(
            1 for r in arts.inserts + arts.updates if r.get("source") == "sandbox_generated")
    jobs = plans.get("research_jobs")
    if jobs is not None:
        out["research_jobs_not_terminal"] = sum(
            1 for r in jobs.inserts + jobs.updates
            if (r.get("status") or "").lower() not in TERMINAL_RESEARCH)
    users: dict[str, int] = {}
    convs = plans.get("conversations")
    if convs is not None:
        for r in convs.inserts:
            h = email_hash(r.get("user_email")) or "-"
            users[h] = users.get(h, 0) + 1
    out["new_conversations_per_user_hash"] = users
    return out


def _write(conn: sqlite3.Connection, plans: dict[str, TablePlan], tgt_tables: set,
           src: sqlite3.Connection) -> dict:
    written: dict[str, dict] = {}
    for spec in TABLES:
        p = plans.get(spec.name)
        if p is None or not (p.inserts or p.updates):
            continue
        if spec.name not in tgt_tables:
            ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                              (spec.name,)).fetchone()[0]
            conn.execute(ddl)
        cols = p.cols
        ins = (f'INSERT INTO "{spec.name}" (' + ", ".join(f'"{c}"' for c in cols)
               + ") VALUES (" + ", ".join("?" for _ in cols) + ")")
        for r in p.inserts:
            conn.execute(ins, [r[c] for c in cols])
        nonpk = [c for c in cols if c not in p.pk]
        upd = (f'UPDATE "{spec.name}" SET ' + ", ".join(f'"{c}" = ?' for c in nonpk)
               + " WHERE " + " AND ".join(f'"{c}" = ?' for c in p.pk))
        for r in p.updates:
            cur = conn.execute(upd, [r[c] for c in nonpk] + [r[c] for c in p.pk])
            if cur.rowcount != 1:
                raise RuntimeError(f"{spec.name}: update matched {cur.rowcount} rows")
        written[spec.name] = {"inserted": len(p.inserts), "updated": len(p.updates)}
    return written


def cmd_db(a: argparse.Namespace) -> int:
    since = parse_since(a.since)
    report: dict[str, Any] = {"command": "db", "mode": "apply" if a.apply else "dry-run",
                              "policy": a.on_conflict, "since": a.since,
                              "started_at": utc_stamp()}
    work = a.work_dir or tempfile.mkdtemp(prefix="merge-state-")
    os.makedirs(work, exist_ok=True)
    snap = os.path.join(work, f"source-snapshot-{utc_stamp()}.db")
    report["source_snapshot"] = snapshot_db(a.source, snap)
    if report["source_snapshot"].get("integrity") != "ok":
        report["refused"] = ["source snapshot failed integrity_check"]
        emit(report, a.report)
        return EXIT_REFUSED
    if not os.path.isfile(a.target):
        raise FileNotFoundError(a.target)

    src = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    tgt = sqlite3.connect(a.target, isolation_level=None, timeout=30)
    tgt.execute("PRAGMA busy_timeout=30000")
    tgt.execute("PRAGMA foreign_keys=ON")

    schema, problems, merge_cols = check_schema(src, tgt, a.allow_schema_drift,
                                                a.create_missing_tables)
    report["schema"] = schema
    report["target_fts_before"] = _fts_parity(tgt)
    if problems:
        report["refused"] = problems
        emit(report, a.report)
        return EXIT_REFUSED

    def build() -> tuple[dict[str, TablePlan], list[str]]:
        plans = plan_db(src, tgt, merge_cols, since, a.on_conflict)
        blockers = []
        for p in plans.values():
            if p.conflicts:
                blockers.append(f"{p.name}: {len(p.conflicts)} conflicting rows")
            if p.orphans and not a.skip_orphans:
                blockers.append(f"{p.name}: {len(p.orphans)} orphan rows (pass --skip-orphans)")
        return plans, blockers

    plans, blockers = build()
    report["tables"] = {t: p.summary() for t, p in plans.items()}
    report["conflict_samples"] = {t: p.conflicts[:20] for t, p in plans.items() if p.conflicts}
    report["orphan_samples"] = {t: p.orphans[:20] for t, p in plans.items() if p.orphans}
    report["external"] = _extras(src, plans, a.docs_dir)
    pending = sum(len(p.inserts) + len(p.updates) for p in plans.values())
    report["pending_writes"] = pending
    if blockers:
        report["refused"] = blockers
        emit(report, a.report)
        return EXIT_REFUSED
    if not a.apply or pending == 0:
        report["result"] = "nothing to do" if pending == 0 else "dry run, nothing written"
        emit(report, a.report)
        return EXIT_OK

    # Hold the write lock first, then back the target up through a second,
    # read-only connection: the backup is exactly the state the merge starts
    # from, and the service cannot commit in between. (Backing up from the
    # connection that holds the write transaction returns SQLITE_LOCKED, which
    # Connection.backup retries forever.)
    tgt_tables = set(_user_tables(tgt))
    backup_dir = a.backup_dir or os.path.dirname(os.path.abspath(a.target))
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, f"{os.path.basename(a.target)}.pre-merge-{utc_stamp()}.db")
    tgt.execute("BEGIN IMMEDIATE")
    try:
        plans, blockers = build()  # re-plan under the lock
        if blockers:
            raise RuntimeError(f"target changed between plan and apply: {blockers}")
        reader = sqlite3.connect(f"file:{os.path.abspath(a.target)}?mode=ro", uri=True,
                                 timeout=30)
        dst = sqlite3.connect(backup)
        reader.backup(dst)
        reader.close()
        dst.execute("PRAGMA journal_mode=DELETE")
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        if ok != "ok":
            raise RuntimeError(f"target backup failed integrity_check: {ok}")
        report["target_backup"] = {"path": backup, "bytes": os.path.getsize(backup)}
        report["written"] = _write(tgt, plans, tgt_tables, src)
        fk = tgt.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"foreign_key_check reported {len(fk)} rows")
        merged_ids = [r["id"] for r in plans.get("messages", TablePlan("", [], [])).inserts
                      + plans.get("messages", TablePlan("", [], [])).updates]
        report["fts_after"] = _verify_fts(tgt, merged_ids)
        again, again_block = build()
        left = sum(len(p.inserts) + len(p.updates) for p in again.values())
        if left or again_block:
            raise RuntimeError(f"re-plan after write is not a no-op ({left} pending)")
        report["replan_after_write"] = "no-op"
        tgt.execute("COMMIT")
    except Exception as e:
        tgt.execute("ROLLBACK")
        report["result"] = f"rolled back: {e}"
        emit(report, a.report)
        return EXIT_ERROR
    report["result"] = "merged"
    report["target_counts_after"] = {t: tgt.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                                     for t in plans}
    emit(report, a.report)
    return EXIT_OK


# =============================================================================
# files (user_docs)
# =============================================================================

HASH_RE = re.compile(r"[0-9a-f]{16}")
DOC_RE = re.compile(r"doc_[0-9a-f]{12}")


def _scan_docs(root: str) -> tuple[dict[tuple, list[str]], list[str]]:
    """{(user_hash, doc_id): [filenames]} plus anything that does not have the
    shape document_store writes (reported and never copied: the source tree is
    data from another host)."""
    docs: dict[tuple, list[str]] = {}
    odd: list[str] = []
    if not os.path.isdir(root):
        return docs, odd
    for uh in sorted(os.listdir(root)):
        up = os.path.join(root, uh)
        if not HASH_RE.fullmatch(uh) or os.path.islink(up) or not os.path.isdir(up):
            odd.append(uh)
            continue
        for d in sorted(os.listdir(up)):
            dp = os.path.join(up, d)
            if not DOC_RE.fullmatch(d) or os.path.islink(dp) or not os.path.isdir(dp):
                odd.append(f"{uh}/{d}")
                continue
            names = []
            for fn in sorted(os.listdir(dp)):
                fp = os.path.join(dp, fn)
                if os.path.islink(fp) or not os.path.isfile(fp):
                    odd.append(f"{uh}/{d}/<non-regular>")
                    continue
                names.append(fn)
            docs[(uh, d)] = names
    return docs, odd


def cmd_files(a: argparse.Namespace) -> int:
    since = parse_since(a.since)
    report: dict[str, Any] = {"command": "files", "mode": "apply" if a.apply else "dry-run",
                              "policy": a.on_conflict, "since": a.since}
    src_docs, odd = _scan_docs(a.source_dir)
    tgt_docs, _ = _scan_docs(a.target_dir)
    tgt_owner = {d: uh for (uh, d) in tgt_docs}
    plan_copy, identical, kept, conflicts, out_of_scope = [], 0, 0, [], 0
    for (uh, d), names in sorted(src_docs.items()):
        if since is not None:
            newest = max((os.path.getmtime(os.path.join(a.source_dir, uh, d, n)) for n in names),
                         default=0)
            if _dt.datetime.fromtimestamp(newest, tz=_dt.timezone.utc) < since:
                out_of_scope += 1
                continue
        if d in tgt_owner and tgt_owner[d] != uh:
            conflicts.append({"doc": d, "kind": "doc_id_owned_by_other_user"})
            continue
        if (uh, d) not in tgt_docs:
            plan_copy.append((uh, d, names))
            continue
        tnames = tgt_docs[(uh, d)]
        same = (names == tnames) and all(
            sha256_file(os.path.join(a.source_dir, uh, d, n))
            == sha256_file(os.path.join(a.target_dir, uh, d, n)) for n in names)
        if same:
            identical += 1
        elif a.on_conflict == "keep-target":
            kept += 1
        else:
            conflicts.append({"doc": d, "kind": "content_differs"})
    nbytes = sum(os.path.getsize(os.path.join(a.source_dir, uh, d, n))
                 for uh, d, names in plan_copy for n in names)
    report.update({
        "source_documents": len(src_docs),
        "source_files": sum(len(v) for v in src_docs.values()),
        "target_documents_before": len(tgt_docs),
        "out_of_scope": out_of_scope,
        "identical_skipped": identical,
        "kept_target": kept,
        "copy_documents": len(plan_copy),
        "copy_files": sum(len(n) for _, _, n in plan_copy),
        "copy_bytes": nbytes,
        "users_touched": sorted({uh for uh, _, _ in plan_copy}),
        "conflicts": conflicts,
        "skipped_unexpected_entries": len(odd),
    })
    if conflicts:
        report["refused"] = f"{len(conflicts)} conflicting documents"
        emit(report, a.report)
        return EXIT_REFUSED
    if not a.apply or not plan_copy:
        report["result"] = "nothing to do" if not plan_copy else "dry run, nothing written"
        emit(report, a.report)
        return EXIT_OK
    copied = 0
    # Stage under the target ROOT and rename into place, so a crash never
    # leaves a half-copied document: the service only lists inside a user's
    # own directory (document_store._scan_user_disk), never the root, and
    # the rename stays on one filesystem.
    staging = os.path.join(a.target_dir, ".merge-staging")
    os.makedirs(staging, exist_ok=True)
    for uh, d, names in plan_copy:
        dst_dir = os.path.join(a.target_dir, uh, d)
        user_dir = os.path.join(a.target_dir, uh)
        if not os.path.isdir(user_dir):
            os.makedirs(user_dir)
            os.chmod(user_dir, 0o755)
        stage = tempfile.mkdtemp(prefix=f"{d}.", dir=staging)
        for n in names:
            s, t = os.path.join(a.source_dir, uh, d, n), os.path.join(stage, n)
            shutil.copy2(s, t)
            os.chmod(t, 0o644)
            if sha256_file(s) != sha256_file(t):
                raise RuntimeError(f"copy verification failed for {uh}/{d}")
            copied += 1
        os.chmod(stage, 0o755)
        os.rename(stage, dst_dir)
    try:
        os.rmdir(staging)
    except OSError:
        pass  # left over from an interrupted run: inert, reported as unexpected
    report["copied_files"] = copied
    report["result"] = "copied"
    emit(report, a.report)
    return EXIT_OK


# =============================================================================
# qdrant
# =============================================================================

def _qurl(a: argparse.Namespace) -> str:
    if a.url:
        return a.url.rstrip("/")
    host = os.getenv("QDRANT_HOST", "localhost")
    port = os.getenv("QDRANT_PORT", "6333")
    return f"http://{host}:{port}"


def _q(base: str, method: str, path: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    key = os.getenv("QDRANT_API_KEY")
    if key:
        req.add_header("api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"qdrant {method} {path}: {e.code} {e.read()[:300]!r}") from None


def _collection_info(base: str, coll: str) -> Optional[dict]:
    try:
        return _q(base, "GET", f"/collections/{coll}")["result"]
    except RuntimeError as e:
        if " 404 " in str(e):
            return None
        raise


def _scroll(base: str, coll: str, with_vector: Any = True, flt: Optional[dict] = None,
            payload: Any = True) -> Iterable[dict]:
    offset = None
    while True:
        body: dict[str, Any] = {"limit": 256, "with_payload": payload, "with_vector": with_vector}
        if offset is not None:
            body["offset"] = offset
        if flt:
            body["filter"] = flt
        res = _q(base, "POST", f"/collections/{coll}/points/scroll", body)["result"]
        for p in res["points"]:
            yield p
        offset = res.get("next_page_offset")
        if offset is None:
            return


def cmd_qdrant_export(a: argparse.Namespace) -> int:
    base = _qurl(a)
    info = _collection_info(base, a.collection)
    if info is None:
        raise RuntimeError(f"collection {a.collection} not found at {base}")
    since = parse_since(a.since)
    out = sys.stdout if a.out == "-" else open(a.out, "w")
    n = 0
    try:
        meta = {"_meta": {"collection": a.collection,
                          "vectors": info["config"]["params"]["vectors"],
                          "points_count": info.get("points_count"),
                          "exported_at": utc_stamp(), "since": a.since}}
        out.write(json.dumps(meta) + "\n")
        for p in _scroll(base, a.collection):
            if since is not None:
                d = ts_to_dt((p.get("payload") or {}).get("upload_time"), "iso")
                if d is not None and d < since:
                    continue
            out.write(json.dumps({"id": p["id"], "vector": p.get("vector"),
                                  "payload": p.get("payload") or {}}) + "\n")
            n += 1
    finally:
        if out is not sys.stdout:
            out.close()
    note(f"exported {n} points from {a.collection}")
    return EXIT_OK


def _vec_close(a: Any, b: Any, tol: float = 1e-4) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_vec_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))
    return a == b


def cmd_qdrant(a: argparse.Namespace) -> int:
    base = _qurl(a)
    report: dict[str, Any] = {"command": "qdrant", "mode": "apply" if a.apply else "dry-run",
                              "policy": a.on_conflict, "collection": a.collection}
    with open(a.source) as f:
        first = json.loads(f.readline())
        meta = first.get("_meta") or {}
        points = [json.loads(line) for line in f if line.strip()]
    report["source_points"] = len(points)
    src_vec = meta.get("vectors")
    info = _collection_info(base, a.collection)
    if info is None:
        if not a.create_collection:
            report["refused"] = "target collection missing; pass --create-collection"
            emit(report, a.report)
            return EXIT_REFUSED
        tgt_vec = None
    else:
        tgt_vec = info["config"]["params"]["vectors"]
        report["target_points_before"] = info.get("points_count")
        if src_vec != tgt_vec:
            report["refused"] = f"vector config differs: source {src_vec} target {tgt_vec}"
            emit(report, a.report)
            return EXIT_REFUSED

    def plan() -> tuple[list[dict], dict]:
        stats = {"identical_skipped": 0, "kept_target": 0, "overwrite": 0,
                 "conflicts": [], "documents_indexed_differently": []}
        if info is None:  # collection about to be created: all points are new
            return list(points), stats
        existing: dict[Any, dict] = {}
        ids = [p["id"] for p in points]
        for i in range(0, len(ids), 256):
            res = _q(base, "POST", f"/collections/{a.collection}/points",
                     {"ids": ids[i:i + 256], "with_payload": True, "with_vector": True})
            for p in res["result"]:
                existing[p["id"]] = p
        # A document already indexed under OTHER point ids (re-embedded by a
        # backfill, or merged earlier from a different export) would end up
        # with every chunk twice.
        by_doc: dict[str, set] = {}
        for p in points:
            d = (p.get("payload") or {}).get("document_id")
            if d:
                by_doc.setdefault(d, set()).add(p["id"])
        skip_docs = set()
        docs = sorted(by_doc)
        for i in range(0, len(docs), 64):
            flt = {"must": [{"key": "document_id", "match": {"any": docs[i:i + 64]}}]}
            for tp in _scroll(base, a.collection, with_vector=False, flt=flt,
                              payload=["document_id"]):
                d = (tp.get("payload") or {}).get("document_id")
                if d in by_doc and tp["id"] not in by_doc[d]:
                    skip_docs.add(d)
        todo = []
        for p in points:
            d = (p.get("payload") or {}).get("document_id")
            if d in skip_docs:
                continue
            e = existing.get(p["id"])
            if e is None:
                todo.append(p)
            elif (e.get("payload") or {}) == (p.get("payload") or {}) \
                    and _vec_close(e.get("vector"), p.get("vector")):
                stats["identical_skipped"] += 1
            elif a.on_conflict == "source-wins":
                stats["overwrite"] += 1
                todo.append(p)
            elif a.on_conflict == "keep-target":
                stats["kept_target"] += 1
            else:
                stats["conflicts"].append(str(p["id"]))
        stats["documents_indexed_differently"] = sorted(skip_docs)
        return todo, stats

    todo, stats = plan()
    blocking = list(stats["conflicts"])
    if stats["documents_indexed_differently"] and a.on_conflict == "refuse":
        blocking += stats["documents_indexed_differently"]
    report.update({k: (len(v) if isinstance(v, list) else v) for k, v in stats.items()})
    report["documents_indexed_differently_ids"] = stats["documents_indexed_differently"][:50]
    report["upsert"] = len(todo)
    report["source_documents"] = len({(p.get("payload") or {}).get("document_id") for p in points})
    if blocking:
        report["refused"] = f"{len(blocking)} conflicting points/documents"
        emit(report, a.report)
        return EXIT_REFUSED
    if not a.apply or not todo:
        report["result"] = "nothing to do" if not todo else "dry run, nothing written"
        emit(report, a.report)
        return EXIT_OK
    if info is None:
        _q(base, "PUT", f"/collections/{a.collection}", {"vectors": src_vec})
        report["created_collection"] = True
    for field_name in ("user_email", "conversation_id", "document_id"):
        # Same indexes document_store.ensure_collection creates; idempotent.
        _q(base, "PUT", f"/collections/{a.collection}/index?wait=true",
           {"field_name": field_name, "field_schema": "keyword"})
    for i in range(0, len(todo), 64):
        batch = [{"id": p["id"], "vector": p["vector"], "payload": p.get("payload") or {}}
                 for p in todo[i:i + 64]]
        _q(base, "PUT", f"/collections/{a.collection}/points?wait=true", {"points": batch})
    info = _collection_info(base, a.collection)
    again, again_stats = plan()
    report["target_points_after"] = info.get("points_count") if info else None
    report["replan_after_write"] = "no-op" if not again and not again_stats["conflicts"] \
        else f"{len(again)} still pending"
    report["result"] = "upserted"
    emit(report, a.report)
    return EXIT_OK if not again else EXIT_ERROR


# =============================================================================
# CLI
# =============================================================================

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="backup-API copy of a SQLite DB (OUT '-' = stdout)")
    s.add_argument("src")
    s.add_argument("out")

    d = sub.add_parser("db", help="merge chats.db rows (dry run unless --apply)")
    d.add_argument("--source", required=True, help="source chats.db or snapshot")
    d.add_argument("--target", required=True, help="target chats.db (live path is fine)")
    d.add_argument("--since", help="ISO time; only rows written at/after it (plus their parents)")
    d.add_argument("--on-conflict", choices=POLICIES_DB, default="refuse")
    d.add_argument("--skip-orphans", action="store_true")
    d.add_argument("--allow-schema-drift", action="store_true")
    d.add_argument("--create-missing-tables", action="store_true")
    d.add_argument("--backup-dir", help="where the pre-merge target backup goes "
                                        "(default: beside the target)")
    d.add_argument("--work-dir", help="where the source snapshot goes (default: a temp dir)")
    d.add_argument("--docs-dir", help="source user_docs dir, to check attachment files exist")
    d.add_argument("--report", help="also write the JSON report here")
    d.add_argument("--apply", action="store_true")

    f = sub.add_parser("files", help="copy user_docs trees (dry run unless --apply)")
    f.add_argument("--source-dir", required=True)
    f.add_argument("--target-dir", required=True)
    f.add_argument("--since")
    f.add_argument("--on-conflict", choices=POLICIES_FILES, default="refuse")
    f.add_argument("--report")
    f.add_argument("--apply", action="store_true")

    e = sub.add_parser("qdrant-export", help="scroll a collection to JSONL")
    e.add_argument("--url", help="default http://$QDRANT_HOST:$QDRANT_PORT")
    e.add_argument("--collection", default="user_docs")
    e.add_argument("--since", help="only points whose payload upload_time is at/after it")
    e.add_argument("--out", default="-")

    q = sub.add_parser("qdrant", help="upsert an export (dry run unless --apply)")
    q.add_argument("--source", required=True, help="JSONL from qdrant-export")
    q.add_argument("--url", help="default http://$QDRANT_HOST:$QDRANT_PORT")
    q.add_argument("--collection", default="user_docs")
    q.add_argument("--on-conflict", choices=POLICIES_QDRANT, default="refuse")
    q.add_argument("--create-collection", action="store_true")
    q.add_argument("--report")
    q.add_argument("--apply", action="store_true")

    a = ap.parse_args(argv)
    # Snapshots, backups, exports and reports all hold user data: private by
    # default. Copied user_docs get explicit modes matching the service's.
    os.umask(0o077)
    try:
        if a.cmd == "snapshot":
            info = snapshot_db(a.src, a.out)
            if a.out != "-":
                print(json.dumps(info))
            return EXIT_OK
        return {"db": cmd_db, "files": cmd_files, "qdrant-export": cmd_qdrant_export,
                "qdrant": cmd_qdrant}[a.cmd](a)
    except Exception as exc:  # noqa: BLE001 - one place turns errors into exit 1
        note(f"error: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
