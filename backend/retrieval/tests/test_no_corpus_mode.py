"""MUNIN_CORPUS=0 and MUNIN_DISABLED_TOOLS, as backend/docker/lite.yml sets them.

Run in the retrieval image with those variables set; see tests/README.md.
Standalone: prints [PASS]/[FAIL] and exits non-zero on failure.
"""

import asyncio
import os
import sys

os.environ["MUNIN_CORPUS"] = "0"
os.environ["MUNIN_DISABLED_TOOLS"] = "paper_search,get_citations,compile_latex"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import capabilities  # noqa: E402
import database  # noqa: E402
from mcp import executor  # noqa: E402
from mcp._dispatch import verify_dispatch_registry  # noqa: E402
from mcp.schemas import CORE_TOOLS, DISABLED_TOOLS, MCP_TOOLS  # noqa: E402

failures = 0


def check(name, ok):
    global failures
    failures += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


check("disabled tools leave the registry", not DISABLED_TOOLS & set(MCP_TOOLS))
check("other tools stay", {"web_search", "run_python", "search"} <= set(MCP_TOOLS))
check("core set stays usable", "web_search" in CORE_TOOLS and "paper_search" not in CORE_TOOLS)

try:
    verify_dispatch_registry()
    check("dispatch registry still verifies", True)
except RuntimeError as exc:
    check(f"dispatch registry still verifies: {exc}", False)

result = asyncio.run(executor.execute_mcp_tool("get_citations", {"doi": "10.1/x"}))
check("a disabled tool cannot be called", "Unknown tool" in str(result.get("error")))

features = " ".join(capabilities._STATIC_FEATURES)
check("capabilities say the corpus is offline", "offline" in features and "/knowledge" not in features)

sentinel = object()
database._bge = sentinel
database._paper_encoder = None
original = database.get_bge
database.get_bge = lambda: sentinel
check("the paper encoder is BGE-base", database.get_paper_encoder() is sentinel)
database.get_bge = original

sys.exit(1 if failures else 0)
