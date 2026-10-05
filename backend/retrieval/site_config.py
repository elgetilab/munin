"""Where this Munin instance lives, read from the environment.

Every public URL, contact address and instance name used to be a literal
`muninai.org` default scattered across a dozen modules, which meant another
group's install quietly linked to, and identified itself as, the reference
deployment. They all derive from here now.

  MUNIN_DOMAIN         the instance's base domain (required by compose)
  MUNIN_PUBLIC_URL     paper links,           default https://search.<domain>
  MUNIN_SITE_URL       the landing page,      default https://<domain>
  MUNIN_CONTACT_EMAIL  polite-pool mailto,    no default: omitted when unset
  MUNIN_CLUSTER_NAME   named in the prompt,   default "your group's research cluster"

`resolve()` is a PURE function of the mapping it is given, for the same reason
as `database.resolve_llm_endpoint`: config read inline at import time cannot be
tested without reimporting, and reimporting does not reliably re-read the
environment.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

DEFAULT_CLUSTER_NAME = "your group's research cluster"


def _get(env, name: str) -> str:
    return (env.get(name) or "").strip()


def resolve(env=None) -> dict:
    env = os.environ if env is None else env
    domain = _get(env, "MUNIN_DOMAIN")
    public_url = _get(env, "MUNIN_PUBLIC_URL") or (
        f"https://search.{domain}" if domain else "")
    site_url = _get(env, "MUNIN_SITE_URL") or (
        f"https://{domain}" if domain else "")
    return {
        "domain": domain,
        "public_url": public_url.rstrip("/"),
        "site_url": site_url.rstrip("/"),
        "contact_email": _get(env, "MUNIN_CONTACT_EMAIL"),
        "cluster_name": _get(env, "MUNIN_CLUSTER_NAME") or DEFAULT_CLUSTER_NAME,
    }


def auth_url(path: str, env=None) -> str:
    """https://auth.<domain><path>, or '' when no domain is configured."""
    domain = resolve(env)["domain"]
    return f"https://auth.{domain}{path}" if domain else ""


def bot_user_agent(cfg: dict | None = None, *, kind: str = "api") -> str:
    """The User-Agent Munin sends.

      api      scholarly APIs: "MuninBot/1.0 (<site>; mailto:<contact>)".
               Crossref, Unpaywall and NCBI ask for a contact address; it is
               included only when MUNIN_CONTACT_EMAIL is set, never invented.
      web      plain web fetches: "MuninBot/1.0 (+<site>)"
      browser  sites that reject bots outright:
               "Mozilla/5.0 (compatible; MuninBot/1.0; +<site>)"
    """
    cfg = cfg or resolve()
    site = cfg["site_url"]
    if kind == "browser":
        return f"Mozilla/5.0 (compatible; MuninBot/1.0{'; +' + site if site else ''})"
    if kind == "web":
        return f"MuninBot/1.0 (+{site})" if site else "MuninBot/1.0"
    parts = [p for p in (site, f"mailto:{cfg['contact_email']}"
                         if cfg["contact_email"] else "") if p]
    return f"MuninBot/1.0 ({'; '.join(parts)})" if parts else "MuninBot/1.0"


def paper_url_pattern(cfg: dict | None = None) -> re.Pattern:
    """Matches a `<public_url>/paper/...` link anywhere in text.

    The citation audit uses this to spot paper links no tool returned. It used
    to be the literal `search.muninai.org`, which made the audit a silent no-op
    on every other domain.
    """
    cfg = cfg or resolve()
    host = urlparse(cfg["public_url"]).netloc
    if not host:
        # No public URL configured: match nothing rather than everything.
        return re.compile(r"(?!x)x")
    host = re.escape(host.removeprefix("www."))
    return re.compile(r"https?://(?:www\.)?" + host + r"/paper/[^\s)>\"\]]+")


def scihub_enabled(env=None) -> bool:
    """Whether paper tools may point at Sci-Hub when a PDF is not in the corpus.

    Off unless SCIHUB_ENABLED is set: whether that is legal, and acceptable to
    the institution running the instance, is the operator's call, not a
    default.
    """
    env = os.environ if env is None else env
    return (env.get("SCIHUB_ENABLED") or "").strip().lower() in ("1", "true", "yes")


_CFG = resolve()
DOMAIN = _CFG["domain"]
PUBLIC_URL = _CFG["public_url"]
SITE_URL = _CFG["site_url"]
CONTACT_EMAIL = _CFG["contact_email"]
CLUSTER_NAME = _CFG["cluster_name"]
SCIHUB_ENABLED = scihub_enabled()
