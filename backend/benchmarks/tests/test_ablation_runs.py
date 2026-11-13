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
    # Corpus provenance is part of the sidecar contract. Names are asserted,
    # not point counts: those need a live Qdrant and are None without one.
    assert meta["corpus"]["papers_collection"] == "papers_bge"
    assert meta["corpus"]["chunks_collection"] == "papers_chunks"
    assert "papers_points" in meta["corpus"] and "counted_at" in meta["corpus"]
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


# --- concurrency, direct-arm sampling and the thinking budget (2026-11-13) ---
# Sage-A1.5 ran out of its 16K output cap inside <think> on 80/199 bare
# questions; the direct arms gained the profile's sampling and vLLM's
# thinking_token_budget, and Track D gained --concurrency for the re-run.

def test_concurrent_run_keeps_question_order_and_runs_each_once(tmp_path, monkeypatch):
    import threading
    import time as _t
    monkeypatch.setattr(ablation, "ABLATION_ROOT", str(tmp_path))
    qs = [{"qid": f"q{i}", "question": f"Q{i}?"} for i in range(12)]
    monkeypatch.setattr(run_arm, "_questions", lambda n: qs[:n])
    monkeypatch.setattr(run_arm, "build_mcq", lambda q: {**_mcq(), "prompt": q["question"]})
    monkeypatch.setattr(run_arm, "_served_model", lambda: "stub-model")
    calls, lock, live, peak = [], threading.Lock(), [0], [0]

    def fake_bare(prompt):
        with lock:
            calls.append(prompt); live[0] += 1; peak[0] = max(peak[0], live[0])
        _t.sleep(0.02 * (12 - int(prompt[1:-1])) / 12)   # later questions finish first
        with lock:
            live[0] -= 1
        return {"content": "B", "elapsed_s": 0.1, "prompt_tokens": 10, "completion_tokens": 1}
    monkeypatch.setattr(run_arm.V, "bare_answer", fake_bare)

    run_arm.run("bare", 12, tag="c", concurrency=3)
    work = ablation.runs_dir("c")
    assert sorted(calls) == sorted(q["question"] for q in qs) and len(calls) == 12
    assert 1 < peak[0] <= 3, "parallel, but never more than --concurrency in flight"
    out = json.load(open(os.path.join(work, "bare.json")))
    assert [r["qid"] for r in out["per_q"]] == [q["qid"] for q in qs]
    lines = [json.loads(l) for l in open(os.path.join(work, "bare.capture.jsonl"))]
    assert len(lines) == 12, "one capture line per question, none torn"
    assert json.load(open(os.path.join(work, "bare.meta.json")))["concurrency"] == 3


def _reload_cfg(monkeypatch, **env):
    from munin_bench import config
    for k in ("LLM_THINKING_BUDGET", "BENCH_DIRECT_SAMPLING", "SAMPLING_DEFAULT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


def test_direct_arm_defaults_are_the_legacy_body(monkeypatch):
    """Old scorecards stay reproducible: no env = temperature 0.7, no budget."""
    cfg = _reload_cfg(monkeypatch)
    assert cfg.direct_arm_sampling() == {"temperature": 0.7}
    assert cfg.thinking_budget_fields(16384) == {}
    importlib.reload(cfg)


def test_direct_arm_profile_sampling_and_budget(monkeypatch):
    card = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
    cfg = _reload_cfg(monkeypatch, BENCH_DIRECT_SAMPLING="profile",
                      SAMPLING_DEFAULT=json.dumps(card), LLM_THINKING_BUDGET="8192")
    assert cfg.direct_arm_sampling() == card
    assert cfg.thinking_budget_fields(16384) == {"thinking_token_budget": 8192}
    assert cfg.thinking_budget_fields(4096) == {"thinking_token_budget": 2048}, "half of max_tokens at most"
    _reload_cfg(monkeypatch)


def test_direct_arm_profile_sampling_needs_the_profile(monkeypatch):
    cfg = _reload_cfg(monkeypatch, BENCH_DIRECT_SAMPLING="profile")
    with pytest.raises(ValueError):
        cfg.direct_arm_sampling()
    with pytest.raises(ValueError):
        _reload_cfg(monkeypatch, BENCH_DIRECT_SAMPLING="card")
    _reload_cfg(monkeypatch)


def test_vllm_answer_body_carries_sampling_and_budget(monkeypatch):
    card = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
    _reload_cfg(monkeypatch, BENCH_DIRECT_SAMPLING="profile",
                SAMPLING_DEFAULT=json.dumps(card), LLM_THINKING_BUDGET="8192")
    from munin_bench.ablation import vllm_answer as V
    seen = {}

    class _Resp:
        def __init__(self, body): self.body = body
        def read(self): return json.dumps({"choices": [{"message": {"content": "A"}, "finish_reason": "stop"}],
                                           "usage": {}}).encode()
    def fake_urlopen(req, timeout=None):
        seen.update(json.loads(req.data)); return _Resp(seen)
    monkeypatch.setattr(V.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(V.json, "load", lambda r: json.loads(r.read()))
    V.bare_answer("Q?")
    assert {k: seen[k] for k in card} == card
    assert seen["thinking_token_budget"] == 8192 and seen["max_tokens"] == 16384
    V.complete("Q?", system="s", max_tokens=4096, temperature=0.2)
    assert seen["temperature"] == 0.2 and seen["thinking_token_budget"] == 2048
    _reload_cfg(monkeypatch)
