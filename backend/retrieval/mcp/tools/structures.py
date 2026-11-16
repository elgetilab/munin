"""
MCP tool: show_protein (an interactive 3D viewer in the message).

The artifact holds only a small JSON spec: which entry, and how to draw it. The
coordinates can run to many megabytes, past the artifact store's cap, so they are
fetched here when needed and served to the browser by an owner-checked route. The
viewer itself runs with no network access, so it cannot fetch them for itself.

Only RCSB and AlphaFold are contacted, and only for ids that match their formats,
so nothing the model or a user writes can steer a request anywhere else.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

import site_config

from .artifacts import _require_persistent_conversation

WIDGET_CONTENT_TYPE = "application/vnd.munin.widget+json"

STYLES = ("cartoon", "stick", "sphere", "line")
COLORS = ("spectrum", "chain", "secondary", "bfactor")

MAX_STRUCTURE_BYTES = 20 * 1024 * 1024
_MAX_CACHE_BYTES = 64 * 1024 * 1024
_MAX_HIGHLIGHT_RESIDUES = 200
_TIMEOUT_S = 30.0

_PDB_ID_RE = re.compile(r"[0-9][A-Za-z0-9]{3}")
# The accession format published by UniProt.
_UNIPROT_ID_RE = re.compile(
    r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}"
)
_CHAIN_RE = re.compile(r"[A-Za-z0-9]{1,4}")

_ALPHAFOLD_HOST = "alphafold.ebi.ac.uk"

# (source, id) -> {"format", "data", "title"}. Most recently used last.
_cache: "OrderedDict[tuple[str, str], dict]" = OrderedDict()


class StructureError(Exception):
    """A structure could not be provided. The message is safe to show."""


def _remember(key: tuple[str, str], entry: dict) -> None:
    _cache[key] = entry
    _cache.move_to_end(key)
    while sum(len(e["data"]) for e in _cache.values()) > _MAX_CACHE_BYTES and len(_cache) > 1:
        _cache.popitem(last=False)


async def _download(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """The body of ``url``, or None if it does not exist. Stops at the size cap."""
    async with client.stream("GET", url) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_STRUCTURE_BYTES:
                raise StructureError(
                    f"this structure is larger than the viewer's "
                    f"{MAX_STRUCTURE_BYTES // (1024 * 1024)} MB limit"
                )
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


async def _fetch_pdb(client: httpx.AsyncClient, entry: str) -> dict:
    # Large assemblies have no legacy .pdb file, only mmCIF.
    fmt = "pdb"
    data = await _download(client, f"https://files.rcsb.org/download/{entry}.pdb")
    if data is None:
        fmt = "cif"
        data = await _download(client, f"https://files.rcsb.org/download/{entry}.cif")
    if data is None:
        raise StructureError(f"no PDB entry {entry}")

    title = f"PDB {entry}"
    try:
        response = await client.get(f"https://data.rcsb.org/rest/v1/core/entry/{entry}")
        response.raise_for_status()
        title = f"{entry}: {response.json()['struct']['title']}"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass  # The coordinates are what matter; a bare id is a fine title.
    return {"format": fmt, "data": data, "title": title}


async def _fetch_alphafold(client: httpx.AsyncClient, accession: str) -> dict:
    response = await client.get(f"https://{_ALPHAFOLD_HOST}/api/prediction/{accession}")
    if response.status_code == 404:
        raise StructureError(f"no AlphaFold model for UniProt {accession}")
    response.raise_for_status()
    try:
        prediction = response.json()[0]
        url = prediction["pdbUrl"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise StructureError(f"no AlphaFold model for UniProt {accession}") from None

    parsed = urlparse(url) if isinstance(url, str) else None
    if parsed is None or parsed.scheme != "https" or parsed.hostname != _ALPHAFOLD_HOST:
        raise StructureError("AlphaFold returned an unexpected file location")
    data = await _download(client, url)
    if data is None:
        raise StructureError(f"no AlphaFold model for UniProt {accession}")

    name = prediction.get("uniprotDescription")
    title = f"{name} ({accession}, AlphaFold)" if isinstance(name, str) and name else f"AlphaFold {accession}"
    return {"format": "pdb", "data": data, "title": title}


async def fetch_structure(source: str, structure_id: str) -> dict:
    """Coordinates for an entry, as ``{"format", "data", "title"}``."""
    if source == "pdb" and _PDB_ID_RE.fullmatch(structure_id):
        fetch, key = _fetch_pdb, (source, structure_id.upper())
    elif source == "alphafold" and _UNIPROT_ID_RE.fullmatch(structure_id):
        fetch, key = _fetch_alphafold, (source, structure_id)
    else:
        raise StructureError(f"not a valid {source} id: {structure_id!r}")

    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_S,
            headers={"User-Agent": site_config.bot_user_agent()},
        ) as client:
            entry = await fetch(client, key[1])
    except httpx.HTTPError as exc:
        raise StructureError(
            f"could not reach the structure database ({type(exc).__name__})"
        ) from exc
    _remember(key, entry)
    return entry


def parse_protein_spec(content: str) -> dict:
    """The ``source`` and ``id`` of a stored spec. Raises StructureError if invalid.

    The model can rewrite a spec with update_artifact, so a stored one gets the
    same checks as a new one when it is used.
    """
    try:
        spec = json.loads(content)
    except (TypeError, ValueError):
        raise StructureError("not a valid protein spec") from None
    if not isinstance(spec, dict) or spec.get("widget") != "protein":
        raise StructureError("not a protein spec")
    if not isinstance(spec.get("source"), str) or not isinstance(spec.get("id"), str):
        raise StructureError("not a valid protein spec")
    return spec


def _clean_highlight(highlight: Any) -> tuple[list[dict], Optional[str]]:
    if highlight is None:
        return [], None
    if not isinstance(highlight, list):
        return [], "highlight must be a list of {chain, residues} objects"
    cleaned: list[dict] = []
    total = 0
    for item in highlight:
        if not isinstance(item, dict):
            return [], "highlight must be a list of {chain, residues} objects"
        residues = item.get("residues")
        if not isinstance(residues, list) or not residues or not all(
            isinstance(r, int) and not isinstance(r, bool) for r in residues
        ):
            return [], "each highlight needs a non-empty 'residues' list of residue numbers"
        total += len(residues)
        if total > _MAX_HIGHLIGHT_RESIDUES:
            return [], f"highlight at most {_MAX_HIGHLIGHT_RESIDUES} residues"
        entry: dict = {"residues": residues}
        chain = item.get("chain")
        if chain is not None:
            if not isinstance(chain, str) or not _CHAIN_RE.fullmatch(chain):
                return [], f"not a valid chain id: {chain!r}"
            entry["chain"] = chain
        cleaned.append(entry)
    return cleaned, None


_RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_RCSB_GRAPHQL = "https://data.rcsb.org/graphql"
_MAX_CANDIDATES = 8


async def find_structures(query: str, limit: int = 6) -> dict:
    """PDB entries matching a protein name, best first, for the model to pick
    from instead of recalling an id (TURN-RESULTS-PLAN.md, part 3).

    Returns {"candidates": [{pdb_id, title, organisms, method, resolution_A}]}
    or {"error": ...}. Uses RCSB's full-text search, then one GraphQL call for
    the details of the hits.
    """
    import provenance
    q = (query or "").strip()
    if not q:
        return {"error": "query is empty"}
    if len(q) > 200:
        return {"error": "query is too long (200 characters at most)"}
    if not provenance.may_fetch(provenance.NET_WEB):
        return {"error": "structure search is not available here (network egress is off); "
                         "find the PDB id with search, then call show_protein with pdb_id"}
    limit = max(1, min(int(limit or 6), _MAX_CANDIDATES))
    body = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": q}},
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": limit},
                            "results_content_type": ["experimental"],
                            "sort": [{"sort_by": "score", "direction": "desc"}]},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S,
                                     headers={"User-Agent": site_config.bot_user_agent()}) as client:
            r = await client.post(_RCSB_SEARCH, json=body)
            if r.status_code == 204:
                return {"candidates": [], "note": f"no PDB entry matches {q!r}"}
            r.raise_for_status()
            ids = [h["identifier"] for h in r.json().get("result_set", [])][:limit]
            if not ids:
                return {"candidates": [], "note": f"no PDB entry matches {q!r}"}
            gql = ("query($ids:[String!]!){entries(entry_ids:$ids){rcsb_id struct{title} "
                   "rcsb_entry_info{experimental_method resolution_combined} "
                   "polymer_entities{rcsb_entity_source_organism{scientific_name}}}}")
            d = await client.post(_RCSB_GRAPHQL, json={"query": gql, "variables": {"ids": ids}})
            d.raise_for_status()
            entries = {e["rcsb_id"]: e for e in (d.json().get("data") or {}).get("entries") or []}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"error": f"could not reach the RCSB search ({type(exc).__name__})"}
    out = []
    for pid in ids:
        e = entries.get(pid) or {}
        info = e.get("rcsb_entry_info") or {}
        res = info.get("resolution_combined") or []
        orgs = sorted({o.get("scientific_name") for pe in (e.get("polymer_entities") or [])
                       for o in (pe.get("rcsb_entity_source_organism") or []) if o.get("scientific_name")})
        out.append({"pdb_id": pid, "title": (e.get("struct") or {}).get("title"),
                    "organisms": orgs, "method": info.get("experimental_method"),
                    "resolution_A": res[0] if res else None})
    return {"candidates": out}


async def show_protein(
    pdb_id: Optional[str] = None,
    uniprot_id: Optional[str] = None,
    style: str = "cartoon",
    color: str = "spectrum",
    highlight: Any = None,
    title: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Create an interactive 3D viewer for a protein in the current conversation.

    With only `query` (a protein name), nothing is shown: the matching PDB
    entries come back for the model to choose from.
    """
    import artifact_store  # lazy to avoid circular init

    if query and not (pdb_id or uniprot_id):
        found = await find_structures(query)
        if "candidates" in found:
            found["note"] = ("Nothing is shown yet. Pick the entry that is the protein the user asked "
                             "for (check title and organism) and call show_protein with its pdb_id.")
        return found

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None

    pdb_id = pdb_id.strip() if isinstance(pdb_id, str) else ""
    uniprot_id = uniprot_id.strip().upper() if isinstance(uniprot_id, str) else ""
    if bool(pdb_id) == bool(uniprot_id):
        return {"error": "pass exactly one of pdb_id or uniprot_id"}
    if style not in STYLES:
        return {"error": f"style must be one of: {', '.join(STYLES)}"}
    if color not in COLORS:
        return {"error": f"color must be one of: {', '.join(COLORS)}"}
    cleaned_highlight, problem = _clean_highlight(highlight)
    if problem:
        return {"error": problem}

    source, structure_id = ("pdb", pdb_id.upper()) if pdb_id else ("alphafold", uniprot_id)
    # Checked now so a mistyped id reaches the model as an error it can correct,
    # rather than the user as an empty viewer.
    try:
        structure = await fetch_structure(source, structure_id)
    except StructureError as exc:
        return {"error": str(exc)}

    spec = {
        "widget": "protein",
        "source": source,
        "id": structure_id,
        "style": style,
        "color": color,
        "highlight": cleaned_highlight,
    }
    display_title = title.strip() if isinstance(title, str) and title.strip() else structure["title"]
    try:
        artifact = await artifact_store.create_artifact(
            user_email=user_email,
            conversation_id=conv_id,
            title=display_title[:artifact_store.MAX_TITLE_CHARS],
            content=json.dumps(spec),
            content_type=WIDGET_CONTENT_TYPE,
        )
    except artifact_store.ArtifactError as exc:
        return {"error": str(exc)}
    return {
        **artifact,
        "structure_title": structure["title"],
        "note": (
            "The interactive viewer now appears in your message and the user can "
            "rotate and restyle it. Do not link to it or describe it as a file."
        ),
    }
