"""Case schema and loader.

A case is a directory holding `case.json`, its input files and usually a
`truth.json`:

    {
      "id": "s1_4pl_seed3",            unique across all suites
      "suite": "S1",
      "inputs": ["dose_response.csv"], uploaded and attached to the first turn
      "turns": [                       one or more user turns, in order
        {"prompt": "...", "checks": [...]}
      ],
      "results_keys": {"logEC50": "..."}   asked for in results.json
    }

A turn may also be a plain string, which means "no checks on this turn".
Check values written as "truth:<dotted.key>" are replaced by the value from
truth.json when the case is loaded, so a fixture keeps one source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SUITES = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")

# Appended to the LAST turn that has results_keys, identical in both arms, so
# graders read numbers from a file instead of parsing prose (plan D1).
#
# v1 (Phase 2 to Phase 4 round 4) said "Use plain numbers ..., lists for
# intervals as [low, high]". The model followed it over the case wording:
# per-residue maps saved as bare lists, a Gen5 result keyed by well rewritten
# as lists, standard-curve unknowns saved as [value, [low, high]]
# (LABKIT-013-PLAN.md). v2 says what each kind of key holds. v1 stays
# selectable (--results-suffix v1) to measure the change.
RESULTS_SUFFIXES = {
    "v1": (
        "\n\nWhen you are done, also save your final numbers with Python as "
        "'results.json' in the working directory: one JSON object with exactly "
        "these keys: {keys}. Use plain numbers (null if you cannot determine a "
        "value), lists for intervals as [low, high], and put any warning about "
        "the data or the fit as strings in a 'warnings' list."
    ),
    "v2": (
        "\n\nWhen you are done, also save your final numbers with Python as "
        "'results.json' in the working directory: one JSON object with exactly "
        "these keys: {keys}. Under each key put what the request asks for under "
        "that name: a single value as a number (null if you cannot determine it); "
        "values per item (per residue, well, sample, peak or pair) as an object "
        "mapping each item's name to its number; an interval as [low, high] only "
        "for a key that is an interval. Put any warning about the data or the fit "
        "as strings in a 'warnings' list."
    ),
}
DEFAULT_RESULTS_SUFFIX = "v2"
RESULTS_SUFFIX = RESULTS_SUFFIXES[DEFAULT_RESULTS_SUFFIX]


class CaseError(ValueError):
    pass


@dataclass
class Turn:
    prompt: str
    checks: list[dict] = field(default_factory=list)
    results_keys: dict[str, str] = field(default_factory=dict)


@dataclass
class Case:
    id: str
    suite: str
    dir: Path
    inputs: list[str]
    turns: list[Turn]
    results_keys: dict[str, str]
    truth: dict
    tags: list[str] = field(default_factory=list)
    persona: Optional[str] = None

    def input_paths(self) -> list[Path]:
        return [self.dir / name for name in self.inputs]

    def prompts(self, suffix: str = DEFAULT_RESULTS_SUFFIX) -> list[str]:
        """Turn prompts as sent. A turn with its own results_keys gets the
        results.json request (wording `suffix`, see RESULTS_SUFFIXES);
        otherwise the case-level keys go on the last turn."""
        template = RESULTS_SUFFIXES[suffix]
        out = []
        for i, t in enumerate(self.turns):
            keys = t.results_keys or (self.results_keys if i == len(self.turns) - 1
                                      and not any(x.results_keys for x in self.turns) else {})
            tail = template.format(keys=", ".join(f'"{k}"' for k in keys)) if keys else ""
            out.append(t.prompt + tail)
        return out


def lookup(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(dotted)
    return cur


def _resolve(value: Any, truth: dict, where: str) -> Any:
    if isinstance(value, str) and value.startswith("truth:"):
        try:
            return lookup(truth, value[len("truth:"):])
        except (KeyError, IndexError, ValueError):
            raise CaseError(f"{where}: {value!r} not in truth.json") from None
    if isinstance(value, dict):
        return {k: _resolve(v, truth, where) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, truth, where) for v in value]
    return value


def load_case(case_dir: Path) -> Case:
    case_dir = Path(case_dir)
    spec = json.loads((case_dir / "case.json").read_text())
    truth_file = case_dir / "truth.json"
    truth = json.loads(truth_file.read_text()) if truth_file.exists() else {}
    cid = spec.get("id") or case_dir.name
    suite = spec.get("suite")
    if suite not in SUITES:
        raise CaseError(f"{cid}: unknown suite {suite!r}")
    turns_raw = spec.get("turns") or []
    if not turns_raw:
        raise CaseError(f"{cid}: no turns")
    turns = []
    for i, t in enumerate(turns_raw):
        if isinstance(t, str):
            t = {"prompt": t}
        checks = _resolve(t.get("checks") or [], truth, f"{cid} turn {i + 1}")
        for c in checks:
            if "type" not in c:
                raise CaseError(f"{cid} turn {i + 1}: check without a type")
        turns.append(Turn(prompt=t["prompt"], checks=checks,
                          results_keys=dict(t.get("results_keys") or {})))
    inputs = list(spec.get("inputs") or [])
    for name in inputs:
        if not (case_dir / name).exists():
            raise CaseError(f"{cid}: input {name} missing")
    return Case(id=cid, suite=suite, dir=case_dir, inputs=inputs, turns=turns,
                results_keys=dict(spec.get("results_keys") or {}), truth=truth,
                tags=list(spec.get("tags") or []), persona=spec.get("persona"))


def load_cases(root: Path, suites: Optional[list[str]] = None,
               ids: Optional[list[str]] = None) -> list[Case]:
    """Every case under `root` (searched recursively), sorted by id."""
    cases = [load_case(p.parent) for p in sorted(Path(root).rglob("case.json"))]
    seen: dict[str, Path] = {}
    for c in cases:
        if c.id in seen:
            raise CaseError(f"duplicate case id {c.id}: {seen[c.id]} and {c.dir}")
        seen[c.id] = c.dir
    if suites:
        cases = [c for c in cases if c.suite in suites]
    if ids:
        wanted = set(ids)
        cases = [c for c in cases if c.id in wanted or any(c.id.startswith(i) for i in wanted)]
    return sorted(cases, key=lambda c: c.id)
