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

import csv
import os
import secrets
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from urllib.parse import quote, urlencode

import aiosmtplib
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import TimestampSigner, BadSignature
from jinja2 import Environment, FileSystemLoader

# ── Configuration ────────────────────────────────────────────────────────────

SECRET_KEY = os.environ["SECRET_KEY"]
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.hosteurope.de")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SENDER = os.environ.get("SMTP_SENDER", "noreply@muninai.org")
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", "2592000"))  # 30 days
OTP_EXPIRY = int(os.environ.get("OTP_EXPIRY", "600"))  # 10 minutes
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", ".muninai.org")
WHITELIST_PATH = Path(os.environ.get("WHITELIST_PATH", "/data/whitelist.csv"))
DB_PATH = Path(os.environ.get("DB_PATH", "/data/db/sessions.db"))

# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://muninai.org",
        "https://chat.muninai.org",
        "https://search.muninai.org",
        "https://research.muninai.org",
        "https://docs.muninai.org",
        "https://upload.muninai.org",
    ],
    allow_credentials=True,
    allow_methods=["GET", "PATCH", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)

signer = TimestampSigner(SECRET_KEY)
templates = Environment(loader=FileSystemLoader("templates"), autoescape=True)

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
            name TEXT NOT NULL,
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
    conn.commit()
    conn.close()


# ── User lookup + mutation helpers ────────────────────────────────────────

def lookup_user(email: str) -> dict | None:
    """Resolve an email (any alias) to its user. Returns None if not found."""
    email = email.strip().lower()
    conn = get_db()
    row = conn.execute(
        """
        SELECT u.id, u.name, u.role, u.research_group, u.username
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
        "name": row["name"],
        "role": row["role"],
        "group": row["research_group"],
        "username": row["username"],
        "emails": emails,
        "primary_email": primary_row["email"] if primary_row else (emails[0] if emails else email),
    }


def _insert_user(conn: sqlite3.Connection, name: str, role: str,
                 research_group: str | None = None, username: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO users (name, role, research_group, username, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, role, research_group, username, now, now),
    )
    return cur.lastrowid


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
                name = (row.get("name") or "").strip() or email
                role = (row.get("role") or "").strip() or "user"
                if role not in ("user", "group_leader", "admin"):
                    role = "user"
                user_id = _insert_user(conn, name=name, role=role)
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
    """Send OTP code via SMTP."""
    tmpl = templates.get_template("email_otp.html")
    html_body = tmpl.render(code=code, name=name, expiry_minutes=OTP_EXPIRY // 60)

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_SENDER
    msg["To"] = email
    msg["Subject"] = f"Munin login code: {code}"
    msg.attach(MIMEText(f"Your Munin login code is: {code}\nIt expires in {OTP_EXPIRY // 60} minutes.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    await aiosmtplib.send(
        msg,
        hostname=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USERNAME,
        password=SMTP_PASSWORD,
        start_tls=True,
    )


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    seed_users_from_whitelist()


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
    except Exception:
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

    dest = redirect if redirect and redirect.startswith("http") else "https://muninai.org"
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
        "X-Munin-Email": session["email"],
        "X-Munin-Name": name,
        "X-Munin-Role": role,
    }
    if user and user["group"]:
        headers["X-Munin-Group"] = user["group"]
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

SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@muninai.org")
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
        "name": row["name"],
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


# ── Admin: users ─────────────────────────────────────────────────────────────

@app.get("/admin/users")
async def admin_list_users(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, role, research_group, username, created_at, updated_at "
        "FROM users ORDER BY LOWER(name) ASC"
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
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    role = (body.get("role") or "user").strip()
    group = body.get("group")
    if group is not None:
        group = group.strip() or None
    username = body.get("username")
    if username is not None:
        username = username.strip() or None

    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
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

        user_id = _insert_user(conn, name=name, role=role,
                               research_group=group, username=username)
        _attach_email(conn, user_id, email, is_primary=True)
        conn.commit()

        row = conn.execute(
            "SELECT id, name, role, research_group, username, created_at, updated_at "
            "FROM users WHERE id = ?",
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
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                return JSONResponse({"error": "name cannot be empty"}, status_code=400)
            updates.append(("name", name))
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

        set_clause = ", ".join(f"{col} = ?" for col, _ in updates) + ", updated_at = ?"
        params = [v for _, v in updates] + [datetime.now(timezone.utc).isoformat(), user_id]
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", params)
        conn.commit()

        row = conn.execute(
            "SELECT id, name, role, research_group, username, created_at, updated_at "
            "FROM users WHERE id = ?",
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
            "SELECT id, name, role, research_group, username, created_at, updated_at "
            "FROM users WHERE id = ?",
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
            "SELECT id, name, role, research_group, username, created_at, updated_at "
            "FROM users WHERE id = ?",
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
            "SELECT id, name, role, research_group, username, created_at, updated_at "
            "FROM users WHERE id = ?",
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
               (SELECT COUNT(*) FROM users u WHERE u.research_group = g.slug) AS member_count
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
            "SELECT COUNT(*) AS n FROM users WHERE research_group = ?", (slug,)
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
            "SELECT COUNT(*) AS n FROM users WHERE research_group = ?", (slug,)
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


# ── Admin: exports ───────────────────────────────────────────────────────────

@app.get("/admin/users.csv")
async def admin_export_users_csv(request: Request):
    _, err = _require_admin(request)
    if err:
        return err
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.name, u.role, ue.email
          FROM users u
          JOIN user_emails ue ON ue.user_id = u.id AND ue.is_primary = 1
         ORDER BY LOWER(u.name) ASC
        """
    ).fetchall()
    conn.close()
    lines = ["email,name,role"]
    for r in rows:
        # Same field order and quoting style as the legacy whitelist.csv.
        name = r["name"].replace(",", " ")
        lines.append(f"{r['email']},{name},{r['role']}")
    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="munin-users.csv"'},
    )
