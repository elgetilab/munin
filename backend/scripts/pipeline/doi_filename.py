"""
DOIs from `doi_<...>.pdf` filenames, without guessing.

The crawler and the pipeline's dispose step name PDFs `doi_<doi>.pdf` with
every `/` (and, in the crawler, every `:`) replaced by `_`. That is lossy: a
DOI may itself contain `_`, so `doi_10.1093_molehr_3.5.431.pdf` could be
`10.1093/molehr_3.5.431` or `10.1093/molehr/3.5.431`. The old decoder restored
only the first `_`, and its result overrode GROBID's DOI, so multi-slash and
colon DOIs were re-keyed to DOIs that do not exist (`10.1093/molehr_3.5.431`,
SICI DOIs with `::` turned into `__`). Found in the 2026-09 review.

`decode()` separates the exact case from the ambiguous one:
- one `_` after the `10.<registrant>` prefix: it must be the `/`, exact;
- more: `candidates()` lists the plausible originals, and `verify()` accepts
  one only when Crossref resolves it AND its title matches the PDF's.

Kept free of the pipeline's imports so the read-only audit
(audit_doi_filenames.py) and the tests can use it directly.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

_PREFIX_RE = re.compile(r"^(10\.\d+)_")
MAX_CANDIDATES = 12


def _stem(filename: str) -> Optional[str]:
    if not filename.startswith("doi_"):
        return None
    stem = filename[4:]
    return stem[:-4] if stem.lower().endswith(".pdf") else stem


def decode(filename: str) -> tuple[Optional[str], list[str]]:
    """(exact_doi, ambiguous_candidates). exact_doi is set only when the
    filename can stand for one DOI; otherwise the candidates need verify()."""
    stem = _stem(filename)
    if stem is None:
        return None, []
    m = _PREFIX_RE.match(stem)
    if not m:
        return None, []
    prefix, suffix = m.group(1), stem[m.end():]
    if not suffix:
        return None, []
    if "_" not in suffix:
        return f"{prefix}/{suffix}", []
    return None, candidates(prefix, suffix)


def candidates(prefix: str, suffix: str) -> list[str]:
    """Plausible original DOIs for an ambiguous suffix, most likely first:
    the old decode (every `_` real), every `_` a `/`, every `_` a `:`
    (SICI DOIs), then one `/` or `:` at a time."""
    pos = [i for i, ch in enumerate(suffix) if ch == "_"]

    def sub(indices: Iterable[int], ch: str) -> str:
        chars = list(suffix)
        for i in indices:
            chars[i] = ch
        return "".join(chars)

    out = [suffix, sub(pos, "/"), sub(pos, ":")]
    for i in pos:
        out += [sub([i], "/"), sub([i], ":")]
    return list(dict.fromkeys(f"{prefix}/{s}" for s in out))[:MAX_CANDIDATES]


def verify(cands: list[str], pdf_title: str,
           fetch_crossref: Callable[[str], Optional[dict]],
           title_similarity: Callable[[str, str], float],
           threshold: float = 0.3) -> tuple[Optional[str], list[dict]]:
    """First candidate Crossref resolves with a title matching `pdf_title`
    (the GROBID title). Returns (doi_or_None, per-candidate trace). With no
    title to compare there is nothing to confirm against, so nothing is
    accepted: a wrong key is worse than falling back to GROBID's DOI path."""
    trace: list[dict] = []
    if not (pdf_title or "").strip():
        return None, trace
    for doi in cands:
        record = fetch_crossref(doi)
        if not record:
            trace.append({"doi": doi, "crossref": False})
            continue
        titles = record.get("title") or []
        if isinstance(titles, str):
            titles = [titles]
        sim = max((title_similarity(pdf_title, t) for t in titles), default=0.0)
        trace.append({"doi": doi, "crossref": True, "title_sim": round(sim, 3)})
        if sim >= threshold:
            return doi, trace
    return None, trace
