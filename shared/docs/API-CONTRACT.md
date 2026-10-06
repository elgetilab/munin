# API Contract: retired

This document was the original VPS-side draft of the cluster API
contract. It has been retired in favour of the canonical contract
at [`BACKEND-API.md`](BACKEND-API.md).

For SSE event types, error envelope shape, `/api/status` response
layout, RAG source families, persona handling, and every endpoint
the frontend talks to, **read `BACKEND-API.md` instead**. The old
content of this file disagreed with the live code in several
places (phantom `event: metadata` SSE frame; wrong `/api/status`
fields; `notion`/`graph` listed as RAG sources; `code` field in
the error envelope; `persona` flagged as required) and was a
liability rather than an aid. The 2026-05-04 docs audit that listed
the deltas was not kept.

The file is left in place rather than deleted so that prior links
from external docs and archived specs don't 404. Treat it as a
redirect.
