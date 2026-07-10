"""Corpus-membership oracle for Track C (confabulation detection).

A local DOI cited in an answer that is NOT in `papers_bge` is a confabulated
local citation - the core judge-free abstention signal. This module owns the set
of the 67,675 corpus DOIs (cached; refreshable from Qdrant).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

_CACHE = os.path.expanduser("~/.cache/munin_bench_data/papers_bge_dois.json")
_QDRANT = "http://127.0.0.1:6333/collections/papers_bge/points/scroll"

_dois: set[str] | None = None


def normalize_doi(doi: str) -> str:
    """Lowercase, strip a leading doi:/https://doi.org/ and trailing junk."""
    d = (doi or "").strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d)
    return d.strip().rstrip(".,);]")


def build_from_qdrant(save: bool = True) -> set[str]:
    """Scroll papers_bge and collect all payload DOIs."""
    dois: set[str] = set()
    offset = None
    while True:
        body = {"limit": 4000, "with_payload": ["doi"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        req = urllib.request.Request(
            _QDRANT, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=60))["result"]
        for p in r["points"]:
            d = (p.get("payload") or {}).get("doi")
            if d:
                dois.add(normalize_doi(d))
        offset = r.get("next_page_offset")
        if offset is None:
            break
    if save:
        os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
        json.dump(sorted(dois), open(_CACHE, "w"))
    return dois


def corpus_dois() -> set[str]:
    """The cached corpus DOI set (built from Qdrant on first miss)."""
    global _dois
    if _dois is None:
        if os.path.exists(_CACHE):
            _dois = {normalize_doi(d) for d in json.load(open(_CACHE))}
        else:
            _dois = build_from_qdrant()
    return _dois


def in_corpus(doi: str) -> bool:
    return normalize_doi(doi) in corpus_dois()
