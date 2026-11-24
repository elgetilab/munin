"""Case schema, loading and validation for the user-failure track.

A case is one JSON file in ``<store>/cases/``. The schema is documented in
``<store>/staging/CURATE.md`` (the brief the cases were written from) and
enforced here, so a malformed case fails at load time, not mid-run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_STORE = Path("/opt/munin/data/evals/user-failures")

CATEGORIES = {
    "wrong_fact", "fabricated_citation", "retrieval_miss", "tool_failure", "code_error",
    "instruction_not_followed", "incomplete_or_truncated", "file_or_attachment",
    "rendering_or_format", "language", "over_refusal_or_abstain", "latency_or_timeout",
    "context_lost", "other",
}

# type -> required parameter names
CHECKS: dict[str, tuple[str, ...]] = {
    "nonempty": (),
    "no_error_marker": (),
    "no_phantom_artifact": (),
    "tool_used": ("name",),
    "tool_not_used": ("name",),
    "artifact": ("ext",),
    "dois_resolve": (),
    "contains": ("pattern",),
    "not_contains": ("pattern",),
    "language": ("lang",),
    "no_clarification": (),
    "max_words": ("n",),
    "max_seconds": ("n",),
}


class CaseError(ValueError):
    pass


@dataclass
class Turn:
    text: str
    attachments: list[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    category: str
    tier: str                      # "mechanical" | "rubric"
    egress: str                    # "off" | "on"
    turns: list[Turn]
    checks: list[dict]
    rubric: list[str]
    truth: Optional[str]
    source: dict
    notes: str = ""
    path: Optional[Path] = None

    def attachment_paths(self, store: Path) -> list[Path]:
        return [store / a for t in self.turns for a in t.attachments]


def store_dir() -> Path:
    return Path(os.environ.get("MUNIN_USERFAIL_DIR") or DEFAULT_STORE)


def parse_case(obj: dict, where: str = "<case>", store: Optional[Path] = None) -> Case:
    def need(key, typ):
        if key not in obj or not isinstance(obj[key], typ):
            raise CaseError(f"{where}: `{key}` missing or not {typ.__name__}")
        return obj[key]

    cid = need("id", str)
    if not re.fullmatch(r"uf-[A-Za-z0-9_]+", cid):
        raise CaseError(f"{where}: bad id {cid!r}")
    cat = need("category", str)
    if cat not in CATEGORIES:
        raise CaseError(f"{where}: unknown category {cat!r}")
    tier = need("tier", str)
    if tier not in ("mechanical", "rubric"):
        raise CaseError(f"{where}: tier must be mechanical|rubric")
    egress = obj.get("egress", "off")
    if egress not in ("on", "off"):
        raise CaseError(f"{where}: egress must be on|off")
    raw_turns = need("turns", list)
    if not 1 <= len(raw_turns) <= 4:
        raise CaseError(f"{where}: 1 to 4 turns required, got {len(raw_turns)}")
    turns = []
    for i, t in enumerate(raw_turns):
        if not isinstance(t, dict) or not isinstance(t.get("text"), str) or not t["text"].strip():
            raise CaseError(f"{where}: turn {i} needs non-empty text")
        att = t.get("attachments") or []
        if not all(isinstance(a, str) and a.startswith(f"files/{cid}/") for a in att):
            raise CaseError(f"{where}: turn {i} attachments must live under files/{cid}/")
        if store is not None:
            for a in att:
                if not (store / a).is_file():
                    raise CaseError(f"{where}: attachment {a} not found in the store")
        turns.append(Turn(t["text"], list(att)))
    checks = need("checks", list)
    for c in checks:
        typ = c.get("type") if isinstance(c, dict) else None
        if typ not in CHECKS:
            raise CaseError(f"{where}: unknown check {c!r}")
        missing = [p for p in CHECKS[typ] if p not in c]
        if missing:
            raise CaseError(f"{where}: check {typ} needs {missing}")
        if typ in ("contains", "not_contains"):
            try:
                re.compile(c["pattern"])
            except re.error as exc:
                raise CaseError(f"{where}: bad regex in {typ}: {exc}") from exc
    rubric = obj.get("rubric") or []
    if not isinstance(rubric, list) or not all(isinstance(r, str) and r.strip() for r in rubric):
        raise CaseError(f"{where}: rubric must be a list of strings")
    if tier == "rubric" and not rubric:
        raise CaseError(f"{where}: a rubric-tier case needs rubric criteria")
    if tier == "mechanical" and not checks:
        raise CaseError(f"{where}: a mechanical case needs checks")
    truth = obj.get("truth")
    if truth is not None and not isinstance(truth, str):
        raise CaseError(f"{where}: truth must be a string or null")
    return Case(id=cid, category=cat, tier=tier, egress=egress, turns=turns, checks=checks,
                rubric=rubric, truth=truth, source=obj.get("source") or {},
                notes=obj.get("notes") or "")


def load_cases(store: Optional[Path] = None, ids: Optional[list[str]] = None) -> list[Case]:
    store = store or store_dir()
    out = []
    for p in sorted((store / "cases").glob("uf-*.json")):
        case = parse_case(json.loads(p.read_text()), where=p.name, store=store)
        case.path = p
        if ids and case.id not in ids:
            continue
        out.append(case)
    return out
