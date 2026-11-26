"""
Tool-result serialisation for the model-facing ``tool`` message.

A tool result is handed back to the model as the ``content`` of a
``tool``-role message. It used to be ``json.dumps(result)[:8000]`` — a
raw character slice, which turns any oversized result into *syntactically
broken JSON*. The model then parses garbage and either silently re-quotes
it or gets confused (P1 #15 from the 2026-05 internal harness audit).

``truncate_tool_result`` always returns a string that is **valid JSON**
and within the character budget. Three tiers:

1. Fits  -> the result serialised unchanged.
2. Too large but shaped as a dict with lists (the common case:
   paper_search / web_search / search / deep_research / tool_search /
   get_citations) -> shorten long strings first, then drop items from the
   end of whichever list is largest, with a ``_truncated`` note; a bare
   list is trimmed from the end. Still valid, correctly typed JSON.
3. Anything else (a huge string field, no trimmable list, or a single
   item bigger than the whole budget) -> wrap into
   ``{"_truncated": true, "note": ..., "preview": "<prefix>"}``. The
   preview is a JSON string value, valid even when the prefix cuts mid
   structure.
"""

from __future__ import annotations

import copy
import json
import os
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


def _cap_strings(x: Any, cap: int) -> tuple[Any, int]:
    """Copy of `x` with every string longer than `cap` cut to its first `cap`
    chars plus an ellipsis; also returns how many strings were cut."""
    if isinstance(x, str):
        return (x[:cap].rstrip() + " [...]", 1) if len(x) > cap else (x, 0)
    if isinstance(x, dict):
        out, n = {}, 0
        for k, v in x.items():
            out[k], m = _cap_strings(v, cap)
            n += m
        return out, n
    if isinstance(x, list):
        out, n = [], 0
        for v in x:
            w, m = _cap_strings(v, cap)
            out.append(w)
            n += m
        return out, n
    return x, 0


def _lists(d: dict) -> list[str]:
    return [k for k, v in d.items() if isinstance(v, list) and v]


# Text fields are shortened to these lengths, in turn, before any list item is
# dropped: a passage cut to its first 400 chars still carries the finding, a
# dropped passage carries nothing.
STRING_CAPS = (1500, 1000, 700, 400, 250)


def _truncate_shorten(result: Any, limit: int) -> str:
    """Serialise ``result`` for a model-facing tool message, guaranteeing
    the output is valid JSON within ``limit`` characters.

    Over the limit, a dict is first shortened (long strings cut, see
    STRING_CAPS), then list items are dropped from the end of whichever list
    is largest at the time, so the top-ranked items of every list survive
    (before 2026-11 all items of the single largest list went first: a search
    envelope lost all 8 evidence passages and kept its lower-ranked hits).
    A note says what was cut. A bare list is trimmed from the end."""
    serialised = _dumps(result)
    if len(serialised) <= limit:
        return serialised

    if isinstance(result, dict) and _lists(result):
        shortened = 0
        target = result
        for cap in STRING_CAPS:
            target, shortened = _cap_strings(result, cap)
            if len(_dumps(target)) <= limit:
                break
        target = copy.deepcopy(target)
        counts = {k: len(target[k]) for k in _lists(target)}

        def with_note() -> dict:
            parts = [f"{k}: {len(target[k])} of {n} shown" for k, n in counts.items()
                     if len(target[k]) < n]
            if shortened:
                parts.append(f"{shortened} long texts shortened")
            return {**target, "_truncated": "; ".join(parts) + " (to fit the context budget)"}

        while True:
            out = _dumps(with_note())
            if len(out) <= limit:
                return out
            live = [k for k in counts if target[k]]
            if not live:
                break
            biggest = max(live, key=lambda k: len(_dumps(target[k])))
            target[biggest].pop()
        # Even empty lists overflowed (huge non-list fields): fall through.
    elif isinstance(result, list) and result:
        items = copy.deepcopy(result)
        original_count = len(items)
        while items:
            items.pop()  # drop from the end
            note = (
                f"[{original_count - len(items)} of {original_count} "
                f"items omitted to fit the context budget]"
            )
            out = _dumps(items + [note])
            if len(out) <= limit:
                return out

    # Tier 3: generic preview wrap.
    return _generic_wrap(serialised, limit)


def _truncate_drop(result: Any, limit: int) -> str:
    """The cutter before 2026-11 (MUNIN_TOOL_RESULT_CUT=drop): drops items
    from the end of the single largest list."""
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


def cut_mode() -> str:
    """MUNIN_TOOL_RESULT_CUT: `shorten` (strings first, then items across
    lists) or `drop` (the old cutter). `shorten` is the default since the
    2026-11-25 user-failure regression (scorecards/userfail/2026-11-25_full-*,
    2026-11-26_mech-*); `drop` turns it off."""
    m = (os.environ.get("MUNIN_TOOL_RESULT_CUT") or "shorten").strip().lower()
    return m if m in ("shorten", "drop") else "shorten"


def truncate_tool_result(result: Any, limit: int = TOOL_RESULT_CHAR_LIMIT) -> str:
    """Serialise ``result`` for a model-facing tool message, guaranteeing
    the output is valid JSON within ``limit`` characters."""
    if cut_mode() == "shorten":
        return _truncate_shorten(result, limit)
    return _truncate_drop(result, limit)

