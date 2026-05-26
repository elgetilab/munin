"""
Memory extraction classifier (P2 #25).

One vLLM call per turn that asks the model: "given this conversation
exchange, what facts about the user are worth remembering across
conversations?" Returns a JSON array of ``{key, value, reason}``
candidates which the calling hook persists as ``proposed_memories``
rows (rendered as accept/reject pills in the UI).

Cost shape: ``enable_thinking=False`` (Qwen3's reasoning would
otherwise burn the budget on a mechanical task), ``max_tokens=400``,
``foreground=False`` (short retry budget — this is a best-effort
side task, not user-facing latency). Empty output is the normal case;
the classifier returns ``[]`` on most turns.

The hook gates on terminal_reason and dedupes the candidates against
``all_known_keys`` (accepted + pending + rejected) before persisting.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from database import VLLM_MODEL_NAME
from vllm_client import VLLMRequestError, vllm_post_json
from memory_store import MAX_KEY_CHARS, MAX_VALUE_CHARS


logger = logging.getLogger(__name__)


# Hard cap so a runaway classifier (or a malicious user trying to
# poison the table) can't flood the UI in one turn. Combined with the
# proposals-store FIFO cap (10/user) this bounds total exposure.
MAX_PROPOSALS_PER_TURN = 3


_SYSTEM_PROMPT = """\
You decide which facts from a chat turn are worth saving as cross-conversation memories about THE USER.

Return ONLY a JSON array on a single line, no prose, no markdown fences. Each element MUST be:
  {"key": "<short_snake_case_identifier>", "value": "<one-line fact>", "reason": "<one-line rationale>"}

SAVE facts that:
- name a stable identity (role, affiliation, location, language preference)
- describe an ongoing project, dataset, codebase, paper, or research area
- express a working style or formatting preference the user states explicitly
- list a recurring tool, library, or environment the user works with

DO NOT save:
- facts about THIS specific conversation ("user asked about X today")
- assistant-side facts ("assistant explained membrane proteins")
- things the user did not actually state (no inference, no guessing)
- duplicates: any key in the EXCLUDE list below MUST NOT appear in your output
- transient context (time of day, current emotion, one-off question topic)

Constraints:
- At most 3 entries
- key: snake_case, max 100 chars, descriptive (e.g. "user_role", "research_focus", "preferred_language")
- value: max 200 chars, one factual sentence
- reason: max 200 chars, why this fact is worth keeping

If nothing is worth saving, return [].
"""


def _build_user_prompt(
    user_message: str,
    assistant_message: str,
    exclude_keys: list[str],
) -> str:
    excl = ", ".join(f'"{k}"' for k in sorted(exclude_keys)) if exclude_keys else "(none)"
    return (
        f"EXCLUDE list (do not re-propose these keys): [{excl}]\n\n"
        f"USER said:\n{user_message}\n\n"
        f"ASSISTANT said:\n{assistant_message}\n\n"
        "Return the JSON array now."
    )


def _coerce_proposal(item) -> Optional[dict]:
    """Validate a single classifier output element. Returns None on any
    malformed entry rather than raising — we'd rather silently drop a
    bad candidate than abort the whole extraction."""
    if not isinstance(item, dict):
        return None
    key = item.get("key")
    value = item.get("value")
    reason = item.get("reason")
    if not isinstance(key, str) or not isinstance(value, str):
        return None
    key = key.strip()
    value = value.strip()
    if not key or not value:
        return None
    if len(key) > MAX_KEY_CHARS or len(value) > MAX_VALUE_CHARS:
        return None
    if not isinstance(reason, str):
        reason = ""
    return {"key": key, "value": value, "reason": reason.strip()[:200]}


def _parse_output(raw: str) -> list[dict]:
    """Tolerant JSON parser. The model is told to emit a bare array but
    sometimes wraps it in code fences or prefaces with prose despite
    instructions; we strip both and reject anything that doesn't parse."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences if present.
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop the opening fence line and any trailing closing fence.
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    # Find the array boundaries if there's any preamble we missed.
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        proposal = _coerce_proposal(item)
        if proposal is not None:
            out.append(proposal)
        if len(out) >= MAX_PROPOSALS_PER_TURN:
            break
    return out


async def extract_memories(
    user_message: str,
    assistant_message: str,
    exclude_keys: list[str],
) -> list[dict]:
    """Run the classifier. Returns 0..MAX_PROPOSALS_PER_TURN proposals,
    each shaped ``{key, value, reason}``. Caller is responsible for
    persisting them and emitting SSE.

    Failure modes (network error, parse fail, empty content) all
    collapse to ``[]`` — extraction is best-effort by design."""
    if not user_message.strip() and not assistant_message.strip():
        return []

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_prompt(
                user_message, assistant_message, exclude_keys,
            ),
        },
    ]

    try:
        data = await vllm_post_json(
            {
                "model": VLLM_MODEL_NAME,
                "messages": messages,
                "max_tokens": 400,
                "temperature": 0.1,
                "stream": False,
                # Disable Qwen3 reasoning — classifier is mechanical;
                # any <think> trace eats the small token budget.
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=60.0,
            foreground=False,
            purpose="memory_extract",
        )
    except VLLMRequestError as e:
        logger.info("memory_extract: vLLM unavailable, returning []: %s", e)
        return []

    choices = data.get("choices") or []
    if not choices:
        return []
    content = (choices[0].get("message") or {}).get("content") or ""
    if not content.strip():
        return []
    return _parse_output(content)
