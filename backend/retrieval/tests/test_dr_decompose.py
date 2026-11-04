"""Unit tests for the Deep Research planner call and its failure signals. The
model endpoint is faked, so these need no vLLM or network."""

import asyncio
import logging
import sys
from pathlib import Path

# Same import setup as test_dr_report.py.
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deep_research_agent as D

QUESTION = "How do optical and microwave DNP compare?"


def _fake_llm(monkeypatch, reply):
    calls = []

    async def llm(system, user, **kw):
        calls.append(kw)
        return reply

    monkeypatch.setattr(D, "_llm", llm)
    return calls


def test_decompose_returns_the_parsed_sub_questions(monkeypatch, caplog):
    _fake_llm(monkeypatch, '["First?", "Second?", "Third?"]')
    with caplog.at_level(logging.WARNING, logger=D.logger.name):
        subs = asyncio.run(D._decompose(QUESTION, 2))
    assert subs == ["First?", "Second?"]
    assert caplog.records == []


def test_decompose_allows_a_long_list_of_sub_questions(monkeypatch):
    calls = _fake_llm(monkeypatch, '["First?"]')
    asyncio.run(D._decompose(QUESTION, 6))
    assert calls[0]["max_tokens"] >= 2000


def test_a_cut_off_plan_falls_back_with_a_warning(monkeypatch, caplog):
    _fake_llm(monkeypatch, '["First?", "Second, which never fini')
    with caplog.at_level(logging.WARNING, logger=D.logger.name):
        subs = asyncio.run(D._decompose(QUESTION, 6))
    assert subs == [QUESTION]
    assert "no usable sub-questions" in caplog.text


class _FakeResponse:
    status_code = 200

    def __init__(self, finish_reason):
        self._finish_reason = finish_reason

    def json(self):
        return {"choices": [{"finish_reason": self._finish_reason,
                             "message": {"content": " text "}}]}


def _fake_client(monkeypatch, finish_reason):
    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kw):
            return _FakeResponse(finish_reason)

    monkeypatch.setattr(D.httpx, "AsyncClient", Client)


def test_llm_warns_when_the_reply_was_cut_off(monkeypatch, caplog):
    _fake_client(monkeypatch, "length")
    with caplog.at_level(logging.WARNING, logger=D.logger.name):
        assert asyncio.run(D._llm("s", "u", max_tokens=7)) == "text"
    assert "cut off at max_tokens=7" in caplog.text


def test_llm_stays_quiet_on_a_finished_reply(monkeypatch, caplog):
    _fake_client(monkeypatch, "stop")
    with caplog.at_level(logging.WARNING, logger=D.logger.name):
        assert asyncio.run(D._llm("s", "u")) == "text"
    assert caplog.records == []
