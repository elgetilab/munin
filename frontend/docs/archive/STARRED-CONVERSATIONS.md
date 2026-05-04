# Starred Conversations — Backend Sync

## Current State

Starring is implemented frontend-only using `localStorage`. This means stars don't sync across devices or browsers.

## Goal

Persist starred state per-user on the backend so it syncs across all sessions.

## Backend Changes Required

### 1. Database: Add `starred` column to conversations table

On the cluster's conversation storage (SQLite):

```sql
ALTER TABLE conversations ADD COLUMN starred BOOLEAN NOT NULL DEFAULT 0;
```

### 2. API: Add star/unstar endpoint

```
PUT /api/chats/{conversation_id}/star
DELETE /api/chats/{conversation_id}/star
```

Both return `204 No Content` on success. The `PUT` sets `starred = 1`, the `DELETE` sets `starred = 0`.

Authentication: Uses `X-Munin-Email` header (from forward-auth). Users can only star their own conversations.

### 3. API: Include `starred` in conversation list response

`GET /api/chats` should return `starred: true/false` on each conversation in the list:

```json
{
  "conversations": [
    {
      "id": "...",
      "title": "...",
      "starred": true,
      ...
    }
  ]
}
```

The `starred` field should also appear on `GET /api/chats/{id}`.

### 4. API: Optional `starred` filter

`GET /api/chats?starred=true` — returns only starred conversations.

Not strictly required (the frontend can filter client-side), but useful if the conversation list grows large.

### 5. API: PATCH support (alternative to dedicated endpoints)

Instead of separate star/unstar endpoints, the existing `PATCH /api/chats/{id}` could be extended to accept `starred`:

```json
PATCH /api/chats/{conversation_id}
{ "starred": true }
```

This is simpler since the PATCH endpoint already exists for renaming (`{ "title": "..." }`).

## Frontend Changes Required

Once the backend supports `starred`, the frontend changes are minimal:

1. **Remove localStorage** — Delete `loadStarred()`, `saveStarred()`, and the `STARRED_KEY` constant from `Sidebar.tsx`.

2. **Read from API** — The `starred` field comes with `fetchChats()` response. Use it directly instead of the local `Set<string>`.

3. **Toggle via API** — Replace the `toggleStar` function to call `PATCH /api/chats/{id}` with `{ starred: true/false }` instead of updating localStorage.

4. **Add to `api.ts`**:
```typescript
export async function starChat(id: string, starred: boolean): Promise<void> {
  const res = await fetch(`${API}/chats/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ starred }),
  });
  if (!res.ok) throw new Error('Failed to update starred status');
}
```

5. **Add to `types.ts`** — Add `starred: boolean` to `ConversationSummary` interface.

## Recommended Approach

Use the PATCH approach (#5 above) since it extends the existing endpoint. The cluster backend already handles `PATCH /api/chats/{id}` for title updates — just add `starred` to the accepted fields.

## Migration

When enabling backend sync, the frontend should do a one-time migration:
1. On first load after the update, check if `localStorage` has starred IDs
2. If so, PATCH each one to set `starred: true` on the backend
3. Clear the localStorage key

This preserves any stars users set before the backend was ready.
