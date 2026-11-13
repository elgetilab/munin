"""
Every model profile, loaded through model-env.sh, must export
VLLM_MODEL_NAME equal to its MODEL_NAME.

Regression for the 2026-09 review: munin_load_model_env exported
VLLM_MODEL_NAME but never set it, so docker compose fell back to its literal
default (qwen3.8-27b). `deploy.sh model activate gpt-oss-20b` then left the
retrieval container requesting a model vLLM no longer served, and every chat
turn would 404. Pure bash + python; no docker, no /opt access.

Run:
    python backend/scripts/vllm/tests/test_model_env.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
MODEL_ENV = BACKEND / "scripts" / "vllm" / "model-env.sh"
PROFILES = sorted((BACKEND / "config" / "models").glob("*.env"))


def load(profile: Path) -> dict[str, str]:
    script = (f"source '{MODEL_ENV}' && munin_load_model_env '{profile}' >/dev/null && "
              "env -i bash -c 'printf \"%s|%s\" \"$1\" \"$2\"' _ \"$MODEL_NAME\" \"$VLLM_MODEL_NAME\" && "
              "printf '|%s' \"$(bash -c 'printf %s \"$VLLM_MODEL_NAME\"')\"")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "MUNIN_ROOT": "/nonexistent"})
    if out.returncode != 0:
        raise RuntimeError(out.stdout + out.stderr)
    model, vllm_name, exported = out.stdout.split("|")
    return {"MODEL_NAME": model, "VLLM_MODEL_NAME": vllm_name, "exported": exported}


def venv_after(*profiles: Path) -> str:
    """VLLM_VENV after loading the profiles in order, in one shell."""
    loads = " && ".join(f"munin_load_model_env '{p}' >/dev/null" for p in profiles)
    out = subprocess.run(["bash", "-c", f"source '{MODEL_ENV}' && {loads} && printf %s \"$VLLM_VENV\""],
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "MUNIN_ROOT": "/nonexistent"})
    if out.returncode != 0:
        raise RuntimeError(out.stdout + out.stderr)
    return out.stdout


def check_venv() -> int:
    """A profile without VLLM_VENV runs on the default venv, even right after
    one that names its own (the optional key must not leak between loads)."""
    default = "/nonexistent/services/vllm/venv"
    own = [p for p in PROFILES if "\nVLLM_VENV=" in "\n" + p.read_text()]
    plain = [p for p in PROFILES if p not in own]
    failures = 0
    for p in plain:
        got = venv_after(*own, p)
        ok = got == default
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {p.name} after {[o.name for o in own]}: VLLM_VENV={got}")
    return failures


def reasoning_after(*profiles: Path) -> tuple[str, list[str]]:
    """(exported LLM_THINKING_BUDGET as a child sees it, VLLM_REASONING_ARGS)
    after loading the profiles in order, in one shell."""
    loads = " && ".join(f"munin_load_model_env '{p}' >/dev/null" for p in profiles)
    script = (f"source '{MODEL_ENV}' && {loads} && "
              "bash -c 'printf %s \"$LLM_THINKING_BUDGET\"' && printf '\\0' && "
              "printf '%s\\0' \"${VLLM_REASONING_ARGS[@]}\"")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "MUNIN_ROOT": "/nonexistent"})
    if out.returncode != 0:
        raise RuntimeError(out.stdout + out.stderr)
    parts = out.stdout.split("\0")
    return parts[0], [x for x in parts[1:] if x]


def check_reasoning() -> int:
    """A profile with LLM_THINKING_BUDGET / VLLM_REASONING_END_STR exports the
    budget to children and builds one JSON `--reasoning-config` argument with
    real newlines; a profile without them, loaded after, gets neither."""
    import json
    failures = 0
    with_rc = [p for p in PROFILES if "\nVLLM_REASONING_END_STR=" in "\n" + p.read_text()]
    plain = [p for p in PROFILES if p not in with_rc]
    for p in with_rc:
        budget, args = reasoning_after(p)
        try:
            end = json.loads(args[1])["reasoning_end_str"] if len(args) == 2 else ""
        except ValueError:
            end = ""
        ok = (budget.isdigit() and args[:1] == ["--reasoning-config"]
              and end.endswith("</think>\n\n") and "\\n" not in end)
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {p.name}: budget={budget} reasoning args={args}")
    for p in plain:
        budget, args = reasoning_after(*with_rc, p)
        ok = budget == "" and args == []
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {p.name} after {[o.name for o in with_rc]}: budget={budget!r} args={args}")
    return failures


def main() -> int:
    failures = check_venv() + check_reasoning()
    for profile in PROFILES:
        got = load(profile)
        ok = got["MODEL_NAME"] and got["VLLM_MODEL_NAME"] == got["MODEL_NAME"] == got["exported"]
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {profile.name}: {got}")
    print(f"\n{len(PROFILES) - failures}/{len(PROFILES)} passed")
    return 1 if failures or not PROFILES else 0


if __name__ == "__main__":
    sys.exit(main())
