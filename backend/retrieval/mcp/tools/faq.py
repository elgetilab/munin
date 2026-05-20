"""
MCP tool: faq (§4 active half).

Admin-curated how-to answers loaded from ``config/faq.yml`` (mounted
into the container at ``/app/config/faq.yml``). The model calls this
when the user asks user-facing questions with a clear topic - "how
do I upload a document?", "what's the difference between the
personas?", "what is incognito mode?" - rather than research
questions.

Design notes:

- **Lazy load + module cache**: the YAML is parsed on the first call
  and cached. Changes require a container restart to take effect
  (no hot reload - ~8 topics change rarely enough that hot reload
  isn't worth the complexity).
- **Three call modes**:
  - ``faq(topic="...")`` returns the answer for that exact topic,
    or an error listing valid topics if the id is unknown.
  - ``faq(search="...")`` does case-insensitive substring match
    across topic ids, questions, and answers. Returns a list of
    {topic, question, answer_preview} matches.
  - ``faq()`` returns the table of contents: all topic ids with
    their questions but WITHOUT the answer bodies, so the model
    can summarise what's available without burning tokens on
    full answers.
- **Admin-curated**, NOT user-editable. Edit ``config/faq.yml`` in
  the repo and run ``./deploy.sh agents`` to sync. There is no
  runtime write path.
- **Discovery**: the list of topic ids is also surfaced by
  ``retrieval/capabilities.py``'s system-prompt block, so the
  model knows what topics exist without having to call this tool
  just to discover them.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

FAQ_PATH = os.environ.get("FAQ_PATH", "/app/config/faq.yml")

# Module-level cache. Populated lazily on first access and reused
# for the lifetime of the process. None = not yet attempted;
# {} = attempted and either empty or failed to load.
_cached_topics: Optional[dict[str, dict]] = None


def _load_topics() -> dict[str, dict]:
    """Parse the YAML file. Returns an empty dict on any failure so
    callers can degrade gracefully instead of crashing the tool."""
    global _cached_topics
    if _cached_topics is not None:
        return _cached_topics
    topics: dict[str, dict] = {}
    try:
        with open(FAQ_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw = data.get("topics") or {}
        if isinstance(raw, dict):
            for key, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                question = (entry.get("question") or "").strip()
                answer = (entry.get("answer") or "").strip()
                if not question and not answer:
                    continue
                topics[str(key)] = {
                    "topic": str(key),
                    "question": question,
                    "answer": answer,
                }
    except FileNotFoundError:
        logger.info("faq.yml not found at %s, faq tool disabled", FAQ_PATH)
    except Exception as exc:
        logger.warning("faq.yml load failed: %s", exc)
    _cached_topics = topics
    return topics


# ---------------------------------------------------------------------------
# Helpers used by capabilities.py
# ---------------------------------------------------------------------------

def list_faq_topics() -> list[str]:
    """Return the list of FAQ topic ids for the capabilities block."""
    return sorted(_load_topics().keys())


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def _toc(topics: dict[str, dict]) -> list[dict]:
    """Table of contents: just topic + question, no answer body."""
    return [
        {"topic": t["topic"], "question": t["question"]}
        for t in sorted(topics.values(), key=lambda x: x["topic"])
    ]


async def faq(
    topic: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    topics = _load_topics()
    if not topics:
        return {
            "error": (
                "FAQ store is empty or could not be loaded. "
                f"Expected YAML at {FAQ_PATH}."
            ),
            "topics": [],
        }

    # Mode 1: specific topic
    if topic and isinstance(topic, str):
        key = topic.strip()
        entry = topics.get(key)
        if entry is None:
            return {
                "error": f"unknown topic: {key!r}",
                "available_topics": sorted(topics.keys()),
            }
        return {
            "topic": entry["topic"],
            "question": entry["question"],
            "answer": entry["answer"],
        }

    # Mode 2: substring search
    if search and isinstance(search, str):
        needle = search.strip().lower()
        if not needle:
            return {"table_of_contents": _toc(topics)}
        matches: list[dict] = []
        for entry in topics.values():
            haystack = " ".join([
                entry["topic"],
                entry["question"],
                entry["answer"],
            ]).lower()
            if needle in haystack:
                preview = entry["answer"]
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                matches.append({
                    "topic": entry["topic"],
                    "question": entry["question"],
                    "answer_preview": preview,
                })
        return {
            "search": search,
            "matches": sorted(matches, key=lambda m: m["topic"]),
            "total_matches": len(matches),
        }

    # Mode 3: table of contents
    return {
        "table_of_contents": _toc(topics),
        "total_topics": len(topics),
    }
