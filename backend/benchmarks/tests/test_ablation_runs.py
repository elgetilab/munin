"""Track D run-artifact hygiene: tag-scoped dirs, resumable capture, verdict
reasons, and the backbone-aware thinking-off helper.

Pure-function tests; no vLLM, no live chat. Written 2026-09-15 when the arms
gained resume (the 08-26 agentic arm died at 191/199) and stopped writing to a
flat shared dir (one re-run away from destroying the committed run's arrays).
"""

from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
BENCH = os.path.abspath(os.path.join(HERE, ".."))
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

from munin_bench import ablation  # noqa: E402
from munin_bench.ablation import run_arm  # noqa: E402


def test_runs_dir_is_tag_scoped_and_never_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "ABLATION_ROOT", str(tmp_path))
    monkeypatch.delenv("MUNIN_ABLATION_TAG", raising=False)
    # untagged -> the legacy (last committed) tag, never the flat root
    assert ablation.runs_dir() == str(tmp_path / ablation.LEGACY_TAG)
    assert ablation.runs_dir("gpt-oss-20b", create=True) == str(tmp_path / "gpt-oss-20b")
    assert (tmp_path / "gpt-oss-20b").is_dir()
    monkeypatch.setenv("MUNIN_ABLATION_TAG", "from-env")
    assert ablation.runs_dir().endswith("/from-env")
    assert ablation.runs_dir("explicit").endswith("/explicit")  # arg beats env
    for bad in ("a/b", ".hidden", "../x"):
        with pytest.raises(ValueError):
            ablation.runs_dir(bad)


def _mcq():
    return {"letters": ["A", "B", "C", "D"],
            "options": {"A": "w", "B": "x", "C": "y", "D": "z"},
            "correct": "B", "abstain": "D"}


def test_verdict_reasons_split_parser_from_model():
    mcq = _mcq()
    assert run_arm._verdict(mcq, "The answer is B.")["verdict"] == "correct"
    assert run_arm._verdict(mcq, "D")["verdict"] == "abstain"
    v = run_arm._verdict(mcq, "")
    assert (v["verdict"], v["reason"]) == ("unparseable", "empty_content")
    v = run_arm._verdict(mcq, "still thinking about", deadline_hit=True)
    assert (v["verdict"], v["reason"]) == ("unparseable", "deadline_hit")
    v = run_arm._verdict(mcq, "no letter here at all")
    assert (v["verdict"], v["reason"]) == ("unparseable", "letter_not_found")
    # a parsed verdict carries no reason key at all
    assert "reason" not in run_arm._verdict(mcq, "B")


def test_verdict_flags_tool_markup_leaks_without_scoring_them():
    mcq = _mcq()
    leak = run_arm._verdict(mcq, '<tool_call>{"name":"paper_search"}</tool_call> Answer: B')
    assert leak["tool_markup_in_content"] is True
    assert leak["verdict"] == "correct"      # counted, not penalised
    harmony = run_arm._verdict(mcq, "<|channel|>commentary to=functions.search<|call|>")
    assert harmony["tool_markup_in_content"] is True
    assert run_arm._verdict(mcq, "Answer: B")["tool_markup_in_content"] is False


def test_run_resumes_from_capture_and_writes_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "ABLATION_ROOT", str(tmp_path))
    qs = [{"qid": f"q{i}", "question": f"Q{i}?", "options": ["w", "x", "y", "z"],
           "ideal": "x", "distractors": ["w", "y", "z"]} for i in range(5)]
    monkeypatch.setattr(run_arm, "_questions", lambda n: qs[:n])
    monkeypatch.setattr(run_arm, "build_mcq", lambda q: {**_mcq(), "prompt": q["question"]})
    monkeypatch.setattr(run_arm, "_served_model", lambda: "stub-model")
    calls: list[str] = []

    def fake_bare(prompt):
        calls.append(prompt)
        return {"content": "B", "elapsed_s": 0.1, "prompt_tokens": 10, "completion_tokens": 1}
    monkeypatch.setattr(run_arm.V, "bare_answer", fake_bare)

    # First run: 3 of 5, then pretend a crash by pre-seeding the capture.
    work = ablation.runs_dir("t", create=True)
    cap = os.path.join(work, "bare.capture.jsonl")
    with open(cap, "w") as fh:
        for q in qs[:3]:
            fh.write(json.dumps({"qid": q["qid"], "verdict": "correct", "letter": "B",
                                 "tool_calls": 0, "elapsed_s": 0.1, "prompt_tokens": 10,
                                 "completion_tokens": 1, "n_contexts": 0}) + "\n")
    res = run_arm.run("bare", 5, tag="t")
    assert calls == ["Q3?", "Q4?"], "only the two uncaptured questions ran"
    out = json.load(open(os.path.join(work, "bare.json")))
    assert [r["qid"] for r in out["per_q"]] == [q["qid"] for q in qs], "question order, not completion order"
    assert res["accuracy"] == 1.0 and res["runs_dir"] == work
    meta = json.load(open(os.path.join(work, "bare.meta.json")))
    assert meta["egress"] == "n/a-no-tools" and meta["tag"] == "t" and meta["model"] == "stub-model"
    # Second invocation: nothing left to run, artifacts rewritten identically.
    calls.clear()
    run_arm.run("bare", 5, tag="t")
    assert calls == []
    assert not os.path.exists(os.path.join(str(tmp_path), "bare.json")), "never writes to the flat root"


@pytest.mark.parametrize("mode,expected", [
    ("enable_thinking", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("effort_low", {"chat_template_kwargs": {"reasoning_effort": "low"}}),
    ("none", {}),
    ("", {"chat_template_kwargs": {"enable_thinking": False}}),   # blank -> Qwen default
])
def test_thinking_off_fields_follows_backbone_mode(monkeypatch, mode, expected):
    monkeypatch.setenv("LLM_THINKING_MODE", mode)
    from munin_bench import config
    cfg = importlib.reload(config)
    assert cfg.thinking_off_fields() == expected


def test_thinking_off_fields_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("LLM_THINKING_MODE", "sometimes")
    from munin_bench import config
    cfg = importlib.reload(config)
    with pytest.raises(ValueError):
        cfg.thinking_off_fields()
    monkeypatch.delenv("LLM_THINKING_MODE")
    importlib.reload(config)
