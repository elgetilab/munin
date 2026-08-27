# Stream reliability: findings and open items

Opened 2026-08-27 after a user reported persistent stream failures. Evidence is
from that user's production conversations (89 conversations, 169 assistant
turns) plus his 11 filed error reports.

> Sibling: [`TODO.md`](TODO.md) (agent architecture, Deep Research).

---

## What the user sees

His own report, verbatim:

> "the (stream is no longer available on this server) message keeps popping up
> even without me doing anything. I am just waiting for the output..."

Behavioural signature in the data: **26 of his 169 user turns (15%) are followed
by another user turn with no assistant reply persisted in between**, and the
retry is him typing "resume" 35 to 175 seconds later.

---

## 1. The 410 gate ignores the client's checkpoint  [BUG, fix first]

`stream_registry.Stream.record()` caps the replay buffer at
`MAX_LOG_EVENTS = 1000`. On overflow it drops the oldest event and sets
`truncated = True` **permanently for the life of the stream**. `main.py:1547`
then does:

```python
if stream is None or stream.truncated:
    raise HTTPException(status_code=410, ...)
```

**It never consults the client's `Last-Event-ID`.** That header is parsed at
line 1563, *sixteen lines after* the 410 has already been raised. The comment
above the raise claims the buffer "overflowed past the client's checkpoint", but
the checkpoint is not read. A client reconnecting with a seq still comfortably
inside the retained 1000 events is refused anyway.

**Exposure.** Estimated SSE events per turn over his 169 turns (~1 event per
output token, plus tool events):

| median | p75 | p90 | max |
|---|---|---|---|
| 2,061 | 3,806 | 6,554 | 28,255 |

**78% of his turns exceed the cap**, so they are permanently unresumable. His
turns are long (manuscript polishing, multi-step plotting), which is why he sees
this constantly and lighter users do not.

**Fix:** move the truncation test after `Last-Event-ID` parsing and 410 only
when the requested seq is genuinely below the oldest retained event. Raising
`MAX_LOG_EVENTS` is complementary but weaker on its own: it moves the cliff
rather than removing it.

## 2. Grace expiry CANCELS the turn when the user is at the background cap  [BUG]

`GRACE_S = 60.0`, `MAX_BACKGROUND_PER_USER = 2`. On disconnect a stream gets 60s
of grace, then is promoted to a background turn **or cancelled outright if the
user already has 2 background streams**.

This explains the missing assistant messages. If the turn survived server-side
it would be persisted to `chats.db` even though the client never saw it, and it
is not there. So the work is being **thrown away**, not merely hidden.

It also compounds: each failed attempt leaves a background stream behind, so a
user who retries twice hits the cap and subsequent disconnects are cancelled
rather than backgrounded. That matches the observed clusters of consecutive
"resume" messages.

**Open question for the fix:** cancelling on cap is presumably there to bound
GPU cost. Preferred direction is to persist the partial answer before
cancelling, so a cancelled turn still leaves something in the conversation
rather than a silent gap.

## 3. Why the connection drops in the first place  [UNKNOWN, do not guess]

Not established. What has been **ruled out**:

- **SSH tunnel**: `NRestarts=0`, continuously active since 2026-08-04. Not it.
- **Keepalive**: `ping=KEEPALIVE_S` (15s) is set on both the chat and resume
  endpoints, so a plain proxy idle timeout should not fire.
- **Multi-worker registry split**: uvicorn runs a single worker, so the
  in-process registry is not being sharded across processes.
- **Gateway buffering**: the gateway streams via `aiter_bytes()` into a
  `StreamingResponse`; it does not buffer.

Remaining candidates, untested: client-side network/browser (laptop sleep, wifi
roaming, tab backgrounding), Caddy on the VPS, or something server-side closing
the response early. `_consumeSSE` returns `drop` whenever "the reader ended
without a `done` event", which does not distinguish these.

**Needed:** server-side logging of disconnect cause and frequency per user, so
this stops being a guess. Note that fixing items 1 and 2 makes drops *survivable*
regardless of cause, which is why they come first.

## 4. Resume does not survive a retrieval restart  [DESIGN, after 1 and 2]

The registry is in-process, so any `deploy.sh retrieval` kills every in-flight
stream and their event logs. Fixing this means persisting the event log
(`chats.db` or similar). Deliberately **not** bundled with item 1: much larger
change, different risk profile.

## 5. Model chains small tools instead of the unified `search`  [BEHAVIOUR]

Across his recent turns: `search` used **58** times against **145** calls to the
three tools it is documented to supersede (`web_search` 78,
`semantic_scholar_search` 35, `paper_search` 32), a 2.5:1 preference for
chaining.

Likely compounded by item 6: the `code` persona's resident tools are
`edit_python`, `compile_latex`, `sandbox_reset`, `save_artifact_to_documents`,
with no research tools, so a misrouted research question must discover them via
`tool_search` and lands on whatever it finds first.

## 6. Router sends research questions to the `code` profile  [BEHAVIOUR]

Routed profiles on his turns: chat 69, research 58, **code 31**. Most code
routings are legitimate (plotting, figure work). One is unambiguously wrong: a
**bare DOI, `10.1039/c9pp00328b`, routed to `code`**. That is a paper lookup.

Worth a routing-eval item: bare identifiers (DOI / PMID / arXiv) should route to
research.

## 7. Attached knowledge base should be searched first  [FEATURE, user request]

Currently in his tagged conversations the first tool call is corpus-first 27
times vs external-first 8, so it usually does the right thing but not reliably.
Tags already flow through the `current_query_tags` ContextVar into
`paper_search`, so enforcing "attached KB is searched first" is a small change
rather than new plumbing.

**Not the cause of the stream failures**: failure rate 0.169 with tags vs 0.134
without, consistent with tagged conversations simply being longer.
