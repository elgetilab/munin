"""Persona placeholders render back to the measured prompts.

The persona files carry {{MUNIN_CLUSTER_NAME}}, {{MUNIN_PUBLIC_URL}} and
{{MUNIN_PUBLIC_HOST}} instead of the reference deployment's values. With the
reference env, rendering must reproduce the prompts every published number was
measured with, byte for byte: the hashes below are of the persona JSON before
the placeholders went in (2026-10-04). If you change a persona on purpose,
update the hash in the same commit and say so.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import personas  # noqa: E402
import site_config  # noqa: E402

PERSONAS = HERE.parent.parent.parent / "shared" / "personas"

REFERENCE_ENV = {
    "MUNIN_DOMAIN": "muninai.org",
    "MUNIN_CLUSTER_NAME": "the Hugin research cluster",
}

# All three changed on purpose 2026-10-16: the invoke_agent, deep_research,
# remember and forget tools were retired, so the shared frame's gated-tool and
# unlocked-tool examples name live tools, chat drops `remember` from its
# resident tools, research's memory rule no longer routes to `remember`, and
# every version is bumped.
MEASURED_SHA256 = {
    "chat.json": "a91d5aab24088a4158266216b10e44e1318318ab7d527f880d44596079ccffb3",
    "code.json": "06163f1c05b75c691a97f616f4174391d8d094f923ac06f04a212f2472523554",
    # Changed on purpose 2026-10-15: `search` is the primary search tool, so
    # the "ALWAYS pass `queries`" and "paper_search first" guidance went.
    "research.json": "2778b217d6b20ddc4c6b0ea10f973a4cabad2dd3315bbee8a49879c9beb3c3e0",
}


def _render(name: str, env: dict) -> dict:
    ph = personas.persona_placeholders(site_config.resolve(env))
    return json.loads(personas.render_persona_text(
        (PERSONAS / name).read_text(encoding="utf-8"), ph))


def _digest(data: dict) -> str:
    s = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode()).hexdigest()


def test_reference_env_renders_the_measured_prompts():
    for name, want in MEASURED_SHA256.items():
        assert _digest(_render(name, REFERENCE_ENV)) == want, name


def test_no_placeholder_survives_and_no_reference_value_leaks():
    other = {"MUNIN_DOMAIN": "lab.example.edu"}
    for name in MEASURED_SHA256:
        text = json.dumps(_render(name, other))
        assert "{{" not in text, name
        assert "muninai.org" not in text and "Hugin" not in text, name
        assert "https://search.lab.example.edu/paper/" in text, name


def test_values_are_json_escaped():
    env = {"MUNIN_DOMAIN": "x.org", "MUNIN_CLUSTER_NAME": 'the "Odin" cluster'}
    data = _render("chat.json", env)
    assert 'the "Odin" cluster' in data["params"]["system"]
