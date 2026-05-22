"""
Tool-result serialisation for the model-facing ``tool`` message.

A tool result is handed back to the model as the ``content`` of a
``tool``-role message. It used to be ``json.dumps(result)[:8000]`` — a
raw character slice, which turns any oversized result into *syntactically
broken JSON*. The model then parses garbage and either silently re-quotes
it or gets confused (P1 #15 from munin-audit.md).

``truncate_tool_result`` always returns a string that is **valid JSON**
and within the character budget. Three tiers:

1. Fits  -> the result serialised unchanged.
2. Too large but shaped as a dict/list with a dominant list (the common
   case: paper_search / web_search / deep_research / tool_search /
   get_citations all return a results list) -> drop list items from the
   end until it fits, with a ``_truncated`` note. Still valid, correctly
   typed JSON the model can consume.
3. Anything else (a huge string field, no trimmable list, or a single
   item bigger than the whole budget) -> wrap into
   ``{"_truncated": true, "note": ..., "preview": "<prefix>"}``. The
   preview is a JSON string value, valid even when the prefix cuts mid
   structure.
"""

from __future__ import annotations

import copy
import json
from typing import Any

# Character budget for a tool result in the model-facing message. ~2K
# tokens. Char-based (not token-based) to match the prior behaviour;
# a token-budgeted variant is a possible follow-up.
TOOL_RESULT_CHAR_LIMIT = 8000


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _largest_list_key(d: dict) -> str | None:
    """Return the dict key whose list value serialises largest, or None
    if the dict has no non-empty list values."""
    best_key = None
    best_len = 0
    for k, v in d.items():
        if isinstance(v, list) and v:
            size = len(_dumps(v))
            if size > best_len:
                best_key, best_len = k, size
    return best_key


def _generic_wrap(serialised: str, limit: int) -> str:
    """Last-resort: wrap a too-large dump into a valid JSON object whose
    ``preview`` field is a (truncated) string. Guaranteed <= limit."""
    # Budget the preview conservatively: JSON-escaping can expand the
    # prefix (quotes, backslashes, control chars), so leave generous
    # headroom for the wrapper keys + escaping, then shrink if needed.
    note = (
        f"tool result was {len(serialised)} chars, too large to include "
        f"in full; showing a prefix"
    )
    preview_budget = max(0, limit - len(note) - 120)
    preview = serialised[:preview_budget]
    while True:
        wrapped = _dumps(
            {"_truncated": True, "note": note, "preview": preview}
        )
        if len(wrapped) <= limit or not preview:
            return wrapped
        # Overshot (escaping expanded it) — drop 10% of the preview and retry.
        preview = preview[: max(0, int(len(preview) * 0.9) - 1)]


def truncate_tool_result(result: Any, limit: int = TOOL_RESULT_CHAR_LIMIT) -> str:
    """Serialise ``result`` for a model-facing tool message, guaranteeing
    the output is valid JSON within ``limit`` characters."""
    serialised = _dumps(result)
    if len(serialised) <= limit:
        return serialised

    # Tier 2: trim items off the dominant list.
    trimmable: Any = None
    list_key: str | None = None
    if isinstance(result, dict):
        list_key = _largest_list_key(result)
        if list_key is not None:
            trimmable = result
    elif isinstance(result, list) and result:
        trimmable = result

    if trimmable is not None:
        target = copy.deepcopy(trimmable)
        items = target[list_key] if list_key is not None else target
        original_count = len(items)
        while items:
            items.pop()  # drop from the end
            if list_key is not None:
                note = (
                    f"{original_count - len(items)} of {original_count} "
                    f"items omitted to fit the context budget"
                )
                candidate = dict(target)
                candidate["_truncated"] = note
                out = _dumps(candidate)
            else:
                # Bare list: append the note as a trailing string element
                # so the structure stays a JSON array.
                note = (
                    f"[{original_count - len(items)} of {original_count} "
                    f"items omitted to fit the context budget]"
                )
                out = _dumps(items + [note])
            if len(out) <= limit:
                return out
        # Even an empty list overflowed (huge non-list fields) — fall through.

    # Tier 3: generic preview wrap.
    return _generic_wrap(serialised, limit)
