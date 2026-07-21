"""
Provenance controls: egress and corpus_scope (design D29 / design-v2 §4.3).

Two orthogonal, config-not-inference controls (the model never sets them):

  egress:       off | oa_only | full
      What the network layer may reach. `off` = no external fetch at all;
      `oa_only` = scholarly open-access PDF fetches only (no general web / search
      egress); `full` = unrestricted.

  corpus_scope: curated_only | curated+oa_cache | all
      Which document provenances an answer may draw on, at rest. `curated_only`
      = the curated local corpus only; `curated+oa_cache` = plus previously
      downloaded OA PDFs on local disk; `all` = anything (incl. fresh web).

They are SEPARATE because `egress: off` is necessary but not sufficient for a
private-corpus certification: a cached OA paper sits on local disk and answers
without touching the network, so egress-off sails straight past it. egress gates
the network; corpus_scope gates provenance at rest. A certification run forces
BOTH `egress: off` AND `corpus_scope: curated_only`.

Set per request/run (chat_service for interactive use; a cert/bench harness
forces the strict pair via `controls(...)`). Read by the source/search agents to
decide whether a fetch is permitted and whether a resolved document is in scope.
Recorded in every agent trace so a result is never uninterpretable.

The predicates are PURE (level passed in) so they are testable without any
request context; the `get_*` wrappers read the ContextVars defensively.
"""

from __future__ import annotations

import contextlib

# --- egress levels ---------------------------------------------------------
EGRESS_OFF = "off"
EGRESS_OA_ONLY = "oa_only"
EGRESS_FULL = "full"
EGRESS_LEVELS = (EGRESS_OFF, EGRESS_OA_ONLY, EGRESS_FULL)

# --- corpus_scope levels ---------------------------------------------------
SCOPE_CURATED_ONLY = "curated_only"
SCOPE_CURATED_OA_CACHE = "curated+oa_cache"
SCOPE_ALL = "all"
SCOPE_LEVELS = (SCOPE_CURATED_ONLY, SCOPE_CURATED_OA_CACHE, SCOPE_ALL)

# --- document origins (match the Source envelope's source.origin) ----------
ORIGIN_LOCAL_KB = "local_kb"        # curated local corpus
ORIGIN_OA_CACHE = "oa_cache"        # previously downloaded OA PDF, on disk
ORIGIN_OA_DOWNLOAD = "oa_download"  # fresh OA network fetch
ORIGIN_WEB = "web"                  # general web content

# --- network fetch kinds (what a tool is about to reach for) ---------------
NET_OA_DOWNLOAD = "oa_download"     # fetch an open-access scholarly PDF
NET_WEB = "web"                     # general web search / fetch
NET_SCHOLARLY_API = "scholarly_api"  # Semantic Scholar / CrossRef metadata


# --- pure predicates -------------------------------------------------------
def network_allowed(kind: str, egress: str) -> bool:
    """May a fetch of `kind` proceed under this `egress` level?"""
    if egress == EGRESS_FULL:
        return True
    if egress == EGRESS_OFF:
        return False
    if egress == EGRESS_OA_ONLY:
        # OA scholarly fetches (and the metadata lookups that resolve them) are
        # allowed; general web egress is not.
        return kind in (NET_OA_DOWNLOAD, NET_SCHOLARLY_API)
    return False  # unknown level -> deny (fail closed)


def scope_allows(origin: str, corpus_scope: str) -> bool:
    """May a document of `origin` be used under this `corpus_scope`?"""
    if corpus_scope == SCOPE_ALL:
        return True
    if corpus_scope == SCOPE_CURATED_ONLY:
        return origin == ORIGIN_LOCAL_KB
    if corpus_scope == SCOPE_CURATED_OA_CACHE:
        return origin in (ORIGIN_LOCAL_KB, ORIGIN_OA_CACHE)
    return False  # unknown level -> deny (fail closed)


# --- ContextVar wrappers (lazy + defensive, like agent_trace) --------------
def get_egress() -> str:
    try:
        from mcp.context import current_egress
        return current_egress.get()
    except Exception:
        return EGRESS_FULL


def get_corpus_scope() -> str:
    try:
        from mcp.context import current_corpus_scope
        return current_corpus_scope.get()
    except Exception:
        return SCOPE_ALL


def may_fetch(kind: str) -> bool:
    """Context-aware `network_allowed` using the active egress level."""
    return network_allowed(kind, get_egress())


def may_use(origin: str) -> bool:
    """Context-aware `scope_allows` using the active corpus_scope."""
    return scope_allows(origin, get_corpus_scope())


def snapshot() -> dict:
    """The active controls, for the trace record."""
    return {"egress": get_egress(), "corpus_scope": get_corpus_scope()}


@contextlib.contextmanager
def controls(*, egress: str | None = None, corpus_scope: str | None = None):
    """Scoped override of the provenance controls (validates; resets on exit).

    A certification / private-corpus benchmark run wraps its work in
    ``with controls(egress="off", corpus_scope="curated_only"): ...``.
    """
    from mcp.context import current_corpus_scope, current_egress

    tokens = []
    if egress is not None:
        if egress not in EGRESS_LEVELS:
            raise ValueError(f"invalid egress {egress!r}; expected {EGRESS_LEVELS}")
        tokens.append((current_egress, current_egress.set(egress)))
    if corpus_scope is not None:
        if corpus_scope not in SCOPE_LEVELS:
            raise ValueError(
                f"invalid corpus_scope {corpus_scope!r}; expected {SCOPE_LEVELS}")
        tokens.append((current_corpus_scope, current_corpus_scope.set(corpus_scope)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)
