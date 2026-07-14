"""Track E reliability registry: wrap the existing behavioral QA scripts.

Deployment-behavioral regression, NEVER cited in the paper (master plan sec 6):
run each scenario a few times against the live service and summarise PASS /
FLAKY / FAIL. Wraps the scripts in place (does not move them) - starting with the
router-era pong test (`backend/retrieval/evals/run_eval.py`). `run_all
--with-reliability` folds the summary into the scorecard under `meta.reliability`.

(The master plan names this `backend/eval/`; kept here so `run_all` imports it
without a cross-tree path, but it still just SUBPROCESSES the backend QA scripts.)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                     "..", "..", "..", ".."))
# name -> (script path, argv builder). Extend with flakiness-suite / smoke-* later.
_SCENARIOS = {
    "pong": os.path.join(_REPO, "backend", "retrieval", "evals", "run_eval.py"),
}
_CHECK_RE = re.compile(r"^\s+(\S.*?)\s+\.*\s+(\d+)/(\d+)\s*$")


def _status(passed: int, n: int) -> str:
    if n == 0:
        return "FAIL"
    if passed == n:
        return "PASS"
    return "FAIL" if passed == 0 else "FLAKY"


def _run_pong(base_url: str, runs: int) -> dict:
    script = _SCENARIOS["pong"]
    if not os.path.exists(script):
        return {"status": "ERROR", "detail": "run_eval.py not found"}
    try:
        out = subprocess.run(
            [sys.executable, script, "--scenario", "pong",
             "--runs", str(runs), "--base", base_url],
            capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "detail": "timeout"}
    checks: dict[str, str] = {}
    for line in out.stdout.splitlines():
        m = _CHECK_RE.match(line)
        if m:
            name, passed, n = m.group(1), int(m.group(2)), int(m.group(3))
            checks[name] = f"{passed}/{n} ({_status(passed, n)})"
    # scenario status = worst check (FAIL > FLAKY > PASS); empty -> ERROR
    if not checks:
        return {"status": "ERROR", "detail": "no checks parsed",
                "exit_code": out.returncode}
    order = {"FAIL": 2, "FLAKY": 1, "PASS": 0}
    worst = max((v.split("(")[1].rstrip(")") for v in checks.values()),
                key=lambda s: order.get(s, 0))
    return {"status": worst, "runs": runs, "checks": checks}


def run_registry(base_url: str = "http://127.0.0.1:8080", runs: int = 3) -> dict:
    """Run the behavioral QA scenarios; return {scenario: {status, checks}}."""
    return {"pong": _run_pong(base_url, runs)}


if __name__ == "__main__":
    import json
    print(json.dumps(run_registry(), indent=2))
