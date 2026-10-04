"""
munin-auth: Email OTP authentication service for the Munin platform.

Endpoints:
    GET   /login       Login page (enter email)
    POST  /login       Submit email, look up user in DB, send OTP
    GET   /verify      OTP entry page
    POST  /verify      Verify OTP, set session cookie, redirect
    GET   /auth/check  Caddy forward-auth: 200 + headers or 401
    GET   /auth/me     Current user info (JSON: email, name)
    PATCH /auth/me     Update display name (JSON body: {name})
    POST  /logout      Clear session cookie
    GET   /health      Health check

User identity model:
    users           one row per person (name, role, research_group, username)
    user_emails     one row per email; many emails can point at the same user
                    (is_primary flags the canonical email for display)
    groups          first-class research groups (slug + display_name)

The legacy whitelist.csv is treated as a *seed*. On startup, any rows
in the CSV whose email is not yet present in user_emails are imported
(additive only, never deletes). After first boot the database is the
authoritative source; the CSV remains as a backup / manual-edit path.
"""

import asyncio
import csv
import logging
import os
import secrets
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import aiosmtplib
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import TimestampSigner, BadSignature
from jinja2 import Environment, FileSystemLoader

# ── Configuration ────────────────────────────────────────────────────────────

SECRET_KEY = os.environ["SECRET_KEY"]
# Where this instance lives. Compose requires MUNIN_DOMAIN; the session cookie,
# the CORS origins, the post-login fallback and the default sender/support
# addresses all derive from it. Until 2026-10 each was a muninai.org literal.
MUNIN_DOMAIN = os.environ.get("MUNIN_DOMAIN", "").strip()
SITE_URL = (os.environ.get("MUNIN_SITE_URL", "").strip().rstrip("/")
            or (f"https://{MUNIN_DOMAIN}" if MUNIN_DOMAIN else ""))

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SENDER = (os.environ.get("SMTP_SENDER")
               or (f"noreply@{MUNIN_DOMAIN}" if MUNIN_DOMAIN else ""))
# The relay (GoDaddy's smtpout.secureserver.net:587) intermittently drops the
# connection on connect; a retry lands on a healthy node. Failures are fast,
# so a few attempts with short backoff cost little.
SMTP_MAX_ATTEMPTS = int(os.environ.get("SMTP_MAX_ATTEMPTS", "4"))
SMTP_RETRY_BASE_DELAY = float(os.environ.get("SMTP_RETRY_BASE_DELAY", "0.5"))
SMTP_TIMEOUT = float(os.environ.get("SMTP_TIMEOUT", "20"))

# Development / demo login. When on, the OTP is written to the service log
# instead of being emailed, and SMTP is not contacted at all.
#
# Why this exists: OTP delivery was SMTP-only, so bringing Munin up on a fresh
# machine required working mail credentials before you could log in even once.
# That made "clone it and try it" impossible without a mail relay, which is a
# hard stop for anyone evaluating the system.
#
# It is OFF by default and announces itself loudly at startup, because leaving
# it on in a real deployment means anyone who can read the logs can log in as
# anyone on the whitelist.
DEV_ECHO_OTP = os.environ.get("AUTH_DEV_ECHO_OTP", "").strip().lower() in (
    "1", "true", "yes", "on")
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", "2592000"))  # 30 days
OTP_EXPIRY = int(os.environ.get("OTP_EXPIRY", "600"))  # 10 minutes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("munin-auth")
# Unset: `.<MUNIN_DOMAIN>`, so one login covers every subdomain. Set but empty:
# a host-only cookie, which is what a single host on localhost needs.
_cookie_domain = os.environ.get("COOKIE_DOMAIN")
if _cookie_domain is None:
    _cookie_domain = f".{MUNIN_DOMAIN}" if MUNIN_DOMAIN else ""
COOKIE_DOMAIN = _cookie_domain.strip() or None
WHITELIST_PATH = Path(os.environ.get("WHITELIST_PATH", "/data/whitelist.csv"))
CONTRIBUTORS_PATH = Path(os.environ.get("CONTRIBUTORS_PATH", "/data/contributors.yml"))
CONTRIBUTORS_SYNC_TOKEN = os.environ.get("CONTRIBUTORS_SYNC_TOKEN", "")
# P1 #11 commit 4: token the tusd hook-service uses to check a user's
# KB-contribution eligibility on every pre-create. Same shape as
# CONTRIBUTORS_SYNC_TOKEN but scoped to a different caller.
KB_GATE_TOKEN = os.environ.get("KB_GATE_TOKEN", "")
DB_PATH = Path(os.environ.get("DB_PATH", "/data/db/sessions.db"))

def is_own_url(url: str | None) -> bool:
    """True for an absolute http(s) URL on this instance: MUNIN_DOMAIN or one
    of its subdomains, or the host SITE_URL names. Anything else is refused as
    a post-login redirect, which used to accept any URL starting with "http"
    (an open redirect)."""
    if not url:
        return False
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        return False
    own = {MUNIN_DOMAIN.lower()} if MUNIN_DOMAIN else set()
    if SITE_URL:
        own.add((urlparse(SITE_URL).hostname or "").lower())
    own.discard("")
    return any(host == d or host.endswith("." + d) for d in own)


# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"https://{h}{MUNIN_DOMAIN}" for h in
        ("", "chat.", "search.", "research.", "docs.", "upload.")
    ] if MUNIN_DOMAIN else [],
    allow_credentials=True,
    allow_methods=["GET", "PATCH", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)

signer = TimestampSigner(SECRET_KEY)
# Resolve templates relative to THIS FILE, not the working directory. The
# container's WORKDIR made a bare "templates" work in production, but it made
# every caller cwd-dependent: rendering worked when run from frontend/auth and
# raised TemplateNotFound from anywhere else, which is how it surfaced in CI
# (pytest runs from the repo root).
templates = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "templates")),
    autoescape=True,
)
# Templates link back to the instance's landing page and load its logo.
templates.globals["site_url"] = SITE_URL

# In-memory stores (transient state only — user data lives in SQLite)
otp_store: dict[str, dict] = {}  # email -> {code, expires, attempts}
rate_send: dict[str, list[float]] = {}  # email -> [timestamps]
rate_verify: dict[str, list[float]] = {}  # email -> [timestamps]
rate_support: dict[str, list[float]] = {}  # email -> [timestamps]


def _handle_sighup(*_):
    # Re-run the additive CSV seed. Existing DB rows are not touched;
    # only rows whose email is absent from user_emails are imported.
    seed_users_from_whitelist()


signal.signal(signal.SIGHUP, _handle_sighup)


# ── Database ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Enable FK enforcement so user_emails rows cascade-delete with users.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS display_names (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            email TEXT PRIMARY KEY,
            full_name TEXT,
            nickname TEXT,
            avatar TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT,
            role TEXT NOT NULL CHECK(role IN ('user', 'group_leader', 'admin')),
            research_group TEXT REFERENCES groups(slug) ON DELETE SET NULL,
            username TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_emails (
            email TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            is_primary INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_emails_user ON user_emails(user_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            slug TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Many-to-many membership. A user can belong to several groups;
    # users.research_group remains the *primary* group (single-valued
    # attribution: X-Munin-Group, contributors.yaml). This table is the
    # source of truth for the full membership list.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_slug TEXT NOT NULL REFERENCES groups(slug) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            added_at TEXT NOT NULL,
            PRIMARY KEY (group_slug, user_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _migration_applied(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
    ).fetchone() is not None


def _mark_migration(conn: sqlite3.Connection, name: str):
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def migrate_split_name_v1() -> dict:
    """One-shot split of users.name into users.first_name + users.last_name.

    Reason: the original schema stored a single `name` field per user.
    The admin panel and the chat header both needed two fields once
    multiple institutes joined; one field reads ambiguously across
    cultures and surname collisions. This migration:

      1. Adds first_name + last_name columns (NULLable so the ALTER
         doesn't reject existing rows; tightened to NOT NULL on
         first_name by app-level validation).
      2. Backfills first_name + last_name from the existing single
         `name` by splitting on the first whitespace token.
      3. Overlays the new whitelist.csv (email, first_name,
         last_name, role) on top -- whitelist wins for any user whose
         primary email is present, since the user judged the
         whitelist a more reliable source than the post-merge DB
         (only one row was created via the admin panel; everything
         else came from the same seed). The overwrite is logged so
         the one admin-panel entry can be spot-checked afterward.
      4. Drops the old `name` column (SQLite >= 3.35).

    Gated by schema_migrations.name = 'split_name_v1'.

    Returns a small summary dict for logging on startup.
    """
    summary = {
        "already_run": False,
        "rows_backfilled": 0,
        "rows_overwritten_from_whitelist": 0,
        "rows_missing_from_whitelist": 0,
        "name_column_dropped": False,
    }
    conn = get_db()
    try:
        if _migration_applied(conn, "split_name_v1"):
            summary["already_run"] = True
            return summary

        # ── Step 1: add the two new columns if absent.
        if not _column_exists(conn, "users", "first_name"):
            conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        if not _column_exists(conn, "users", "last_name"):
            conn.execute("ALTER TABLE users ADD COLUMN last_name TEXT")

        # ── Step 2: split existing `name` on first whitespace.
        if _column_exists(conn, "users", "name"):
            rows = conn.execute(
                "SELECT id, name FROM users WHERE first_name IS NULL OR first_name = ''"
            ).fetchall()
            for r in rows:
                raw = (r["name"] or "").strip()
                if not raw:
                    continue
                first, _, last = raw.partition(" ")
                conn.execute(
                    "UPDATE users SET first_name = ?, last_name = ? WHERE id = ?",
                    (first.strip(), last.strip() or None, r["id"]),
                )
                summary["rows_backfilled"] += 1

        # ── Step 3: overlay new-shape whitelist (whitelist wins).
        if WHITELIST_PATH.exists():
            with open(WHITELIST_PATH, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    email = (row.get("email") or "").strip().lower()
                    first = (row.get("first_name") or "").strip()
                    last = (row.get("last_name") or "").strip() or None
                    if not email or not first:
                        continue
                    user_row = conn.execute(
                        "SELECT u.id FROM users u "
                        "JOIN user_emails ue ON ue.user_id = u.id "
                        "WHERE ue.email = ?",
                        (email,),
                    ).fetchone()
                    if user_row:
                        conn.execute(
                            "UPDATE users SET first_name = ?, last_name = ?, "
                            "updated_at = ? WHERE id = ?",
                            (first, last, datetime.now(timezone.utc).isoformat(),
                             user_row["id"]),
                        )
                        summary["rows_overwritten_from_whitelist"] += 1
                    else:
                        summary["rows_missing_from_whitelist"] += 1

        # ── Step 4: drop the old name column. SQLite added DROP COLUMN
        # support in 3.35 (2021-03). The retrieval container's Python
        # ships 3.40+, but we guard with a try so a corrupted index
        # error (some rows still have NULL first_name) doesn't kill
        # startup; the column can stay around harmlessly if drop fails.
        if _column_exists(conn, "users", "name"):
            try:
                conn.execute("ALTER TABLE users DROP COLUMN name")
                summary["name_column_dropped"] = True
            except sqlite3.OperationalError:
                # Index or trigger references it; leave the column in
                # place. Reads will continue to use first_name.
                pass

        _mark_migration(conn, "split_name_v1")
        conn.commit()
    finally:
        conn.close()
    return summary


def migrate_group_members_v1() -> dict:
    """Backfill the group_members join table from users.research_group.

    Reason: membership became many-to-many (a user can belong to
    several labs). research_group stays as the user's *primary* group
    (single-valued attribution: X-Munin-Group, contributors.yaml);
    group_members is the source of truth for the full membership list.
    This one-shot copies every existing (research_group, user) pair into
    the join table. Gated by schema_migrations.name = 'group_members_v1'.
    """
    summary = {"already_run": False, "rows_backfilled": 0}
    conn = get_db()
    try:
        if _migration_applied(conn, "group_members_v1"):
            summary["already_run"] = True
            return summary
        rows = conn.execute(
            "SELECT id, research_group FROM users "
            "WHERE research_group IS NOT NULL AND research_group != ''"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO group_members (group_slug, user_id, added_at) "
                "VALUES (?, ?, ?)",
                (r["research_group"], r["id"], now),
            )
            summary["rows_backfilled"] += 1
        _mark_migration(conn, "group_members_v1")
        conn.commit()
    finally:
        conn.close()
    return summary


# ── User lookup + mutation helpers ────────────────────────────────────────

def lookup_user(email: str) -> dict | None:
    """Resolve an email (any alias) to its user. Returns None if not found."""
    email = email.strip().lower()
    conn = get_db()
    row = conn.execute(
        """
        SELECT u.id, u.first_name, u.last_name, u.role, u.research_group, u.username
          FROM users u
          JOIN user_emails ue ON ue.user_id = u.id
         WHERE ue.email = ?
        """,
        (email,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    emails = [
        r["email"]
        for r in conn.execute(
            "SELECT email FROM user_emails WHERE user_id = ? ORDER BY is_primary DESC, email ASC",
            (row["id"],),
        ).fetchall()
    ]
    primary_row = conn.execute(
        "SELECT email FROM user_emails WHERE user_id = ? AND is_primary = 1",
        (row["id"],),
    ).fetchone()
    conn.close()
    return {
        "id": row["id"],
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "name": _compose_name(row["first_name"], row["last_name"]),
        "role": row["role"],
        "group": row["research_group"],
        "username": row["username"],
        "emails": emails,
        "primary_email": primary_row["email"] if primary_row else (emails[0] if emails else email),
    }


def _compose_name(first: str | None, last: str | None) -> str:
    """Render a single display name from the first/last pair. Used in
    API response payloads so downstream services (retrieval's
    X-Munin-Name header, session greetings, the chat UI's left-rail
    user pill) keep working unchanged. Trims internal whitespace so a
    missing last_name doesn't render as "Bernd " with a trailing
    space."""
    f = (first or "").strip()
    l = (last or "").strip()
    if f and l:
        return f"{f} {l}"
    return f or l or ""


def _insert_user(conn: sqlite3.Connection, first_name: str,
                 last_name: str | None, role: str,
                 research_group: str | None = None,
                 username: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO users (first_name, last_name, role, research_group, "
        "username, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (first_name, last_name, role, research_group, username, now, now),
    )
    user_id = cur.lastrowid
    # The primary group is always also a membership; keep the join table
    # consistent so member counts and the Groups page see this user.
    if research_group:
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_slug, user_id, added_at) "
            "VALUES (?, ?, ?)",
            (research_group, user_id, now),
        )
    return user_id


def _attach_email(conn: sqlite3.Connection, user_id: int, email: str, is_primary: bool):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO user_emails (email, user_id, is_primary, added_at) VALUES (?, ?, ?, ?)",
        (email.strip().lower(), user_id, 1 if is_primary else 0, now),
    )


def seed_users_from_whitelist() -> dict:
    """Additively import whitelist.csv rows whose email is absent from user_emails.

    Returns a small summary dict for logging. Never deletes; never modifies
    existing rows. Intended to run on startup and on SIGHUP.
    """
    if not WHITELIST_PATH.exists():
        return {"imported": 0, "skipped": 0, "missing_csv": True}

    imported = 0
    skipped = 0
    conn = get_db()
    try:
        with open(WHITELIST_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = (row.get("email") or "").strip().lower()
                if not email:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM user_emails WHERE email = ?", (email,)
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                first = (row.get("first_name") or "").strip()
                last = (row.get("last_name") or "").strip() or None
                # Legacy single-`name` fallback in case the CSV
                # hasn't been migrated to the two-field shape yet.
                if not first:
                    legacy = (row.get("name") or "").strip()
                    if legacy:
                        first, _, rest = legacy.partition(" ")
                        last = rest.strip() or last
                if not first:
                    first = email
                role = (row.get("role") or "").strip() or "user"
                if role not in ("user", "group_leader", "admin"):
                    role = "user"
                user_id = _insert_user(
                    conn,
                    first_name=first,
                    last_name=last,
                    role=role,
                )
                _attach_email(conn, user_id, email, is_primary=True)
                imported += 1
        conn.commit()
    finally:
        conn.close()
    return {"imported": imported, "skipped": skipped, "missing_csv": False}


def count_users() -> int:
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    conn.close()
    return row["n"] if row else 0


def _upsert_group(conn: sqlite3.Connection, slug: str, display_name: str):
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT slug FROM groups WHERE slug = ?", (slug,)
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO groups (slug, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (slug, display_name, now, now),
    )


def seed_contributors_from_yaml() -> dict:
    """One-shot import of contributors.yml into users + groups.

    Runs only once (gated by schema_migrations). For each YAML entry:
      - Ensure the research_group exists in `groups`.
      - For every email in the entry, look it up in user_emails.
        * If found, upgrade that user: role -> 'group_leader' (if
          currently 'user'), set research_group + username if unset.
          Attach any additional emails from the same entry to the
          same user.
        * If no email matches, create a new group_leader user.
      - Attribution metadata (research_group, username) is set only
        when currently empty, so admin edits made before this
        migration are never overwritten.
    """
    import yaml  # local import keeps cold-start cheap

    summary = {"created": 0, "promoted": 0, "groups": 0,
               "skipped_existing": 0, "missing_yaml": False, "already_run": False}

    conn = get_db()
    try:
        if _migration_applied(conn, "seed_contributors_v1"):
            summary["already_run"] = True
            return summary
        if not CONTRIBUTORS_PATH.exists():
            # Don't mark the migration applied — a later boot with the
            # YAML present should still be able to run the seed.
            summary["missing_yaml"] = True
            return summary

        try:
            with open(CONTRIBUTORS_PATH, encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            summary["missing_yaml"] = True
            return summary

        for entry in doc.get("contributors", []) or []:
            emails: list[str] = []
            single = entry.get("email")
            if isinstance(single, str) and single.strip():
                emails.append(single.strip().lower())
            listed = entry.get("emails")
            if isinstance(listed, list):
                for a in listed:
                    if isinstance(a, str) and a.strip():
                        emails.append(a.strip().lower())
            if not emails:
                continue

            group_slug = (entry.get("research_group") or "").strip() or None
            group_display = (entry.get("research_group_display_name") or "").strip()
            username = (entry.get("username") or "").strip() or None
            display_name = (entry.get("display_name") or "").strip()

            if group_slug and group_display:
                pre = conn.execute(
                    "SELECT slug FROM groups WHERE slug = ?", (group_slug,)
                ).fetchone()
                _upsert_group(conn, group_slug, group_display)
                if not pre:
                    summary["groups"] += 1

            # Find an existing user via any of the entry's emails.
            existing_user_id: int | None = None
            for em in emails:
                row = conn.execute(
                    "SELECT user_id FROM user_emails WHERE email = ?", (em,)
                ).fetchone()
                if row:
                    existing_user_id = row["user_id"]
                    break

            if existing_user_id is not None:
                # Promote + fill missing fields. Never overwrite existing
                # role-other-than-user or pre-set username/group.
                cur = conn.execute(
                    "SELECT role, research_group, username FROM users WHERE id = ?",
                    (existing_user_id,),
                ).fetchone()
                updates: list[tuple[str, object]] = []
                if cur["role"] == "user":
                    updates.append(("role", "group_leader"))
                if cur["research_group"] is None and group_slug:
                    updates.append(("research_group", group_slug))
                if cur["username"] is None and username:
                    updates.append(("username", username))
                if updates:
                    set_clause = ", ".join(f"{c} = ?" for c, _ in updates) + ", updated_at = ?"
                    params = [v for _, v in updates] + [
                        datetime.now(timezone.utc).isoformat(), existing_user_id,
                    ]
                    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", params)
                    summary["promoted"] += 1
                else:
                    summary["skipped_existing"] += 1

                # Attach any of the entry's emails that aren't already on this user.
                for em in emails:
                    er = conn.execute(
                        "SELECT user_id FROM user_emails WHERE email = ?", (em,)
                    ).fetchone()
                    if not er:
                        _attach_email(conn, existing_user_id, em, is_primary=False)
            else:
                # Brand new user from this YAML entry. The contributors
                # YAML carries a single `display_name`; we split on
                # whitespace to populate first/last, falling back to
                # the primary email when the display name is empty.
                raw_name = (display_name or emails[0]).strip()
                first, _, last = raw_name.partition(" ")
                new_id = _insert_user(
                    conn,
                    first_name=first.strip() or emails[0],
                    last_name=last.strip() or None,
                    role="group_leader",
                    research_group=group_slug,
                    username=username,
                )
                _attach_email(conn, new_id, emails[0], is_primary=True)
                for em in emails[1:]:
                    _attach_email(conn, new_id, em, is_primary=False)
                summary["created"] += 1

        _mark_migration(conn, "seed_contributors_v1")
        conn.commit()
    finally:
        conn.close()
    return summary


def emit_contributors_yaml() -> str:
    """Regenerate cluster-compatible contributors.yml from the DB.

    Emits one entry per user that has a research_group set and is
    eligible to contribute (role in group_leader / admin). Users
    with one email use the `email:` form; users with multiple emails
    use the `emails:` form (preserving the original schema).
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.id, u.first_name, u.last_name, u.username,
               u.research_group AS slug, g.display_name AS group_display
          FROM users u
          JOIN groups g ON g.slug = u.research_group
         WHERE u.role IN ('group_leader', 'admin')
           AND u.research_group IS NOT NULL
         ORDER BY g.display_name, LOWER(u.last_name), LOWER(u.first_name)
        """
    ).fetchall()

    lines: list[str] = []
    lines.append("# Auto-generated by munin-auth /admin/contributors.yaml.")
    lines.append("# DO NOT edit by hand: the auth DB is the source of truth and")
    lines.append("# the cluster overwrites this file on the next sync.")
    lines.append(f"# Generated at {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("contributors:")

    if not rows:
        lines.append("  []")
        conn.close()
        return "\n".join(lines) + "\n"

    for row in rows:
        emails = [
            r["email"]
            for r in conn.execute(
                "SELECT email FROM user_emails WHERE user_id = ? "
                "ORDER BY is_primary DESC, email ASC",
                (row["id"],),
            ).fetchall()
        ]
        if not emails:
            continue
        if len(emails) == 1:
            lines.append(f"  - email: {emails[0]}")
        else:
            lines.append("  - emails:")
            for em in emails:
                lines.append(f"      - {em}")
        if row["username"]:
            lines.append(f"    username: {row['username']}")
        lines.append(
            f"    display_name: {_yaml_str(_compose_name(row['first_name'], row['last_name']))}"
        )
        lines.append(f"    research_group: {row['slug']}")
        lines.append(f"    research_group_display_name: {_yaml_str(row['group_display'])}")
    conn.close()
    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """Minimal YAML scalar quoting: quote if it contains a colon or starts with
    a character that would otherwise be parsed as something special."""
    if not s:
        return '""'
    if any(ch in s for ch in (":", "#", "{", "}", "[", "]", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _header_safe(value: str) -> str:
    """Strip non-ASCII so a value can ride in a forward-auth header.

    Starlette encodes response headers as latin-1, so a name outside that
    range ("Lukasz" with a slashed L, any CJK name) raises inside the
    Response constructor and 500s /auth/check, which fails forward-auth
    and locks the user out of every protected route. Latin-1-representable
    accents survived this far but then died one hop later, where httpx
    encodes request headers as ASCII (see gateway header_safe()). Strip to
    the intersection here so neither boundary can fire.

    Display fidelity is unaffected: the UI reads names from /auth/me and
    /api/profile as JSON, which is UTF-8 and carries the real spelling.
    """
    return value.encode("ascii", "ignore").decode("ascii")


def get_display_name(email: str) -> str | None:
    conn = get_db()
    row = conn.execute("SELECT name FROM display_names WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row["name"] if row else None


def set_display_name(email: str, name: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO display_names (email, name) VALUES (?, ?) ON CONFLICT(email) DO UPDATE SET name = ?",
        (email, name, name),
    )
    conn.commit()
    conn.close()


def get_user_profile(email: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT full_name, nickname, avatar FROM user_profiles WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"full_name": row["full_name"], "nickname": row["nickname"], "avatar": row["avatar"]}


def set_user_profile(email: str, full_name: str | None, nickname: str | None, avatar: str | None):
    conn = get_db()
    existing = conn.execute("SELECT email FROM user_profiles WHERE email = ?", (email,)).fetchone()
    if existing:
        updates = []
        params = []
        if full_name is not None:
            updates.append("full_name = ?")
            params.append(full_name)
        if nickname is not None:
            updates.append("nickname = ?")
            params.append(nickname)
        if avatar is not None:
            updates.append("avatar = ?")
            params.append(avatar)
        if updates:
            params.append(email)
            conn.execute(f"UPDATE user_profiles SET {', '.join(updates)} WHERE email = ?", params)
    else:
        conn.execute(
            "INSERT INTO user_profiles (email, full_name, nickname, avatar) VALUES (?, ?, ?, ?)",
            (email, full_name, nickname, avatar),
        )
    conn.commit()
    conn.close()


def create_session(email: str, name: str) -> str:
    """Create a new session and return the signed token."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=SESSION_MAX_AGE)
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (token, email, name, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, email, name, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    conn.close()
    return signer.sign(token).decode()


def validate_session(signed_token: str) -> dict | None:
    """Validate a signed session token. Returns {email, name} or None."""
    try:
        token = signer.unsign(signed_token, max_age=SESSION_MAX_AGE).decode()
    except BadSignature:
        return None

    conn = get_db()
    row = conn.execute("SELECT email, name, expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()

    if not row:
        return None

    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return None

    return {"email": row["email"], "name": row["name"]}


def delete_session(signed_token: str):
    """Delete a session from the database."""
    try:
        token = signer.unsign(signed_token, max_age=SESSION_MAX_AGE).decode()
    except BadSignature:
        return
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Rate Limiting ────────────────────────────────────────────────────────────

def check_rate(store: dict[str, list[float]], key: str, limit: int, window: int = 900) -> bool:
    """Return True if under the rate limit."""
    now = time.time()
    timestamps = store.get(key, [])
    timestamps = [t for t in timestamps if now - t < window]
    store[key] = timestamps
    return len(timestamps) < limit


def record_rate(store: dict[str, list[float]], key: str):
    store.setdefault(key, []).append(time.time())


# ── OTP ──────────────────────────────────────────────────────────────────────

def generate_otp(email: str) -> str:
    """Generate and store a 6-digit OTP for the given email."""
    code = f"{secrets.randbelow(1000000):06d}"
    otp_store[email] = {
        "code": code,
        "expires": time.time() + OTP_EXPIRY,
        "attempts": 0,
    }
    return code


def verify_otp(email: str, code: str) -> tuple[bool, str]:
    """Verify an OTP. Returns (success, error_message)."""
    entry = otp_store.get(email)
    if not entry:
        return False, "No code found. Please request a new one."
    if time.time() > entry["expires"]:
        del otp_store[email]
        return False, "Code expired. Please request a new one."
    if entry["code"] != code.strip():
        entry["attempts"] += 1
        if entry["attempts"] >= 5:
            del otp_store[email]
            return False, "Too many failed attempts. Please request a new code."
        return False, "Invalid code. Please try again."
    del otp_store[email]
    return True, ""


# ── Email ────────────────────────────────────────────────────────────────────

async def send_otp_email(email: str, code: str, name: str):
    """Send OTP code via SMTP, or log it when AUTH_DEV_ECHO_OTP is set.

    The dev branch returns BEFORE any SMTP work, so a host with no mail relay
    (and no credentials configured) can still complete a login.
    """
    if DEV_ECHO_OTP:
        log.warning(
            "AUTH_DEV_ECHO_OTP is on: login code for %s is %s "
            "(not emailed; unset AUTH_DEV_ECHO_OTP for real delivery)",
            email, code)
        return

    tmpl = templates.get_template("email_otp.html")
    html_body = tmpl.render(code=code, name=name, expiry_minutes=OTP_EXPIRY // 60)

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_SENDER
    msg["To"] = email
    msg["Subject"] = f"Munin login code: {code}"
    msg.attach(MIMEText(f"Your Munin login code is: {code}\nIt expires in {OTP_EXPIRY // 60} minutes.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # Retry transient SMTP failures. The relay drops ~40% of connects (fast,
    # ~0.02s), so without this a single attempt fails roughly that often and
    # the user sees "Failed to send email" until they happen to retry. Four
    # independent attempts take that to a fraction of a percent.
    last_exc: Exception | None = None
    for attempt in range(1, SMTP_MAX_ATTEMPTS + 1):
        try:
            await aiosmtplib.send(
                msg,
                hostname=SMTP_HOST,
                port=SMTP_PORT,
                username=SMTP_USERNAME,
                password=SMTP_PASSWORD,
                start_tls=True,
                timeout=SMTP_TIMEOUT,
            )
            if attempt > 1:
                log.info("OTP email to %s sent on attempt %d/%d",
                         email, attempt, SMTP_MAX_ATTEMPTS)
            return
        except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as e:
            last_exc = e
            log.warning("OTP email to %s failed (attempt %d/%d): %s: %s",
                        email, attempt, SMTP_MAX_ATTEMPTS, type(e).__name__, e)
            if attempt < SMTP_MAX_ATTEMPTS:
                await asyncio.sleep(SMTP_RETRY_BASE_DELAY * attempt)
    assert last_exc is not None
    raise last_exc


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    if DEV_ECHO_OTP:
        # Loud on every boot, on purpose. This is the difference between a
        # demo instance and one where reading the logs is enough to sign in
        # as any whitelisted user.
        log.warning("=" * 72)
        log.warning("AUTH_DEV_ECHO_OTP IS ENABLED - login codes are written to")
        log.warning("this log instead of being emailed. Development and demo")
        log.warning("use only. Do NOT run a real deployment like this.")
        log.warning("=" * 72)
    init_db()
    migrate_split_name_v1()
    migrate_group_members_v1()
    seed_users_from_whitelist()
    seed_contributors_from_yaml()


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, redirect: str = ""):
    tmpl = templates.get_template("login.html")
    return tmpl.render(error="", redirect=redirect)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, email: str = Form(...), redirect: str = Form("")):
    email = email.strip().lower()
    tmpl = templates.get_template("login.html")

    user = lookup_user(email)
    if not user:
        return tmpl.render(error="This email is not authorized.", redirect=redirect)

    if not check_rate(rate_send, email, limit=3):
        return tmpl.render(error="Too many code requests. Please wait 15 minutes.", redirect=redirect)

    code = generate_otp(email)
    record_rate(rate_send, email)

    try:
        await send_otp_email(email, code, user["name"])
    except Exception as e:
        log.error("OTP send ultimately failed for %s after %d attempts: %s: %s",
                  email, SMTP_MAX_ATTEMPTS, type(e).__name__, e)
        return tmpl.render(error="Failed to send email. Please try again.", redirect=redirect)

    query = {"email": email}
    if redirect:
        query["redirect"] = redirect
    return RedirectResponse(url=f"/verify?{urlencode(query)}", status_code=303)


@app.get("/verify", response_class=HTMLResponse)
async def verify_page(email: str = "", redirect: str = ""):
    tmpl = templates.get_template("verify.html")
    return tmpl.render(email=email, error="", redirect=redirect)


@app.post("/verify")
async def verify_submit(
    email: str = Form(...),
    code: str = Form(...),
    redirect: str = Form(""),
):
    email = email.strip().lower()
    tmpl = templates.get_template("verify.html")

    if not check_rate(rate_verify, email, limit=5):
        return HTMLResponse(tmpl.render(
            email=email,
            error="Too many failed attempts. Please wait 15 minutes.",
            redirect=redirect,
        ))

    success, error_msg = verify_otp(email, code)

    if not success:
        record_rate(rate_verify, email)
        return HTMLResponse(tmpl.render(email=email, error=error_msg, redirect=redirect))

    user = lookup_user(email)
    name = user["name"] if user else email
    signed_token = create_session(email, name)

    dest = redirect if is_own_url(redirect) else (SITE_URL or "/")
    response = RedirectResponse(url=dest, status_code=303)
    response.set_cookie(
        key="munin_session",
        value=signed_token,
        domain=COOKIE_DOMAIN,
        max_age=SESSION_MAX_AGE,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/auth/check")
async def auth_check(request: Request):
    cookie = request.cookies.get("munin_session")
    if not cookie:
        return Response(status_code=401)

    session = validate_session(cookie)
    if not session:
        return Response(status_code=401)

    profile = get_user_profile(session["email"]) or {}
    name = (
        profile.get("nickname")
        or profile.get("full_name")
        or get_display_name(session["email"])
        or session["name"]
    )
    user = lookup_user(session["email"])
    role = user["role"] if user else "user"
    headers = {
        "X-Munin-Email": _header_safe(session["email"]),
        "X-Munin-Name": _header_safe(name),
        "X-Munin-Role": _header_safe(role),
    }
    if user and user["group"]:
        headers["X-Munin-Group"] = _header_safe(user["group"])
    return Response(status_code=200, headers=headers)


@app.get("/auth/me")
async def auth_me(request: Request):
    cookie = request.cookies.get("munin_session")
    if not cookie:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    session = validate_session(cookie)
    if not session:
        return JSONResponse({"error": "invalid session"}, status_code=401)

    profile = get_user_profile(session["email"]) or {}
    # Greeting name priority: nickname > full_name > display_name (legacy) > whitelist name
    greeting_name = (
        profile.get("nickname")
        or profile.get("full_name")
        or get_display_name(session["email"])
        or session["name"]
    )
    return JSONResponse({
        "email": session["email"],
        "name": greeting_name,
        "full_name": profile.get("full_name") or session["name"],
        "nickname": profile.get("nickname") or "",
        "avatar": profile.get("avatar") or "",
    })


@app.patch("/auth/me")
async def auth_me_update(request: Request):
    cookie = request.cookies.get("munin_session")
    if not cookie:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    session = validate_session(cookie)
    if not session:
        return JSONResponse({"error": "invalid session"}, status_code=401)

    body = await request.json()
    full_name = body.get("full_name")
    nickname = body.get("nickname")
    avatar = body.get("avatar")

    # Validate lengths
    if full_name is not None:
        full_name = full_name.strip()
        if len(full_name) > 100:
            return JSONResponse({"error": "full_name must be under 100 characters"}, status_code=400)
    if nickname is not None:
        nickname = nickname.strip()
        if len(nickname) > 50:
            return JSONResponse({"error": "nickname must be under 50 characters"}, status_code=400)
    if avatar is not None and len(avatar) > 200_000:
        return JSONResponse({"error": "avatar too large"}, status_code=400)

    set_user_profile(session["email"], full_name, nickname, avatar)

    # Return updated profile
    profile = get_user_profile(session["email"]) or {}
    greeting_name = (
        profile.get("nickname")
        or profile.get("full_name")
        or get_display_name(session["email"])
        or session["name"]
    )
    return JSONResponse({
        "email": session["email"],
        "name": greeting_name,
        "full_name": profile.get("full_name") or session["name"],
        "nickname": profile.get("nickname") or "",
        "avatar": profile.get("avatar") or "",
    })


@app.post("/logout")
async def logout(request: Request):
    cookie = request.cookies.get("munin_session")
    if cookie:
        delete_session(cookie)

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="munin_session", domain=COOKIE_DOMAIN)
    return response


# ── Support Contact ──────────────────────────────────────────────────────

SUPPORT_EMAIL = (os.environ.get("SUPPORT_EMAIL")
                 or (f"support@{MUNIN_DOMAIN}" if MUNIN_DOMAIN else ""))
SUPPORT_RATE_LIMIT = 3  # max 3 support messages per hour

@app.post("/support/contact")
async def support_contact(request: Request):
    cookie = request.cookies.get("munin_session")
    if not cookie:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    session = validate_session(cookie)
    if not session:
        return JSONResponse({"error": "Invalid session"}, status_code=401)

    sender_email = session["email"]
    sender_name = session["name"]

    # Rate limit: 3 per hour
    now = time.time()
    timestamps = rate_support.get(sender_email, [])
    timestamps = [t for t in timestamps if now - t < 3600]
    rate_support[sender_email] = timestamps
    if len(timestamps) >= SUPPORT_RATE_LIMIT:
        return JSONResponse({"error": "Too many support requests. Please try again later."}, status_code=429)

    body = await request.json()
    subject = (body.get("subject") or "").strip()[:200]
    message = (body.get("message") or "").strip()[:5000]

    if not message:
        return JSONResponse({"error": "Message is required."}, status_code=400)

    email_subject = f"[Munin Support] {subject}" if subject else f"[Munin Support] Message from {sender_name}"

    plain_body = f"From: {sender_name} <{sender_email}>\nSubject: {subject or '(no subject)'}\n\n{message}"
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #1a1f26; color: #e6edf3; border-radius: 8px;">
      <h3 style="color: #58a6ff; margin-top: 0;">Support Request</h3>
      <p style="color: #8b949e; margin: 4px 0;"><strong>From:</strong> {sender_name} &lt;{sender_email}&gt;</p>
      <p style="color: #8b949e; margin: 4px 0;"><strong>Subject:</strong> {subject or '(no subject)'}</p>
      <hr style="border: none; border-top: 1px solid #30363d; margin: 16px 0;">
      <div style="white-space: pre-wrap; line-height: 1.6;">{message}</div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_SENDER
    msg["To"] = SUPPORT_EMAIL
    msg["Reply-To"] = sender_email
    msg["Subject"] = email_subject
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USERNAME,
            password=SMTP_PASSWORD,
            start_tls=True,
        )
    except Exception:
        return JSONResponse({"error": "Failed to send message. Please try again later."}, status_code=500)

    rate_support[sender_email] = timestamps + [now]
    return {"sent": True}


@app.get("/health")
async def health():
    return {"status": "ok", "user_count": count_users()}


# ── Admin helpers ────────────────────────────────────────────────────────────

def _require_admin(request: Request) -> tuple[dict | None, Response | None]:
    """Resolve the current session to an admin user, or return an error response."""
    cookie = request.cookies.get("munin_session")
    if not cookie:
        return None, JSONResponse({"error": "not authenticated"}, status_code=401)
    session = validate_session(cookie)
    if not session:
        return None, JSONResponse({"error": "invalid session"}, status_code=401)
    user = lookup_user(session["email"])
    if not user or user["role"] != "admin":
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return user, None


def _user_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    emails = [
        r["email"]
        for r in conn.execute(
            "SELECT email FROM user_emails WHERE user_id = ? ORDER BY is_primary DESC, email ASC",
            (row["id"],),
        ).fetchall()
    ]
    primary_row = conn.execute(
        "SELECT email FROM user_emails WHERE user_id = ? AND is_primary = 1",
        (row["id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "name": _compose_name(row["first_name"], row["last_name"]),
        "role": row["role"],
        "group": row["research_group"],
        "username": row["username"],
        "emails": emails,
        "primary_email": primary_row["email"] if primary_row else (emails[0] if emails else None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _count_admins(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").fetchone()["n"]


def _touch_user(conn: sqlite3.Connection, user_id: int):
    conn.execute(
        "UPDATE users SET updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )


def _group_members(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Return the member list for a group (full user dicts), ordered by name."""
    rows = conn.execute(
        f"""
        SELECT {_USERS_SELECT_COLUMNS}
          FROM users u
          JOIN group_members gm ON gm.user_id = u.id
         WHERE gm.group_slug = ?
         ORDER BY LOWER(u.last_name), LOWER(u.first_name)
        """,
        (slug,),
    ).fetchall()
    return [_user_row_to_dict(conn, r) for r in rows]


# ── Admin: users ─────────────────────────────────────────────────────────────

_USERS_SELECT_COLUMNS = (
    "id, first_name, last_name, role, research_group, username, "
    "created_at, updated_at"
)


@app.get("/admin/users")
async def admin_list_users(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        f"SELECT {_USERS_SELECT_COLUMNS} "
        "FROM users "
        "ORDER BY LOWER(last_name), LOWER(first_name)"
    ).fetchall()
    users = [_user_row_to_dict(conn, r) for r in rows]
    conn.close()
    return JSONResponse({"users": users})


@app.post("/admin/users")
async def admin_create_user(request: Request):
    _, err = _require_admin(request)
    if err:
        return err

    body = await request.json()
    first_name = (body.get("first_name") or "").strip()
    last_name_raw = (body.get("last_name") or "").strip()
    last_name = last_name_raw or None
    email = (body.get("email") or "").strip().lower()
    role = (body.get("role") or "user").strip()
    group = body.get("group")
    if group is not None:
        group = group.strip() or None
    username = body.get("username")
    if username is not None:
        username = username.strip() or None

    if not first_name:
        return JSONResponse({"error": "first_name is required"}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "valid email is required"}, status_code=400)
    if role not in ("user", "group_leader", "admin"):
        return JSONResponse({"error": "role must be user, group_leader, or admin"}, status_code=400)

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT user_id FROM user_emails WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return JSONResponse({"error": "email already in use"}, status_code=409)

        if group is not None:
            grp = conn.execute(
                "SELECT slug FROM groups WHERE slug = ?", (group,)
            ).fetchone()
            if not grp:
                return JSONResponse({"error": f"group '{group}' does not exist"}, status_code=400)

        user_id = _insert_user(
            conn,
            first_name=first_name,
            last_name=last_name,
            role=role,
            research_group=group,
            username=username,
        )
        _attach_email(conn, user_id, email, is_primary=True)
        conn.commit()

        row = conn.execute(
            f"SELECT {_USERS_SELECT_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        result = _user_row_to_dict(conn, row)
    finally:
        conn.close()
    return JSONResponse(result, status_code=201)


@app.patch("/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request):
    admin, err = _require_admin(request)
    if err:
        return err

    body = await request.json()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"error": "user not found"}, status_code=404)

        updates: list[tuple[str, object]] = []
        if "first_name" in body:
            first = (body.get("first_name") or "").strip()
            if not first:
                return JSONResponse(
                    {"error": "first_name cannot be empty"}, status_code=400
                )
            updates.append(("first_name", first))
        if "last_name" in body:
            last_raw = (body.get("last_name") or "").strip()
            # Empty string clears the column to NULL; we never block on
            # last_name being missing because the whitelist seed leaves
            # ~30 rows blank by design.
            updates.append(("last_name", last_raw or None))
        if "role" in body:
            role = (body.get("role") or "").strip()
            if role not in ("user", "group_leader", "admin"):
                return JSONResponse({"error": "invalid role"}, status_code=400)
            # Demoting the last admin is forbidden.
            if row["role"] == "admin" and role != "admin" and _count_admins(conn) <= 1:
                return JSONResponse(
                    {"error": "cannot demote the last admin"}, status_code=409
                )
            updates.append(("role", role))
        if "group" in body:
            grp = body.get("group")
            if grp is not None:
                grp = grp.strip() or None
                if grp is not None:
                    exists = conn.execute(
                        "SELECT slug FROM groups WHERE slug = ?", (grp,)
                    ).fetchone()
                    if not exists:
                        return JSONResponse(
                            {"error": f"group '{grp}' does not exist"}, status_code=400
                        )
            updates.append(("research_group", grp))
        if "username" in body:
            uname = body.get("username")
            if uname is not None:
                uname = uname.strip() or None
            updates.append(("username", uname))

        if not updates:
            return JSONResponse({"error": "no fields to update"}, status_code=400)

        now = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{col} = ?" for col, _ in updates) + ", updated_at = ?"
        params = [v for _, v in updates] + [now, user_id]
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", params)
        # Setting a primary group also enrolls the user in it (the Groups
        # page manages full membership; the Users tab sets the primary).
        # Multi-group: we never strip existing memberships here.
        if "group" in body and grp:
            conn.execute(
                "INSERT OR IGNORE INTO group_members (group_slug, user_id, added_at) "
                "VALUES (?, ?, ?)",
                (grp, user_id, now),
            )
        conn.commit()

        row = conn.execute(
            f"SELECT {_USERS_SELECT_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        result = _user_row_to_dict(conn, row)
    finally:
        conn.close()
    return JSONResponse(result)


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    admin, err = _require_admin(request)
    if err:
        return err

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return JSONResponse({"error": "user not found"}, status_code=404)
        if row["id"] == admin["id"]:
            return JSONResponse({"error": "cannot delete yourself"}, status_code=409)
        if row["role"] == "admin" and _count_admins(conn) <= 1:
            return JSONResponse({"error": "cannot delete the last admin"}, status_code=409)

        # Capture emails first so we can invalidate sessions.
        email_rows = conn.execute(
            "SELECT email FROM user_emails WHERE user_id = ?", (user_id,)
        ).fetchall()
        emails = [r["email"] for r in email_rows]

        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        # user_emails rows cascade via FK.
        for em in emails:
            conn.execute("DELETE FROM sessions WHERE email = ?", (em,))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


# ── Admin: user emails ───────────────────────────────────────────────────────

@app.post("/admin/users/{user_id}/emails")
async def admin_add_email(user_id: int, request: Request):
    _, err = _require_admin(request)
    if err:
        return err

    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "valid email is required"}, status_code=400)

    conn = get_db()
    try:
        user_row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user_row:
            return JSONResponse({"error": "user not found"}, status_code=404)
        existing = conn.execute(
            "SELECT user_id FROM user_emails WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return JSONResponse({"error": "email already in use"}, status_code=409)

        _attach_email(conn, user_id, email, is_primary=False)
        _touch_user(conn, user_id)
        conn.commit()

        row = conn.execute(
            f"SELECT {_USERS_SELECT_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        result = _user_row_to_dict(conn, row)
    finally:
        conn.close()
    return JSONResponse(result, status_code=201)


@app.delete("/admin/users/{user_id}/emails/{email}")
async def admin_remove_email(user_id: int, email: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err

    email = email.strip().lower()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT email, is_primary FROM user_emails WHERE user_id = ? AND email = ?",
            (user_id, email),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "email not found for user"}, status_code=404)

        all_emails = conn.execute(
            "SELECT email FROM user_emails WHERE user_id = ? ORDER BY email ASC",
            (user_id,),
        ).fetchall()
        if len(all_emails) <= 1:
            return JSONResponse(
                {"error": "cannot remove the last email of a user"}, status_code=409
            )

        was_primary = bool(row["is_primary"])
        conn.execute("DELETE FROM user_emails WHERE email = ?", (email,))
        conn.execute("DELETE FROM sessions WHERE email = ?", (email,))

        if was_primary:
            # Promote next remaining email (alphabetical) to primary.
            next_email = conn.execute(
                "SELECT email FROM user_emails WHERE user_id = ? ORDER BY email ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            if next_email:
                conn.execute(
                    "UPDATE user_emails SET is_primary = 1 WHERE email = ?",
                    (next_email["email"],),
                )
        _touch_user(conn, user_id)
        conn.commit()

        urow = conn.execute(
            f"SELECT {_USERS_SELECT_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        result = _user_row_to_dict(conn, urow)
    finally:
        conn.close()
    return JSONResponse(result)


@app.put("/admin/users/{user_id}/emails/{email}/primary")
async def admin_set_primary_email(user_id: int, email: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err

    email = email.strip().lower()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT email FROM user_emails WHERE user_id = ? AND email = ?",
            (user_id, email),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "email not found for user"}, status_code=404)

        conn.execute(
            "UPDATE user_emails SET is_primary = 0 WHERE user_id = ?", (user_id,)
        )
        conn.execute(
            "UPDATE user_emails SET is_primary = 1 WHERE email = ?", (email,)
        )
        _touch_user(conn, user_id)
        conn.commit()

        urow = conn.execute(
            f"SELECT {_USERS_SELECT_COLUMNS} FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        result = _user_row_to_dict(conn, urow)
    finally:
        conn.close()
    return JSONResponse(result)


# ── Admin: groups ────────────────────────────────────────────────────────────

@app.get("/admin/groups")
async def admin_list_groups(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        """
        SELECT g.slug, g.display_name, g.created_at, g.updated_at,
               (SELECT COUNT(*) FROM group_members gm WHERE gm.group_slug = g.slug) AS member_count
          FROM groups g
         ORDER BY LOWER(g.display_name) ASC
        """
    ).fetchall()
    conn.close()
    return JSONResponse({
        "groups": [
            {
                "slug": r["slug"],
                "display_name": r["display_name"],
                "member_count": r["member_count"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    })


@app.post("/admin/groups")
async def admin_create_group(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    body = await request.json()
    slug = (body.get("slug") or "").strip().lower()
    display_name = (body.get("display_name") or "").strip()
    if not slug or " " in slug or not slug.replace("-", "").replace("_", "").isalnum():
        return JSONResponse(
            {"error": "slug must be lowercase alphanumeric (hyphens/underscores allowed)"},
            status_code=400,
        )
    if not display_name:
        return JSONResponse({"error": "display_name is required"}, status_code=400)
    conn = get_db()
    try:
        existing = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if existing:
            return JSONResponse({"error": "group already exists"}, status_code=409)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO groups (slug, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (slug, display_name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(
        {"slug": slug, "display_name": display_name, "member_count": 0,
         "created_at": now, "updated_at": now},
        status_code=201,
    )


@app.patch("/admin/groups/{slug}")
async def admin_update_group(slug: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    body = await request.json()
    display_name = body.get("display_name")
    if display_name is None or not display_name.strip():
        return JSONResponse({"error": "display_name is required"}, status_code=400)
    display_name = display_name.strip()

    conn = get_db()
    try:
        row = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return JSONResponse({"error": "group not found"}, status_code=404)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE groups SET display_name = ?, updated_at = ? WHERE slug = ?",
            (display_name, now, slug),
        )
        conn.commit()
        member_count = conn.execute(
            "SELECT COUNT(*) AS n FROM group_members WHERE group_slug = ?", (slug,)
        ).fetchone()["n"]
        created_at = conn.execute(
            "SELECT created_at FROM groups WHERE slug = ?", (slug,)
        ).fetchone()["created_at"]
    finally:
        conn.close()
    return JSONResponse({
        "slug": slug,
        "display_name": display_name,
        "member_count": member_count,
        "created_at": created_at,
        "updated_at": now,
    })


@app.delete("/admin/groups/{slug}")
async def admin_delete_group(slug: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    try:
        row = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return JSONResponse({"error": "group not found"}, status_code=404)
        member_count = conn.execute(
            "SELECT COUNT(*) AS n FROM group_members WHERE group_slug = ?", (slug,)
        ).fetchone()["n"]
        if member_count > 0:
            return JSONResponse(
                {"error": f"group has {member_count} member(s); reassign before deleting"},
                status_code=409,
            )
        conn.execute("DELETE FROM groups WHERE slug = ?", (slug,))
        conn.commit()
    finally:
        conn.close()
    return Response(status_code=204)


# ── Admin: group membership ──────────────────────────────────────────────────

@app.get("/admin/groups/{slug}/members")
async def admin_list_group_members(slug: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    try:
        grp = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if not grp:
            return JSONResponse({"error": "group not found"}, status_code=404)
        members = _group_members(conn, slug)
    finally:
        conn.close()
    return JSONResponse({"members": members})


@app.post("/admin/groups/{slug}/members")
async def admin_add_group_member(slug: str, request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    body = await request.json()
    user_id = body.get("user_id")
    if user_id is None:
        return JSONResponse({"error": "user_id is required"}, status_code=400)
    conn = get_db()
    try:
        grp = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if not grp:
            return JSONResponse({"error": "group not found"}, status_code=404)
        urow = conn.execute(
            "SELECT id, research_group FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not urow:
            return JSONResponse({"error": "user not found"}, status_code=404)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO group_members (group_slug, user_id, added_at) "
            "VALUES (?, ?, ?)",
            (slug, user_id, now),
        )
        # A user's first group becomes their primary group (the single
        # value used for upload attribution and the X-Munin-Group header).
        if not urow["research_group"]:
            conn.execute(
                "UPDATE users SET research_group = ?, updated_at = ? WHERE id = ?",
                (slug, now, user_id),
            )
        else:
            _touch_user(conn, user_id)
        conn.commit()
        members = _group_members(conn, slug)
    finally:
        conn.close()
    return JSONResponse({"members": members}, status_code=201)


@app.delete("/admin/groups/{slug}/members/{user_id}")
async def admin_remove_group_member(slug: str, user_id: int, request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    try:
        grp = conn.execute("SELECT slug FROM groups WHERE slug = ?", (slug,)).fetchone()
        if not grp:
            return JSONResponse({"error": "group not found"}, status_code=404)
        membership = conn.execute(
            "SELECT user_id FROM group_members WHERE group_slug = ? AND user_id = ?",
            (slug, user_id),
        ).fetchone()
        if not membership:
            return JSONResponse(
                {"error": "user is not a member of this group"}, status_code=404
            )

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "DELETE FROM group_members WHERE group_slug = ? AND user_id = ?",
            (slug, user_id),
        )
        # If we just removed the user's primary group, repoint it to
        # another remaining membership (oldest first), or NULL.
        urow = conn.execute(
            "SELECT research_group FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if urow and urow["research_group"] == slug:
            nxt = conn.execute(
                "SELECT group_slug FROM group_members WHERE user_id = ? "
                "ORDER BY added_at ASC, group_slug ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            conn.execute(
                "UPDATE users SET research_group = ?, updated_at = ? WHERE id = ?",
                (nxt["group_slug"] if nxt else None, now, user_id),
            )
        else:
            _touch_user(conn, user_id)
        conn.commit()
        members = _group_members(conn, slug)
    finally:
        conn.close()
    return JSONResponse({"members": members})


# ── Admin: exports ───────────────────────────────────────────────────────────

@app.get("/admin/check-role")
async def admin_check_role(request: Request, email: str = ""):
    """Internal role lookup used by the tusd hook-service KB gate.

    Auth: bearer token matching KB_GATE_TOKEN OR an admin session
    cookie. Returns role + allowed_kb_contribution boolean.
    """
    auth_header = request.headers.get("Authorization", "")
    token_ok = (
        KB_GATE_TOKEN
        and auth_header.startswith("Bearer ")
        and secrets.compare_digest(auth_header[7:], KB_GATE_TOKEN)
    )
    if not token_ok:
        _, err = _require_admin(request)
        if err:
            return err

    email = (email or "").strip().lower()
    if not email:
        return JSONResponse({"error": "email query param required"}, status_code=400)

    user = lookup_user(email)
    if not user:
        return JSONResponse(
            {
                "email": email,
                "role": None,
                "group": None,
                "allowed_kb_contribution": False,
            },
            status_code=404,
        )
    # Admins may always contribute; group leaders must be assigned to a
    # research group (their uploads are attributed to that group).
    allowed = user["role"] == "admin" or (
        user["role"] == "group_leader" and bool(user["group"])
    )
    return JSONResponse({
        "email": user["primary_email"],
        "role": user["role"],
        "group": user["group"],
        "allowed_kb_contribution": allowed,
    })


@app.get("/admin/contributors.yaml")
async def admin_export_contributors_yaml(request: Request):
    """Cluster-compatible YAML emission.

    Accepts either an admin session cookie (for browser inspection) OR
    a bearer token matching CONTRIBUTORS_SYNC_TOKEN (used by the cluster
    poll-sync). Returns text/yaml.
    """
    # Bearer token path takes precedence so the cluster doesn't need a session.
    auth_header = request.headers.get("Authorization", "")
    token_ok = (
        CONTRIBUTORS_SYNC_TOKEN
        and auth_header.startswith("Bearer ")
        and secrets.compare_digest(auth_header[7:], CONTRIBUTORS_SYNC_TOKEN)
    )
    if not token_ok:
        _, err = _require_admin(request)
        if err:
            return err

    body = emit_contributors_yaml()
    return Response(content=body, media_type="text/yaml; charset=utf-8")


@app.get("/admin/users.csv")
async def admin_export_users_csv(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.first_name, u.last_name, u.role, ue.email
          FROM users u
          JOIN user_emails ue ON ue.user_id = u.id AND ue.is_primary = 1
         ORDER BY LOWER(u.last_name), LOWER(u.first_name)
        """
    ).fetchall()
    conn.close()
    # Same field order and quoting style as whitelist.csv. Commas
    # inside a name are replaced with spaces so the line stays a
    # 4-column CSV without a quoting layer.
    lines = ["email,first_name,last_name,role"]
    for r in rows:
        first = (r["first_name"] or "").replace(",", " ")
        last = (r["last_name"] or "").replace(",", " ")
        lines.append(f"{r['email']},{first},{last},{r['role']}")
    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="munin-users.csv"'},
    )
