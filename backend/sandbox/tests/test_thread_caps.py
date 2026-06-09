"""
Regression guard for chat 56b39f33 (2026-06-03).

The firejail kernel runs under RLIMIT_NPROC. When numpy / scipy / sklearn
import OpenBLAS, OpenMP, MKL, or numexpr without thread caps, they each
detect the host's core count and try to spin up that many worker
threads. On a multi-core host this immediately exceeds the process
limit and fails with errors like
``OpenBLAS pthread_create failed for thread 11 of 24: Resource
temporarily unavailable``, which cascade into every downstream import
that touches numpy (matplotlib, sklearn, ...).

The fix is in the Dockerfile: set OPENBLAS_NUM_THREADS,
OMP_NUM_THREADS, MKL_NUM_THREADS, and NUMEXPR_NUM_THREADS to 1 so the
libraries don't try to expand. The kernel inherits the container env
unchanged, so setting it on the container is enough.

This test reads the Dockerfile and asserts all four caps are declared.
Pure-Python; no docker required.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"

REQUIRED_CAPS = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def _env_vars_from_dockerfile() -> dict[str, str]:
    """Parse out the ENV directives' key=value pairs. Handles the
    line-continuation form Docker accepts (``ENV A=1 \\\n    B=2 \\``),
    which is what our Dockerfile uses."""
    text = DOCKERFILE.read_text()
    # Collapse `\<newline>` continuations.
    collapsed = re.sub(r"\\\s*\n\s*", " ", text)
    env: dict[str, str] = {}
    for line in collapsed.splitlines():
        m = re.match(r"^\s*ENV\s+(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        # Tokenise on whitespace. Each token is KEY=VALUE.
        for tok in rest.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def test_dockerfile_caps_blas_threads():
    env = _env_vars_from_dockerfile()
    missing = []
    for k, expected in REQUIRED_CAPS.items():
        actual = env.get(k)
        if actual != expected:
            missing.append(f"{k} expected={expected!r} got={actual!r}")
    assert not missing, (
        "Dockerfile is missing thread caps required to prevent "
        "OpenBLAS / OpenMP / MKL / numexpr from spawning a thread per "
        "host core inside the firejail kernel (chat 56b39f33). "
        "Missing or wrong:\n  " + "\n  ".join(missing)
    )


def test_dockerfile_still_sets_mplbackend_agg():
    """Sanity check: the new ENV block didn't accidentally drop the
    matplotlib backend cap, which we need for headless rendering."""
    env = _env_vars_from_dockerfile()
    assert env.get("MPLBACKEND") == "Agg", (
        f"MPLBACKEND should still be Agg, got {env.get('MPLBACKEND')!r}"
    )
