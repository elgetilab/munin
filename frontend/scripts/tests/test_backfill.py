"""Backfill retry policy: permanent rejections leave complete/, transient
failures stay, and nothing overwrites an earlier file of the same name.

Regression for 2026-10: 12 uploads that are not PDFs (empty files, saved
HTML pages, a JPEG) got "HTTP 400 File is empty or not a PDF" and were
re-POSTed every 30 minutes from April on, because every non-200 left the
file in complete/.
"""
import importlib.util
import logging
import pathlib
import sys

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "backfill_contributed.py"


class _Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text, self.headers = code, body, text, {}

    def json(self):
        return self._body


@pytest.fixture
def bf(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backfill_contributed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("COMPLETE", "PROCESSED", "REJECTED"):
        monkeypatch.setattr(mod, f"{name}_DIR", tmp_path / name.lower())
    monkeypatch.setattr(mod, "LOG_PATH", tmp_path / "backfill.log")
    monkeypatch.setattr(mod, "_setup_logging", lambda: logging.getLogger("bf-test"))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("ADMIN_INGEST_TOKEN", "t" * 64)
    return mod


def _upload(bf, email, name, data=b"%PDF-1.4"):
    d = bf.COMPLETE_DIR / email
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)


def test_permanent_rejection_moves_aside_and_transient_stays(bf, monkeypatch):
    replies = {"bad.pdf": _Resp(400, text='{"detail":{"error":{"message":"File is empty or not a PDF"}}}'),
               "good.pdf": _Resp(200, {"status": "ingested", "doi": "10.1/x"}),
               "busy.pdf": _Resp(503)}
    monkeypatch.setattr(bf.requests, "post",
                        lambda url, files, **kw: replies[files["file"][0]])
    for name in replies:
        _upload(bf, "u@example.org", name)

    rc = bf.run(email_filter=None, dry_run=False, limit=None)

    rejected = bf.REJECTED_DIR / "u@example.org"
    assert (rejected / "bad.pdf").is_file()
    assert "not a PDF" in (rejected / "bad.pdf.reason.txt").read_text()
    assert (bf.PROCESSED_DIR / "u@example.org" / "good.pdf").is_file()
    assert [p.name for p in (bf.COMPLETE_DIR / "u@example.org").iterdir()] == ["busy.pdf"]
    assert rc == 1  # the 503 is still a failure to retry


def test_same_name_never_overwrites(bf, monkeypatch):
    monkeypatch.setattr(bf.requests, "post",
                        lambda url, files, **kw: _Resp(200, {"status": "ingested"}))
    done = bf.PROCESSED_DIR / "u@example.org"
    done.mkdir(parents=True)
    (done / "paper.pdf").write_bytes(b"first")
    _upload(bf, "u@example.org", "paper.pdf", b"second")

    bf.run(email_filter=None, dry_run=False, limit=None)

    assert (done / "paper.pdf").read_bytes() == b"first"
    assert (done / "paper_1.pdf").read_bytes() == b"second"
