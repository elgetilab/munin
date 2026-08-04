"""A3 up-front per-turn router.

Decides the turn's PROFILE (chat | research | code) BEFORE the first model
call, from the query, biased by the pinned persona. The profile drives the
layered system prompt (base[pin] + fragment[routed], see personas.py),
sampling, and (pre-A4) tool subset.

Cheapest-first cascade (handoff §A3, decisions in docs/paper-track/done/A3-PLAN.md):
  1. RULES (tier 1): slash commands /research /code /chat -> force that
     profile (absolute; overrides pin + KNN). Q1.
  2. KNN (tier 2): BGE-embed the query, distance-weighted vote over a small
     labelled example set, with the pinned profile as a prior. Accept iff the
     winner clears a margin AND the nearest neighbour is in-distribution
     (OOD guard). Q4.
  3. FALLBACK: the pin if explicitly pinned, else chat. (Tier-3 LLM
     classifier is NOT built here; gated on sign-off per the handoff.)

The embedder is INJECTED (`embed_fn: list[str] -> np.ndarray (N,D)`,
L2-normalised) so this module is unit-testable without loading BGE; the
integration layer (chat_service) wires `database.get_bge().encode`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

PROFILES = ("chat", "research", "code")
DEFAULT_PROFILE = "chat"

# Tunable (Q4: tuned against the routing eval; these are sensible starting
# values, NOT final). Kept as module constants so the tuning step is one place.
KNN_K = 8
MARGIN_THRESHOLD = 0.10     # winner must lead runner-up by this fraction of total vote
OOD_SIM_THRESHOLD = 0.45    # nearest-neighbour cosine sim must exceed this (else OOD -> fallback)
PIN_PRIOR = 0.5             # vote weight the pinned profile gets as a prior

_EXAMPLES_PATH = os.path.join(os.path.dirname(__file__), "router_examples.json")

EmbedFn = Callable[[list], "np.ndarray"]


@dataclass
class RoutingDecision:
    profile: str          # chat | research | code (the turn's routed profile)
    method: str           # "rule" | "knn" | "fallback"
    confidence: float     # margin for knn; 1.0 for a rule; the (rejected) margin for fallback
    pin: Optional[str] = None
    # When a slash command fired, the query with the leading `/<profile>`
    # token removed (the integration sends THIS to the model). None otherwise.
    stripped_query: Optional[str] = None


# --- tier 1: slash commands -------------------------------------------------
_SLASH_RE = re.compile(r"^\s*/(\w+)\b[ \t]*(.*)$", re.S)
_SLASH_MAP = {"research": "research", "code": "code", "chat": "chat"}


def parse_slash(query: str) -> Optional[tuple]:
    """If the query starts with a known profile slash command, return
    (profile, stripped_query); else None. `/<profile> <query>`: the leading
    token is stripped. A bare `/<profile>` yields an empty stripped query."""
    m = _SLASH_RE.match(query or "")
    if not m:
        return None
    profile = _SLASH_MAP.get(m.group(1).lower())
    if profile is None:
        return None
    return profile, m.group(2).strip()


# --- tier 2: KNN index ------------------------------------------------------
class RouterIndex:
    """Labelled example queries + their embeddings for KNN routing."""

    def __init__(self, queries: list, profiles: list, embeddings: "np.ndarray"):
        self.queries = queries
        self.profiles = profiles
        self.embeddings = embeddings  # (N, D), L2-normalised

    @classmethod
    def from_examples(cls, examples: list, embed_fn: EmbedFn) -> "RouterIndex":
        queries = [e["query"] for e in examples]
        profiles = [e["profile"] for e in examples]
        embs = _l2_normalise(np.asarray(embed_fn(queries), dtype=np.float32))
        return cls(queries, profiles, embs)

    @classmethod
    def from_file(cls, embed_fn: EmbedFn, path: str = _EXAMPLES_PATH) -> "RouterIndex":
        with open(path) as f:
            doc = json.load(f)
        return cls.from_examples(doc["examples"], embed_fn)


def _l2_normalise(m: "np.ndarray") -> "np.ndarray":
    if m.ndim == 1:
        m = m[None, :]
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


# --- routing ----------------------------------------------------------------
def route(
    query: str,
    pin: Optional[str],
    index: RouterIndex,
    embed_fn: EmbedFn,
) -> RoutingDecision:
    """Route one turn. `pin` is the pinned persona id (the user's selector),
    used as a prior and as the fallback target."""
    # Tier 1: slash command is absolute.
    slash = parse_slash(query)
    if slash is not None:
        return RoutingDecision(slash[0], "rule", 1.0, pin=pin, stripped_query=slash[1])

    # Tier 2: distance-weighted KNN with pin prior + OOD guard.
    q = _l2_normalise(np.asarray(embed_fn([query]), dtype=np.float32))[0]
    sims = index.embeddings @ q                      # cosine (both normalised)
    k = min(KNN_K, len(sims))
    top_idx = np.argsort(-sims)[:k]

    votes = {p: 0.0 for p in PROFILES}
    for i in top_idx:
        votes[index.profiles[i]] += max(float(sims[i]), 0.0)   # closer = bigger vote
    if pin in PROFILES:
        votes[pin] += PIN_PRIOR                       # the pin biases the vote (Q2)

    total = sum(votes.values()) or 1.0
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    top_profile, top_w = ranked[0]
    runner_w = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_w - runner_w) / total
    nearest_sim = float(sims[top_idx[0]]) if k else 0.0

    in_distribution = nearest_sim >= OOD_SIM_THRESHOLD
    decisive = margin >= MARGIN_THRESHOLD
    if in_distribution and decisive:
        return RoutingDecision(top_profile, "knn", margin, pin=pin)

    # Fallback: pin if explicitly pinned, else chat (Q4).
    fallback = pin if pin in PROFILES else DEFAULT_PROFILE
    return RoutingDecision(fallback, "fallback", margin, pin=pin)
