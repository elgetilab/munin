# Archived frontend docs

Specs and plans that drove features which have shipped, been replaced or been
dropped. They are kept so the history and the intent behind the code can be
read together. None of them describes the current system: for that read
[frontend/README.md](../../README.md), [INSTALL.md](../../../INSTALL.md) and
the API contract, [shared/docs/BACKEND-API.md](../../../shared/docs/BACKEND-API.md).

| Doc | What it was |
|---|---|
| `DESIGN.md` | The original VPS design, from when the frontend was its own repository |
| `MIGRATION.md` | Moving the VPS services out of the cluster repo into their own (since merged into this monorepo) |
| `API-GATEWAY.md` | API keys and usage tracking (shipped) |
| `CHAT-PERSISTENCE.md` | Server-side chat persistence and context memory (shipped) |
| `CHAT-UI-TASK-LOG.md`, `FEATHER-VORTEX.md` | Loading messages, the task log and the thinking indicator (shipped) |
| `FRONTEND-IMPLEMENTATION-PLAN.md`, `FRONTEND-TASKS.md` | The chat UI's integration with the v2 backend (shipped) |
| `FRONTEND-KNOWLEDGE-TAB.md` | The knowledge browser and `#tag` composer (shipped) |
| `FRONTEND-REPORT-CHAT.md` | "Report this chat" (shipped) |
| `SLEEPING-PAGE.md` | The off-hours page while vLLM is stopped (shipped) |
| `STARRED-CONVERSATIONS.md` | Syncing starred chats to the backend (shipped) |
| `ADDITIONAL-FEATURES.md` | 2026-Q1 specs: PWA, user memory, BM25, compaction (mostly shipped) |
| `AGENTIC-ORCHESTRATION.md` | The first agent design, replaced in 2026-07 by the four-agent architecture |
