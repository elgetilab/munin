"""Static consistency guard for the vLLM context-window configuration.

The served window (and the backend's budget against it) is spelled out in FIVE
places that must agree, but nothing links them:

  * backend/scripts/vllm/start-vllm-service.sh      (single-GPU / 64k, the default)
  * backend/scripts/vllm/start-vllm-service-tp2.sh  (2-GPU tensor-parallel / 128k)
  * backend/docker/docker-compose.yml               (retrieval container env defaults)
  * backend/retrieval/chat_context.py               (budgeting defaults)
  * backend/retrieval/main.py                        (raw-mode passthrough default)

When those drift, the failure is silent: vLLM serves one window while the
retrieval container budgets against another, so long answers get clamped to the
256-token floor with finish_reason=length (the exact bug the runtime
check-context-window.sh probe exists to catch). This test catches the same
drift statically, with no cluster / vLLM / docker needed, so it runs in CI.

It also asserts each launch script is internally coherent: the window it
exports for the backend == the window it hands to `vllm serve --max-model-len`
== the window it hands to check-context-window.sh. If those three disagree, the
backend budgets against a window vLLM never serves, or the post-launch
assertion checks the wrong number.

Run standalone or under pytest:
    python backend/retrieval/tests/test_vllm_window_consistency.py
    pytest backend/retrieval/tests/test_vllm_window_consistency.py
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
CHAT_CONTEXT = BACKEND / "retrieval" / "chat_context.py"
MAIN = BACKEND / "retrieval" / "main.py"
COMPOSE = BACKEND / "docker" / "docker-compose.yml"
SINGLE = BACKEND / "scripts" / "vllm" / "start-vllm-service.sh"
TP2 = BACKEND / "scripts" / "vllm" / "start-vllm-service-tp2.sh"

# The model's native cap (Qwen3.6-35B-A3B). No mode may exceed it.
NATIVE_MAX = 262144


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text()


def _find(text: str, pattern: str, what: str) -> str:
    """First capture group of `pattern` in `text`, or a loud failure. A miss
    means the config line was renamed/moved — that must break this test, not
    silently pass it."""
    m = re.search(pattern, text, re.MULTILINE)
    assert m, f"could not locate {what} (pattern {pattern!r})"
    return m.group(1)


def _getenv_default(text: str, var: str, what: str) -> int:
    # os.getenv("VAR", "65536")
    return int(_find(text, rf'os\.getenv\(\s*"{var}"\s*,\s*"(\d+)"\s*\)', what))


def _compose_default(text: str, var: str) -> int:
    # - VAR=${VAR:-65536}
    return int(_find(text, rf'{var}=\$\{{{var}:-(\d+)\}}', f"compose default for {var}"))


def _sh_int(text: str, var: str, what: str) -> int:
    # `export VAR=65536` or `VAR=65536` (bash literal assignment)
    return int(_find(text, rf'^(?:export\s+)?{var}=(\d+)', what))


def _serve_window(text: str, what: str) -> str:
    # `    --max-model-len 65536 \`  or  `    --max-model-len $MAX_MODEL_LEN \`
    return _find(text, r'--max-model-len\s+(\$?[A-Za-z0-9_]+)', what)


def _ctxcheck_arg(text: str, what: str) -> str:
    # `check-context-window.sh" 65536`  or  `check-context-window.sh" "$MAX_MODEL_LEN"`
    return _find(text, r'check-context-window\.sh"?\s+"?(\$?[A-Za-z0-9_]+)"?', what)


# --- single-GPU script: internally coherent (the default mode) --------------

def test_single_gpu_script_window_is_self_consistent():
    text = _read(SINGLE)
    exported = _sh_int(text, "VLLM_MAX_MODEL_LEN", "single-GPU exported VLLM_MAX_MODEL_LEN")
    served = _serve_window(text, "single-GPU --max-model-len")
    checked = _ctxcheck_arg(text, "single-GPU check-context-window.sh arg")
    # All three are hardcoded literals here; they must be the same number, or the
    # backend budgets against a window vLLM doesn't serve / the probe checks the
    # wrong one.
    assert served == str(exported), f"--max-model-len {served} != exported {exported}"
    assert checked == str(exported), f"check-context-window.sh {checked} != exported {exported}"


def test_single_gpu_context_leaves_output_room():
    text = _read(SINGLE)
    window = _sh_int(text, "VLLM_MAX_MODEL_LEN", "single-GPU VLLM_MAX_MODEL_LEN")
    ctx = _sh_int(text, "VLLM_MAX_CONTEXT", "single-GPU VLLM_MAX_CONTEXT")
    assert ctx < window, f"history-trim ceiling {ctx} must be below the served window {window}"
    assert window <= NATIVE_MAX


# --- compose defaults are the single-GPU (default-mode) window --------------

def test_compose_defaults_match_single_gpu_window():
    single = _read(SINGLE)
    compose = _read(COMPOSE)
    # If `docker compose up` runs without a launch script having exported these
    # (the :- fallback path), the container must still land on the default mode's
    # window, not some other number.
    assert _compose_default(compose, "VLLM_MAX_MODEL_LEN") == _sh_int(
        single, "VLLM_MAX_MODEL_LEN", "single-GPU VLLM_MAX_MODEL_LEN")
    assert _compose_default(compose, "VLLM_MAX_CONTEXT") == _sh_int(
        single, "VLLM_MAX_CONTEXT", "single-GPU VLLM_MAX_CONTEXT")


# --- python fallbacks match the compose defaults ----------------------------

def test_python_defaults_match_compose_defaults():
    compose = _read(COMPOSE)
    cc = _read(CHAT_CONTEXT)
    main = _read(MAIN)
    win = _compose_default(compose, "VLLM_MAX_MODEL_LEN")
    ctx = _compose_default(compose, "VLLM_MAX_CONTEXT")
    # If the container env is ever unset, the code's own hardcoded fallback must
    # still equal the intended default window (belt-and-braces with compose).
    assert _getenv_default(cc, "VLLM_MAX_MODEL_LEN", "chat_context MAX_MODEL_LEN") == win
    assert _getenv_default(cc, "VLLM_MAX_CONTEXT", "chat_context MAX_CONTEXT") == ctx
    # main.py's raw-mode passthrough must budget against the same window as the
    # persona/tool path in chat_context.py.
    assert _getenv_default(main, "VLLM_MAX_MODEL_LEN", "main _RAW_MAX_MODEL_LEN") == win


# --- tp2 script: drift-proof by construction, and sane values ---------------

def test_tp2_script_wires_one_window_everywhere():
    text = _read(TP2)
    # The whole point: the tp2 script feeds the SAME shell variable to the
    # backend export, to `vllm serve`, and to the probe, so they can't drift.
    # A hardcoded number in any of the three would reintroduce the drift risk.
    assert _find(text, r'export VLLM_MAX_MODEL_LEN="?(\$[A-Za-z0-9_{}]+)"?',
                 "tp2 exported VLLM_MAX_MODEL_LEN") == "$MAX_MODEL_LEN"
    assert _serve_window(text, "tp2 --max-model-len") == "$MAX_MODEL_LEN"
    assert _ctxcheck_arg(text, "tp2 check-context-window.sh arg") == "$MAX_MODEL_LEN"


def test_tp2_values_are_sane():
    text = _read(TP2)
    window = _sh_int(text, "MAX_MODEL_LEN", "tp2 MAX_MODEL_LEN")
    ctx = _sh_int(text, "BACKEND_MAX_CONTEXT", "tp2 BACKEND_MAX_CONTEXT")
    single_window = _sh_int(_read(SINGLE), "VLLM_MAX_MODEL_LEN", "single-GPU window")
    assert ctx < window, f"tp2 trim ceiling {ctx} must be below its window {window}"
    assert window <= NATIVE_MAX
    # tp2 exists to grow the window; if it isn't larger than single-GPU, the
    # mode has no reason to claim both cards.
    assert window > single_window, (
        f"tp2 window {window} should exceed single-GPU {single_window}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"[OK] {name}")
    print("all vLLM window-consistency checks passed")
