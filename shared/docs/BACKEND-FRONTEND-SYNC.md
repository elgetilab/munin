# Backend ↔ Frontend Sync Log

This is the live coordination log between the cluster (`backend/`)
and VPS (`frontend/`) sides of the monorepo. Add an entry here
when a change needs both sides to coordinate, when one side has a
question for the other, or when a decision is reached that should
be findable later.

The previous sync log (which ran roughly 2026-03 through
2026-04) was archived to
[`archive/SYNC-2026-04.md`](archive/SYNC-2026-04.md) on
2026-05-04 once its self-archival criterion was met
(`default_tags` shipped + both sides done with the
`munin-vps` → monorepo housekeeping). Read that doc for the
historical record of why various design decisions look the way
they do.

For the canonical API contract, see
[`BACKEND-API.md`](BACKEND-API.md). For contributor-ingest, see
[`CONTRIBUTOR-INGEST.md`](CONTRIBUTOR-INGEST.md).

---

## Format

```
### Q<N>. <one-line topic>  (<status>)

**From:** backend | frontend
**Date:** YYYY-MM-DD
**Question / decision / context:**

  ...

**Answer / outcome:**

  ...
```

`status` is one of `open`, `answered`, `decided`, `obsolete`. Mark
status changes inline; don't delete entries, they're load-bearing
context for whoever's catching up.

---

## Open

(none yet)

## Resolved

(none yet, see archive)
