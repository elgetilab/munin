"""An encoder directory counts as a local model only if it holds one.

Compose bind-mounts a models directory for every encoder; with nothing staged,
Docker creates it EMPTY. Treating "exists" as "is a model" made a fresh
install skip the Hugging Face download and fail to load any encoder.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import local_model_or_hub  # noqa: E402


def test_empty_directory_falls_back_to_the_hub(tmp_path):
    assert local_model_or_hub(str(tmp_path), "BAAI/x") == "BAAI/x"


def test_missing_directory_falls_back_to_the_hub(tmp_path):
    assert local_model_or_hub(str(tmp_path / "nope"), "BAAI/x") == "BAAI/x"


def test_sentence_transformers_export_is_local(tmp_path):
    (tmp_path / "modules.json").write_text("[]")
    assert local_model_or_hub(str(tmp_path), "BAAI/x") == str(tmp_path)


def test_plain_transformers_export_is_local(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    assert local_model_or_hub(str(tmp_path), "BAAI/x") == str(tmp_path)
