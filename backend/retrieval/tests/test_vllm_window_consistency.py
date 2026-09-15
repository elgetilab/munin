"""Static consistency guard for the vLLM context-window configuration.

The served window (and the backend's budget against it) is spelled out in
several places that must agree, but nothing links them:

  * backend/config/models/*.env                     (VLLM_MAX_MODEL_LEN per backbone
                                                     profile; since 2026-09-15 the
                                                     launch scripts read it from here)
  * backend/scripts/vllm/start-vllm-service.sh      (single-GPU, the default)
  * backend/scripts/vllm/start-vllm-service-tp2.sh  (2-GPU tensor-parallel)
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

import sys

_parents = Path(__file__).resolve().parents
BACKEND = _parents[2] if len(_parents) > 2 else Path("/nonexistent")
CHAT_CONTEXT = BACKEND / "retrieval" / "chat_context.py"
MAIN = BACKEND / "retrieval" / "main.py"
COMPOSE = BACKEND / "docker" / "docker-compose.yml"
SINGLE = BACKEND / "scripts" / "vllm" / "start-vllm-service.sh"
TP2 = BACKEND / "scripts" / "vllm" / "start-vllm-service-tp2.sh"
PROFILES_DIR = BACKEND / "config" / "models"

# This file cross-checks REPO artifacts against each other (the SLURM launch
# scripts, docker-compose, and the Python defaults must all name one context
# window). None of them ship inside the retrieval image: from /app/tests,
# parents[2] is "/", so every path above pointed at /retrieval, /docker,
# /scripts and the first assert failed with "expected file missing". That is
# the wrong environment, not a defect, and per tests/README.md it must be one
# SKIP line rather than a failure.
_REQUIRED = (CHAT_CONTEXT, MAIN, COMPOSE, SINGLE, TP2)
if not all(p.is_file() for p in _REQUIRED) or not PROFILES_DIR.is_dir():
    _missing = [str(p) for p in _REQUIRED if not p.is_file()]
    if not PROFILES_DIR.is_dir():
        _missing.append(str(PROFILES_DIR))
    print(
        "[SKIP] test_vllm_window_consistency - host-only: it compares repo "
        "files that are not in the container. Missing: " + ", ".join(_missing)
    )
    raise SystemExit(0)

# The smallest native cap among the shipped profiles (gpt-oss-20b: 131072).
# No profile's window may exceed it.
NATIVE_MAX = 131072


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
    """A window literal from a launch script.

    Accepts the plain form (`export VAR=65536`) and the overridable form
    (`VAR=${VAR:-65536}`). The tp2 script moved to the latter so the
    large-window variant is one env var away, which the literal-only pattern
    could not parse at all: it failed with "could not locate tp2
    MAX_MODEL_LEN" rather than reporting a real disagreement."""
    for pattern in (
        rf'^\s*(?:export\s+)?{var}=(\d+)',
        rf'^\s*(?:export\s+)?{var}=\$\{{{var}:-(\d+)\}}',
    ):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return int(m.group(1))
    raise AssertionError(f"could not locate {what}")


def _sh_derived_int(text: str, var: str, base_var: str, base_value: int,
                    what: str) -> int:
    """A value DERIVED from another in the script, e.g.
    `BACKEND_MAX_CONTEXT=${BACKEND_MAX_CONTEXT:-$((MAX_MODEL_LEN - 5536))}`.

    Matching the derivation rather than a literal is deliberate: the script
    comment claims the ceiling "cannot drift out of step with the window", and
    that claim is only true while the value stays computed from it. A literal
    reappearing here should therefore fail to parse and get noticed."""
    m = re.search(
        rf'^(?:export\s+)?{var}=\$\{{{var}:-\$\(\(\s*{base_var}\s*-\s*(\d+)\s*\)\)\}}',
        text, re.MULTILINE,
    )
    if m:
        return base_value - int(m.group(1))
    return _sh_int(text, var, what)


def _serve_window(text: str, what: str) -> str:
    # `    --max-model-len 65536 \`  or  `    --max-model-len $MAX_MODEL_LEN \`
    return _find(text, r'--max-model-len\s+(\$?[A-Za-z0-9_]+)', what)


def _ctxcheck_arg(text: str, what: str) -> str:
    """The window the launch script hands to the check-context-window probe.

    Both scripts now RESOLVE the probe first (a candidate loop that sets
    CHECK_WINDOW) and then invoke `"$CHECK_WINDOW" <window>`, so the literal
    `check-context-window.sh` is never directly followed by the argument any
    more. The old pattern matched the next such literal it could find, which
    is the `[WARN] check-context-window.sh not found` message, and returned
    the word "not". That is a stale parser, not config drift: both scripts do
    pass the right window. Try the indirection first, then the direct form for
    any script that still invokes the probe by name.
    """
    for pattern in (
        r'"\$CHECK_WINDOW"\s+"?(\$?\{?[A-Za-z0-9_}]+)"?',
        r'check-context-window\.sh"?\s+"?(\$?\{?[A-Za-z0-9_}]+)"?',
    ):
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return m.group(1)
    raise AssertionError(f"could not locate {what}")


# --- the profiles: one window, shared by every shipped backbone --------------

def _profile_windows() -> dict[str, int]:
    out = {}
    for prof in sorted(PROFILES_DIR.glob("*.env")):
        out[prof.stem] = int(_find(_read(prof), r'^VLLM_MAX_MODEL_LEN=(\d+)',
                                   f"{prof.name} VLLM_MAX_MODEL_LEN"))
    assert out, f"no profiles under {PROFILES_DIR}"
    return out


def test_every_profile_serves_the_same_window():
    """Every committed benchmark number was produced at 64k. A profile with a
    different window would entangle "new backbone" with "bigger window" in
    the paper's comparison, so the profiles agree until that is a decision."""
    windows = _profile_windows()
    assert len(set(windows.values())) == 1, f"profiles disagree on the window: {windows}"
    window = next(iter(windows.values()))
    assert window <= NATIVE_MAX


# --- single-GPU script: wires the profile's window everywhere ---------------

def test_single_gpu_script_window_is_self_consistent():
    text = _read(SINGLE)
    # The script no longer carries a window literal: it sources the active
    # profile and must feed THE SAME variable to the backend export, to
    # `vllm serve`, and to the probe. A literal in any of the three would let
    # them drift again.
    assert re.search(r'^export VLLM_MAX_MODEL_LEN\s*$', text, re.MULTILINE), \
        "single-GPU script must re-export the profile's VLLM_MAX_MODEL_LEN (bare `export VLLM_MAX_MODEL_LEN`)"
    served = _serve_window(text, "single-GPU --max-model-len")
    checked = _ctxcheck_arg(text, "single-GPU check-context-window.sh arg")
    assert served == "$VLLM_MAX_MODEL_LEN", f"--max-model-len {served} is not the profile variable"
    assert checked == "$VLLM_MAX_MODEL_LEN", f"check-context-window.sh {checked} is not the profile variable"


def test_single_gpu_context_leaves_output_room():
    text = _read(SINGLE)
    window = next(iter(_profile_windows().values()))
    # 65536 -> 60000 is pinned as a literal branch so the historical trim
    # ceiling stays exact; any other window derives the ceiling.
    ctx = _sh_int(text, "VLLM_MAX_CONTEXT", "single-GPU VLLM_MAX_CONTEXT (65536 branch)")
    assert ctx < window, f"history-trim ceiling {ctx} must be below the served window {window}"
    assert re.search(r'VLLM_MAX_CONTEXT=\$\(\(VLLM_MAX_MODEL_LEN - \d+\)\)', text), \
        "non-65536 windows must derive VLLM_MAX_CONTEXT from the window"


# --- compose defaults are the profiles' window ------------------------------

def test_compose_defaults_match_single_gpu_window():
    single = _read(SINGLE)
    compose = _read(COMPOSE)
    window = next(iter(_profile_windows().values()))
    # If `docker compose up` runs without a launch script having exported these
    # (the :- fallback path), the container must still land on the served
    # window, not some other number.
    assert _compose_default(compose, "VLLM_MAX_MODEL_LEN") == window
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
    # The tp2 default window is the PROFILE's window (an operator can still
    # override MAX_MODEL_LEN for the large-window variant).
    assert re.search(r'^MAX_MODEL_LEN=\$\{MAX_MODEL_LEN:-\$VLLM_MAX_MODEL_LEN\}', text, re.MULTILINE), \
        "tp2 MAX_MODEL_LEN must default to the profile's VLLM_MAX_MODEL_LEN"
    single_window = window = next(iter(_profile_windows().values()))
    ctx = _sh_derived_int(text, "BACKEND_MAX_CONTEXT", "MAX_MODEL_LEN", window,
                          "tp2 BACKEND_MAX_CONTEXT")
    assert ctx < window, f"tp2 trim ceiling {ctx} must be below its window {window}"
    assert window <= NATIVE_MAX
    # This used to assert `window > single_window`, on the reasoning that tp2
    # exists to GROW the window. That stopped being true in 2d931e2, which
    # retargeted TP=2 at concurrency instead: the script now says the window
    # "deliberately STAYS at 65,536", because every committed benchmark number
    # was produced at 64k and raising it here would entangle "new model" with
    # "bigger window" in the paper's before/after diff. The second card buys
    # KV-cache pool (and MAX_NUM_SEQS 8), not context.
    #
    # So the invariant is now that the two modes agree, which is what keeps the
    # backend's budget correct whichever profile is running.
    assert window == single_window, (
        f"tp2 window {window} should match single-GPU {single_window}; raising "
        "it is an explicit MAX_MODEL_LEN override, not a default")


if __name__ == "__main__":
    # Run every check even after one fails. The previous runner let the first
    # AssertionError propagate, so a single stale assertion hid every check
    # after it: the tp2 arm below was never reached while the single-GPU arm
    # was red.
    import traceback as _traceback

    _passed = _failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
                _passed += 1
            except Exception:
                print(f"[FAIL] {name}")
                _traceback.print_exc()
                _failed += 1
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
