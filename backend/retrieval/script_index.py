"""Which script produced each sandbox output file in a conversation.

History is replayed to the model as role/content only (chat_context.build),
so on a new turn the model no longer sees the code it wrote earlier. Asked to
change a figure, it rewrote the whole script from memory: guessed column
names, dropped the title, changed the size, renamed the file (lab Phase 1
smoke run, docs/agent-track/LAB-BUGFIX-PLAN.md F1). This module recovers, from
the persisted tool calls, the source of the last successful run that wrote
each output file, so that

- assemble_context can show the current script for the newest files, and
- edit_python(file=...) can patch that script instead of whatever ran last.

Pure functions over stored messages; no sandbox call.
"""
from __future__ import annotations

from typing import Callable, Optional

SCRIPT_TOOLS = ("run_python", "edit_python")
MAX_FILES = 3
MAX_BLOCK_TOKENS = 6000
BLOCK_HEADER = (
    "Current scripts (the code that last wrote each output file in this "
    "conversation; the kernel state from these runs may still be live). To "
    "change one of these files, call edit_python with file set to its name "
    "and change only what the user asked for."
)


def _source_of(call: dict) -> Optional[str]:
    args = call.get("arguments")
    result = call.get("result")
    if not isinstance(args, dict) or not isinstance(result, dict):
        return None
    if call.get("name") == "edit_python":
        code = result.get("resulting_code")
    else:
        code = args.get("code")
    return code if isinstance(code, str) and code.strip() else None


def _ok(result: dict) -> bool:
    return not result.get("error") and not result.get("timed_out")


def scripts_by_file(messages: list[dict]) -> list[tuple[str, str]]:
    """``[(filename, source)]`` for every output file written by a successful
    run_python / edit_python call, newest write first. The source is the run
    that LAST wrote the file. A file whose last write failed keeps its last
    successful source."""
    latest: dict[str, tuple[int, str]] = {}
    seq = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") not in SCRIPT_TOOLS:
                continue
            seq += 1
            result = call.get("result")
            source = _source_of(call)
            if source is None or not isinstance(result, dict) or not _ok(result):
                continue
            for art in result.get("artifacts") or []:
                if not isinstance(art, dict):
                    continue
                name = art.get("filename")
                if isinstance(name, str) and name:
                    latest[name] = (seq, source)
    ordered = sorted(latest.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(name, src) for name, (_, src) in ordered]


def script_for_file(messages: list[dict], filename: str) -> Optional[str]:
    """Source that last wrote ``filename`` (exact name, else basename match)."""
    pairs = scripts_by_file(messages)
    for name, src in pairs:
        if name == filename:
            return src
    base = filename.rsplit("/", 1)[-1]
    for name, src in pairs:
        if name.rsplit("/", 1)[-1] == base:
            return src
    return None


def current_scripts_block(
    messages: list[dict],
    count_tokens: Callable[[str], int],
    max_files: int = MAX_FILES,
    max_tokens: int = MAX_BLOCK_TOKENS,
) -> str:
    """Text block for the system prompt, or "" when there is nothing to show.

    Several files written by the same script are listed together under one
    source. Scripts are added newest first while they fit in ``max_tokens``;
    one that does not fit is skipped, so an old huge script never pushes out a
    recent small one.
    """
    if max_tokens <= 0:
        return ""
    groups: list[tuple[list[str], str]] = []
    for name, src in scripts_by_file(messages):
        for names, gsrc in groups:
            if gsrc == src:
                names.append(name)
                break
        else:
            if len(groups) >= max_files:
                continue
            groups.append(([name], src))
    if not groups:
        return ""
    parts = [BLOCK_HEADER]
    used = count_tokens(BLOCK_HEADER)
    shown = 0
    for names, src in groups:
        piece = f"\n\n### {', '.join(names)}\n```python\n{src.rstrip()}\n```"
        cost = count_tokens(piece)
        if used + cost > max_tokens:
            continue
        parts.append(piece)
        used += cost
        shown += 1
    return "".join(parts) if shown else ""
