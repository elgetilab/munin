"""Which of a turn's results are final, and which text is the answer.

docs/agent-track/TURN-RESULTS-PLAN.md. A turn that corrects itself (a wrong
PDB id, a redrawn figure, a rewritten file) used to show every attempt as a
result, and every step's narration as part of the answer (chat 13550817:
"solved in 2017 (PDB ID: 6JFQ)", then "That PDB ID was wrong", then a second
viewer, both under a text saying the viewer is "above").

Two pieces, both pure functions over the turn's tool-call entries:
- supersede(): marks results replaced by a later call in the same turn
  (explicit `replaces`, the same create_artifact title, a redraw of the same
  script). Marked results stay openable; the UI lists them as earlier attempts.
- answer_and_narration(): the text of steps that ended in tool calls is
  narration (stored as `step_text` on the step's first tool call); the
  message content keeps only the answer.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Optional

# Below this, an answer is too thin to stand alone: keep the narration in the
# message (the model said everything before its last tool call).
MIN_ANSWER_CHARS = 40
# A run_python whose code is at least this similar to an earlier
# figure-producing call in the turn is a redraw of that figure. Measured on
# the reported redraw turns (2026-11-04): 0.78 to 1.0.
REDRAW_SIMILARITY = 0.75

_WIDGET_TOOLS = ("create_artifact", "show_protein", "design_primers")
_SANDBOX_TOOLS = ("run_python", "edit_python", "compile_latex", "compute")


def artifacts_of(entry: dict) -> list[dict]:
    """[{id, content_type, filename}] for the artifacts a tool call produced."""
    name = entry.get("name")
    res = entry.get("result")
    if not isinstance(res, dict) or res.get("error"):
        return []
    if name in _WIDGET_TOOLS:
        aid = res.get("id")
        return [{"id": aid, "content_type": res.get("content_type") or "",
                 "filename": res.get("title")}] if isinstance(aid, str) else []
    if name in _SANDBOX_TOOLS:
        out = []
        for a in res.get("artifacts") or []:
            if isinstance(a, dict) and isinstance(a.get("registered_artifact_id"), str):
                out.append({"id": a["registered_artifact_id"], "content_type": a.get("content_type") or "",
                            "filename": a.get("filename")})
        return out
    return []


def _code_of(entry: dict) -> Optional[str]:
    args = entry.get("arguments") or {}
    res = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    name = entry.get("name")
    if name == "run_python":
        return args.get("code")
    if name == "edit_python":
        return res.get("resulting_code") or None
    if name == "compile_latex":
        return args.get("source") or None
    return None


def _is_image(a: dict) -> bool:
    return str(a.get("content_type", "")).startswith("image/")


_AUTO_FIGURE = re.compile(r"^[0-9a-f]{32}\.(png|svg|jpg|jpeg)$")
# Primary marks: what the figure IS. Reference lines, shading and text are
# what a redraw typically adds, so they do not count.
_PLOT_CALL = re.compile(
    r"\.(plot|scatter|bar|barh|hist|hist2d|errorbar|imshow|pcolormesh|contour|contourf|boxplot|"
    r"violinplot|step|semilogx|semilogy|loglog|stem|pie|hexbin|lineplot|scatterplot|barplot|"
    r"histplot|kdeplot|stripplot|swarmplot|pointplot|heatmap|regplot|ecdfplot)\(")


def _is_auto_figure(a: dict) -> bool:
    return _is_image(a) and bool(_AUTO_FIGURE.match(str(a.get("filename") or "")))


def _plot_kinds(code: str) -> frozenset:
    kinds = set(_PLOT_CALL.findall(code or ""))
    # semilog/loglog are line plots on log axes; a redraw may switch between them
    return frozenset("plot" if k in ("semilogx", "semilogy", "loglog", "step") else k for k in kinds)


def _norm_title(t: Any) -> str:
    return " ".join(str(t or "").lower().split())


def _similar(a: str, b: str) -> float:
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if m.real_quick_ratio() < REDRAW_SIMILARITY or m.quick_ratio() < REDRAW_SIMILARITY:
        return 0.0
    return m.ratio()


def _already_superseded(entries: list[dict]) -> set:
    done = set()
    for e in entries:
        done.update((e.get("superseded_by") or {}).keys())
    return done


def supersede(entries: list[dict], start: int) -> list[dict]:
    """Mark earlier results of this turn replaced by the entries from `start` on.

    entries  the turn's tool-call entries in order (final_tool_calls);
             entries[start:] are the calls that just ran.
    Writes entry["superseded_by"] = {old_artifact_id: new_artifact_id} on the
    entry that produced each replaced result and returns
    [{"artifact_id", "superseded_by", "tool_call_id"}] for the SSE events.
    """
    events: list[dict] = []
    gone = _already_superseded(entries)

    def mark(old_idx: int, old_id: str, new_id: str) -> None:
        if old_id == new_id or old_id in gone:
            return
        old = entries[old_idx]
        old.setdefault("superseded_by", {})[old_id] = new_id
        gone.add(old_id)
        events.append({"artifact_id": old_id, "superseded_by": new_id, "tool_call_id": old.get("id")})

    for ni in range(start, len(entries)):
        new = entries[ni]
        new_arts = artifacts_of(new)
        if not new_arts:
            continue
        first_new = new_arts[0]["id"]

        # 1. explicit: the model says which earlier results this one replaces
        wanted = (new.get("arguments") or {}).get("replaces") or []
        if isinstance(wanted, str):
            wanted = [wanted]
        wanted = {str(w).strip() for w in wanted if str(w).strip()}
        if wanted:
            for oi in range(ni):
                for a in artifacts_of(entries[oi]):
                    if a["id"] in wanted:
                        mark(oi, a["id"], first_new)

        # 2. create_artifact with the title of one made earlier in the turn
        if new.get("name") == "create_artifact":
            t = _norm_title((new.get("arguments") or {}).get("title"))
            if t:
                for oi in range(ni):
                    old = entries[oi]
                    if old.get("name") == "create_artifact" and \
                            _norm_title((old.get("arguments") or {}).get("title")) == t:
                        for a in artifacts_of(old):
                            mark(oi, a["id"], first_new)

        # 3. a redraw of an auto-captured figure (plt.show(); the sandbox names
        #    those with 32 hex digits). Named files need no rule: saving the
        #    same name again keeps the artifact id, and different names are
        #    different figures. Code similarity alone cannot tell a redraw from
        #    a second figure that shares the data loading (0.88 measured), so
        #    the main plot types must match too.
        new_imgs = [a for a in new_arts if _is_auto_figure(a)]
        code = _code_of(new)
        if new_imgs and code and new.get("name") in ("run_python", "edit_python"):
            kinds = _plot_kinds(code)
            for oi in range(ni - 1, -1, -1):
                old = entries[oi]
                if old.get("name") not in ("run_python", "edit_python"):
                    continue
                old_imgs = [a for a in artifacts_of(old) if _is_auto_figure(a) and a["id"] not in gone]
                old_code = _code_of(old)
                if not old_imgs or not old_code:
                    continue
                if _plot_kinds(old_code) == kinds and _similar(old_code, code) >= REDRAW_SIMILARITY:
                    for k, a in enumerate(old_imgs):
                        repl = new_imgs[k] if k < len(new_imgs) else new_imgs[-1]
                        mark(oi, a["id"], repl["id"])
                    break   # the most recent matching drawing only
    return events


def answer_and_narration(step_texts: Iterable[tuple[Optional[str], str]], answer: str) -> tuple[bool, list]:
    """Decide whether to split narration from the answer.

    step_texts  [(tool_call_id or None, text)] for each step that ended in
                tool calls, in order; answer is the text after the last one.
    Returns (split, [(tool_call_id, text)]). split=False means keep the
    whole text in the message (thin answer, or no narration at all).
    """
    items = [(tid, t) for tid, t in step_texts if tid and t and t.strip()]
    if not items:
        return False, []
    if len((answer or "").strip()) < MIN_ANSWER_CHARS:
        return False, []
    return True, items
