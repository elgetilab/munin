"""model_facts: the active profile, the model history and the references
behind /api/system (docs) and the publication-usage summary. Pure file
parsing, so no retrieval runtime is needed."""
import json
import os
import shutil
import sys
from pathlib import Path

try:
    import pytest
except ImportError:  # the production image has no pytest (suite.txt runner)
    print("[SKIP] test_model_facts.py needs pytest fixtures")
    raise SystemExit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
REPO_BACKEND = HERE.parent.parent


@pytest.fixture
def facts(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    for f in (REPO_BACKEND / "config" / "models").glob("*.env"):
        shutil.copy(f, tmp_path / "models" / f.name)
    active = (REPO_BACKEND / "config" / "models" / "sage-a1.5.env").read_text()
    (tmp_path / "active-model.env").write_text(active + "\nACTIVATED_AT=2026-11-21T10:00:00Z\n")
    shutil.copy(REPO_BACKEND / "config" / "model-history.jsonl", tmp_path / "model-history.jsonl")
    for mod in [m for m in sys.modules if m == "model_facts"]:
        del sys.modules[mod]
    monkeypatch.setenv("MUNIN_CONFIG_DIR", str(tmp_path))
    import model_facts
    return model_facts, tmp_path


def test_env_parser_never_executes(tmp_path):
    import model_facts
    p = tmp_path / "x.env"
    p.write_text('# c\nA="quoted value"\nB=plain # trailing\nC=$(touch /tmp/pwned)\nlower=x\n')
    d = model_facts.parse_env_file(str(p))
    assert d == {"A": "quoted value", "B": "plain", "C": "$(touch /tmp/pwned)"}


def test_backbone_from_profile(facts):
    mf, _ = facts
    b = mf.backbone()
    assert b["display"] == "Sage-A1.5" and b["served_name"] == "sage-a1.5"
    assert b["revision"].startswith("12e664cc") and b["vllm_version"] == "0.33.0"
    assert b["vision"] is False and b["context_window"] == 65536
    # A same-slug re-activation (ACTIVATED_AT 11-21) does not move "since".
    assert b["since"] == "2026-11-12T17:46:00Z"
    assert "arXiv:2610.28578" in b["reference"]["apa"]
    assert b["reference"]["bibtex"].startswith("@misc{")


def test_history_periods_and_lookup(facts):
    mf, _ = facts
    h = mf.history()
    assert [r["slug"] for r in h] == ["qwen3.6-35b-a3b", "qwen3.8-27b", "claude-haiku-5-5", "sage-a1.5"]
    assert h[-1]["to"] is None and h[0]["to"] == h[1]["from"]
    assert mf.model_at("2026-09-01T00:00:00Z")["slug"] == "qwen3.8-27b"
    assert mf.model_at("2026-11-01T00:00:00+00:00")["slug"] == "claude-haiku-5-5"
    assert mf.model_at("2026-01-01T00:00:00Z") is None


def test_history_appends_unrecorded_active_profile(facts):
    mf, cfg = facts
    (cfg / "active-model.env").write_text(
        (REPO_BACKEND / "config" / "models" / "gpt-oss-20b.env").read_text()
        + "\nACTIVATED_AT=2026-12-01T00:00:00Z\n")
    h = mf.history()
    assert h[-1]["slug"] == "gpt-oss-20b" and h[-1]["from"] == "2026-12-01T00:00:00Z"
    assert h[-2]["to"] == "2026-12-01T00:00:00Z"


def test_profile_without_citation_keys_has_no_reference(facts):
    mf, cfg = facts
    (cfg / "active-model.env").write_text("MODEL_SLUG=old\nMODEL_NAME=old\nMODEL_DESC=\"Old\"\n")
    assert mf.backbone()["reference"] == {}


def test_system_facts_shape(facts):
    mf, _ = facts
    f = mf.system_facts(served=True, fallback={"active": False, "standby_model": "claude-haiku-5-5"},
                        paper_encoder="bge-large", fallback_model="claude-haiku-5-5")
    assert f["embeddings"]["papers"] == {"model": "BGE-large-en-v1.5", "dims": 1024}
    assert f["fallback"] == {"model": "claude-haiku-5-5", "display": "Claude Haiku 5.5",
                             "active": False, "since": None, "hosted_by": "Anthropic API"}
    assert {"sage-a1.5", "qwen3.8-27b", "claude-haiku-5-5"} <= set(f["catalog"])
    assert f["references"]["vllm"]["doi"] == "10.1145/3600006.3613165"
    assert f["references"]["munin"]["arxiv"] == "2610.23480"
    text = json.dumps(f)
    for secret_ish in ("host.docker.internal", ":8000", "API_KEY", "/opt/munin"):
        assert secret_ish not in text


def test_every_profile_has_citation_keys():
    import model_facts
    for f in (REPO_BACKEND / "config" / "models").glob("*.env"):
        p = model_facts.parse_env_file(str(f))
        ref = model_facts.model_reference(p)
        assert ref.get("title") and ref.get("year") and ref.get("url"), f.name
        assert len(p["MODEL_REVISION"]) == 40, f.name


def test_seed_history_is_valid():
    rows = [json.loads(l) for l in (REPO_BACKEND / "config" / "model-history.jsonl").read_text().splitlines() if l]
    assert [r["at"] for r in rows] == sorted(r["at"] for r in rows)
    profiles = {p.stem for p in (REPO_BACKEND / "config" / "models").glob("*.env")}
    import model_facts
    for r in rows:
        assert r["slug"] in profiles or r["slug"] in model_facts.HOSTED_MODELS, r
