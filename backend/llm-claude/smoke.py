#!/usr/bin/env python3
"""Check a running llm-claude against the real API. This spends a little money
(well under a cent on Haiku 5.5).

    python3 smoke.py [http://localhost:8000]

Uses only the standard library. Exits non-zero if a check fails. The caching
check needs a prefix above the model's minimum, so it pads the system prompt.
"""

import json
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
TOOL = {"type": "function", "function": {
    "name": "get_time", "description": "Current time in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}}
SYSTEM = {"role": "system", "content": "You are terse. " + "Background: Munin is a research tool. " * 200}
ASK = {"role": "user", "content": "What time is it in Oslo? Use the tool."}
failed = 0


def check(name, ok, detail=""):
    global failed
    failed += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")


def post(body):
    request = urllib.request.Request(
        f"{BASE}/v1/chat/completions", json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=120)


def stream(body):
    content, finish, usage, calls = "", None, None, {}
    with post({**body, "stream": True, "stream_options": {"include_usage": True}}) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            usage = chunk.get("usage") or usage
            for choice in chunk["choices"]:
                content += choice["delta"].get("content") or ""
                finish = choice["finish_reason"] or finish
                for call in choice["delta"].get("tool_calls") or []:
                    slot = calls.setdefault(call["index"], {"arguments": ""})
                    slot.update({k: call[k] for k in ("id",) if call.get(k)})
                    fn = call.get("function") or {}
                    slot["name"] = fn.get("name") or slot.get("name")
                    slot["arguments"] += fn.get("arguments") or ""
    return content, finish, usage, calls


# A title call: tiny max_tokens and a temperature the API alone would refuse.
with post({"messages": [{"role": "user", "content": "Reply with the word pong."}],
           "max_tokens": 40, "temperature": 0.3, "top_p": 0.9}) as response:
    reply = json.load(response)
text = reply["choices"][0]["message"]["content"] or ""
check("short non-streaming call", "pong" in text.lower(), repr(text))

# Forced tool choice, which retrieval's recovery paths use.
content, finish, usage, calls = stream({"messages": [SYSTEM, ASK], "tools": [TOOL], "tool_choice": "required"})
check("forced tool call", finish == "tool_calls" and calls, json.dumps(calls.get(0)))

# Same again unforced, then the full tool round trip.
_, finish, first_usage, calls = stream({"messages": [SYSTEM, ASK], "tools": [TOOL]})
check("tool call", finish == "tool_calls" and calls, json.dumps(calls.get(0)))
call = calls[0]
history = [SYSTEM, ASK,
           {"role": "assistant", "content": "", "tool_calls": [{"id": call["id"], "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"]}}]},
           {"role": "tool", "tool_call_id": call["id"], "content": "14:05"}]
content, finish, usage, _ = stream({"messages": history, "tools": [TOOL]})
check("answer after tool result", finish == "stop" and "14" in content, repr(content[:80]))
cached = usage["prompt_tokens_details"]["cached_tokens"]
check("second round reads the cache", cached > 0, f"{cached} of {usage['prompt_tokens']} prompt tokens")

sys.exit(1 if failed else 0)
