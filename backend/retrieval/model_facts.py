"""
What this deployment serves, and what it served when: the facts behind the
public docs (`/api/system`, rendered into docs pages by Caddy) and behind the
publication-usage summary (which model answered a user's turns in a date range).

Two files, both under the read-only config mount (/app/config =
/opt/munin/config):

- ``active-model.env``: the active backbone profile written by
  ``deploy.sh model activate`` (backend/config/models/<slug>.env plus
  ACTIVATED_AT). Its display and citation keys (MODEL_DISPLAY, MODEL_REF_*)
  are what the docs show; nothing here hardcodes a model name.
- ``model-history.jsonl``: one line per activation, seeded from
  backend/config/model-history.jsonl and appended by ``model activate``. An
  entry runs until the next one starts.

The env file is parsed, never executed: KEY=VALUE lines, optional single or
double quotes, comments skipped. Both files are re-read when their mtime
changes, so a model switch shows up without a container restart.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

CONFIG_DIR = os.getenv("MUNIN_CONFIG_DIR", "/app/config")
ACTIVE_MODEL_ENV = os.getenv("MUNIN_ACTIVE_MODEL_ENV_PATH",
                             os.path.join(CONFIG_DIR, "active-model.env"))
MODEL_HISTORY = os.getenv("MUNIN_MODEL_HISTORY_PATH",
                          os.path.join(CONFIG_DIR, "model-history.jsonl"))

# Serving software, cited alongside the model (Kwon et al., SOSP '23).
VLLM_REFERENCE = {
    "author": "Kwon, Woosuk and Li, Zhuohan and Zhuang, Siyuan and Sheng, Ying and "
              "Zheng, Lianmin and Yu, Cody Hao and Gonzalez, Joseph E. and "
              "Zhang, Hao and Stoica, Ion",
    "author_short": "Kwon, W., Li, Z., Zhuang, S., Sheng, Y., Zheng, L., Yu, C. H., "
                    "Gonzalez, J. E., Zhang, H., & Stoica, I.",
    "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
    "venue": "Proceedings of the 29th Symposium on Operating Systems Principles (SOSP '23)",
    "pages": "611--626",
    "year": "2023",
    "doi": "10.1145/3600006.3613165",
}

# Munin itself (CITATION.cff preferred-citation and the software archive).
MUNIN_REFERENCE = {
    "author": "Fischer, Markus and Meiler, Jens and Elgeti, Matthias",
    "author_short": "Fischer, M., Meiler, J., & Elgeti, M.",
    "title": "MuninAI: A Self-Hosted Agentic Framework for independent Research Groups",
    "year": "2026",
    "arxiv": "2610.23480",
    "url": "https://arxiv.org/abs/2610.23480",
    "software_doi": "10.5281/zenodo.23191403",
}

# Paper-corpus encoders by PAPER_ENCODER value: (display, dims).
PAPER_ENCODERS = {
    "bge-large": ("BGE-large-en-v1.5", 1024),
    "specter": ("SPECTER", 768),
}
UPLOAD_ENCODER = ("BGE-base-en-v1.5", 768)
PROFILES_DIR = os.getenv("MUNIN_MODEL_PROFILES_PATH", os.path.join(CONFIG_DIR, "models"))

# Hosted models that answer when the cluster cannot (llm-failover's secondary,
# the VPS standby). They have no profile; cited the way APA cites a tool.
HOSTED_MODELS = {
    "claude-haiku-5-5": ("Claude Haiku 5.5", "Anthropic", "2026"),
    "claude-sonnet-5": ("Claude Sonnet 5", "Anthropic", "2026"),
}

_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_cache: dict[str, tuple[float, object]] = {}


def parse_env_file(path: str) -> dict[str, str]:
    """KEY=VALUE pairs of a profile file, without running it."""
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                m = _LINE.match(raw.strip())
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                elif " #" in val:
                    val = val.split(" #", 1)[0].rstrip()
                out[key] = val
    except OSError:
        return {}
    return out


def _cached(path: str, loader):
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return loader(None)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    val = loader(path)
    _cache[path] = (mtime, val)
    return val


def active_profile() -> dict[str, str]:
    return _cached(ACTIVE_MODEL_ENV, lambda p: parse_env_file(p) if p else {})


def _load_history(path: Optional[str]) -> list[dict]:
    if not path:
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("at") and row.get("slug"):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _vllm_version(profile: dict[str, str]) -> Optional[str]:
    m = re.search(r"vllm-(\d[\w.]*)", profile.get("VLLM_VENV", ""))
    return m.group(1) if m else None


def history() -> list[dict]:
    """Activations, oldest first, each with ``from`` and ``to`` (None = now).

    If the active profile is newer than the last line (the file was never
    written on this box, or the activation predates the history file), the
    active profile is appended from its ACTIVATED_AT so the current period
    always has an entry.
    """
    rows = [dict(r) for r in _cached(MODEL_HISTORY, _load_history)]
    prof = active_profile()
    slug = prof.get("MODEL_SLUG")
    rows.sort(key=lambda r: r["at"])
    if slug and (not rows or rows[-1]["slug"] != slug):
        rows.append({
            "at": prof.get("ACTIVATED_AT") or (rows[-1]["at"] if rows else None),
            "slug": slug, "display": prof.get("MODEL_DISPLAY") or prof.get("MODEL_NAME") or slug,
            "model_id": prof.get("MODEL_ID"), "revision": prof.get("MODEL_REVISION") or None,
            "vllm": _vllm_version(prof), "approximate": not prof.get("ACTIVATED_AT"),
            "note": "from active-model.env",
        })
    # Re-activating the same profile is not a model change: keep the first
    # of consecutive same-slug entries.
    merged: list[dict] = []
    for r in rows:
        if r.get("at") and not (merged and merged[-1]["slug"] == r["slug"]):
            merged.append(r)
    rows = merged
    for i, r in enumerate(rows):
        r["from"] = r["at"]
        r["to"] = rows[i + 1]["at"] if i + 1 < len(rows) else None
    return rows


def model_at(ts: str, rows: Optional[list[dict]] = None) -> Optional[dict]:
    """The history entry that covers ISO timestamp ``ts``, or None."""
    rows = history() if rows is None else rows
    hit = None
    for r in rows:
        if _iso(r["from"]) <= _iso(ts):
            hit = r
        else:
            break
    return hit


def _iso(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def model_reference(p: dict[str, str]) -> dict:
    """The model's own reference as structured fields, APA text and BibTeX."""
    title = p.get("MODEL_REF_TITLE") or ""
    if not title:
        # A profile from before the citation keys: no reference rather than
        # a made-up one.
        return {}
    author = p.get("MODEL_REF_AUTHOR") or p.get("MODEL_DEVELOPER") or ""
    year = p.get("MODEL_REF_YEAR") or ""
    url = p.get("MODEL_REF_URL") or p.get("MODEL_URL") or ""
    arxiv = p.get("MODEL_REF_ARXIV") or ""
    key = re.sub(r"[^a-z0-9]", "", (p.get("MODEL_SLUG") or title).lower()) + year
    apa = f"{author}. ({year}). {title}."
    if arxiv:
        apa += f" arXiv:{arxiv}."
    if url:
        apa += f" {url}"
    bib = [f"@misc{{{key},", f"  title = {{{{{title}}}}},", f"  author = {{{{{author}}}}},",
           f"  year = {{{year}}},"]
    if arxiv:
        bib += [f"  eprint = {{{arxiv}}},", "  archivePrefix = {arXiv},"]
    if url:
        bib.append(f"  url = {{{url}}},")
    bib.append("}")
    return {"author": author, "title": title, "year": year, "url": url,
            "arxiv": arxiv or None, "apa": apa, "bibtex": "\n".join(bib)}


def static_references() -> dict:
    v, m = VLLM_REFERENCE, MUNIN_REFERENCE
    return {
        "munin": {
            **m,
            "apa": f"{m['author_short']} ({m['year']}). {m['title']}. arXiv:{m['arxiv']}. {m['url']}",
            "bibtex": "\n".join([
                "@misc{munin2026,", f"  title = {{{m['title']}}},", f"  author = {{{m['author']}}},",
                f"  year = {{{m['year']}}},", f"  eprint = {{{m['arxiv']}}},",
                "  archivePrefix = {arXiv},", f"  url = {{{m['url']}}},", "}"]),
        },
        "vllm": {
            **v,
            "apa": f"{v['author_short']} ({v['year']}). {v['title']}. In {v['venue']} "
                   f"(pp. {v['pages'].replace('--', '-')}). https://doi.org/{v['doi']}",
            "bibtex": "\n".join([
                "@inproceedings{kwon2023vllm,", f"  title = {{{v['title']}}},",
                f"  author = {{{v['author']}}},", f"  booktitle = {{{v['venue']}}},",
                f"  pages = {{{v['pages']}}},", f"  year = {{{v['year']}}},",
                f"  doi = {{{v['doi']}}},", "}"]),
        },
    }


def backbone(p: Optional[dict[str, str]] = None) -> dict:
    """The active backbone as the docs and the citation helper show it."""
    p = active_profile() if p is None else p
    if not p.get("MODEL_SLUG"):
        return {}
    ctx = p.get("VLLM_MAX_MODEL_LEN")
    return {
        "slug": p.get("MODEL_SLUG"),
        "served_name": p.get("MODEL_NAME"),
        "display": p.get("MODEL_DISPLAY") or p.get("MODEL_NAME"),
        "description": p.get("MODEL_DESC"),
        "developer": p.get("MODEL_DEVELOPER") or None,
        "base": p.get("MODEL_BASE") or None,
        "architecture": p.get("MODEL_ARCH") or None,
        "quantization": p.get("MODEL_QUANT") or None,
        "checkpoint": p.get("MODEL_ID"),
        "revision": p.get("MODEL_REVISION") or None,
        "license": p.get("MODEL_LICENSE") or None,
        "url": p.get("MODEL_URL") or None,
        "context_window": int(ctx) if ctx and ctx.isdigit() else None,
        "vision": not ("--language-model-only" in p.get("VLLM_EXTRA_ARGS", "")
                       or p.get("LLM_VISION") == "0"),
        "thinking_budget": int(p["LLM_THINKING_BUDGET"]) if p.get("LLM_THINKING_BUDGET", "").isdigit() else None,
        "since": _current_since(p),
        "vllm_version": _vllm_version(p),
        "reference": model_reference(p),
    }


def _current_since(p: dict[str, str]) -> Optional[str]:
    """When the active backbone started serving: the start of its history
    entry, which survives a same-profile re-activation, else ACTIVATED_AT."""
    rows = history()
    if rows and rows[-1]["slug"] == p.get("MODEL_SLUG"):
        return rows[-1]["from"]
    return p.get("ACTIVATED_AT") or None


def _hosted_entry(name: str) -> dict:
    display, dev, year = HOSTED_MODELS.get(name, (name, "Anthropic", ""))
    url = "https://www.anthropic.com/claude"
    return {
        "slug": name, "served_name": name, "display": display, "developer": dev,
        "hosted_by": "Anthropic API", "url": url,
        "reference": {"author": dev, "title": display, "year": year, "url": url, "arxiv": None,
                      "apa": f"{dev}. ({year}). {display} [Large language model]. {url}",
                      "bibtex": "\n".join([
                          f"@misc{{{re.sub(r'[^a-z0-9]', '', name)},",
                          f"  title = {{{display} [Large language model]}},",
                          f"  author = {{{dev}}},", f"  year = {{{year}}},",
                          f"  url = {{{url}}},", "}"])},
    }


def catalog() -> dict[str, dict]:
    """Every model this deployment knows how to describe, by served name:
    the installed profiles (/app/config/models/*.env, the active one taking
    precedence) and the hosted fallbacks. The usage summary uses it to cite
    a model that answered in the past but is no longer active."""
    out: dict[str, dict] = {}
    for name in HOSTED_MODELS:
        out[name] = _hosted_entry(name)
    try:
        files = sorted(f for f in os.listdir(PROFILES_DIR) if f.endswith(".env"))
    except OSError:
        files = []
    for f in files:
        p = _cached(os.path.join(PROFILES_DIR, f), lambda q: parse_env_file(q) if q else {})
        if p.get("MODEL_NAME"):
            b = backbone(p)
            b.pop("since", None)
            out[p["MODEL_NAME"]] = b
    active = backbone()
    if active.get("served_name"):
        out[active["served_name"]] = {k: v for k, v in active.items() if k != "since"}
    return out


def system_facts(*, served: Optional[bool], fallback: Optional[dict],
                 paper_encoder: str, fallback_model: Optional[str]) -> dict:
    """Everything `/api/system` returns. No hostnames, ports or secrets."""
    enc = PAPER_ENCODERS.get(paper_encoder, (paper_encoder, None))
    rows = history()
    return {
        "backbone": backbone(),
        "served": served,
        "fallback": {
            "model": fallback_model or None,
            "display": (HOSTED_MODELS.get(fallback_model, (fallback_model,))[0]
                        if fallback_model else None),
            "active": bool(fallback and fallback.get("active")),
            "since": (fallback or {}).get("since"),
            "hosted_by": "Anthropic API" if fallback_model else None,
        },
        "embeddings": {
            "papers": {"model": enc[0], "dims": enc[1]},
            "uploads": {"model": UPLOAD_ENCODER[0], "dims": UPLOAD_ENCODER[1]},
        },
        "history": [{k: r.get(k) for k in ("slug", "display", "model_id", "revision", "vllm",
                                            "from", "to", "approximate", "hosted")}
                    for r in rows],
        "catalog": catalog(),
        "references": static_references(),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
