"""Claude judge for rubric-tier cases (pinned model, Message Batches API).

Only the case's turns, verified truth, rubric and Munin's final answer go to
the API. The judge returns a per-criterion verdict as structured JSON; a case
passes the judge when every criterion passes.
"""

from __future__ import annotations

import json
import time
from typing import Optional

JUDGE_MODEL = "claude-opus-5-5"
JUDGE_EFFORT = "high"
MAX_ANSWER_CHARS = 60_000
MAX_TURN_CHARS = 12_000

SYSTEM = """You grade one answer from Munin, a scientific research assistant, against a fixed rubric.

You get the user's message(s), reference facts verified by a human-supervised curator, a numbered rubric, and Munin's final answer (with the tools it called and files it created).

Rules:
- Judge each rubric criterion independently, only from the answer text and the listed tools/files.
- Use the reference facts as ground truth. Do not reward claims that contradict them, however well argued.
- A criterion about citations fails if any cited work is clearly not real or misattributed according to the reference facts.
- Partial fulfilment of a criterion is a fail; say what is missing.
- Ignore style unless a criterion is about style, length or language.
- Keep each reason to one or two sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "pass": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "pass", "reason"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["criteria", "summary"],
    "additionalProperties": False,
}


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 2000] + f"\n[... {len(s) - n} chars omitted ...]\n" + s[-2000:]


def build_prompt(case, rec: dict) -> str:
    turns = "\n\n".join(f"<user_turn n={i}>\n{_clip(t.text, MAX_TURN_CHARS)}\n</user_turn>"
                        for i, t in enumerate(case.turns, 1))
    final = rec["turns"][-1] if rec.get("turns") else {}
    tools = sorted({x for t in rec.get("turns") or [] for x in t.get("tools") or []})
    files = [a.get("filename") for a in rec.get("artifacts") or []]
    rubric = "\n".join(f"{i}. {r}" for i, r in enumerate(case.rubric, 1))
    return (f"{turns}\n\n<reference_facts>\n{case.truth or '(none)'}\n</reference_facts>\n\n"
            f"<rubric>\n{rubric}\n</rubric>\n\n"
            f"<munin_tools_called>{', '.join(tools) or 'none'}</munin_tools_called>\n"
            f"<munin_files_created>{', '.join(files) or 'none'}</munin_files_created>\n\n"
            f"<munin_final_answer>\n{_clip(final.get('answer') or '', MAX_ANSWER_CHARS)}\n</munin_final_answer>\n\n"
            f"Grade criteria 1 to {len(case.rubric)}.")


def request_params(case, rec: dict) -> dict:
    return {
        "model": JUDGE_MODEL,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_prompt(case, rec)}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": JUDGE_EFFORT,
                          "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def parse_verdict(msg, n_criteria: int) -> dict:
    """Message -> {"pass", "criteria", "summary", "usage"}; refusals become errors."""
    usage = {"input": getattr(msg.usage, "input_tokens", 0),
             "output": getattr(msg.usage, "output_tokens", 0),
             "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0}
    if msg.stop_reason == "refusal":
        return {"pass": None, "error": "judge refused", "usage": usage}
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        return {"pass": None, "error": f"unparseable judge output ({msg.stop_reason})", "usage": usage}
    crit = {c["index"]: c for c in data.get("criteria") or []}
    rows = [crit.get(i, {"index": i, "pass": False, "reason": "not graded"})
            for i in range(1, n_criteria + 1)]
    return {"pass": all(r["pass"] for r in rows), "criteria": rows,
            "summary": data.get("summary", ""), "usage": usage}


def judge_batch(client, items: list[tuple], poll_s: int = 60, log=print,
                batch_id: Optional[str] = None) -> dict[str, dict]:
    """items: [(case, rec)] -> {case_id: verdict}. Blocks until the batch ends.

    batch_id collects an already submitted batch instead of creating one (for
    when the create call reached the API but its response was lost)."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if not items:
        return {}
    by_id = {c.id: c for c, _ in items}
    if batch_id:
        batch = client.messages.batches.retrieve(batch_id)
    else:
        batch = client.messages.batches.create(requests=[
            Request(custom_id=c.id, params=MessageCreateParamsNonStreaming(**request_params(c, r)))
            for c, r in items])
    log(f"[judge] batch {batch.id}: {len(items)} requests")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        log(f"[judge] {b.request_counts.processing} processing, {b.request_counts.succeeded} done")
        time.sleep(poll_s)
    out: dict[str, dict] = {}
    for res in client.messages.batches.results(batch.id):
        case = by_id.get(res.custom_id)
        if case is None:   # batch_id names a batch with other cases
            continue
        if res.result.type == "succeeded":
            out[case.id] = parse_verdict(res.result.message, len(case.rubric))
        else:
            out[case.id] = {"pass": None, "error": f"batch {res.result.type}"}
    out["_batch"] = {"id": batch.id}
    return out


def judge_one(client, case, rec: dict) -> dict:
    """Synchronous retry for one case, with server-side refusal fallback."""
    params = request_params(case, rec)
    msg = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                      fallbacks="default", **params)
    return parse_verdict(msg, len(case.rubric))


def cost_usd(usage: dict, batch: bool = True) -> float:
    """Opus 5.5 list price: $4 in, $20 out, $0.20 cache read per MTok; batch halves it."""
    c = (usage.get("input", 0) * 4 + usage.get("output", 0) * 20
         + usage.get("cache_read", 0) * 0.2) / 1e6
    return c / 2 if batch else c


def make_client(api_key: Optional[str] = None):
    import importlib
    # An older system `brotli` (anaconda ships 1.0.9) lacks the
    # output_buffer_limit argument the SDK's HTTP client passes, so a brotli
    # response crashes with "process() takes no keyword arguments". Drop the
    # br decoder from the HTTP client so no request asks for brotli, the OAuth
    # token refresh included; the gzip header below covers API calls too.
    import anthropic
    for pkg in ("httpx2", "httpx"):
        try:
            dec = importlib.import_module(f"{pkg}._decoders")
            cli = importlib.import_module(f"{pkg}._client")
        except ImportError:
            continue
        dec.SUPPORTED_DECODERS.pop("br", None)
        cli.ACCEPT_ENCODING = ", ".join(k for k in dec.SUPPORTED_DECODERS if k != "identity")
    headers = {"Accept-Encoding": "gzip, deflate"}
    return anthropic.Anthropic(api_key=api_key, default_headers=headers) if api_key \
        else anthropic.Anthropic(default_headers=headers)
