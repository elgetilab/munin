"""show_protein validates its input and fetches only from RCSB and AlphaFold.

Run in the retrieval image; see tests/README.md. Standalone: prints [PASS]/[FAIL]
and exits non-zero on failure. No network: the HTTP transport is faked.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from mcp.context import current_conversation_id, current_user_email  # noqa: E402
from mcp.schemas import MCP_TOOLS  # noqa: E402
from mcp.tools import structures  # noqa: E402

PDB_TEXT = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 10.00           C\n"
failures = 0


def check(name, ok):
    global failures
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def fake_transport(seen, routes):
    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url in routes:
            status, body = routes[url]
            return httpx.Response(status, content=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def run_fetch(routes, source, structure_id):
    """fetch_structure against a fake network. Returns (entry or error, urls seen)."""
    structures._cache.clear()
    seen = []
    real = httpx.AsyncClient
    transport = fake_transport(seen, routes)
    structures.httpx.AsyncClient = lambda **kw: real(transport=transport, **kw)
    try:
        return asyncio.run(structures.fetch_structure(source, structure_id)), seen
    except structures.StructureError as exc:
        return str(exc), seen
    finally:
        structures.httpx.AsyncClient = real


CRN = "https://files.rcsb.org/download/1CRN"
ENTRY = "https://data.rcsb.org/rest/v1/core/entry/1CRN"
AF_API = "https://alphafold.ebi.ac.uk/api/prediction/P69905"
AF_FILE = "https://alphafold.ebi.ac.uk/files/AF-P69905-F1-model_v6.pdb"

# ---- fetching
entry, _ = run_fetch(
    {f"{CRN}.pdb": (200, PDB_TEXT), ENTRY: (200, json.dumps({"struct": {"title": "Crambin"}}))},
    "pdb", "1crn",
)
check("a PDB entry comes back with its title", entry["format"] == "pdb" and entry["title"] == "1CRN: Crambin")

entry, _ = run_fetch({f"{CRN}.pdb": (200, PDB_TEXT)}, "pdb", "1CRN")
check("a missing title does not lose the structure", entry["data"] == PDB_TEXT and entry["title"] == "PDB 1CRN")

entry, seen = run_fetch({f"{CRN}.cif": (200, "data_1CRN\n")}, "pdb", "1CRN")
check("an entry with no .pdb falls back to mmCIF", entry["format"] == "cif" and f"{CRN}.pdb" in seen)

entry, _ = run_fetch({}, "pdb", "1CRN")
check("an unknown entry is reported", entry == "no PDB entry 1CRN")

prediction = [{"pdbUrl": AF_FILE, "uniprotDescription": "Hemoglobin subunit alpha"}]
entry, _ = run_fetch({AF_API: (200, json.dumps(prediction)), AF_FILE: (200, PDB_TEXT)}, "alphafold", "P69905")
check("an AlphaFold model comes back", entry["data"] == PDB_TEXT and "Hemoglobin" in entry["title"])

evil = [{"pdbUrl": "http://169.254.169.254/latest/meta-data"}]
entry, seen = run_fetch({AF_API: (200, json.dumps(evil))}, "alphafold", "P69905")
check("a file location off the AlphaFold host is refused", isinstance(entry, str) and len(seen) == 1)

entry, _ = run_fetch({f"{CRN}.pdb": (500, "boom")}, "pdb", "1CRN")
check("an upstream failure is reported, not raised raw", isinstance(entry, str) and "could not reach" in entry)

big = "x" * (structures.MAX_STRUCTURE_BYTES + 1)
entry, _ = run_fetch({f"{CRN}.pdb": (200, big)}, "pdb", "1CRN")
check("an oversized structure is refused", isinstance(entry, str) and "limit" in entry)

for source, bad in [
    ("pdb", "../../etc/passwd"),
    ("pdb", "1CRN\n"),
    ("alphafold", "P69905\n"),
    ("pdb", "1CRN/../x"),
    ("pdb", "http://evil"),
    ("alphafold", "P69905/../../x"),
    ("alphafold", "not-an-id"),
    ("elsewhere", "1CRN"),
]:
    entry, seen = run_fetch({}, source, bad)
    check(f"{source} id {bad!r} is refused with no request", isinstance(entry, str) and not seen)

structures._cache.clear()
structures._cache[("pdb", "1CRN")] = {"format": "pdb", "data": PDB_TEXT, "title": "t"}


async def cached():
    return await structures.fetch_structure("pdb", "1crn")


check("a cached entry is served without a request", asyncio.run(cached())["title"] == "t")

structures._cache.clear()
original_cap = structures._MAX_CACHE_BYTES
structures._MAX_CACHE_BYTES = 10
structures._remember(("pdb", "A"), {"data": "x" * 8})
structures._remember(("pdb", "B"), {"data": "x" * 8})
check("the cache drops its oldest entry at the cap", list(structures._cache) == [("pdb", "B")])
structures._MAX_CACHE_BYTES = original_cap
structures._cache.clear()

# ---- stored specs
check("a good spec parses", structures.parse_protein_spec('{"widget":"protein","source":"pdb","id":"1CRN"}')["id"] == "1CRN")
for bad in ["not json", "[]", '{"widget":"other"}', '{"widget":"protein","source":1,"id":"1CRN"}']:
    try:
        structures.parse_protein_spec(bad)
        check(f"spec {bad!r} is refused", False)
    except structures.StructureError:
        check(f"spec {bad!r} is refused", True)


# ---- the tool
def run_tool(conversation="c1", **arguments):
    created = []

    async def fake_fetch(source, structure_id):
        return {"format": "pdb", "data": PDB_TEXT, "title": f"{structure_id}: Crambin"}

    async def fake_create(**kwargs):
        created.append(kwargs)
        return {"id": "art_1", "title": kwargs["title"], "content_type": kwargs["content_type"], "version": 1}

    import artifact_store

    real_fetch, real_create = structures.fetch_structure, artifact_store.create_artifact
    structures.fetch_structure, artifact_store.create_artifact = fake_fetch, fake_create
    email_token = current_user_email.set("a@b.c")
    conv_token = current_conversation_id.set(conversation)
    try:
        return asyncio.run(structures.show_protein(**arguments)), created
    finally:
        current_user_email.reset(email_token)
        current_conversation_id.reset(conv_token)
        structures.fetch_structure, artifact_store.create_artifact = real_fetch, real_create


result, created = run_tool(pdb_id="1crn", style="stick", highlight=[{"chain": "A", "residues": [3, 4]}])
stored = json.loads(created[0]["content"])
check("the tool stores a spec, not coordinates", stored["id"] == "1CRN" and PDB_TEXT not in created[0]["content"])
check("the spec keeps style and highlight", stored["style"] == "stick" and stored["highlight"] == [{"chain": "A", "residues": [3, 4]}])
check("the artifact has the widget type", created[0]["content_type"] == structures.WIDGET_CONTENT_TYPE)
check("the title comes from the database", result["title"] == "1CRN: Crambin")

result, _ = run_tool(pdb_id="1CRN", title="My protein")
check("a given title is used", result["title"] == "My protein")

result, created = run_tool(uniprot_id=" p69905 ")
check("a UniProt id is normalised", json.loads(created[0]["content"])["id"] == "P69905")

for name, arguments, expected in [
    ("neither id", {}, "exactly one"),
    ("both ids", {"pdb_id": "1CRN", "uniprot_id": "P69905"}, "exactly one"),
    ("a bad style", {"pdb_id": "1CRN", "style": "<script>"}, "style must be"),
    ("a bad color", {"pdb_id": "1CRN", "color": "red"}, "color must be"),
    ("a bad highlight", {"pdb_id": "1CRN", "highlight": "1-5"}, "highlight"),
    ("a string residue", {"pdb_id": "1CRN", "highlight": [{"residues": ["1"]}]}, "residue"),
    ("a bad chain", {"pdb_id": "1CRN", "highlight": [{"chain": "A;B", "residues": [1]}]}, "chain"),
    ("too many residues", {"pdb_id": "1CRN", "highlight": [{"residues": list(range(201))}]}, "at most"),
]:
    result, created = run_tool(**arguments)
    check(f"{name} is refused", expected in result.get("error", "") and not created)

result, created = run_tool(conversation="ephemeral-1", pdb_id="1CRN")
check("ephemeral chats are refused", "ephemeral" in result.get("error", "") and not created)

# ---- the schema offers only what the tool accepts
schema = MCP_TOOLS["show_protein"]["inputSchema"]["properties"]
check("the schema lists the styles", tuple(schema["style"]["enum"]) == structures.STYLES)
check("the schema lists the colors", tuple(schema["color"]["enum"]) == structures.COLORS)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
