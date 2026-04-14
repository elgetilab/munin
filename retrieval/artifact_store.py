"""
Versioned document artifacts (§22 Stage A).

An artifact is a versioned document associated with a single
conversation. The chat model creates artifacts via ``create_artifact``,
updates them via ``update_artifact`` (new version), and the user can
edit them directly via ``PATCH /api/chats/{cid}/artifacts/{aid}`` in
the side panel. Every write lands in ``artifact_versions`` as a full
content snapshot (no diff chain): storage is cheap, rollback is
trivial, diff views are a client-side concern.

Scope rules:

- **Conversation-scoped**: artifacts belong to the conversation they
  were created in. Projects (§21) provide organisational context but
  don't share artifacts across conversations. If a user wants the
  same artifact in another chat, they copy the content.
- **Text-only in Stage A**: content is stored as TEXT in SQLite. The
  500 KB per-version cap protects against pathological inputs but
  comfortably fits a full paper draft (~100k words). Binary
  artifacts (PNG plots, PDFs, xlsx) stay on disk via the §2/§3
  sandbox pipeline until Stage C unifies the two concepts.
- **Full-content updates in Stage A**: ``update_artifact`` takes a
  replacement content string. Diff-based updates (to save tokens on
  large documents) are deferred to Stage B.

Context injection is summary-only: chat_service prepends an
``=== ACTIVE ARTIFACTS ===`` block listing title + type + version +
word count per artifact. The model fetches full content on demand
via ``read_artifact`` so context overhead stays bounded regardless
of how many artifacts a conversation accumulates.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from chat_store import _iso_now, _row_to_dict, get_db


# Content cap per version. 500 KB of text is ~100k words, comfortably
# larger than any real paper. Anything over this is almost certainly
# a pathological paste and gets rejected with a clear error rather
# than silently truncated.
MAX_CONTENT_BYTES = 500 * 1024
MAX_TITLE_CHARS = 200
MAX_CHANGE_SUMMARY_CHARS = 500


class ArtifactError(ValueError):
    """Raised for any invalid artifact input. Callers convert to the
    tool-result ``error`` shape or HTTP 400."""


# ---------------------------------------------------------------------------
# Unified diff parser + applier (Stage B)
# ---------------------------------------------------------------------------
#
# Hand-rolled because unified diff is a small, well-understood format and
# we only need a narrow subset:
#
#   - Optional ``--- a/...`` / ``+++ b/...`` file headers (ignored)
#   - ``@@ -old_start,old_len +new_start,new_len @@`` hunk headers
#   - Lines prefixed with ' ' (context), '-' (remove), '+' (add)
#   - ``\\ No newline at end of file`` markers (respected on both sides)
#
# We don't accept git-style diffs (``diff --git``, ``index`` lines), binary
# diffs, multi-file diffs, or fuzzy application. Strict matching: if any
# hunk's context or removed lines don't match the source exactly at the
# indicated position, the whole update is rejected with a clear error
# message identifying the failing hunk. See §22 Stage B decisions.
#
# The model is the only caller of this path (user edits always go through
# the full-content PATCH endpoint), and the model just called
# read_artifact before producing the diff, so strict is a safe default.
# If we see real-world "line numbers drifted by 1" errors we can add
# fuzzy application as a follow-up.

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))? @@"
)


def _strip_file_headers(lines: list[str]) -> list[str]:
    """
    Drop a standard ``--- a/... \\n +++ b/...`` preamble if present. We
    know the target artifact from the tool call arguments, so these
    lines carry no information we need - but rejecting them would be
    annoying for callers generating diffs with ``diff -u``.
    """
    out = list(lines)
    while out and out[0].startswith(("---", "+++", "diff --git", "index ")):
        out.pop(0)
    return out


def _split_hunks(diff_lines: list[str]) -> list[list[str]]:
    """Break a diff into hunks. Each hunk is a list starting with its ``@@``
    header followed by its body lines."""
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in diff_lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = [line]
        elif current:
            current.append(line)
        else:
            # Content before the first hunk header is a protocol
            # violation. Be strict.
            stripped = line.strip()
            if stripped:
                raise ArtifactError(
                    f"diff content before first @@ hunk header: {stripped!r}"
                )
    if current:
        hunks.append(current)
    return hunks


def apply_unified_diff(source: str, diff_text: str) -> tuple[str, dict]:
    """
    Apply a unified diff to ``source`` and return ``(new_content, stats)``.
    Stats is a dict with keys ``hunks_applied``, ``lines_added``,
    ``lines_removed``. Raises ``ArtifactError`` on any mismatch so
    callers can surface a single, actionable error to the model.
    """
    if not isinstance(diff_text, str) or not diff_text.strip():
        raise ArtifactError("diff must be a non-empty string")

    # Normalise line endings. We operate on lists of lines without
    # terminators, then re-join with \n at the end.
    diff_normalised = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_diff_lines = diff_normalised.split("\n")
    # Strip a trailing empty entry from the final newline of the diff
    # blob (split("\n") gives one extra "" for a trailing \n).
    if raw_diff_lines and raw_diff_lines[-1] == "":
        raw_diff_lines.pop()
    diff_lines = _strip_file_headers(raw_diff_lines)
    if not diff_lines:
        raise ArtifactError("diff has no content after stripping headers")

    source_normalised = source.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = source_normalised.split("\n")
    # If the source ends in a newline, split leaves a trailing empty
    # element. Track the "source ends in newline" state separately so
    # we can reproduce it on output.
    source_ends_in_newline = False
    if source_lines and source_lines[-1] == "":
        source_lines.pop()
        source_ends_in_newline = True

    hunks = _split_hunks(diff_lines)
    if not hunks:
        raise ArtifactError("diff has no @@ hunks")

    # We rebuild the new content as a list of lines. Walk the source
    # and splice each hunk's replacement in at the right index.
    output_lines: list[str] = []
    cursor = 0  # index into source_lines (0-based)
    lines_added_total = 0
    lines_removed_total = 0
    result_ends_in_newline = source_ends_in_newline

    for hunk_index, hunk in enumerate(hunks, start=1):
        header = hunk[0]
        match = _HUNK_HEADER_RE.match(header)
        if not match:
            raise ArtifactError(
                f"hunk {hunk_index}: malformed header {header!r}"
            )
        old_start = int(match.group("old_start"))
        old_len_raw = match.group("old_len")
        old_len = int(old_len_raw) if old_len_raw is not None else 1

        # Unified diff uses 1-based line numbers, and old_start=0 is
        # used for "add at beginning of empty file". Translate to a
        # 0-based index.
        if old_start == 0:
            hunk_source_index = 0
        else:
            hunk_source_index = old_start - 1

        # Carry over any unchanged source lines between the cursor and
        # this hunk's starting line.
        if hunk_source_index < cursor:
            raise ArtifactError(
                f"hunk {hunk_index}: old_start={old_start} is before "
                f"previous hunk's end (cursor={cursor + 1})"
            )
        output_lines.extend(source_lines[cursor:hunk_source_index])
        cursor = hunk_source_index

        # Walk the hunk body. Verify context/removed lines match the
        # source exactly; append added lines to the output.
        hunk_added = 0
        hunk_removed = 0
        for body_line in hunk[1:]:
            if body_line.startswith("\\"):
                # "\\ No newline at end of file" markers affect the
                # expected EOL state of the side they follow. We track
                # the final state for the output.
                if body_line.strip() == "\\ No newline at end of file":
                    # The preceding operation's side is now "no EOL".
                    # Our output line list doesn't carry EOL info; we
                    # just remember this for the final join step.
                    result_ends_in_newline = False
                continue
            if not body_line:
                # Empty string in the body = a blank line in the source
                # (context " "), but a properly-formed unified diff
                # always has a single-space prefix on blank context
                # lines. A truly empty body line is a protocol glitch -
                # accept it as a blank context line to be forgiving.
                sig = " "
                content_line = ""
            else:
                sig = body_line[0]
                content_line = body_line[1:]
            if sig == " ":  # context
                if cursor >= len(source_lines):
                    raise ArtifactError(
                        f"hunk {hunk_index}: context line past end of "
                        f"source: {content_line!r}"
                    )
                if source_lines[cursor] != content_line:
                    raise ArtifactError(
                        f"hunk {hunk_index}: context mismatch at line "
                        f"{cursor + 1}: expected {content_line!r}, "
                        f"found {source_lines[cursor]!r}"
                    )
                output_lines.append(content_line)
                cursor += 1
            elif sig == "-":  # removal
                if cursor >= len(source_lines):
                    raise ArtifactError(
                        f"hunk {hunk_index}: removal past end of source: "
                        f"{content_line!r}"
                    )
                if source_lines[cursor] != content_line:
                    raise ArtifactError(
                        f"hunk {hunk_index}: removal mismatch at line "
                        f"{cursor + 1}: expected {content_line!r}, "
                        f"found {source_lines[cursor]!r}"
                    )
                cursor += 1
                hunk_removed += 1
            elif sig == "+":  # addition
                output_lines.append(content_line)
                hunk_added += 1
            else:
                raise ArtifactError(
                    f"hunk {hunk_index}: unknown line prefix {sig!r} in "
                    f"body line {body_line!r}"
                )

        # Sanity-check the hunk's old_len if it was explicit. Discrepancy
        # is usually a sign the model miscounted and the diff is
        # nonsense.
        consumed = cursor - hunk_source_index
        if consumed != old_len:
            raise ArtifactError(
                f"hunk {hunk_index}: header claims old_len={old_len} "
                f"but body consumed {consumed} source lines"
            )

        lines_added_total += hunk_added
        lines_removed_total += hunk_removed

    # Append any trailing source lines past the last hunk.
    output_lines.extend(source_lines[cursor:])

    # Rejoin with \n. Restore the trailing newline if the source had
    # one (and wasn't flipped by a ``\\ No newline at end of file``
    # marker).
    result = "\n".join(output_lines)
    if result_ends_in_newline:
        result += "\n"

    return result, {
        "hunks_applied": len(hunks),
        "lines_added": lines_added_total,
        "lines_removed": lines_removed_total,
    }


def _compute_line_delta(old: str, new: str) -> tuple[int, int]:
    """
    Cheap before/after line-delta for full-content updates so the
    response shape matches what diff updates return. Uses
    ``difflib.ndiff`` at module import time rather than a full diff
    library since we already depend on stdlib.
    """
    import difflib
    old_lines = old.split("\n")
    new_lines = new.split("\n")
    added = 0
    removed = 0
    for token in difflib.ndiff(old_lines, new_lines):
        if token.startswith("+ "):
            added += 1
        elif token.startswith("- "):
            removed += 1
    return added, removed


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_title(title: Any) -> str:
    if not isinstance(title, str):
        raise ArtifactError("title must be a string")
    title = title.strip()
    if not title:
        raise ArtifactError("title must be non-empty")
    if len(title) > MAX_TITLE_CHARS:
        raise ArtifactError(
            f"title exceeds {MAX_TITLE_CHARS}-character cap "
            f"(got {len(title)})"
        )
    return title


def _validate_content_type(content_type: Any) -> str:
    if not isinstance(content_type, str) or not content_type.strip():
        raise ArtifactError("content_type is required")
    return content_type.strip()


def _validate_content(content: Any) -> str:
    if not isinstance(content, str):
        raise ArtifactError("content must be a string")
    # Byte length (not char length) matches the server-side storage cost
    # and protects against unicode pathologies.
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise ArtifactError(
            f"content exceeds {MAX_CONTENT_BYTES} byte cap "
            f"(got {size} bytes)"
        )
    return content


def _validate_change_summary(summary: Any) -> Optional[str]:
    if summary is None:
        return None
    if not isinstance(summary, str):
        raise ArtifactError("change_summary must be a string")
    summary = summary.strip()
    if not summary:
        return None
    if len(summary) > MAX_CHANGE_SUMMARY_CHARS:
        raise ArtifactError(
            f"change_summary exceeds {MAX_CHANGE_SUMMARY_CHARS}-character cap"
        )
    return summary


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def _verify_conversation_owned(
    conversation_id: str, user_email: str
) -> bool:
    """Check that ``user_email`` owns the conversation. Used as a guard
    before any artifact write so the caller can't create artifacts
    under someone else's conversation id."""
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    row = await cur.fetchone()
    return row is not None


async def _verify_artifact_owned(
    artifact_id: str,
    conversation_id: str,
    user_email: str,
) -> Optional[dict]:
    """
    Return the artifact row as a dict if it exists, belongs to the
    given conversation, and the conversation belongs to the user.
    Returns None otherwise — the caller converts None into a 404.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT a.* FROM artifacts a "
        "WHERE a.id = ? AND a.conversation_id = ? AND a.user_email = ?",
        (artifact_id, conversation_id, user_email),
    )
    return _row_to_dict(await cur.fetchone())


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

SOURCE_MODEL_WRITTEN = "model_written"
SOURCE_SANDBOX_GENERATED = "sandbox_generated"


async def create_artifact(
    user_email: str,
    conversation_id: str,
    title: str,
    content: str,
    content_type: str,
    language: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> dict:
    """
    Create a brand-new artifact with a single v1 snapshot. Returns the
    artifact metadata + version info suitable for an SSE event.
    """
    if not await _verify_conversation_owned(conversation_id, user_email):
        raise ArtifactError("conversation not found or not owned by user")

    title = _validate_title(title)
    content_type = _validate_content_type(content_type)
    content = _validate_content(content)
    summary = _validate_change_summary(change_summary)
    if language is not None and not isinstance(language, str):
        raise ArtifactError("language must be a string or null")

    db = await get_db()
    aid = f"art_{uuid.uuid4().hex[:12]}"
    now = _iso_now()

    await db.execute(
        """
        INSERT INTO artifacts
            (id, conversation_id, user_email, title, content_type,
             language, latest_version, created_at, updated_at, source)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            aid, conversation_id, user_email, title, content_type,
            language, now, now, SOURCE_MODEL_WRITTEN,
        ),
    )
    await db.execute(
        """
        INSERT INTO artifact_versions
            (artifact_id, version, content, change_summary,
             created_at, created_by)
        VALUES (?, 1, ?, ?, ?, 'assistant')
        """,
        (aid, content, summary, now),
    )
    await db.commit()
    return {
        "id": aid,
        "conversation_id": conversation_id,
        "title": title,
        "content_type": content_type,
        "language": language,
        "source": SOURCE_MODEL_WRITTEN,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


async def register_sandbox_artifact(
    user_email: str,
    conversation_id: str,
    sandbox_artifact_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> dict:
    """
    Register a sandbox-generated file in the unified artifacts table
    (§22 Stage C). The actual file stays on disk inside the sandbox
    container and is served via the existing
    ``/api/artifacts/{cid}/{sandbox_artifact_id}`` proxy endpoint -
    this helper just creates a row pointing at it so the side panel
    (and the model's list_artifacts / read_artifact tools) can see
    sandbox outputs alongside model-written documents.

    We verify the caller's conversation ownership but we don't
    re-validate filename/content_type/size - those came from the
    sandbox-svc response which already enforces its own caps.
    """
    if not await _verify_conversation_owned(conversation_id, user_email):
        raise ArtifactError("conversation not found or not owned by user")

    if not isinstance(sandbox_artifact_id, str) or not sandbox_artifact_id.strip():
        raise ArtifactError("sandbox_artifact_id must be a non-empty string")
    if not isinstance(filename, str) or not filename.strip():
        filename = "unnamed"

    # Use the filename as the artifact title - that's what the user
    # will see in the side panel ("plot.png", "data.xlsx", etc.).
    title = filename.strip()[:MAX_TITLE_CHARS]
    external_url = f"/api/artifacts/{conversation_id}/{sandbox_artifact_id}"

    # Placeholder content string so artifact_versions isn't empty
    # and read_artifact has something meaningful to return for
    # sandbox rows. We don't store the actual bytes - those live on
    # disk in the sandbox container and are fetched via external_url.
    placeholder = (
        f"[Sandbox-generated file: {filename} ({content_type}, "
        f"{size_bytes} bytes). The actual bytes live in the sandbox "
        f"container; fetch them via GET {external_url}.]"
    )

    db = await get_db()
    aid = f"art_{uuid.uuid4().hex[:12]}"
    now = _iso_now()

    await db.execute(
        """
        INSERT INTO artifacts
            (id, conversation_id, user_email, title, content_type,
             language, latest_version, created_at, updated_at,
             source, filename, external_url)
        VALUES (?, ?, ?, ?, ?, NULL, 1, ?, ?, ?, ?, ?)
        """,
        (
            aid, conversation_id, user_email, title, content_type,
            now, now, SOURCE_SANDBOX_GENERATED, filename, external_url,
        ),
    )
    await db.execute(
        """
        INSERT INTO artifact_versions
            (artifact_id, version, content, change_summary,
             created_at, created_by)
        VALUES (?, 1, ?, ?, ?, 'assistant')
        """,
        (aid, placeholder, f"sandbox produced {filename}", now),
    )
    await db.commit()
    return {
        "id": aid,
        "conversation_id": conversation_id,
        "title": title,
        "content_type": content_type,
        "source": SOURCE_SANDBOX_GENERATED,
        "filename": filename,
        "size_bytes": size_bytes,
        "external_url": external_url,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def get_artifact_version(
    user_email: str,
    conversation_id: str,
    artifact_id: str,
    version: Optional[int] = None,
) -> Optional[dict]:
    """
    Load the latest (or a specific) version of an artifact owned by
    this user within this conversation. Returns None for any ownership
    or lookup failure so callers can distinguish 404 paths.
    """
    meta = await _verify_artifact_owned(artifact_id, conversation_id, user_email)
    if meta is None:
        return None

    target_version = version if version is not None else int(meta["latest_version"])
    db = await get_db()
    cur = await db.execute(
        """
        SELECT version, content, change_summary, created_at, created_by
        FROM artifact_versions
        WHERE artifact_id = ? AND version = ?
        """,
        (artifact_id, target_version),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    # Older rows (created before §22 Stage C) don't have source set;
    # treat missing/NULL as model_written for back-compat.
    source = meta.get("source") or SOURCE_MODEL_WRITTEN
    return {
        "id": artifact_id,
        "conversation_id": conversation_id,
        "title": meta["title"],
        "content_type": meta["content_type"],
        "language": meta["language"],
        "source": source,
        "filename": meta.get("filename"),
        "external_url": meta.get("external_url"),
        "latest_version": int(meta["latest_version"]),
        "version": int(row["version"]),
        "content": row["content"],
        "change_summary": row["change_summary"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "artifact_created_at": meta["created_at"],
        "artifact_updated_at": meta["updated_at"],
    }


async def list_artifacts(
    user_email: str,
    conversation_id: str,
) -> list[dict]:
    """
    Return one row per artifact in the conversation (latest-version
    metadata only, no content). Used for the context-injection
    summary block and for the HTTP list endpoint.
    """
    if not await _verify_conversation_owned(conversation_id, user_email):
        return []
    db = await get_db()
    cur = await db.execute(
        """
        SELECT
            a.id, a.title, a.content_type, a.language,
            a.latest_version, a.created_at, a.updated_at,
            a.source, a.filename, a.external_url,
            v.content AS latest_content
        FROM artifacts a
        JOIN artifact_versions v
          ON v.artifact_id = a.id AND v.version = a.latest_version
        WHERE a.conversation_id = ? AND a.user_email = ?
        ORDER BY a.updated_at DESC
        """,
        (conversation_id, user_email),
    )
    rows = await cur.fetchall()
    out = []
    for row in rows:
        content = row["latest_content"] or ""
        source = row["source"] or SOURCE_MODEL_WRITTEN
        # For sandbox rows the `content` we store is a placeholder
        # describing the file, not the real content. Word-count of
        # the placeholder would be misleading, so we zero it out
        # for sandbox rows and let the frontend show size_bytes or
        # a file icon instead.
        if source == SOURCE_SANDBOX_GENERATED:
            word_count = 0
            byte_size = 0
        else:
            word_count = _count_words(content)
            byte_size = len(content.encode("utf-8"))
        out.append({
            "id": row["id"],
            "title": row["title"],
            "content_type": row["content_type"],
            "language": row["language"],
            "source": source,
            "filename": row["filename"],
            "external_url": row["external_url"],
            "latest_version": int(row["latest_version"]),
            "word_count": word_count,
            "byte_size": byte_size,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return out


def _count_words(content: str) -> int:
    # Simple whitespace-split word count. Matches what researchers
    # mean by "how long is my draft" and is cheap to compute.
    return len([w for w in content.split() if w])


# ---------------------------------------------------------------------------
# Update (full content — diffs deferred to Stage B)
# ---------------------------------------------------------------------------

async def update_artifact(
    user_email: str,
    conversation_id: str,
    artifact_id: str,
    content: str,
    change_summary: Optional[str] = None,
    created_by: str = "assistant",
    is_diff: bool = False,
    base_version: Optional[int] = None,
) -> Optional[dict]:
    """
    Append a new version to an existing artifact.

    Stage A path (``is_diff=False``): ``content`` is the full
    replacement text. The new version is a complete snapshot.

    Stage B path (``is_diff=True``): ``content`` is a unified diff to
    apply to the content of ``base_version`` (or the latest version
    if ``base_version`` is omitted). Token-efficient for small edits
    on long documents.

    Concurrency guard: when ``base_version`` is supplied it must
    match the artifact's current ``latest_version``. A mismatch
    means someone else (typically the user via the PATCH endpoint)
    has bumped the artifact since the caller read it, and applying
    our write would silently clobber their edit. We reject with a
    clear error so the caller can re-read and retry.

    Returns a dict with the enriched response shape: in addition to
    the basic metadata, ``applied_hunks`` (None for full-content
    updates), ``lines_added``, ``lines_removed``, and the
    ``base_version`` that was actually applied against.
    """
    meta = await _verify_artifact_owned(artifact_id, conversation_id, user_email)
    if meta is None:
        return None

    # §22 Stage C: sandbox-generated artifacts are read-only from the
    # update path. If the model wants to regenerate, it calls
    # run_python again which produces a fresh artifact with a new id.
    source = meta.get("source") or SOURCE_MODEL_WRITTEN
    if source == SOURCE_SANDBOX_GENERATED:
        raise ArtifactError(
            "cannot update sandbox-generated artifacts; call run_python "
            "again to regenerate the file"
        )

    summary = _validate_change_summary(change_summary)
    if created_by not in ("assistant", "user"):
        raise ArtifactError("created_by must be 'assistant' or 'user'")

    latest_version = int(meta["latest_version"])

    # Concurrency check: if the caller pinned a base_version, it must
    # still be the latest. This applies uniformly to both is_diff
    # paths and full-content updates - a stale full-content overwrite
    # is just as destructive as a stale diff.
    if base_version is not None:
        if not isinstance(base_version, int):
            raise ArtifactError("base_version must be an integer")
        if base_version != latest_version:
            raise ArtifactError(
                f"base_version {base_version} is stale; the artifact is "
                f"now at version {latest_version}. Re-read the latest "
                f"content via read_artifact and retry."
            )

    applied_base = base_version if base_version is not None else latest_version
    applied_hunks: Optional[int] = None

    if is_diff:
        # Load the base version's content and apply the diff.
        db = await get_db()
        cur = await db.execute(
            "SELECT content FROM artifact_versions "
            "WHERE artifact_id = ? AND version = ?",
            (artifact_id, applied_base),
        )
        base_row = await cur.fetchone()
        if base_row is None:
            raise ArtifactError(
                f"base version {applied_base} not found for this artifact"
            )
        base_content = base_row["content"] or ""
        try:
            new_content, stats = apply_unified_diff(base_content, content)
        except ArtifactError:
            raise
        applied_hunks = stats["hunks_applied"]
        lines_added = stats["lines_added"]
        lines_removed = stats["lines_removed"]
        # Now apply the SIZE cap against the RESULT, not the diff.
        new_content = _validate_content(new_content)
    else:
        # Full-content path: validate the incoming string then
        # compute lines_added/removed by diffing the previous latest
        # version against the new content so the response shape is
        # the same for both paths.
        new_content = _validate_content(content)
        db = await get_db()
        prev_cur = await db.execute(
            "SELECT content FROM artifact_versions "
            "WHERE artifact_id = ? AND version = ?",
            (artifact_id, latest_version),
        )
        prev_row = await prev_cur.fetchone()
        prev_content = (prev_row["content"] or "") if prev_row else ""
        lines_added, lines_removed = _compute_line_delta(
            prev_content, new_content
        )

    db = await get_db()
    now = _iso_now()
    next_version = latest_version + 1

    await db.execute(
        """
        INSERT INTO artifact_versions
            (artifact_id, version, content, change_summary,
             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (artifact_id, next_version, new_content, summary, now, created_by),
    )
    await db.execute(
        "UPDATE artifacts SET latest_version = ?, updated_at = ? "
        "WHERE id = ?",
        (next_version, now, artifact_id),
    )
    await db.commit()
    return {
        "id": artifact_id,
        "conversation_id": conversation_id,
        "title": meta["title"],
        "content_type": meta["content_type"],
        "language": meta["language"],
        "source": source,
        "version": next_version,
        "change_summary": summary,
        "created_by": created_by,
        "updated_at": now,
        "base_version": applied_base,
        "applied_hunks": applied_hunks,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------

def build_artifact_summary_block(artifacts: list[dict]) -> Optional[str]:
    """
    Render the ``=== ACTIVE ARTIFACTS ===`` block chat_service
    prepends to the system prompt. Summary-only by design: for
    model-written artifacts we show title + type + version +
    word count; for sandbox-generated ones we show filename +
    type + ``[sandbox output, read-only]`` since they aren't
    editable via update_artifact. Returns None when there are no
    artifacts so the caller can skip the block.
    """
    if not artifacts:
        return None
    lines = ["=== ACTIVE ARTIFACTS ==="]
    for i, a in enumerate(artifacts, start=1):
        source = a.get("source") or SOURCE_MODEL_WRITTEN
        ctype = a.get("content_type") or "text/plain"
        aid = a.get("id") or "?"
        if source == SOURCE_SANDBOX_GENERATED:
            filename = a.get("filename") or a.get("title") or "unnamed"
            lines.append(
                f'{i}. id={aid} "{filename}" ({ctype}, sandbox output, '
                f"read-only)"
            )
        else:
            title = (a.get("title") or "").strip() or "untitled"
            version = a.get("latest_version") or 1
            words = a.get("word_count") or 0
            lines.append(
                f'{i}. id={aid} "{title}" ({ctype}, v{version}, '
                f"{words} words)"
            )
    lines.append("")
    lines.append(
        "Model-written artifacts can be read with read_artifact(id), "
        "edited with update_artifact(id, content) to produce a new "
        "version, or created fresh with create_artifact(...). "
        "Sandbox-generated artifacts (plots, spreadsheets, files "
        "produced by run_python) are read-only - call run_python "
        "again if you need to regenerate. Either kind can be "
        "promoted to the user's persistent document store via "
        "save_artifact_to_documents(artifact_id)."
    )
    lines.append("=== END ACTIVE ARTIFACTS ===")
    return "\n".join(lines)
