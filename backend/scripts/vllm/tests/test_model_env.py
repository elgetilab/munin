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


def main() -> int:
    failures = 0
    for profile in PROFILES:
        got = load(profile)
        ok = got["MODEL_NAME"] and got["VLLM_MODEL_NAME"] == got["MODEL_NAME"] == got["exported"]
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {profile.name}: {got}")
    print(f"\n{len(PROFILES) - failures}/{len(PROFILES)} passed")
    return 1 if failures or not PROFILES else 0


if __name__ == "__main__":
    sys.exit(main())
