"""
Shared query-expansion helper for the search tools.

When the main model calls web_search / paper_search / semantic_scholar_search
with a single `query` string, the tool uses this helper to fan it out into
3-5 variants (rephrased, broadened, narrowed, alternative keywords) via one
cheap vLLM call. Each variant is then executed in parallel inside the tool.

The model can also bypass expansion entirely by passing a `queries: list[str]`
argument — we trust whatever list it provides.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from database import VLLM_URL, VLLM_MODEL_NAME, thinking_off_fields, LLM_HEADERS

EXPANSION_SYSTEM_PROMPT = (
    "You are a search query expansion helper. Given one base query, return a "
    "JSON array of 3 to 5 alternative search queries for the SAME question.\n\n"
    "Hard rule: every variant MUST keep the base query's salient entities and "
    "constraints - the specific compounds, techniques, organisms, diseases or "
    "quantities it names. Vary only the phrasing, the synonyms and the angle. "
    "A variant that drops a named entity to become a general topic query is "
    "wrong: for 'small-molecule kinase inhibitor membrane partitioning', "
    "'membrane biology' is WRONG (it dropped the inhibitor), while 'lipid "
    "bilayer partitioning of kinase inhibitor drugs' is right.\n\n"
    "Second rule: the variants must still be genuinely DIFFERENT searches, not "
    "reworded copies of each other. Each one should change the technical "
    "vocabulary, the mechanism, the method, or the sub-aspect being asked "
    "about, while keeping the entities. Continuing the example, a good set "
    "varies like: 'molecular dynamics simulation of kinase inhibitor "
    "partitioning into lipid bilayers', 'effect of cholesterol and "
    "sphingolipid content on kinase inhibitor binding affinity', "
    "'ATP-competitive kinase inhibitor lipophilicity and membrane retention'. "
    "Do not add commentary. Return ONLY a JSON array of strings."
)


def _parse_variants(raw: str) -> list[str]:
    """
    Extract a list of query strings from whatever the model returned. vLLM
    output from the non-streaming endpoint can include leading/trailing text,
    markdown fences, or a mix of quotes — so we grep for the first `[ ... ]`
    block and fall back to line-splitting if that fails.
    """
    if not raw:
        return []

    # Strip Qwen-style reasoning if the reasoning parser didn't split it out.
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # First-pass: find a JSON array.
    match = re.search(r"\[[^\[\]]*\]", raw, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if isinstance(x, (str, int, float)) and str(x).strip()]
        except json.JSONDecodeError:
            pass

    # Fallback: line-split, strip bullets/quotes.
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*0123456789. ").strip()
        s = s.strip("\"'`,")
        if s:
            lines.append(s)
    return lines


async def expand_queries(
    base: str,
    n: int = 4,
    context_hint: Optional[str] = None,
) -> list[str]:
    """
    Turn one base query into up to `n` variants via a single cheap vLLM call.
    Always returns at least `[base]` — the base query is included at the
    start of the list so a call-site that just loops over the result still
    runs the original user intent.

    Parameters:
        base: The single-string query the model provided.
        n: Soft target for total variants (including the base query). Defaults
            to 4, clamped to [2, 8].
        context_hint: Optional short string of prior conversation context
            that may help the expander pick better variants. Not used today
            but reserved for future callers (e.g. deep_research).

    Returns:
        A list of distinct query strings starting with the base query. If the
        vLLM call fails, returns `[base]` unchanged so the caller can still
        proceed with a single-query search.
    """
    base = (base or "").strip()
    if not base:
        return []

    n = max(2, min(8, int(n)))
    target_variants = max(1, n - 1)  # leave one slot for the base query

    user_blocks = [f"Base query: {base}"]
    if context_hint:
        user_blocks.append(f"Conversation context: {context_hint.strip()[:500]}")
    user_blocks.append(
        f"Return {target_variants} alternative queries as a JSON array of strings. "
        "Do not repeat the base query in the array."
    )

    messages = [
        {"role": "system", "content": EXPANSION_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_blocks)},
    ]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{VLLM_URL}/v1/chat/completions",
                headers=LLM_HEADERS,
                json={
                    "model": VLLM_MODEL_NAME,
                    "messages": messages,
                    "max_tokens": 250,
                    "temperature": 0.5,
                    "stream": False,
                    # Qwen3's reasoning phase is expensive and eats the whole
                    # token budget for a task this simple — disable it so the
                    # model writes straight to `content`.
                    **thinking_off_fields(),
                },
            )
            if resp.status_code != 200:
                return [base]
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return [base]
            content = (choices[0].get("message") or {}).get("content") or ""
    except Exception:
        return [base]

    variants = _parse_variants(content)

    # Dedupe while preserving order; always start with the base query.
    seen = {base.lower()}
    ordered: list[str] = [base]
    for v in variants:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(v)
        if len(ordered) >= n:
            break

    return ordered
