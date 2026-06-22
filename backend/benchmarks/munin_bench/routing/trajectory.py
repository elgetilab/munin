"""SSE-stream capture for the routing eval.

Parses the `event: <name>\\ndata: <json>\\n\\n` frames from
`POST /api/chat/completions` into the structures `score_item` consumes:
an ordered list of `ToolCall` (with a reconstructed `step` index) plus the
side-channel events a few item checks need (error frames, the final
assistant text for the abstention judge, and the soon-to-be-retired
`delegated` / `persona_changed` events so the A0 baseline can record what
the persona harness did).

WHY STEP RECONSTRUCTION.  The `tool_call` SSE payload is `{id, name,
arguments}` only (chat_service.py ~850, ~2049) — it carries no iteration
index. The routing scorer's `solo` check needs to know which calls were
emitted in the SAME assistant step (ask_clarification must be alone in its
step). The harness emits tool_calls in per-iteration batches and a
`tool_result` event marks the execution boundary of an iteration, so the
boundary between steps is "a tool_call that follows at least one
tool_result since the previous tool_call". That is the rule implemented in
`reconstruct_steps`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

# routing_eval defines ToolCall (pydantic). Import lazily / by structure so
# this module stays usable in isolation for the unit test, but the runner
# converts these into routing_eval.ToolCall before calling score_item.


@dataclass
class RawEvent:
    """One parsed SSE frame."""
    event: str
    data: dict


@dataclass
class CapturedTrajectory:
    """Everything the scorer + judge need from one captured turn."""
    tool_calls: list[dict] = field(default_factory=list)   # {name, arguments, step}
    final_text: str = ""
    errors: list[str] = field(default_factory=list)
    delegated: list[dict] = field(default_factory=list)
    persona_changed: list[dict] = field(default_factory=list)
    conversation_id: Optional[str] = None
    raw_events: list[RawEvent] = field(default_factory=list)


def parse_sse(raw: str) -> list[RawEvent]:
    """Parse a full SSE response body into ordered `RawEvent`s.

    Mirrors the line-pair parser in scripts/test_delegate_persona.py:
    `event: <name>` sets the current event, the following `data: <json>`
    carries its payload. Frames whose data is not JSON are skipped (e.g.
    `token` deltas may be plain text); callers that want token text read
    `final_text` which is assembled separately.
    """
    events: list[RawEvent] = []
    current_event: Optional[str] = None
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event: "):
            current_event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # Non-JSON data (e.g. a raw token chunk). Wrap it so token
            # assembly can still see it downstream.
            data = {"_raw": payload}
        if current_event is not None:
            events.append(RawEvent(event=current_event, data=data))
    return events


def reconstruct_steps(events: list[RawEvent]) -> list[dict]:
    """Assign a `step` index to each tool_call event.

    step starts at 0. Each tool_call is tagged with the current step. A
    `tool_result` since the last tool_call means the next tool_call belongs
    to a new step (the harness executed the batch and looped). This groups
    same-iteration calls into one step, which is what `solo` needs.
    """
    tool_calls: list[dict] = []
    step = 0
    saw_result_since_last_call = False
    for ev in events:
        if ev.event == "tool_call":
            if tool_calls and saw_result_since_last_call:
                step += 1
            saw_result_since_last_call = False
            tool_calls.append({
                "name": ev.data.get("name", ""),
                "arguments": ev.data.get("arguments", {}) or {},
                "step": step,
            })
        elif ev.event == "tool_result":
            saw_result_since_last_call = True
    return tool_calls


def capture(raw: str) -> CapturedTrajectory:
    """Parse a full SSE body into a CapturedTrajectory."""
    events = parse_sse(raw)
    traj = CapturedTrajectory(raw_events=events)
    traj.tool_calls = reconstruct_steps(events)

    text_parts: list[str] = []
    for ev in events:
        if ev.event == "token":
            # Token frames may be {"text": ...}, {"content": ...} or a raw
            # chunk wrapped as {"_raw": ...}. Take whichever is present.
            chunk = (
                ev.data.get("text")
                or ev.data.get("content")
                or ev.data.get("_raw")
                or ""
            )
            text_parts.append(chunk)
        elif ev.event == "error":
            traj.errors.append(ev.data.get("message", ""))
        elif ev.event == "delegated":
            traj.delegated.append(ev.data)
        elif ev.event == "persona_changed":
            traj.persona_changed.append(ev.data)
        elif ev.event == "conversation":
            traj.conversation_id = ev.data.get("id") or traj.conversation_id
    traj.final_text = "".join(text_parts).strip()
    return traj
