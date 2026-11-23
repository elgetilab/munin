"""User-failure track: schema, mechanical checks, judge parsing, compare.

Synthetic cases only: the real cases are private user text outside git.
"""
import json
from types import SimpleNamespace

import pytest

from munin_bench.userfail import checks as ck
from munin_bench.userfail.cases import CaseError, load_cases, parse_case
from munin_bench.userfail.judge import build_prompt, cost_usd, parse_verdict, request_params
from munin_bench.userfail.run import compare, mcnemar_p


def _case(**over):
    base = {"id": "uf-t1", "category": "wrong_fact", "tier": "rubric", "egress": "off",
            "turns": [{"text": "What is the g-factor of a free electron?"}],
            "checks": [{"type": "nonempty"}, {"type": "no_error_marker"}],
            "rubric": ["States g is about 2.0023"], "truth": "g_e = 2.00231930436",
            "source": {"conv": "x"}}
    base.update(over)
    return base


def _rec(*answers, tools=(), artifacts=(), error=None):
    return {"turns": [{"answer": a, "tools": list(tools), "error": None, "seconds": 5} for a in answers],
            "artifacts": list(artifacts), "error": error}


def test_parse_ok_and_errors():
    c = parse_case(_case())
    assert c.tier == "rubric" and c.turns[0].text.startswith("What")
    with pytest.raises(CaseError):
        parse_case(_case(category="nope"))
    with pytest.raises(CaseError):
        parse_case(_case(tier="rubric", rubric=[]))
    with pytest.raises(CaseError):
        parse_case(_case(checks=[{"type": "contains"}]))           # missing pattern
    with pytest.raises(CaseError):
        parse_case(_case(turns=[{"text": "x", "attachments": ["/etc/passwd"]}]))
    with pytest.raises(CaseError):
        parse_case(_case(turns=[{"text": "x"}] * 5))


def test_load_cases_checks_attachments(tmp_path):
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "uf-t1.json").write_text(json.dumps(
        _case(turns=[{"text": "see file", "attachments": ["files/uf-t1/a.csv"]}])))
    with pytest.raises(CaseError):
        load_cases(tmp_path)
    (tmp_path / "files" / "uf-t1").mkdir(parents=True)
    (tmp_path / "files" / "uf-t1" / "a.csv").write_text("x,y\n")
    assert [c.id for c in load_cases(tmp_path)] == ["uf-t1"]


def test_error_markers_and_phantoms():
    res = ck.run_checks([{"type": "no_error_marker"}],
                        _rec("ok", "_(stream interrupted: vLLM returned 400)_"))
    assert not res[0]["pass"] and "turn 2" in res[0]["detail"]
    assert not ck.run_checks([{"type": "no_error_marker"}], _rec("ok", ""))[0]["pass"]
    phantom = _rec("see /api/artifacts/c1/deadbeef12345678/view")
    assert not ck.run_checks([{"type": "no_phantom_artifact"}], phantom)[0]["pass"]
    real = _rec("see /api/artifacts/c1/deadbeef12345678/view",
                artifacts=[{"id": "deadbeef12345678", "filename": "a.pdf"}])
    out = ck.run_checks([{"type": "no_phantom_artifact"}, {"type": "artifact", "ext": ".PDF"}], real)
    assert all(x["pass"] for x in out)


def test_text_checks():
    r = _rec("Die Antwort ist nicht trivial und das ist auch gut so.", tools=["paper_search"])
    out = {x["type"]: x["pass"] for x in ck.run_checks([
        {"type": "language", "lang": "de"}, {"type": "tool_used", "name": "paper_search"},
        {"type": "tool_not_used", "name": "web_search"}, {"type": "contains", "pattern": "antwort"},
        {"type": "not_contains", "pattern": "falsch"}, {"type": "max_words", "n": 5}], r)}
    assert out == {"language": True, "tool_used": True, "tool_not_used": True,
                   "contains": True, "not_contains": True, "max_words": False}
    assert not ck.run_checks([{"type": "no_clarification"}], _rec("Which format do you want?"))[0]["pass"]
    assert not ck.run_checks([{"type": "no_clarification"}],
                             _rec("ok", tools=["ask_clarification"]))[0]["pass"]


def test_dois_resolve_uses_lookup(monkeypatch):
    monkeypatch.setattr(ck, "doi_exists", lambda d: d != "10.9999/fake.1")
    good = _rec("See 10.1145/3600006.3613165.")
    bad = _rec("See 10.1145/3600006.3613165 and 10.9999/fake.1")
    assert ck.run_checks([{"type": "dois_resolve"}], good)[0]["pass"]
    assert not ck.run_checks([{"type": "dois_resolve"}], bad)[0]["pass"]


def test_judge_prompt_and_parse():
    c = parse_case(_case(rubric=["a", "b"]))
    p = build_prompt(c, _rec("g is 2.0023", tools=["paper_search"]))
    assert "<rubric>\n1. a\n2. b" in p and "g_e = 2.00231930436" in p and "paper_search" in p
    params = request_params(c, _rec("x"))
    assert params["model"] == "claude-opus-5-5" and params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["format"]["type"] == "json_schema"
    msg = SimpleNamespace(stop_reason="end_turn",
                          usage=SimpleNamespace(input_tokens=1000, output_tokens=500,
                                                cache_read_input_tokens=0),
                          content=[SimpleNamespace(type="text", text=json.dumps(
                              {"criteria": [{"index": 1, "pass": True, "reason": "ok"}],
                               "summary": "s"}))])
    v = parse_verdict(msg, 2)
    assert v["pass"] is False and v["criteria"][1]["reason"] == "not graded"
    refused = SimpleNamespace(stop_reason="refusal", usage=msg.usage, content=[])
    assert parse_verdict(refused, 1)["pass"] is None
    assert cost_usd({"input": 1_000_000, "output": 0}, batch=False) == 4.0
    assert cost_usd({"input": 0, "output": 1_000_000}) == 10.0


def test_compare_paired():
    def sc(tag, passes):
        return {"tag": tag, "harness": {"git_sha": tag}, "model": {"served_name": "m"},
                "cases": [{"id": f"uf-{i}", "tier": "rubric" if i % 2 else "mechanical", "pass": p}
                          for i, p in enumerate(passes)]}
    res = compare(sc("a", [0, 0, 1, 1, 0]), sc("b", [1, 1, 1, 0, 0]))
    assert res["all"]["fixed"] == ["uf-0", "uf-1"] and res["all"]["regressed"] == ["uf-3"]
    assert "harness" in res["changed"] and "model" not in res["changed"]
    assert mcnemar_p(0, 0) == 1.0 and mcnemar_p(10, 0) < 0.01


def test_infra_errors_are_retried_not_scored():
    from munin_bench.userfail.run import infra_error
    assert infra_error({"error": "RemoteProtocolError: Server disconnected without sending a response."})
    assert infra_error({"error": "ReadError: [Errno 104] Connection reset by peer"})
    assert not infra_error({"error": "RuntimeError: no conversation id"})
    assert not infra_error({"error": None})
