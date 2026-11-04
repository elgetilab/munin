"""The structure route serves only a protein artifact the caller owns.

Run in the retrieval image; see tests/README.md. Standalone: prints [PASS]/[FAIL]
and exits non-zero on failure. No network: the store and the fetch are faked.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from mcp.tools import structures  # noqa: E402

URL = "/api/chats/c1/artifacts/art_1/structure"
OWNER = {"X-Munin-Email": "owner@example.org"}
SPEC = json.dumps({"widget": "protein", "source": "pdb", "id": "1CRN"})
failures = 0
calls = []
versions = []
rows = {}


def check(name, ok):
    global failures
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


async def fake_get_version(user_email, conversation_id, artifact_id, version=None):
    versions.append(version)
    return rows.get((user_email, artifact_id))


async def fake_fetch(source, structure_id):
    calls.append((source, structure_id))
    if structure_id == "FAIL":
        raise structures.StructureError("could not reach the structure database")
    return {"format": "pdb", "data": "ATOM", "title": "t"}


main.artifact_store.get_artifact_version = fake_get_version
structures.fetch_structure = fake_fetch
client = TestClient(main.app, raise_server_exceptions=False)


def owned(content, content_type=structures.WIDGET_CONTENT_TYPE):
    rows.clear()
    calls.clear()
    rows[("owner@example.org", "art_1")] = {"content": content, "content_type": content_type}


owned(SPEC)
response = client.get(URL, headers=OWNER)
check("the owner gets the coordinates", response.status_code == 200 and response.json() == {"format": "pdb", "data": "ATOM"})
check("the entry comes from the artifact", calls == [("pdb", "1CRN")])
check("the response stays private to the user", "private" in response.headers.get("cache-control", ""))

client.get(URL + "?version=3", headers=OWNER)
check("a version is passed through", versions[-1] == 3)

response = client.get(URL, headers={"X-Munin-Email": "other@example.org"})
check("another user gets a 404", response.status_code == 404)

response = client.get(URL)
check("no identity is refused", response.status_code in (401, 403))

owned("# a document", content_type="text/markdown")
response = client.get(URL, headers=OWNER)
check("a document is not a structure", response.status_code == 404 and not calls)

owned("not json")
response = client.get(URL, headers=OWNER)
check("a corrupted spec is an error, with no fetch", response.status_code == 502 and not calls)

owned(json.dumps({"widget": "protein", "source": "pdb", "id": "FAIL"}))
response = client.get(URL, headers=OWNER)
check("a database failure explains itself", response.status_code == 502 and "could not reach" in response.text)

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
