"""
Context assembly + compaction for chat completions.

Responsibilities:
    * Assemble a vLLM-ready message list from a stored conversation, a system
      prompt, optional RAG snippets, and the incoming user message.
    * Keep the total payload inside the model's context window by summarizing
      older messages via vLLM when the budget is exceeded.
    * Generate conversation titles from the first exchange.

Token accounting uses the real Qwen3 tokenizer when its `tokenizer.json`
is mounted (see `approx_tokens`); it falls back to a ~4-chars-per-token
heuristic when the file isn't reachable. The heuristic alone undercounts
code / LaTeX / JSON by 1.5-2x, which used to let oversized prompts slip
past the budget and hit vLLM as a hard error (P1 #8).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from database import VLLM_MODEL_NAME, thinking_off_fields
import asyncio

from chat_store import get_conversation, get_messages_after_index, update_summary
from vllm_client import vllm_post_json, VLLMRequestError
from usage_tracker import record_usage
from answer_tags import strip_answer_tags

logger = logging.getLogger(__name__)

# Default output-token cap for a chat turn. Single source so the history-trim
# reservation (GENERATION_RESERVE) and the actual request cap stay in lockstep;
# a mismatch (reserve 8000 < cap 16384) is how a heavy tool-loop turn overflowed
# the window (docs/paper-track/done/CONTEXT-BUDGET-FIX-SCOPE.md, Tier 3).
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("VLLM_MAX_OUTPUT_TOKENS", "16384"))
MAX_CONTEXT = int(os.getenv("VLLM_MAX_CONTEXT", "60000"))
# Reserve the REAL output budget when trimming history (Tier 3): was 8000, half
# the actual cap, which left the prompt room to overflow once tool results piled
# up. Defaults to DEFAULT_MAX_OUTPUT_TOKENS so the two never drift again.
GENERATION_RESERVE = int(
    os.getenv("VLLM_GENERATION_RESERVE", str(DEFAULT_MAX_OUTPUT_TOKENS))
)
SUMMARY_TOKEN_BUDGET = 1200

# --- Per-call output fitting (Tier 1) --------------------------------------
# The TRUE served context window (prompt + output combined); mirrors
# start-vllm-service.sh --max-model-len. assemble_context trims history once at
# turn start, but tool results accumulate DURING the tool loop, so the output
# budget must be re-fit to the room the (current) prompt leaves before each vLLM
# call or a heavy fan-out turn overflows. (CONTEXT-BUDGET-FIX-SCOPE.md, Tier 1.)
MAX_MODEL_LEN = int(os.getenv("VLLM_MAX_MODEL_LEN", "65536"))
# Cushion for the gap between our token count and vLLM's chat-template framing.
CTX_MARGIN = int(os.getenv("VLLM_CTX_MARGIN", "512"))
# A turn whose prompt leaves less than this for output isn't worth running.
MIN_OUTPUT_TOKENS = int(os.getenv("VLLM_MIN_OUTPUT_TOKENS", "256"))
# Safety-net retries if vLLM still rejects for context length (local count
# under-estimated, e.g. tokenizer not mounted -> char heuristic). Raised 3 -> 5
# in 2026-08: halving from DEFAULT_MAX_OUTPUT_TOKENS reaches only 2048 in three
# steps (16384 -> 8192 -> 4096 -> 2048), and production hit a wall of rejections
# that were still over the window at 2048. Three steps could not reach a viable
# budget, so the ladder exhausted and the turn died; five reaches 512.
MAX_REFIT_RETRIES = int(os.getenv("VLLM_MAX_REFIT_RETRIES", "5"))

# --- Turn headroom and the no-send floor (CONTEXT-OVERFLOW-PLAN.md) ----------
# History is trimmed once at turn start, and used to be allowed the whole
# MAX_CONTEXT - GENERATION_RESERVE (43.6K) while the tools schema (~6K) and
# the turn's own tool calls had to fit in what was left: nothing, once a long
# session's history neared its cap. TURN_HEADROOM is room kept free for the
# turn's tool calls and results, on top of the schema and the output reserve.
TURN_HEADROOM = int(os.getenv("VLLM_TURN_HEADROOM", "12000"))
# Schema size assumed where the routed profile is not known (the background
# pre-compaction). The largest live schema measured was 7,289 (code).
DEFAULT_SCHEMA_TOKENS = int(os.getenv("VLLM_DEFAULT_SCHEMA_TOKENS", "7300"))
# When the prompt leaves less than this for the answer, older history is left
# out of the copy sent to vLLM before giving up on the call.
MIN_USEFUL_OUTPUT_TOKENS = int(os.getenv("VLLM_MIN_USEFUL_OUTPUT_TOKENS", "4096"))
# Tools whose arguments are bulk payload (source code, documents). Their
# arguments from earlier loop iterations may be elided like old tool results;
# small arguments (a search query) cost little and stay.
BULK_ARGUMENT_TOOLS = frozenset({
    "run_python", "edit_python", "create_artifact", "update_artifact",
    "compile_latex",
})
# What the user sees when a turn cannot continue because the context is full.
# The raw vLLM error stays in the log only.
CONTEXT_FULL_MESSAGE = (
    "This conversation got too long for the model to continue in one step. "
    "Press Continue to carry on, or start a new chat and attach the files again."
)
CONTEXT_FULL_REASON = "context_full"


def turn_start_budget(schema_tokens: Optional[int] = None) -> int:
    """Token budget for system prompt + history + the new user message at
    turn start: the window minus the output reserve, the tools schema and
    TURN_HEADROOM, never more than the old MAX_CONTEXT - GENERATION_RESERVE
    (so a deployment that lowered VLLM_MAX_CONTEXT keeps its cap)."""
    schema = DEFAULT_SCHEMA_TOKENS if schema_tokens is None else max(0, schema_tokens)
    window_fit = MAX_MODEL_LEN - GENERATION_RESERVE - schema - TURN_HEADROOM
    return max(4000, min(MAX_CONTEXT - GENERATION_RESERVE, window_fit))

SUMMARIZE_PROMPT = (
    "You are compacting an ongoing conversation so that a language model can "
    "continue it without losing context. Produce a concise third-person "
    "summary that preserves: user intent, stated facts, decisions, named "
    "entities, open questions, and any tool results that influenced the "
    "answer. Keep it under 300 words. Do not add commentary."
)

TITLE_PROMPT = (
    "Generate a 5-8 word title summarizing this conversation. "
    "Return ONLY the title, no quotes or punctuation."
)


# Path to the Qwen3 tokenizer.json. Option B (see DECISIONS.md
# 2026-05-21): deploy.sh copies just the tokenizer files out of the
# vLLM model dir into a small dedicated dir, mounted read-only into the
# retrieval container — the container never needs the 19 GB of weights.
QWEN_TOKENIZER_PATH = os.getenv(
    "QWEN_TOKENIZER_PATH", "/models/qwen/tokenizer.json"
)

# Lazily-loaded HuggingFace tokenizer. `_tokenizer_tried` ensures one
# load attempt per process: a missing file (e.g. the vLLM model hasn't
# downloaded yet, or a misconfigured deploy) degrades to the char
# heuristic without re-attempting the load on every call.
_tokenizer = None
_tokenizer_tried = False


def _get_tokenizer():
    """Return the loaded Qwen tokenizer, or None if it isn't available.
    First call attempts the load; subsequent calls are cached."""
    global _tokenizer, _tokenizer_tried
    if _tokenizer_tried:
        return _tokenizer
    _tokenizer_tried = True
    try:
        from tokenizers import Tokenizer

        _tokenizer = Tokenizer.from_file(QWEN_TOKENIZER_PATH)
        logger.info("Qwen tokenizer loaded from %s", QWEN_TOKENIZER_PATH)
    except Exception as e:
        logger.warning(
            "Qwen tokenizer unavailable (%s); token budgeting falls back "
            "to the ~4-chars-per-token heuristic, which undercounts code.",
            e,
        )
        _tokenizer = None
    return _tokenizer


def approx_tokens(text: str) -> int:
    """Token count for ``text``.

    Exact when the Qwen3 tokenizer is mounted; otherwise a ~4-chars-per-
    token heuristic. The name stays "approx" because callers add their
    own message-framing overhead (see ``_message_tokens``), so the total
    budget figure is still an estimate either way.
    """
    if not text:
        return 0
    tok = _get_tokenizer()
    if tok is not None:
        return max(1, len(tok.encode(text).ids))
    return max(1, len(text) // 4)


def _content_to_text(content: Any) -> str:
    """
    Flatten a vLLM message ``content`` field (str OR OpenAI-style list of
    typed blocks) into a string for approximate token accounting. The
    image_url blocks themselves cost ~100 vLLM tokens each regardless of
    source, which we can budget for explicitly rather than trying to
    approximate from a data URL length.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p)
    return ""


def _count_image_blocks(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1 for block in content
        if isinstance(block, dict) and block.get("type") == "image_url"
    )


# A fixed per-image vLLM token cost. Matches the probe result documented
# in future_features.md §5 (~100 tokens per image regardless of resolution
# because vLLM resizes internally). Budgeted here so multimodal turns
# don't silently overflow the context window.
_PER_IMAGE_TOKENS = 120


def _tool_calls_tokens(message: dict) -> int:
    """Token cost of a message's ``tool_calls`` payload.

    Assistant messages in the tool loop carry the function name and its JSON
    arguments in ``tool_calls``, not in ``content`` (chat_service builds them
    at the OpenAI shape, ``{"function": {"name", "arguments"}}``). vLLM's chat
    template renders those into the prompt, so they are real prompt tokens.

    Counting ``content`` alone scored such a message at the 4-token framing
    allowance while a single ``run_python`` call measured 3.8-6.5K tokens of
    source. That undercount is why both budget guards believed a tool-heavy
    turn had room to spare while its real prompt walked past the window
    (observed live: prompts of 65542-70939 against a 65536 window, with
    Tier 2's 48640 threshold never once tripping).

    Tool RESULTS are deliberately not counted here: they reach vLLM as
    separate ``role: "tool"`` messages whose payload sits in ``content``, so
    they are already counted, and they are what ``budget_tool_results``
    elides.
    """
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return 0
    total = 0
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        fn = fn if isinstance(fn, dict) else {}
        # OpenAI shape nests under "function"; the persisted shape
        # (chat_store) keeps name/arguments at the top level. Accept both.
        name = fn.get("name") or call.get("name") or ""
        args = fn.get("arguments") if "arguments" in fn else call.get("arguments")
        if args is None:
            args_text = ""
        elif isinstance(args, str):
            args_text = args
        else:
            try:
                args_text = json.dumps(args, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                args_text = str(args)
        # +8 for the per-call wrapper (id, type, function envelope).
        total += approx_tokens(str(name)) + approx_tokens(args_text) + 8
    return total


def _message_tokens(message: dict) -> int:
    content = message.get("content")
    text_tokens = approx_tokens(_content_to_text(content))
    image_tokens = _count_image_blocks(content) * _PER_IMAGE_TOKENS
    tool_call_tokens = _tool_calls_tokens(message)
    # Add a small framing overhead per message (~4 tokens for role/wrapping).
    return text_tokens + image_tokens + tool_call_tokens + 4


# Token cost of a rendered tools schema, keyed by a hash of its JSON. The
# schema is stable per routed profile, so this is a handful of entries; the
# cap is only a guard against an unbounded persona set.
_TOOLS_TOKENS_CACHE: dict[int, int] = {}
_TOOLS_TOKENS_CACHE_MAX = 64


def tools_schema_tokens(tools: Optional[list]) -> int:
    """Prompt cost of the ``tools`` schema sent alongside a chat request.

    The schema travels in ``body["tools"]``, NOT in ``messages``, but vLLM
    renders it into the prompt. Neither ``fit_max_tokens`` nor
    ``budget_tool_results`` can see it unless the caller measures it here and
    passes the result in as ``extra_tokens``. Live schemas cost 6220 (chat),
    6898 (research) and 7289 (code) tokens, and until 2026-08 they were
    attached to the body AFTER the fit ran, so every call under-budgeted by
    that much.
    """
    if not tools:
        return 0
    try:
        payload = json.dumps(tools, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(tools)
    key = hash(payload)
    cached = _TOOLS_TOKENS_CACHE.get(key)
    if cached is not None:
        return cached
    # +16 for the template's framing around the tool block itself.
    count = approx_tokens(payload) + 16
    if len(_TOOLS_TOKENS_CACHE) < _TOOLS_TOKENS_CACHE_MAX:
        _TOOLS_TOKENS_CACHE[key] = count
    return count


def prompt_tokens(messages: list) -> int:
    """Token count for a messages list via the real Qwen tokenizer (falls back
    to a char heuristic per message), plus a small allowance for the assistant
    generation prompt / trailing special tokens. Canonical counterpart of
    main.py::_raw_prompt_tokens, which predates this (dedupe is a follow-up)."""
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        try:
            total += _message_tokens(m)
        except Exception:
            total += max(1, len(str(m.get("content", ""))) // 4)
    return total + 8


def fit_max_tokens(
    messages: list, requested_max: Optional[int], *, extra_tokens: int = 0
) -> int:
    """Clamp the output budget so prompt + output stays within MAX_MODEL_LEN.

    ``extra_tokens`` covers prompt content that is NOT in ``messages``: the
    ``tools`` schema (see ``tools_schema_tokens``), plus any measured
    undercount fed back from a vLLM rejection. Omitting it is what let the
    schema's 6-7K tokens go unbudgeted on every call.

    Returns the fitted max_tokens. For the common case (a heavy but sub-window
    prompt) the returned value fits exactly. If the prompt itself (near-)fills
    the window it returns MIN_OUTPUT_TOKENS and logs a warning: the request may
    still be rejected and trimming the conversation (a separate fix) is the real
    remedy, but that is rarer than the accumulated-tool-result overflow this
    guards. (CONTEXT-BUDGET-FIX-SCOPE.md, Tier 1.)"""
    room = MAX_MODEL_LEN - prompt_tokens(messages) - extra_tokens - CTX_MARGIN
    if room < MIN_OUTPUT_TOKENS:
        logger.warning(
            "prompt fills the context window (room=%d < %d); clamping output to "
            "the floor - the conversation likely needs trimming", room,
            MIN_OUTPUT_TOKENS,
        )
        return MIN_OUTPUT_TOKENS
    if not isinstance(requested_max, int) or requested_max <= 0:
        return room
    return min(requested_max, room)


# Compact replacement for a tool result elided to fit the window (Tier 2). The
# caller preserves role/tool_call_id so the assistant<->tool pairing stays valid;
# only the content shrinks.
_ELIDED_TOOL_RESULT = (
    '{"_elided": true, "note": "earlier tool result trimmed to fit the context '
    'window; re-run the tool if you need its details again"}'
)

# Replacement for the arguments of an earlier bulk tool call (the source code
# of an old run_python, say). Valid JSON so the chat template can render it.
# The kernel keeps its state across runs, so the definitions survive.
_ELIDED_TOOL_ARGUMENTS = json.dumps({
    "_elided": True,
    "note": ("arguments of this earlier call trimmed to fit the context "
             "window; for run_python the variables and functions it defined "
             "are still live in the kernel"),
})


def _args_elided(call: dict) -> bool:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    return fn.get("arguments") == _ELIDED_TOOL_ARGUMENTS


def _bulk_calls(message: dict) -> list[int]:
    """Indices of a message's tool calls whose arguments may be elided."""
    out = []
    for j, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        if fn.get("name") in BULK_ARGUMENT_TOOLS and not _args_elided(call):
            out.append(j)
    return out


def prompt_composition(
    messages: list, *, extra_tokens: int = 0, history_end: Optional[int] = None,
) -> dict:
    """Where a prompt's tokens are, for the overflow log line (plan Step 0).

    Buckets: system, history (messages before ``history_end``, when given),
    user, assistant_text, tool_args, tool_results, images, schema, total."""
    comp = {k: 0 for k in ("system", "history", "user", "assistant_text",
                           "tool_args", "tool_results", "images")}
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        text = approx_tokens(_content_to_text(content)) + 4
        comp["images"] += _count_image_blocks(content) * _PER_IMAGE_TOKENS
        role = m.get("role")
        if role == "system":
            comp["system"] += text
        elif history_end is not None and 0 < i < history_end:
            comp["history"] += text + _tool_calls_tokens(m)
        elif role == "tool":
            comp["tool_results"] += text
        elif role == "assistant":
            comp["assistant_text"] += text
            comp["tool_args"] += _tool_calls_tokens(m)
        else:
            comp["user"] += text
    comp["schema"] = extra_tokens
    comp["total"] = sum(comp.values()) + 8
    return comp


def drop_history_to_fit(
    messages: list, history_end: int, target_tokens: int, *, extra_tokens: int = 0,
):
    """Leave the oldest earlier-turn messages (``messages[1:history_end]``)
    out of the copy sent to vLLM until the prompt fits ``target_tokens``.
    Plan Step 3a, the move left when the turn's own messages cannot shrink
    further. Never touches the system prompt, the user's new message or this
    turn's tool loop, and never the persisted conversation. Returns
    (new_messages, n_dropped); a note in the system prompt says how many
    were left out."""
    if not messages or history_end <= 1:
        return list(messages), 0
    total = prompt_tokens(messages) + extra_tokens
    if total <= target_tokens:
        return list(messages), 0
    keep_from = 1
    while keep_from < history_end and total > target_tokens:
        m = messages[keep_from]
        total -= _message_tokens(m) if isinstance(m, dict) else 0
        keep_from += 1
    dropped = keep_from - 1
    system = dict(messages[0])
    note = (f"[{dropped} earlier message(s) of this conversation were left out "
            "to fit the context window.]")
    if isinstance(system.get("content"), str):
        system["content"] = f"{system['content']}\n\n{note}"
    return [system, *messages[keep_from:]], dropped


def budget_loop_messages(
    messages: list,
    *,
    target_tokens: Optional[int] = None,
    extra_tokens: int = 0,
):
    """Keep a tool-loop prompt within budget so the output budget
    (fit_max_tokens) is not starved to the floor. Returns
    (new_messages, counts) with counts ``{"results", "args", "pending"}``.

    Does NOT mutate the input: the full messages stay in the persisted
    conversation; only the copy sent to vLLM is trimmed. In order, until the
    prompt fits:

    1. tool results from PRIOR loop iterations (before the most recent
       assistant message), oldest first (Tier 2);
    2. the arguments of earlier bulk tool calls (``BULK_ARGUMENT_TOOLS``, the
       code of old run_python calls and the like), oldest first, never the
       most recent assistant message's calls. Before 2026-11 nothing could
       remove these, and a long code turn carried up to 37.7K tokens of them
       (CONTEXT-OVERFLOW-PLAN.md, Step 1);
    3. last resort, the pending batch (results after the last assistant
       message), largest first, degrading the turn instead of letting vLLM
       reject it (chat 5938cd64, 2026-07-08).
    """
    if target_tokens is None:
        target_tokens = MAX_MODEL_LEN - GENERATION_RESERVE - CTX_MARGIN
    counts = {"results": 0, "args": 0, "pending": 0}

    def _tok(m: dict) -> int:
        try:
            return _message_tokens(m)
        except Exception:
            return max(1, len(str(m.get("content", ""))) // 4)

    per = [_tok(m) if isinstance(m, dict) else 0 for m in messages]
    # ``extra_tokens`` is prompt weight outside ``messages`` (tools schema, plus
    # any undercount vLLM has already told us about). Without it this comparison
    # ran against a count that omitted the schema, so the threshold was never
    # reached and this function elided nothing in production for two months.
    total = sum(per) + 8 + extra_tokens
    if total <= target_tokens:
        return list(messages), counts

    last_asst = max(
        (i for i, m in enumerate(messages)
         if isinstance(m, dict) and m.get("role") == "assistant"),
        default=-1,
    )
    stub_tokens = _tok({"role": "tool", "content": _ELIDED_TOOL_RESULT})
    out = list(messages)

    # 1. old tool results
    for i, m in enumerate(messages):
        if total <= target_tokens or i >= last_asst:
            break  # fits, or we've reached the pending (protected) batch
        if not (isinstance(m, dict) and m.get("role") == "tool"):
            continue
        if m.get("content") == _ELIDED_TOOL_RESULT:
            continue  # already elided (idempotent)
        total -= (per[i] - stub_tokens)
        out[i] = {**m, "content": _ELIDED_TOOL_RESULT}
        counts["results"] += 1

    # 2. arguments of earlier bulk tool calls
    for i, m in enumerate(messages):
        if total <= target_tokens or i >= last_asst:
            break
        if not (isinstance(m, dict) and m.get("role") == "assistant"):
            continue
        bulk = _bulk_calls(m)
        if not bulk:
            continue
        calls = list(m.get("tool_calls") or [])
        for j in bulk:
            call = calls[j]
            fn = dict(call.get("function") or {})
            before = _tool_calls_tokens({"tool_calls": [call]})
            fn["arguments"] = _ELIDED_TOOL_ARGUMENTS
            calls[j] = {**call, "function": fn}
            after = _tool_calls_tokens({"tool_calls": [calls[j]]})
            total -= max(0, before - after)
            counts["args"] += 1
            if total <= target_tokens:
                break
        out[i] = {**out[i], "tool_calls": calls}

    # 3. Last resort: the passes above stop at the pending batch (i >=
    # last_asst) to preserve the current step's results. But a SINGLE fan-out
    # iteration whose own tool results exceed the window (deep_research + many
    # searches, chat 5938cd64, 2026-07-08) would still overflow. Degrade
    # instead of dying: elide the largest pending tool results, largest first,
    # until the prompt fits. The model loses some tool detail (and can re-run
    # the tool) but the turn completes.
    if total > target_tokens and last_asst >= 0:
        pending = [
            i for i, m in enumerate(messages)
            if i > last_asst
            and isinstance(m, dict)
            and m.get("role") == "tool"
            and m.get("content") != _ELIDED_TOOL_RESULT
            and per[i] > stub_tokens
        ]
        pending.sort(key=lambda i: per[i], reverse=True)
        for i in pending:
            if total <= target_tokens:
                break
            total -= (per[i] - stub_tokens)
            out[i] = {**messages[i], "content": _ELIDED_TOOL_RESULT}
            counts["pending"] += 1

    return out, counts


def budget_tool_results(
    messages: list,
    *,
    target_tokens: Optional[int] = None,
    extra_tokens: int = 0,
):
    """Back-compat wrapper over ``budget_loop_messages``: returns
    (new_messages, total number of elisions)."""
    out, counts = budget_loop_messages(
        messages, target_tokens=target_tokens, extra_tokens=extra_tokens)
    return out, sum(counts.values())


def minimal_wrap_up_messages(
    messages: list, history_end: Optional[int], content_so_far: str,
) -> list[dict]:
    """The smallest prompt that still lets the model answer after the context
    filled up mid-turn (plan Step 3b): the system prompt, the user's message,
    and one note carrying what was written this turn and the latest tool
    results, clipped. No earlier turns, no tool-call trail."""
    system = next((m for m in messages if isinstance(m, dict)
                   and m.get("role") == "system"), None)
    user = None
    if history_end is not None and 0 <= history_end < len(messages):
        user = messages[history_end]
    if not (isinstance(user, dict) and user.get("role") == "user"):
        user = next((m for m in reversed(messages) if isinstance(m, dict)
                     and m.get("role") == "user"), None)
    last_asst = max((i for i, m in enumerate(messages) if isinstance(m, dict)
                     and m.get("role") == "assistant"), default=-1)
    latest = [m for m in messages[last_asst + 1:] if isinstance(m, dict)
              and m.get("role") == "tool"] if last_asst >= 0 else []
    results = "\n\n".join(_content_to_text(m.get("content"))[:6000] for m in latest)
    written = (content_so_far or "").strip()[-8000:]
    note = (
        "The working context of this turn grew past the model's window, so the "
        "earlier tool steps are not shown.\n\n"
        f"What you wrote to the user so far this turn:\n{written or '(nothing yet)'}\n\n"
        f"Latest tool results:\n{results[:24000] or '(none)'}\n\n"
        "Write your final answer to the user now from what was done. Do not "
        "call tools. Say plainly which steps did not finish, and that the user "
        "can press Continue to carry on."
    )
    out = [m for m in (system, user) if m is not None]
    out.append({"role": "user", "content": note})
    return out


def is_ctx_overflow(text: str) -> bool:
    """True if `text` is vLLM's context-length rejection message."""
    return "maximum context length" in (text or "").lower()


def halve_max_tokens(body: dict) -> bool:
    """Halve body['max_tokens'] for a retry after a context-length rejection
    slipped past the preemptive clamp (local count under-estimated). Returns
    False when there's no output budget left to give up."""
    current = body.get("max_tokens")
    if not isinstance(current, int) or current <= MIN_OUTPUT_TOKENS:
        return False
    body["max_tokens"] = max(MIN_OUTPUT_TOKENS, current // 2)
    return True


# vLLM rejects an oversized request in two shapes, and only one carries a real
# measurement:
#
#   "Input length (70787) exceeds model's maximum context length (65536)"
#       -> 70787 is the MEASURED prompt. Usable.
#   "...you requested 2048 output tokens and your prompt contains at least
#    63489 input tokens, for a total of at least 65537 tokens"
#       -> that figure is DERIVED as (window + 1 - max_tokens), not measured.
#          Every observed instance satisfied 65536 + 1 - 2048 == 63489 exactly.
#          Refitting from it cannot converge (it moves with max_tokens), which
#          is the trap main.py::_raw_prompt_tokens documents. Unusable.
_EXACT_PROMPT_TOKENS_RE = re.compile(r"input length \((\d+)\)\s+exceeds", re.I)


def parse_exact_prompt_tokens(text: str) -> Optional[int]:
    """vLLM's MEASURED prompt size, if the rejection carries one.

    Returns None for the "at least N input tokens" shape, whose figure is
    derived from max_tokens rather than measured. Callers must treat None as
    "no ground truth available", not as "no overflow".
    """
    if not text:
        return None
    match = _EXACT_PROMPT_TOKENS_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def refit_max_tokens(body: dict, error_text: str) -> Optional[str]:
    """Shrink the output budget after a context-length rejection.

    Returns:
        ``"exact"``  vLLM handed us a measured prompt size and the output
                     budget was refit to the room it actually leaves.
        ``"halved"`` no measurement available, so fall back to the halving
                     search (the only convergent option for the derived shape).
        ``None``     no output budget can rescue this request: the prompt
                     alone meets or exceeds the window, so only prompt
                     reduction helps. The caller should re-elide and retry.

    Progress is guaranteed in the ``"exact"`` branch by clamping strictly
    below the current value, so a bad measurement cannot spin the retry loop.
    """
    exact = parse_exact_prompt_tokens(error_text)
    if exact is None:
        return "halved" if halve_max_tokens(body) else None

    room = MAX_MODEL_LEN - exact - CTX_MARGIN
    current = body.get("max_tokens")
    if isinstance(current, int) and current > 0:
        room = min(room, current - 1)
    if room < MIN_OUTPUT_TOKENS:
        return None
    body["max_tokens"] = room
    return "exact"


def _augment_with_attachments(message: dict) -> str:
    """
    Return the message content augmented with an inline
    ``[Attachments: ...]`` marker if the stored row has attachments.
    This is how the model discovers the document_ids of past
    attachments so it can reference them via ``view_attachment``
    (§5 re-view). The stored ``messages.content`` column is NOT
    modified - the marker only appears in the assembled context we
    send to vLLM, so FTS search keeps working against the clean text.
    """
    raw = message.get("content") or ""
    if not isinstance(raw, str):
        # Historical rows store text-only content; a non-string is
        # defensive. Fall back to string coercion and skip the marker.
        return str(raw)
    attachments = message.get("attachments") or []
    if not attachments:
        return raw
    parts: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        # Only image attachments are re-viewable via view_attachment, and only
        # images are absent from the reloaded text (images are one-shot). Text
        # documents are inlined into this message's content itself, so a
        # "call view_attachment" hint would be wrong (view_attachment rejects
        # non-images) and redundant - skip them.
        ctype = att.get("content_type") or ""
        if not ctype.startswith("image/"):
            continue
        doc_id = att.get("document_id")
        filename = att.get("filename") or "unknown"
        if doc_id:
            parts.append(f"{doc_id} ({filename}, {ctype})")
    if not parts:
        return raw
    marker = "[Attachments on this message: " + "; ".join(parts) + (
        ". Call view_attachment(document_id=\"...\") to see one again.]"
    )
    return f"{raw}\n\n{marker}" if raw else marker


def _rag_context_to_text(rag_context: Optional[dict]) -> str:
    if not rag_context:
        return ""
    docs = rag_context.get("documents") or []
    if not docs:
        return ""
    lines = ["Relevant context:"]
    for i, d in enumerate(docs, start=1):
        title = d.get("title") or d.get("source") or "doc"
        content = d.get("content") or ""
        lines.append(f"[{i}] {title}: {content}")
    return "\n".join(lines)


async def _call_vllm(
    messages: list[dict], max_tokens: int, *, purpose: str
) -> Optional[str]:
    """
    Call vLLM /v1/chat/completions non-streaming. Returns text or None.

    Qwen3's default reasoning phase would otherwise consume the entire
    token budget for simple tasks like summarisation and title generation,
    returning empty content and silently breaking compaction. We disable
    thinking via chat_template_kwargs — same fix applied to
    query_expansion.py and mcp/tools/llm.py.

    ``purpose`` tags the usage with the call site ("summary" / "title")
    so it shows up under the right bucket in the per-request aggregator.
    """
    try:
        data = await vllm_post_json(
            {
                "model": VLLM_MODEL_NAME,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "stream": False,
                **thinking_off_fields(),
            },
            timeout=60.0,
            foreground=False,
            purpose=purpose,
        )
    except VLLMRequestError:
        return None
    record_usage(purpose, data.get("usage"))
    choices = data.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    return content if content else None


async def summarize_messages(
    conversation_id: str,
    existing_summary: Optional[str],
    messages_to_summarize: list[dict],
) -> Optional[str]:
    """
    Incrementally update a conversation summary. `messages_to_summarize` is
    the slice of messages that must be folded into the summary. Returns the
    new summary text, or None if vLLM is unreachable.
    """
    if not messages_to_summarize:
        return existing_summary

    transcript_lines: list[str] = []
    for m in messages_to_summarize:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        transcript_lines.append(f"{role.upper()}: {content}")
    transcript = "\n".join(transcript_lines)

    user_blocks: list[str] = []
    if existing_summary:
        user_blocks.append(f"Existing summary:\n{existing_summary}")
    user_blocks.append(f"New messages to fold in:\n{transcript}")
    user_blocks.append("Return the updated summary.")

    messages = [
        {"role": "system", "content": SUMMARIZE_PROMPT},
        {"role": "user", "content": "\n\n".join(user_blocks)},
    ]
    return await _call_vllm(messages, max_tokens=SUMMARY_TOKEN_BUDGET, purpose="summary")


async def assemble_context(
    conversation: dict,
    new_message: dict,
    system_prompt: str,
    rag_context: Optional[dict] = None,
    ephemeral: bool = False,
    active_persona_id: Optional[str] = None,
    schema_tokens: Optional[int] = None,
) -> tuple[list[dict], Optional[dict]]:
    """
    Build a list of vLLM chat messages for the next completion.

    Returns a tuple ``(messages, compact_info)``. ``compact_info`` is
    ``None`` when no compaction was needed; otherwise a dict shaped::

        {
          "summary_through_index": int,   # index of last folded-in message
          "dropped_messages": int,        # how many turns the model no longer sees
          "summary": str,                 # the actual summary text
          "is_fresh": bool                # True if a new vLLM call ran this turn
        }

    Callers (chat_service today) yield a ``compact_boundary`` SSE event
    from this dict so the frontend can render a visible "earlier N
    messages summarised" divider in the message list.

    Flow:
        1. Compute the overall budget.
        2. Start from system prompt + existing summary + recent messages.
        3. If the total fits, return it.
        4. Otherwise, keep only the tail that fits in half the budget,
           summarize the dropped prefix via vLLM, persist the summary,
           and rebuild.

    The vLLM call in step 4 used to be on the critical path of every
    overflowing turn (~1-3s). P2 #22 mitigates this with an
    opportunistic prefetch (``_maybe_precompact``) that chat_service
    schedules as a background task at end-of-turn — by the time the
    user types the next message, ``conversation["summary"]`` is
    already populated, ``summary_through_index`` already covers the
    dropped range, and step 4 short-circuits via the
    "already covered" filter at ``already_through``.

    When ``ephemeral`` is True, compaction still runs (long ephemeral
    threads must fit in the context window) but the resulting summary
    is NOT written back to the database, since no row exists for the
    conversation.
    """
    conversation_id = conversation["id"]

    system_parts: list[str] = [system_prompt]
    rag_text = _rag_context_to_text(rag_context)
    if rag_text:
        system_parts.append(rag_text)
    if conversation.get("summary"):
        system_parts.append(
            "Summary of earlier conversation:\n" + conversation["summary"]
        )
    combined_system = "\n\n".join(p for p in system_parts if p)

    stored_messages: list[dict] = conversation.get("messages") or []

    # Persona-switch awareness: if any earlier turn was authored by a
    # different persona than the one answering now, tell the model so it
    # does not deny the switch or claim it has no memory of the thread.
    # The active persona for this turn is the request's persona (passed
    # explicitly so a manual UI switch is handled too); fall back to the
    # conversation's stored persona. Lazy import avoids a
    # chat_context <-> personas import cycle.
    from personas import persona_handoff_note
    active_persona = (
        active_persona_id if active_persona_id is not None
        else conversation.get("persona")
    )
    handoff_note = persona_handoff_note(
        active_persona_id=active_persona,
        prior_persona_ids=[m.get("persona") for m in stored_messages],
    )
    if handoff_note:
        combined_system = f"{combined_system}\n\n{handoff_note}"

    new_content = new_message.get("content", "")
    new_msg = {"role": new_message.get("role", "user"), "content": new_content}

    # Leave the turn room for the tools schema and its own tool calls
    # (turn_start_budget); ``schema_tokens`` is the routed profile's schema.
    budget = turn_start_budget(schema_tokens)
    system_tokens = approx_tokens(combined_system) + 4
    new_tokens = _message_tokens(new_msg)
    history_budget = budget - system_tokens - new_tokens

    # Current scripts: history is replayed as role/content only, so without
    # this the model rewrote a figure from memory on every turn (lost title,
    # size, order, file name; LAB-BUGFIX-PLAN.md F1). The block takes at most
    # a third of what history may use, so it is what gives way near the
    # window, never the user's recent turns.
    scripts = ""
    if not ephemeral:
        try:
            from script_index import MAX_BLOCK_TOKENS, current_scripts_block
            scripts = current_scripts_block(
                stored_messages, approx_tokens,
                max_tokens=min(MAX_BLOCK_TOKENS, max(0, history_budget // 3)),
            )
        except Exception as e:  # never let this break a turn
            logger.warning("current scripts block failed: %s", e)
            scripts = ""
        if scripts:
            combined_system = f"{combined_system}\n\n{scripts}"
            scripts_tokens = approx_tokens(scripts) + 2
            system_tokens += scripts_tokens
            history_budget -= scripts_tokens

    def build(history: list[dict]) -> list[dict]:
        return [
            {"role": "system", "content": combined_system},
            *[
                {"role": m["role"], "content": (
                    # Older rows can carry stray <answer> tags or looped
                    # copies; the model would copy them (LAB-BUGFIX-PLAN F5).
                    strip_answer_tags(_augment_with_attachments(m))
                    if m["role"] == "assistant"
                    else _augment_with_attachments(m)
                )}
                for m in history
            ],
            new_msg,
        ]

    history_tokens = sum(_message_tokens(m) for m in stored_messages)
    if history_tokens <= history_budget:
        return build(stored_messages), None

    # Need compaction: keep newest messages that fit in half the history
    # budget; summarize everything older.
    tail_budget = max(1000, history_budget // 2)
    kept: list[dict] = []
    running = 0
    for m in reversed(stored_messages):
        t = _message_tokens(m)
        if running + t > tail_budget:
            break
        kept.append(m)
        running += t
    kept.reverse()

    drop_count = len(stored_messages) - len(kept)
    if drop_count <= 0:
        return build(stored_messages), None

    dropped = stored_messages[:drop_count]
    last_dropped_index = dropped[-1].get(
        "index_in_conversation", drop_count - 1
    )

    existing_summary = conversation.get("summary")
    already_through = conversation.get("summary_through_index")
    # Only summarize messages not already covered by the previous summary.
    if already_through is not None:
        dropped_for_summary = [
            m for m in dropped
            if m.get("index_in_conversation", -1) > already_through
        ]
    else:
        dropped_for_summary = list(dropped)

    # If the prefetched summary already covers everything we'd drop,
    # summarize_messages short-circuits and returns existing_summary
    # without a vLLM call. The is_fresh flag tracks whether THIS turn
    # actually paid the latency cost.
    is_fresh_summary = bool(dropped_for_summary)
    new_summary = await summarize_messages(
        conversation_id, existing_summary, dropped_for_summary
    )

    if new_summary:
        if not ephemeral and is_fresh_summary:
            await update_summary(conversation_id, new_summary, last_dropped_index)
        conversation["summary"] = new_summary
        conversation["summary_through_index"] = last_dropped_index

        rebuilt_system_parts = [system_prompt]
        if rag_text:
            rebuilt_system_parts.append(rag_text)
        rebuilt_system_parts.append(
            "Summary of earlier conversation:\n" + new_summary
        )
        if handoff_note:
            rebuilt_system_parts.append(handoff_note)
        if scripts:
            rebuilt_system_parts.append(scripts)
        combined_system = "\n\n".join(rebuilt_system_parts)
        messages = [
            {"role": "system", "content": combined_system},
            *[
                {"role": m["role"], "content": (
                    strip_answer_tags(_augment_with_attachments(m))
                    if m["role"] == "assistant"
                    else _augment_with_attachments(m)
                )}
                for m in kept
            ],
            new_msg,
        ]
        compact_info = {
            "summary_through_index": last_dropped_index,
            "dropped_messages": drop_count,
            "summary": new_summary,
            "is_fresh": is_fresh_summary,
        }
        return messages, compact_info

    # vLLM unreachable — fall back to hard truncation. No summary to
    # show the user, so no compact_boundary event either.
    return build(kept), None


async def generate_title(first_user_message: str, first_assistant_response: str) -> str:
    """
    Generate a short title for a new conversation. Uses the first user message
    directly if it's short, otherwise asks vLLM.
    """
    trimmed = (first_user_message or "").strip()
    if trimmed and len(trimmed) <= 60:
        return trimmed

    prompt = (
        f"User: {trimmed}\n\n"
        f"Assistant: {(first_assistant_response or '').strip()[:1200]}"
    )
    messages = [
        {"role": "system", "content": TITLE_PROMPT},
        {"role": "user", "content": prompt},
    ]
    title = await _call_vllm(messages, max_tokens=40, purpose="title")
    if not title:
        return trimmed[:60] if trimmed else "New conversation"
    return title.strip().strip('"').strip("'")[:80]


# ---------------------------------------------------------------------------
# Opportunistic compaction (P2 #22)
# ---------------------------------------------------------------------------

# Fire summarization in the background at the end of a turn IF the
# conversation's history is at or above this fraction of the history
# budget. 70% leaves enough headroom that the next turn's user message
# + RAG context can usually fit without forcing an extra compaction.
# Tune-once constant; below the threshold we save vLLM tokens that
# would be overwritten next turn anyway.
PRECOMPACT_THRESHOLD = 0.70

# Per-conversation lock so two consecutive turns don't both fire the
# prefetch. Module-level set; the retrieval container is single-
# process and asyncio is single-threaded, so a plain set is safe
# without a lock primitive.
_precompact_inflight: set[str] = set()


async def _maybe_precompact(conversation_id: str, user_email: str) -> None:
    """Background task: pre-generate the summary for a conversation
    that's near the budget, so the user's next turn doesn't pay the
    sync vLLM cost.

    Failures (vLLM unreachable, DB error, race) are logged and
    swallowed. The next user turn's sync path will simply do the
    summarization itself if this background pass didn't complete.

    Called from ``chat_service.stream_chat_completion`` as
    ``asyncio.create_task(_maybe_precompact(...))`` after the
    ``done`` event has been yielded — out of the user's perceived
    latency."""
    if conversation_id in _precompact_inflight:
        # A previous turn already kicked one off; let it finish.
        return
    _precompact_inflight.add(conversation_id)
    try:
        conversation = await get_conversation(conversation_id, user_email)
        if conversation is None:
            return
        stored_messages: list[dict] = conversation.get("messages") or []
        if not stored_messages:
            return
        # Approximate budget using the same turn_start_budget as
        # assemble_context, with the default schema size. We don't have the
        # next user message in hand here, so keep ~500 tokens back for it.
        budget = turn_start_budget(None) - 500
        history_tokens = sum(_message_tokens(m) for m in stored_messages)
        if history_tokens < int(budget * PRECOMPACT_THRESHOLD):
            return
        # Same tail-keep logic as assemble_context. Recompute here
        # rather than refactor because the chat_service-side path
        # uses RAG context + system prompt that vary per request.
        tail_budget = max(1000, budget // 2)
        kept_running = 0
        kept_count = 0
        for m in reversed(stored_messages):
            t = _message_tokens(m)
            if kept_running + t > tail_budget:
                break
            kept_running += t
            kept_count += 1
        drop_count = len(stored_messages) - kept_count
        if drop_count <= 0:
            return
        dropped = stored_messages[:drop_count]
        last_dropped_index = dropped[-1].get(
            "index_in_conversation", drop_count - 1
        )
        existing_summary = conversation.get("summary")
        already_through = conversation.get("summary_through_index")
        if already_through is not None:
            dropped = [
                m for m in dropped
                if m.get("index_in_conversation", -1) > already_through
            ]
        if not dropped:
            return  # already covered; nothing to do
        new_summary = await summarize_messages(
            conversation_id, existing_summary, dropped
        )
        if not new_summary:
            return
        # asyncio.shield: a process shutdown landing mid-write would
        # otherwise truncate the summary row. The shield lets the
        # update complete in the background even if our wrapping task
        # gets cancelled.
        await asyncio.shield(
            update_summary(conversation_id, new_summary, last_dropped_index)
        )
        logger.info(
            "precompact: persisted summary for conv %s (through index %d)",
            conversation_id, last_dropped_index,
        )
    except Exception:
        logger.exception(
            "precompact: background summarization failed for conv %s",
            conversation_id,
        )
    finally:
        _precompact_inflight.discard(conversation_id)


# ---------------------------------------------------------------------------
# Test helpers — not part of the public surface.
# ---------------------------------------------------------------------------


def _precompact_inflight_for_tests() -> set[str]:
    return _precompact_inflight
