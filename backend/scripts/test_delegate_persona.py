#!/usr/bin/env python3
"""RETIRED (A4a, 2026-06).

The persona-delegation flow this script tested (`delegate_to_persona` ->
`delegated`/`persona_changed` SSE) was removed in the persona -> router
migration. Mid-conversation delegation no longer exists; each turn's profile
is chosen up-front by the router (`backend/retrieval/router.py`).

Its replacement is the routing eval, which scores the router's per-turn
profile + tool decision:
    backend/benchmarks/munin_bench/routing/

See shared/docs/DECISIONS.md ("2026-06: persona delegation retired ...").
The original script is in git history if ever needed.
"""
import sys

print(__doc__)
sys.exit(0)
