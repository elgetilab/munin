"""site_config: the instance's URLs and identity, from the environment.

The reference values below are what the reference deployment sent before these
were configurable, so a deploy with its env changes nothing on the wire.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import site_config as sc  # noqa: E402

REF = sc.resolve({"MUNIN_DOMAIN": "muninai.org",
                  "MUNIN_CONTACT_EMAIL": "research@muninai.org"})


def test_reference_values_unchanged():
    assert REF["public_url"] == "https://search.muninai.org"
    assert REF["site_url"] == "https://muninai.org"
    assert sc.bot_user_agent(REF) == \
        "MuninBot/1.0 (https://muninai.org; mailto:research@muninai.org)"
    assert sc.bot_user_agent(REF, kind="web") == "MuninBot/1.0 (+https://muninai.org)"
    assert sc.bot_user_agent(REF, kind="browser") == \
        "Mozilla/5.0 (compatible; MuninBot/1.0; +https://muninai.org)"
    assert sc.auth_url("/admin/check-role", {"MUNIN_DOMAIN": "muninai.org"}) == \
        "https://auth.muninai.org/admin/check-role"


def test_explicit_urls_win_and_trailing_slash_is_dropped():
    cfg = sc.resolve({"MUNIN_DOMAIN": "a.org",
                      "MUNIN_PUBLIC_URL": "http://localhost:8081/"})
    assert cfg["public_url"] == "http://localhost:8081"


def test_no_contact_email_is_never_invented():
    cfg = sc.resolve({"MUNIN_DOMAIN": "lab.example.edu"})
    assert "mailto" not in sc.bot_user_agent(cfg)
    assert sc.bot_user_agent(sc.resolve({})) == "MuninBot/1.0"


def test_paper_url_pattern_follows_the_domain():
    pat = sc.paper_url_pattern(sc.resolve({"MUNIN_DOMAIN": "lab.example.edu"}))
    assert pat.findall("see https://search.lab.example.edu/paper/10.1%2Fx/pdf).")
    assert not pat.findall("https://search.muninai.org/paper/10.1%2Fx/pdf")
    ref = sc.paper_url_pattern(REF)
    assert ref.findall("[x](https://search.muninai.org/paper/10.1109%2Fhpcs.2005.55/pdf)") == \
        ["https://search.muninai.org/paper/10.1109%2Fhpcs.2005.55/pdf"]


def test_unconfigured_pattern_matches_nothing():
    assert not sc.paper_url_pattern(sc.resolve({})).findall(
        "https://search.anything.org/paper/x")
