"""Track D (harness ablation) package.

`runs_dir()` is the one place that decides where per-arm artifacts live, so a
re-run can never overwrite a committed run's per-query arrays again. Before
2026-09-15 every arm wrote `ablation_runs/<arm>.json` in place and the 08-26
Qwen3.8 arrays were one `run_arm` invocation away from destruction; the paired
old-vs-new comparison (`pipelines.compare`) reads exactly those arrays.
"""

from __future__ import annotations

import os

_BENCH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ABLATION_ROOT = os.path.join(_BENCH, "ablation_runs")

# Tag of the flat legacy layout after the 2026-09-15 move. Every arm file that
# used to sit directly in ablation_runs/ (the 08-26 Qwen3.8 clean run and its
# sidecars) now lives under this tag.
LEGACY_TAG = "qwen38-27b"


def runs_dir(tag: str | None = None, *, create: bool = False) -> str:
    """`ablation_runs/<tag>/`. `tag` falls back to env MUNIN_ABLATION_TAG, then
    to LEGACY_TAG, so an untagged invocation reads the last committed run and
    never writes over it by accident (writers always pass a tag)."""
    tag = (tag or os.getenv("MUNIN_ABLATION_TAG") or LEGACY_TAG).strip()
    if not tag or "/" in tag or tag.startswith("."):
        raise ValueError(f"bad ablation tag {tag!r}")
    path = os.path.join(ABLATION_ROOT, tag)
    if create:
        os.makedirs(path, exist_ok=True)
    return path
