"""Everything instance-specific in munin-auth derives from MUNIN_DOMAIN.

Until 2026-10 the cookie domain, CORS origins, sender, support address and the
post-login fallback were muninai.org literals, and the post-login redirect
accepted any URL starting with "http" (an open redirect).
"""
import sys

import pytest


def _load(monkeypatch, tmp_path, **env):
    for k in ("MUNIN_DOMAIN", "MUNIN_SITE_URL", "COOKIE_DOMAIN",
              "SMTP_SENDER", "SUPPORT_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SECRET_KEY", "test-only")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "s.db"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("main", None)
    import main
    return main


def test_reference_domain_reproduces_the_old_values(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, MUNIN_DOMAIN="muninai.org")
    assert m.COOKIE_DOMAIN == ".muninai.org"
    assert m.SITE_URL == "https://muninai.org"
    assert m.SMTP_SENDER == "noreply@muninai.org"
    assert m.SUPPORT_EMAIL == "support@muninai.org"
    cors = next(mw for mw in m.app.user_middleware if "CORS" in str(mw.cls))
    assert set(cors.kwargs["allow_origins"]) == {
        "https://muninai.org", "https://chat.muninai.org",
        "https://search.muninai.org", "https://research.muninai.org",
        "https://docs.muninai.org", "https://upload.muninai.org"}


def test_empty_cookie_domain_means_host_only(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, MUNIN_DOMAIN="localhost", COOKIE_DOMAIN="")
    assert m.COOKIE_DOMAIN is None


@pytest.mark.parametrize("url,ok", [
    ("https://chat.lab.example.edu/c/1", True),
    ("https://lab.example.edu/", True),
    ("https://evil.example.com/", False),
    ("https://lab.example.edu.evil.com/", False),
    ("https://evil-lab.example.edu/", False),
    ("javascript:alert(1)", False),
    ("//evil.example.com/", False),
    ("", False),
    (None, False),
])
def test_post_login_redirect_stays_on_the_instance(monkeypatch, tmp_path, url, ok):
    m = _load(monkeypatch, tmp_path, MUNIN_DOMAIN="lab.example.edu")
    assert m.is_own_url(url) is ok


def test_templates_link_to_the_instance(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, MUNIN_DOMAIN="lab.example.edu")
    html = m.templates.get_template("login.html").render(error="", redirect="")
    assert 'href="https://lab.example.edu"' in html
    assert "muninai" not in html
