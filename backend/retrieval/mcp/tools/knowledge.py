"""
MCP tool: browse_tag_papers.

The BROWSE half of the corpus. `paper_search` answers "what is relevant to this
question"; this answers "what is in this collection", and those are different
questions. Asked to list the first 10 papers in a research group's corpus, a
ranker can only return a sample and call it a list.

WHY it exists. A 2026-08-20 user attached the Deibel group scope and asked for
a listing. The model looked for a tool, correctly reported that it had none,
and offered to run a broad search instead - while the Knowledge page in the web
UI beside it was already paginating that exact collection through
`GET /api/tags/{kind}/{slug}/papers`. The capability existed; only the model
could not reach it. Both now call `tag_browse.browse_tag_papers`, so the UI and
the model can never disagree about what a collection contains.

Scope defaulting: when the caller omits kind/slug, the active `#tag` scope is
used. A user who has attached `#deibel` and says "list the first 10" is naming
that collection, and making the model restate a tag the system already holds is
how it ends up guessing the slug.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..context import current_query_tags

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 200


def _scope_default() -> Optional[tuple[str, str]]:
    """(kind, slug) from the active scope tags, or None.

    Only unambiguous when exactly one tag is attached: two tags AND-combine in
    search, but browse takes a single collection, so silently picking one would
    show a narrower or wider set than the user's scope without saying so.
    """
    tags = current_query_tags.get() or []
    usable = [
        t for t in tags
        if isinstance(t, dict)
        and isinstance(t.get("value"), str) and t["value"].strip()
        and (t.get("kind") or "").strip().lower() in ("topic", "group", "contributor")
    ]
    if len(usable) != 1:
        return None
    tag = usable[0]
    return (tag["kind"].strip().lower(), tag["value"].strip().lower())


async def browse_tag_papers(
    kind: Optional[str] = None,
    slug: Optional[str] = None,
    offset: int = 0,
    limit: int = _DEFAULT_LIMIT,
    sort: str = "year_desc",
) -> dict:
    """List (do not search) the papers in a topic / group / contributor corpus."""
    import asyncio

    import tag_browse  # lazy import — avoids circular init

    if not kind or not slug:
        default = _scope_default()
        if default is None:
            active = current_query_tags.get() or []
            return {
                "error": (
                    "browse_tag_papers needs a `kind` (topic/group/contributor) "
                    "and a `slug`, unless exactly one scope tag is attached to "
                    "the conversation."
                ),
                "active_tags": active,
                "hint": (
                    "Two or more tags are attached, so which collection to "
                    "browse is ambiguous - name one."
                    if len(active) > 1 else
                    "No scope tag is attached. Ask the user which collection "
                    "they mean, or use the tag catalogue in the UI."
                ),
            }
        kind, slug = default
        scoped_from_tags = True
    else:
        scoped_from_tags = False

    try:
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        limit, offset = _DEFAULT_LIMIT, 0

    # Blocking Qdrant scroll; keep the event loop free the way paper_search does.
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(
            None,
            lambda: tag_browse.browse_tag_papers(
                kind=kind, slug=slug, offset=offset, limit=limit, sort=sort
            ),
        )
    except tag_browse.TagBrowseError as e:
        return {"error": str(e), "kind": kind, "slug": slug}
    except Exception as e:                       # never take out the turn
        logger.warning("browse_tag_papers failed for %s/%s: %s", kind, slug, e)
        return {"error": f"browse failed: {e}", "kind": kind, "slug": slug}

    if scoped_from_tags:
        res["scoped_from_active_tags"] = True
    total, shown = res.get("total", 0), len(res.get("papers", []))
    if total == 0:
        res["note"] = (
            f"No papers carry the {kind} tag {slug!r}. This is an enumeration "
            f"of the collection, not a search, so this means the collection is "
            f"empty rather than that a query missed."
        )
    elif offset + shown < total:
        # The model must not present page one as the whole corpus. This is the
        # failure the tool exists to prevent, in a different costume.
        res["note"] = (
            f"Showing {shown} of {total} papers (offset {offset}). Say so when "
            f"reporting, and page with `offset` for more."
        )
    return res
