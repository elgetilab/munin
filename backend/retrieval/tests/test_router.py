"""Unit tests for the A3 router (router.py).

A deterministic FAKE embedder maps queries to 3 orthogonal basis vectors
(one per profile) by keyword, so the KNN vote / margin / OOD / pin-prior /
fallback logic is testable without loading BGE.

Run:  python backend/retrieval/tests/test_router.py
      pytest backend/retrieval/tests/test_router.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import router  # noqa: E402

# Orthogonal basis: chat=e0, research=e1, code=e2; OOD=e3 (matches no profile).
_BASIS = {"chat": [1, 0, 0, 0], "research": [0, 1, 0, 0], "code": [0, 0, 1, 0]}


def _fake_embed(queries: list) -> np.ndarray:
    """Keyword -> profile basis vector. Unknown queries -> the OOD axis."""
    out = []
    for q in queries:
        ql = q.lower()
        if any(w in ql for w in ("plot", "python", "code", "figure", "script")):
            out.append(_BASIS["code"])
        elif any(w in ql for w in ("paper", "literature", "citation", "review", "research")):
            out.append(_BASIS["research"])
        elif any(w in ql for w in ("weather", "write", "edit", "define", "explain simply")):
            out.append(_BASIS["chat"])
        else:
            out.append([0, 0, 0, 1])  # out of distribution
    return np.asarray(out, dtype=np.float32)


def _index() -> router.RouterIndex:
    examples = [
        {"query": "write an abstract", "profile": "chat"},
        {"query": "edit my text", "profile": "chat"},
        {"query": "find papers on membranes", "profile": "research"},
        {"query": "literature review of X", "profile": "research"},
        {"query": "explore citations", "profile": "research"},
        {"query": "write python to plot data", "profile": "code"},
        {"query": "make a figure with matplotlib", "profile": "code"},
    ]
    return router.RouterIndex.from_examples(examples, _fake_embed)


# --- tier 1: slash ----------------------------------------------------------

def test_slash_forces_profile_and_strips():
    assert router.parse_slash("/code plot this") == ("code", "plot this")
    assert router.parse_slash("/research recent advances") == ("research", "recent advances")
    assert router.parse_slash("/chat hello") == ("chat", "hello")


def test_slash_bare_and_unknown():
    assert router.parse_slash("/code") == ("code", "")
    assert router.parse_slash("/write something") is None   # /write dropped
    assert router.parse_slash("no slash here") is None


def test_slash_overrides_everything():
    d = router.route("/research plot this", pin="code", index=_index(), embed_fn=_fake_embed)
    assert d.profile == "research" and d.method == "rule"


# --- tier 2: KNN ------------------------------------------------------------

def test_knn_routes_clear_code_query():
    d = router.route("plot the trajectory in python", pin=None, index=_index(), embed_fn=_fake_embed)
    assert d.profile == "code" and d.method == "knn"


def test_knn_routes_clear_research_query():
    d = router.route("find a paper and review the literature", pin=None, index=_index(), embed_fn=_fake_embed)
    assert d.profile == "research" and d.method == "knn"


def test_pin_prior_breaks_a_tie_but_clear_signal_overrides():
    # A clear code query with a research pin still routes to code (Q2: pin
    # biases, a clear cross-profile signal overrides).
    d = router.route("write python to plot data", pin="research", index=_index(), embed_fn=_fake_embed)
    assert d.profile == "code"


def test_ood_query_falls_back_to_pin():
    # The OOD query matches no profile basis -> nearest sim 0 -> fallback to pin.
    d = router.route("zxcv qwer asdf", pin="research", index=_index(), embed_fn=_fake_embed)
    assert d.method == "fallback" and d.profile == "research"


def test_ood_query_unpinned_falls_back_to_chat():
    d = router.route("zxcv qwer asdf", pin=None, index=_index(), embed_fn=_fake_embed)
    assert d.method == "fallback" and d.profile == router.DEFAULT_PROFILE == "chat"


def test_real_example_file_loads_and_routes():
    # The committed router_examples.json loads and produces a decision with
    # the fake embedder (smoke; real routing quality is measured in the eval).
    idx = router.RouterIndex.from_file(_fake_embed)
    assert len(idx.queries) == len(idx.profiles) == idx.embeddings.shape[0] > 0
    d = router.route("write a python script to make a figure", pin=None, index=idx, embed_fn=_fake_embed)
    assert d.profile in router.PROFILES


def _main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn(); print(f"[PASS] {fn.__name__}"); passed += 1
        except Exception as e:
            print(f"[FAIL] {fn.__name__}: {e}"); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
