"""
MCP tool: compute (spec + data -> verified code + artifacts).

`compute(spec, data_handle?, language, budget?)` turns a natural-language spec
into code, runs it in the net-off sandbox, and returns a reproducible bundle
(code + data + figure) with a stated verification level. Scope, honestly: mostly
plots - serious users take the code to the API. That is why this is one rung,
not a ladder.

Design contract (AGENT-IMPLEMENTATION-PLAN.md, D20-D25 + design-v2 §7):
  - Deterministic routing (NOT an LLM judgment): language==python -> sandbox;
    anything else -> strongest static check that language offers.
  - Verifier ladder + `verify_level` in the envelope: for python the only check
    that means anything for a plot is "did it run and produce a figure"
    (executed); static rungs are near-worthless (a script that grabs the wrong
    column lints clean). Per-language guarantees differ and the envelope says so
    (executed | compiled | parsed | returned).
  - Bundle return: code + data.csv + figure.png, where the code reads data.csv
    from its own dir, is byte-identical to what ran, and seeds its RNG - the only
    form where "byte-identical" and "standalone-runnable" are both true.
  - One broad pinned image, no pip install; an import allowlist rejects
    out-of-env imports before spending a subprocess.
  - Data from a document arrives as a HANDLE (source extract mode), never as
    literals the model transcribed - the agent materialises it into the sandbox.
  - Bounded repair loop (<=3), stop on a repeated error signature (stuck, not
    converging). Emits a trace.

The human is the semantic verifier: runs != correct (a wrong axis lints and runs
clean), so the rendered figure comes back via the artifact system for a
glance-check. That is why no grounding contract in the paper sense is needed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from typing import Any, Optional

import httpx

from agent_trace import AgentTrace
from database import VLLM_MODEL_NAME, VLLM_URL
from mcp.tools.source import EXTRACT_DIR

# The one broad pinned image's libraries (design D23) + safe stdlib. An import
# outside this set is rejected before a subprocess is spent.
ALLOWED_IMPORTS = {
    "numpy", "np", "scipy", "pandas", "pd", "matplotlib", "mpl", "seaborn", "sns",
    "sympy", "sklearn", "statsmodels",
    "math", "cmath", "statistics", "random", "itertools", "functools", "collections",
    "json", "csv", "io", "re", "datetime", "decimal", "fractions", "string",
}

# Budget tiers -> sandbox timeout (run_python caps at 120s, so 'compute' rides
# that cap; design's ~5min needs a sandbox-side lift, noted as a follow-up).
BUDGET_TIMEOUTS = {"quick": 15, "compute": 120}
MAX_ATTEMPTS = 3

_CODE_RULES = (
    "Write a SINGLE standalone Python script. Rules: use ONLY numpy, scipy, "
    "pandas, matplotlib, seaborn, sympy, sklearn, statsmodels and the standard "
    "library (no pip installs, no network). Set the matplotlib backend to Agg "
    "('matplotlib.use(\"Agg\")'). Seed all RNG (np.random.seed(0); "
    "import random; random.seed(0)). If input data is needed, read 'data.csv' "
    "from the current directory. Save any figure as 'figure.png' via savefig. "
    "Print a one-line summary at the end. Output ONLY the code inside one "
    "```python fence - no prose before or after."
)


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip()


async def _vllm_code(system: str, user: str, max_tokens: int = 2000) -> str:
    body = {"model": VLLM_MODEL_NAME,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.2, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False}}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
    if r.status_code != 200:
        raise RuntimeError(f"vLLM {r.status_code}: {r.text[:150]}")
    return _extract_code((r.json()["choices"][0]["message"].get("content") or ""))


def _static_gate(code: str) -> tuple[bool, Optional[str]]:
    """Cheap pre-subprocess gate: syntax + import allowlist. Returns (ok, reason)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        for m in mods:
            if m not in ALLOWED_IMPORTS:
                return False, f"disallowed import: {m!r} (not in the pinned image)"
    return True, None


def _is_figure(art: dict) -> bool:
    ct = (art.get("content_type") or "").lower()
    fn = (art.get("filename") or "").lower()
    return ct.startswith("image/") or fn.endswith((".png", ".svg", ".jpg", ".jpeg"))


def _err_signature(error: Optional[str], stderr: str) -> str:
    """Exception type + final traceback frame - stable across cosmetic diffs, so
    a repeated signature means the model is stuck, not converging."""
    head = (error or "").strip().splitlines()[:1]
    tail = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()][-1:]
    return hashlib.sha1(("|".join(head + tail)).encode()).hexdigest()[:12]


def _data_prelude(data_handle: Optional[str], tr: AgentTrace) -> str:
    """Materialise an extract handle's rows into data.csv INSIDE the sandbox.
    Handle-not-payload: the model never held the rows; the agent writes them."""
    if not data_handle:
        return ""
    path = os.path.join(EXTRACT_DIR, f"{data_handle}.json")
    try:
        blob = json.load(open(path))
    except Exception as exc:  # noqa: BLE001
        tr.decide("data handle unreadable", handle=data_handle, err=str(exc))
        return ""
    rows, schema = blob.get("rows") or [], blob.get("schema") or []
    tr.decide("materialised data handle", handle=data_handle, n_rows=len(rows))
    return ("import csv as _csv\n"
            f"_rows = {json.dumps(rows)}\n"
            f"_cols = {json.dumps(schema)} or (list(_rows[0].keys()) if _rows else [])\n"
            "with open('data.csv','w',newline='') as _f:\n"
            "    _w = _csv.DictWriter(_f, fieldnames=_cols); _w.writeheader(); _w.writerows(_rows)\n\n")


def _static_only_level(language: str) -> tuple[str, Optional[str]]:
    """Non-python: the strongest static check we can make WITHOUT executing
    user code in this process. We do not run compilers here (that violates the
    no-user-code rule), so C++/R come back generated but statically unverified."""
    return "returned", (f"{language}: generated but not executed (the sandbox runs "
                        "python; compile/run it in your own environment)")


async def compute(spec: str, data_handle: Optional[str] = None,
                  language: str = "python", budget: str = "quick") -> dict:
    """Spec -> verified code + artifacts. See module docstring for the contract."""
    if not spec or not str(spec).strip():
        return {"error": "compute requires a non-empty spec"}
    language = (language or "python").lower()
    budget = budget if budget in BUDGET_TIMEOUTS else "quick"
    tr = AgentTrace("compute", language=language, budget=budget,
                    data_handle=data_handle)

    # Deterministic routing (D20): non-python never touches the python sandbox.
    if language != "python":
        try:
            code = await _vllm_code(
                f"You write a single standalone {language} program. Output ONLY "
                "the code inside one code fence, no prose.", spec)
            tr.llm(1)
        except Exception as exc:  # noqa: BLE001
            env = {"error": f"code generation failed: {exc}", "language": language}
            env["trace"] = tr.finish(outcome="extraction_failed", reason=str(exc))
            return env
        level, note = _static_only_level(language)
        env = {"code": code, "language": language, "verify_level": level,
               "ran": False, "artifacts": [], "attempts": 1, "errors": [],
               "note": note}
        env["trace"] = tr.finish(outcome="resolved", verify_level=level)
        return env

    # Python path: generate -> static gate -> sandbox -> bounded repair.
    from mcp.tools.sandbox import run_python

    prelude = _data_prelude(data_handle, tr)
    timeout = BUDGET_TIMEOUTS[budget]
    errors: list[dict] = []
    seen: set[str] = set()
    code = await _vllm_code(_CODE_RULES, f"Task: {spec}")
    tr.llm(1)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        ok, reason = _static_gate(code)
        if not ok:
            errors.append({"attempt": attempt, "stage": "static", "reason": reason})
            sig = _err_signature(reason, "")
            if sig in seen:
                break
            seen.add(sig)
            tr.decide("static gate failed; repairing", reason=reason)
            code = await _vllm_code(_CODE_RULES,
                                    f"Task: {spec}\n\nYour previous code failed a "
                                    f"static check: {reason}\nReturn a corrected script.")
            tr.llm(1)
            continue

        payload = await run_python(prelude + code, timeout_s=timeout)
        if payload.get("error") and not payload.get("artifacts") and "stderr" not in payload:
            # Top-level failure (sandbox unreachable / ephemeral refusal): not a
            # code error, no point repairing.
            errors.append({"attempt": attempt, "stage": "sandbox", "reason": payload["error"]})
            tr.decide("sandbox unavailable", reason=payload["error"])
            break

        exec_err = payload.get("error")
        if exec_err:
            sig = _err_signature(exec_err, payload.get("stderr", ""))
            errors.append({"attempt": attempt, "stage": "exec", "reason": exec_err,
                           "stderr_tail": (payload.get("stderr") or "")[-400:]})
            if sig in seen:  # stuck on the same error - stop, don't grind
                tr.decide("repeated error signature; stopping", sig=sig)
                break
            seen.add(sig)
            tr.decide("exec error; repairing", err=exec_err[:120])
            code = await _vllm_code(
                _CODE_RULES,
                f"Task: {spec}\n\nYour previous script raised this error when run:\n"
                f"{exec_err}\n{(payload.get('stderr') or '')[-800:]}\n\n"
                "Return a corrected standalone script.")
            tr.llm(1)
            continue

        # Success.
        arts = payload.get("artifacts") or []
        has_fig = any(_is_figure(a) for a in arts)
        env = {"code": code, "language": "python", "verify_level": "executed",
               "ran": True, "has_figure": has_fig, "artifacts": arts,
               "stdout": (payload.get("stdout") or "")[-1000:],
               "attempts": attempt, "errors": errors}
        env["trace"] = tr.finish(outcome="resolved", verify_level="executed",
                                 attempts=attempt, has_figure=has_fig)
        return env

    # Repair budget exhausted or stuck.
    env = {"code": code, "language": "python", "verify_level": "failed",
           "ran": False, "artifacts": [], "attempts": len(errors), "errors": errors}
    env["trace"] = tr.finish(outcome="extraction_failed",
                             reason="repair budget exhausted", attempts=len(errors))
    return env
