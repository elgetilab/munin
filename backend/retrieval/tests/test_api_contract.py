"""
Contract test: BACKEND-API.md vs FastAPI routes in main.py (and the
APIRouters it includes).

The post-2026-04 monorepo audit (`shared/docs/DECISIONS.md`)
found three frontend-facing endpoints implemented in main.py
that weren't in BACKEND-API.md (silent contract drift). This
test prevents the same class of bug.

**Host-only.** This test compares two files that both live in
the repo (`shared/docs/BACKEND-API.md` ↔ `backend/retrieval/main.py`).
The retrieval container only mounts what it needs at runtime
and doesn't have `shared/docs/` inside it, so running this test
inside the container exits cleanly with a SKIP — invoke from the
host instead:

    python backend/retrieval/tests/test_api_contract.py

Pure stdlib — no FastAPI/Qdrant/vLLM deps.

Exit codes: 0 = pass or skip (doc not reachable), 1 = real
failure. Prints a diff-style report on failure so it's obvious
which side of the contract drifted.

What it checks:
1. Every `/api/*` route in main.py appears somewhere in
   BACKEND-API.md (caught by `\\`METHOD /api/...\\`` in any
   backtick block — including section headers and prose).
2. Every endpoint documented in BACKEND-API.md (matched by the
   same backtick pattern) is actually implemented in main.py.

Allowlist for `/api/*` routes that are intentionally
out-of-contract (ops-only, internal, etc.) lives in
`OUT_OF_CONTRACT` below. Add to it if you add a route that's
genuinely not for the frontend.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# This file lives at .../backend/retrieval/tests/test_api_contract.py.
# parents[0]=tests/ parents[1]=retrieval/ parents[2]=backend/ parents[3]=<repo root>.
# In the retrieval container the file is at /app/tests/... and parents[3]
# doesn't exist — handle that gracefully.
_HERE = Path(__file__).resolve()
_PARENTS = list(_HERE.parents)
REPO_ROOT = _PARENTS[3] if len(_PARENTS) > 3 else None
MAIN_PY = REPO_ROOT / "backend" / "retrieval" / "main.py" if REPO_ROOT else None
DOC = REPO_ROOT / "shared" / "docs" / "BACKEND-API.md" if REPO_ROOT else None

# Routes that are /api/* but not part of the frontend contract.
# Keep this list short and justify each entry inline.
OUT_OF_CONTRACT: set[tuple[str, str]] = {
    # Documented in BACKEND-API.md §1 as out-of-band ingest path
    # — token auth, called by VPS hook service, contract lives in
    # shared/docs/CONTRIBUTOR-INGEST.md.
    ("POST", "/api/admin/ingest"),
}

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Match any `<METHOD> /path` token inside backticks. Picks up
# both section headers (`### 4.1 \`GET /api/status\``) and prose
# mentions (`see \`POST /api/chat/completions\``). Both count as
# "documented" — anything mentioned in the canonical doc is fine.
DOC_PATTERN = re.compile(
    r"`(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`]+)`"
)


def normalize_path(path: str) -> str:
    """Collapse path-parameter names so `/api/chats/{id}` and
    `/api/chats/{conversation_id}` compare equal. Also drops
    any query string — the doc occasionally writes
    `GET /api/chats?project_id=<pid>` to document a query param
    usage, but the test compares route paths, not URLs."""
    path = path.split("?", 1)[0]
    return re.sub(r"\{[^}]+\}", "{}", path)


def extract_documented_routes() -> set[tuple[str, str]]:
    """Return {(METHOD, normalized_path)} for every endpoint
    referenced in BACKEND-API.md."""
    text = DOC.read_text(encoding="utf-8")
    return {
        (method, normalize_path(path))
        for method, path in DOC_PATTERN.findall(text)
    }


def _decorated_routes(tree: ast.AST, owner: str, prefix: str = "") -> set[tuple[str, str]]:
    """{(METHOD, normalized_path)} for every `@<owner>.<method>("/path")`
    decorator in `tree`, with `prefix` prepended."""
    routes: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != owner:
                continue
            method = func.attr.upper()
            if method not in METHODS or not dec.args:
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                routes.add((method, normalize_path(prefix + arg.value)))
    return routes


def _router_prefix(tree: ast.AST) -> str:
    """The `prefix=` of a module-level `router = APIRouter(...)`, or ""."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            continue
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return ""


def _included_router_modules(tree: ast.AST) -> list[Path]:
    """Source files of every `from M import router as X` whose X is passed
    to `app.include_router(X)`. Routes on an APIRouter never appear as
    `@app.<method>`, so without following these the /api/research/* surface
    read as documented-but-unimplemented although it is served."""
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name == "router":
                    imported[alias.asname or alias.name] = node.module
    included: list[Path] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router" and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name) and arg.id in imported:
            rel = Path(*imported[arg.id].split("."))
            for cand in (MAIN_PY.parent / rel.with_suffix(".py"),
                         MAIN_PY.parent / rel / "__init__.py"):
                if cand.is_file():
                    included.append(cand)
                    break
    return included


def extract_implemented_routes() -> set[tuple[str, str]]:
    """Parse main.py and return {(METHOD, normalized_path)} for
    every `@app.<method>("/path")` decorator, plus every
    `@router.<method>` route of an APIRouter main.py includes (with
    that router's prefix). Uses AST so we don't have to import
    main.py (which would drag in Qdrant / Neo4j / vLLM connections)."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    routes = _decorated_routes(tree, "app")
    for mod in _included_router_modules(tree):
        sub = ast.parse(mod.read_text(encoding="utf-8"))
        routes |= _decorated_routes(sub, "router", _router_prefix(sub))
    return routes


def report(label: str, items: set[tuple[str, str]]) -> str:
    if not items:
        return f"{label}: (none)\n"
    lines = [f"{label}:"]
    for method, path in sorted(items):
        lines.append(f"  {method:<6} {path}")
    return "\n".join(lines) + "\n"


def run() -> int:
    # Skip cleanly if either file is unreachable (e.g. running
    # inside the retrieval container, which doesn't have
    # shared/docs/ mounted). The host-side run is the canonical
    # check.
    if DOC is None or not DOC.is_file() or MAIN_PY is None or not MAIN_PY.is_file():
        print(
            "SKIP test_api_contract — BACKEND-API.md or main.py "
            "not reachable from this filesystem (host-only test)."
        )
        return 0

    documented = extract_documented_routes()
    implemented = extract_implemented_routes()
    api_implemented = {
        (m, p) for (m, p) in implemented if p.startswith("/api/")
    }

    # Forward direction: implemented → documented.
    # Skip OUT_OF_CONTRACT explicitly.
    undocumented = api_implemented - documented - OUT_OF_CONTRACT

    # Reverse direction: documented → implemented.
    # Only check /api/* documented entries — the doc also
    # references legacy/internal routes (`/health`, `/retrieve`,
    # `/mcp/*`, `/deepresearch/*`, etc.) by name and those aren't
    # part of the contract reverse-direction either.
    documented_api = {(m, p) for (m, p) in documented if p.startswith("/api/")}
    phantom = documented_api - api_implemented

    failures = []
    if undocumented:
        failures.append(
            "Implemented but not documented in BACKEND-API.md.\n"
            "Either document them in BACKEND-API.md §4, or — if\n"
            "they're intentionally out-of-contract — add to\n"
            "OUT_OF_CONTRACT in this test file with a justifying\n"
            "comment.\n"
            + report("missing from doc", undocumented)
        )
    if phantom:
        failures.append(
            "Documented in BACKEND-API.md but not implemented in\n"
            "main.py. Either add the route or remove the doc\n"
            "entry.\n"
            + report("phantom in doc", phantom)
        )

    if failures:
        print("FAIL test_api_contract\n")
        for f in failures:
            print(f)
        return 1

    print(
        f"OK test_api_contract — "
        f"{len(api_implemented)} /api/* routes documented; "
        f"{len(OUT_OF_CONTRACT)} explicitly out-of-contract."
    )
    return 0


# Allow both: pytest-style (function returns nothing, asserts)
# and script-style (sys.exit on the run() return code).
def test_api_contract():  # pytest entry-point
    assert run() == 0, "see stdout for the diff"


if __name__ == "__main__":
    sys.exit(run())
