#!/usr/bin/env python3
"""Fail when a served page or the chat UI names a model by hand.

The docs render the model, embeddings and fallback from the running
deployment (gateway /api/system, through Caddy's /_facts.json), so a model
switch updates them. A model name typed into a page goes stale silently; the
2026-11 docs still said Qwen3.8-27B and SPECTER months after both were
replaced. This check keeps it from happening again.

Forbidden terms come from the profiles themselves (every MODEL_NAME,
MODEL_DISPLAY and MODEL_ID in backend/config/models/*.env), plus the
embedding models and the hosted fallbacks, so adding a profile extends the
check with no edit here. Matching is case-insensitive.

Scanned: frontend/static (html, js, css), frontend/webui/src (ts, tsx; tests
excluded) and frontend/auth/templates. A line that must name a model, such
as a paper result tied to the model it was measured on, goes in
scripts/docs-drift-allowlist.txt as `path<TAB or spaces>substring  # reason`.

Usage: python3 scripts/check_docs_drift.py   (exit 1 on a finding)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "backend" / "config" / "models"
ALLOWLIST = ROOT / "scripts" / "docs-drift-allowlist.txt"

# Not in any profile: the encoders and the hosted fallback models.
EXTRA_TERMS = ["SPECTER", "bge-large", "bge-base", "claude-haiku", "Claude Haiku",
               "claude-sonnet", "Claude Sonnet"]

SCAN = [
    (ROOT / "frontend" / "static", {".html", ".js", ".css"}),
    (ROOT / "frontend" / "webui" / "src", {".ts", ".tsx"}),
    (ROOT / "frontend" / "auth" / "templates", {".html"}),
]

_KV = re.compile(r'^(MODEL_NAME|MODEL_DISPLAY|MODEL_ID)=["\']?([^"\'\n#]+?)["\']?\s*$')


def terms() -> list[str]:
    out: set[str] = set(EXTRA_TERMS)
    for f in sorted(PROFILES.glob("*.env")):
        for line in f.read_text().splitlines():
            m = _KV.match(line.strip())
            if m and m.group(2).strip():
                val = m.group(2).strip()
                out.add(val)
                # The checkpoint's repo name without the quantizer prefix too:
                # "cyankiwi/Qwen3.8-27B-AWQ-INT4" -> "Qwen3.8-27B-AWQ-INT4".
                if "/" in val:
                    out.add(val.split("/", 1)[1])
    # Longest first, so a finding reports the most specific term.
    return sorted(out, key=lambda t: (-len(t), t.lower()))


def allowlist() -> list[tuple[str, str]]:
    rules = []
    if not ALLOWLIST.exists():
        return rules
    for n, raw in enumerate(ALLOWLIST.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = re.split(r"\s{2,}|\t", line, maxsplit=1)
        if len(parts) != 2 or "#" not in raw:
            raise SystemExit(f"{ALLOWLIST.name}:{n}: expected `path  substring  # reason`")
        rules.append((parts[0].strip(), parts[1].strip()))
    return rules


def files():
    for base, exts in SCAN:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix not in exts:
                continue
            rel = p.relative_to(ROOT).as_posix()
            if "/node_modules/" in rel or ".test." in p.name or "/test/" in rel:
                continue
            # The built chat bundle (gitignored output of webui/src, which is
            # scanned); an old build would otherwise fail the deploy check.
            if rel.startswith("frontend/static/chat/"):
                continue
            yield rel, p


def main() -> int:
    ts = terms()
    pattern = re.compile("|".join(re.escape(t) for t in ts), re.IGNORECASE)
    rules = allowlist()
    used: set[int] = set()
    findings = []
    for rel, p in files():
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            m = pattern.search(line)
            if not m:
                continue
            hit = next((i for i, (path, sub) in enumerate(rules) if path == rel and sub in line), None)
            if hit is not None:
                used.add(hit)
                continue
            findings.append(f"{rel}:{n}: names {m.group(0)!r}: {line.strip()[:140]}")
    stale = [f"{ALLOWLIST.name}: unused entry {path}  {sub}" for i, (path, sub) in enumerate(rules)
             if i not in used]
    for f in findings + stale:
        print(f)
    if findings or stale:
        print(f"\n{len(findings)} hand-typed model name(s), {len(stale)} stale allowlist entr(ies). "
              "Render facts from /_facts.json (see frontend/static/docs/architecture.html), "
              "use the `munin` model alias in examples, or allowlist the line with a reason.")
        return 1
    print(f"[docs-drift] ok: {len(ts)} model terms, none hand-typed in served pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
