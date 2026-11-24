"""What one run of a case produced, as graders see it.

The client fills a RunRecord while it drives the conversation; graders only
read it, so they can be tested offline on hand-made records and a recorded
run can be re-graded after a grader changes (`run.py --regrade`).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Artifact:
    id: str
    filename: str
    sha256: str
    version: int = 1
    content_type: str = ""
    size_bytes: int = 0
    turn: int = 0                      # 1-based turn that created it
    figure: Optional[dict] = None      # full figure description, from the bundle
    figure_summary: Optional[str] = None
    meta: dict = field(default_factory=dict)   # file_meta facts (dpi, pdf fonts, ...)
    served_sha256: Optional[str] = None        # sha of the bytes served at the end


@dataclass
class TurnRecord:
    index: int                          # 1-based
    prompt: str
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    error: Optional[str] = None
    seconds: float = 0.0
    results: Optional[dict] = None      # parsed results.json as of this turn
    results_source: Optional[str] = None  # "artifact", "answer" or None
    # What the tools returned (name + result, each capped), so a wrong answer
    # can be traced to its source after the chat is deleted; and the notes of
    # the post-turn pass (citecheck, source ledger) from `answer_revised`.
    tool_results: list = field(default_factory=list)
    revision_notes: Optional[dict] = None


@dataclass
class RunRecord:
    case_id: str
    suite: str
    rep: int
    conversation_id: Optional[str] = None
    turns: list[TurnRecord] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    bundle_ok: Optional[bool] = None
    repro: Optional[dict] = None        # S5: {"ran": bool, "outputs": {name: bool}, "detail": str}
    error: Optional[str] = None

    # -- queries graders use ---------------------------------------------

    def turn(self, index: int) -> TurnRecord:
        return self.turns[index - 1]

    def artifacts_of(self, turn: int) -> list[Artifact]:
        return [a for a in self.artifacts if a.turn == turn]

    def artifacts_until(self, turn: int) -> list[Artifact]:
        return [a for a in self.artifacts if a.turn <= turn]

    def figure_at(self, turn: int, filename: Optional[str] = None) -> Optional[Artifact]:
        """The newest artifact with a figure description created in `turn`
        (or the newest up to `turn` when none was created in it), optionally
        restricted to a filename or extension (".pdf")."""
        def ok(a: Artifact) -> bool:
            if not a.figure:
                return False
            if filename is None:
                return True
            if filename.startswith("."):
                return a.filename.lower().endswith(filename.lower())
            return a.filename == filename
        here = [a for a in self.artifacts_of(turn) if ok(a)]
        if here:
            return here[-1]
        before = [a for a in self.artifacts_until(turn) if ok(a)]
        return before[-1] if before else None

    def text_until(self, turn: int) -> str:
        return "\n".join(t.answer for t in self.turns[:turn])

    # -- persistence -------------------------------------------------------

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "RunRecord":
        d = dict(d)
        d["turns"] = [TurnRecord(**t) for t in d.get("turns") or []]
        d["artifacts"] = [Artifact(**a) for a in d.get("artifacts") or []]
        return cls(**d)


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def results_from_answer(text: str) -> Optional[dict]:
    """The last fenced JSON object in an answer, the fallback when the model
    did not save results.json (plan D1)."""
    for m in reversed(_FENCE.findall(text or "")):
        try:
            obj = json.loads(m)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
