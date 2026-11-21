"""Which model answered: the tally behind done.model, and Deep Research usage
on the job row (docs/DOCS-SYNC-AND-CREDIT-PLAN.md B1)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import usage_tracker as ut  # noqa: E402


def test_tally_counts_answering_models():
    agg, tally = {}, {}
    t1, t2 = ut.current_usage_aggregator.set(agg), ut.current_model_tally.set(tally)
    try:
        ut.record_usage("main_turn", {"prompt_tokens": 10, "completion_tokens": 5}, "sage-a1.5")
        ut.record_usage("main_turn", {"prompt_tokens": 10, "completion_tokens": 5}, "sage-a1.5")
        ut.record_usage("wrap_up", {"prompt_tokens": 1, "completion_tokens": 1}, "claude-haiku-5-5")
        ut.record_usage("title", None, None)  # no usage, no model: ignored
    finally:
        ut.current_usage_aggregator.reset(t1)
        ut.current_model_tally.reset(t2)
    assert tally == {"sage-a1.5": 2, "claude-haiku-5-5": 1}
    assert ut.primary_model(tally) == "sage-a1.5"
    assert ut.primary_model({}) is None
    assert ut.aggregate_totals(agg)["total_tokens"] == 32


def test_unbound_tally_is_noop():
    ut.record_model("sage-a1.5")  # must not raise without a bound tally


def test_done_event_carries_model():
    src = (Path(__file__).resolve().parent.parent / "chat_service.py").read_text()
    assert '"model": primary_model(model_tally)' in src
    assert 'record_usage("main_turn", acc.usage, acc.model)' in src


def test_research_job_usage_roundtrip():
    tmp = tempfile.mkdtemp(prefix="munin-test-usage-")
    os.environ["CHATS_DB_PATH"] = os.path.join(tmp, "chats.db")
    try:
        import research_store
    except ImportError as e:  # aiosqlite missing outside the container
        import pytest
        pytest.skip(f"research_store unavailable: {e}")

    import chat_store

    async def run():
        try:
            await research_store.create_job("dr_" + "a" * 16, None, "u1", "q")
            await research_store.set_usage("dr_" + "a" * 16,
                                           {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                                           {"sage-a1.5": 4}, "sage-a1.5")
            return await research_store.usage_for_user("u1", 0, 1e11), \
                await research_store.usage_for_user("u2", 0, 1e11)
        finally:
            await chat_store.close_db()  # aiosqlite's thread keeps the process alive

    mine, theirs = asyncio.run(run())
    assert theirs == []
    assert mine[0]["tokens_total"] == 10 and mine[0]["model"] == "sage-a1.5"
