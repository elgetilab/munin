# "Report this chat" — Frontend Implementation Guide

Standalone spec for adding a "Report this chat" action to the Munin
frontend. The backend endpoint is already deployed and tested.

## What it does

Users flag a conversation that had a problem (broken download link,
wrong answer, tool failure, etc.). The backend serialises the full
conversation to a JSON file on disk so developers can review it and
turn it into a regression test.

## Backend endpoint

```
POST /api/chats/{conversation_id}/report
```

**Headers** (same as all other authenticated endpoints):

```
X-Munin-Email: user@example.com
Content-Type: application/json
```

**Request body** (all fields optional):

```json
{
  "reason": "Download link opened the chat page instead of the PDF"
}
```

- `reason`: free-text description of what went wrong. Max 2000
  characters. Can be omitted or empty — the report is still filed.

**Response (200)**:

```json
{
  "reported": true,
  "report_id": "rpt_20260418T091500_979c7fda"
}
```

**Error responses**:

- **404**: conversation not found or not owned by the caller (same
  behaviour as GET/PATCH/DELETE on `/api/chats/{id}`)
- **400**: `reason` exceeds 2000 characters

**Idempotent**: reporting the same conversation multiple times
overwrites the previous report file. No rate limit beyond that.

## What the frontend needs

### 1. Menu item

Add a **"Report this chat"** entry to the chat header's overflow
menu (the `...` or kebab menu where rename and delete already live).

- **Icon**: flag, warning triangle, or megaphone — whichever fits
  the design system.
- **Position**: after "Delete" (it's a less-common action).
- **Visibility**: show on all non-ephemeral conversations that have
  at least one message. Hide for ephemeral chats (they aren't
  persisted so there's nothing to report).

### 2. Reason dialog (optional but recommended)

When the user clicks "Report this chat", show a small dialog or
bottom-sheet with:

```
+-----------------------------------------------+
|  Report this chat                              |
|                                                |
|  What went wrong? (optional)                   |
|  +-------------------------------------------+|
|  |                                           ||
|  |                                           ||
|  +-------------------------------------------+|
|                                                |
|                    [Cancel]  [Send report]      |
+-----------------------------------------------+
```

- Text area: placeholder "Describe the issue...", max 2000 chars.
- Submitting with an empty text area is fine (reason becomes null).
- On submit: `POST /api/chats/{conversationId}/report` with
  `{"reason": textAreaValue}`.

If you prefer a simpler UX, skip the dialog and just fire the POST
immediately on click (with no reason). The report is still useful
without a description — it captures the full conversation.

### 3. Confirmation feedback

- **On success (200)**: show a toast/snackbar: *"Chat reported —
  thank you for helping us improve."*
- **On error (4xx/5xx)**: show a toast with the error message from
  `response.error.message`.

### 4. Visual indicator (nice-to-have)

After a successful report, show a small badge or flag icon on the
conversation in the sidebar so the user knows which chats they've
already reported. Two approaches:

- **Local state**: set a flag in the component state or localStorage
  keyed by conversation id. Cheap, doesn't survive across devices.
- **Backend-backed** (future): the backend doesn't currently store a
  `reported` flag on the conversation row. If cross-device
  persistence matters later, we can add one.

For v1, local state is enough.

### 5. Mobile / responsive

The overflow menu should already work on mobile. Just make sure the
dialog (if implemented) renders correctly on narrow screens — a
bottom-sheet is better than a centered modal on phones.

## Example fetch call

```typescript
async function reportChat(
  conversationId: string,
  reason?: string
): Promise<{ reported: boolean; report_id: string }> {
  const res = await fetch(`/api/chats/${conversationId}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason || undefined }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err?.error?.message || `Report failed (${res.status})`);
  }
  return res.json();
}
```

## How to verify

1. Open any conversation with at least one assistant response.
2. Click the overflow menu, select "Report this chat".
3. Type a short reason (or leave blank), submit.
4. Expect a success toast.
5. Check the cluster: `ls /opt/munin/data/reported/` should show a
   new `rpt_*.json` file.
6. Report the same chat again — should succeed (overwrite, not
   duplicate). No error, no duplicate files.
7. Try reporting an ephemeral chat — the menu item should not be
   visible (or the POST should return 404).
8. Try submitting a reason longer than 2000 chars — expect a 400
   error with a clear message.

## Files to touch (estimated)

Assuming the existing codebase structure from FRONTEND-REFERENCE.md:

| File | Change |
|------|--------|
| `ChatHeader.tsx` (or equivalent overflow menu component) | Add "Report this chat" menu item |
| `api.ts` / `useChat.ts` | Add `reportChat()` API call |
| New: `ReportDialog.tsx` (optional) | Reason text area + submit/cancel |
| `Toast` / notification system | Success/error feedback |
| `ChatListItem.tsx` (optional) | Small "reported" badge |

**Effort estimate**: ~1-2 hours including the dialog.

## What the backend does with reports

The JSON files land in `/opt/munin/data/reported/` and are consumed
by the developer (manually or via a future script) to build
regression tests. Each report contains the full message history
including the model's thinking stream, tool calls + results, RAG
context, and artifact metadata — everything needed to replay the
conversation and assert on the model's behaviour.

No automated pipeline reads these files yet. The flow is:

1. User reports a chat.
2. Developer reviews the JSON.
3. Developer writes a stress-test or flakiness-suite scenario that
   replays the conversation's user turns and asserts on the
   expected behaviour.
4. Bug is fixed, test is committed, regression is permanently
   caught.
