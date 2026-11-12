"""/api/status and /api/models carry the llm-failover proxy's state.

`llm_fallback_block` is lifted out of main.py by AST so the test needs none of
retrieval's runtime (encoders, databases). Runs under pytest or plain python.
"""
import ast
from pathlib import Path
from typing import Optional

MAIN = Path(__file__).resolve().parent.parent / "main.py"


def _load():
    tree = ast.parse(MAIN.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "llm_fallback_block")
    ns = {"Optional": Optional}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(MAIN), "exec"), ns)
    return ns["llm_fallback_block"]


llm_fallback_block = _load()


def test_plain_vllm_has_no_block():
    assert llm_fallback_block({"object": "list", "data": [{"id": "qwen3.8-27b"}]}) is None


def test_proxy_on_primary():
    payload = {"data": [], "munin_llm": {"fallback_active": False, "model": "qwen3.8-27b", "since": None}}
    assert llm_fallback_block(payload) == {"active": False, "model": None, "since": None}


def test_proxy_on_secondary():
    payload = {"data": [], "munin_llm": {"fallback_active": True, "model": "claude-haiku-5-5",
                                         "since": "2026-11-12T10:00:00+00:00", "route": "secondary"}}
    assert llm_fallback_block(payload) == {
        "active": True, "model": "claude-haiku-5-5", "since": "2026-11-12T10:00:00+00:00"}


def test_garbage_is_ignored():
    assert llm_fallback_block({"munin_llm": "yes"}) is None
    assert llm_fallback_block([]) is None


def test_status_route_uses_it():
    src = MAIN.read_text()
    assert '"llm_fallback": llm_fallback' in src
    assert "llm_fallback = llm_fallback_block(data)" in src


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as exc:
                failed += 1
                print(f"[FAIL] {name}: {exc}")
    raise SystemExit(1 if failed else 0)
