"""
Search/replace edits: the shared core of edit_python, update_artifact and
compile_latex.

Search/replace rather than unified diff, by design: update_artifact shipped a
correct unified-diff mode and the model used it in 1 of 66 calls (2%). Line
numbers and hunk lengths are the arithmetic models are worst at. Reproducing a
snippet verbatim is something they do constantly.

Each edit is one {old, new} pair. The key names differ per tool (edit_python
says ``old``/``new``, the artifact tools say ``old_str``/``new_str``), so the
caller passes them; error messages use the same names the model sent.
"""

from __future__ import annotations

import difflib


def near_misses(source: str, needle: str, limit: int = 3) -> list[str]:
    """Closest lines to an unmatched snippet, so the error is actionable.

    A bare "not found" is the fastest route back to re-sending the whole
    document, which is the behaviour these edits exist to remove.
    """
    first = (needle.strip().splitlines() or [""])[0].strip()
    if not first:
        return []
    lines = source.splitlines()
    scored = sorted(
        ((difflib.SequenceMatcher(None, first, ln.strip()).ratio(), ln) for ln in lines),
        key=lambda t: -t[0],
    )
    return [ln.strip()[:160] for score, ln in scored[:limit] if score > 0.5]


def apply_edits(
    source: str,
    edits: list,
    old_key: str = "old",
    new_key: str = "new",
) -> tuple[str, dict]:
    """Apply ordered {old_key, new_key} replacements. Raises ValueError on any miss.

    Each old snippet must match EXACTLY ONCE. Ambiguity is an error rather
    than a first-match guess: silently patching the wrong occurrence produces
    output that looks right and is subtly wrong, which is the worst failure
    available here. Edits apply in order, each against the result of the
    previous one, and either all apply or none do.
    """
    if not isinstance(edits, list) or not edits:
        raise ValueError(
            f"edits must be a non-empty list of {{{old_key}, {new_key}}} objects"
        )

    out = source
    added = removed = 0
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(
                f"edit {i + 1}: must be an object with '{old_key}' and '{new_key}'"
            )
        old = edit.get(old_key)
        new = edit.get(new_key)
        if not isinstance(old, str) or not old:
            raise ValueError(f"edit {i + 1}: '{old_key}' must be a non-empty string")
        if not isinstance(new, str):
            raise ValueError(
                f"edit {i + 1}: '{new_key}' must be a string (use \"\" to delete)"
            )

        count = out.count(old)
        if count == 0:
            hint = near_misses(out, old)
            msg = (
                f"edit {i + 1}: '{old_key}' not found in the current source. It must "
                f"match character-for-character, including indentation."
            )
            if hint:
                msg += " Closest lines in the source: " + " | ".join(hint)
            raise ValueError(msg)
        if count > 1:
            raise ValueError(
                f"edit {i + 1}: '{old_key}' matches {count} places; include more "
                f"surrounding lines so it identifies exactly one."
            )

        out = out.replace(old, new, 1)
        added += len(new.splitlines())
        removed += len(old.splitlines())

    return out, {
        "edits_applied": len(edits),
        "lines_added": added,
        "lines_removed": removed,
    }


def as_sentence(err: Exception) -> str:
    """An apply_edits error ending in a full stop, for callers that append
    more text after it (some messages end with one, some do not)."""
    msg = str(err).rstrip()
    return msg if msg.endswith(".") else msg + "."


# Shape example appended to an edit failure from the artifact tools, so a
# retry can fix the call instead of falling back to re-sending everything.
ARTIFACT_EDITS_EXAMPLE = (
    'expected "edits": [{"old_str": "<exact snippet from the current version>", '
    '"new_str": "<replacement>"}], e.g. "edits": [{"old_str": "\\\\title{Draft}", '
    '"new_str": "\\\\title{Final}"}]'
)
