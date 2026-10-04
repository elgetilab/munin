"""One-time migration: consolidate Meitner/Turing/Curie into ONE Munin identity.

Produces munin-chat.json / munin-code.json / munin-research.json, each with:
  - an IDENTICAL shared FRAME (Munin identity + CORE RULES + OUTPUT STYLE prefix,
    TASK PLANNING + DISCOVERING TOOLS suffix), and
  - the profile's own FRAGMENT (task guidance), sampling, and resident_tools.
`name` becomes "Munin" for all three; the internal `id` stays chat/code/research
(the router, router_examples.json, and the slash parser depend on those ids).

The frame is reconciled here so it is single-sourced: profile-specific frame
content is pushed DOWN into the fragment (notably research's "never answer from
general knowledge alone / don't fabricate papers" rules, which would be wrong in
chat's frame where parametric answers are correct). A drift-guard test
(retrieval/tests/test_munin_frame_shared.py) asserts the three frames stay
byte-identical.

Reads the CURRENT chat/code/research.json (one-time). Run from this dir:
    python shared/personas/_build_munin_personas.py
"""
from __future__ import annotations

import json
from pathlib import Path

P = Path(__file__).resolve().parent
M1 = "=== END OUTPUT STYLE ==="
M2 = "=== TASK PLANNING ==="


def _parts(sp: str):
    i = sp.find(M1)
    j = sp.find(M2)
    return sp[: i + len(M1)], sp[i + len(M1): j], sp[j:]


chat = json.loads((P / "chat.json").read_text())
code = json.loads((P / "code.json").read_text())
research = json.loads((P / "research.json").read_text())

# Guard: one-time, non-idempotent (it overwrites its own inputs and would
# double-apply the relocated blocks). Refuse if already consolidated.
if any(p.get("name") == "Munin" for p in (chat, code, research)):
    raise SystemExit("already consolidated (name == 'Munin'); nothing to do")

chat_pre, chat_frag, chat_suf = _parts(chat["params"]["system"])
_, code_frag, _ = _parts(code["params"]["system"])
_, research_frag, _ = _parts(research["params"]["system"])

# --- canonical frame -------------------------------------------------------
IDENTITY = (
    "You are Munin, an AI assistant on {{MUNIN_CLUSTER_NAME}}. You help "
    "researchers with whatever they need: finding and synthesising scientific "
    "literature, writing and running research code, drafting and editing, doing "
    "calculations, and thinking problems through in conversation.\n\n"
    "You use extended thinking to reason through a problem before you respond."
)

# CORE RULES: take chat's general set verbatim (the complete 6: fabricate /
# fail-honest / could-not-find / artifact-URLs / announcement / tool-error).
_cr_a = chat_pre.find("=== CORE RULES ===")
_cr_b = chat_pre.find("=== END CORE RULES ===") + len("=== END CORE RULES ===")
CORE_RULES = chat_pre[_cr_a:_cr_b]

# OUTPUT STYLE: unified superset (chat's rules + code's fenced-code-block /
# URLs-in-prose-not-code-blocks nuance, so code output stays correct).
OUTPUT_STYLE = (
    "=== OUTPUT STYLE ===\n\n"
    "This is an academic research tool. Your output must match the conventions "
    "of a scientific journal or textbook.\n\n"
    "- Do NOT use emojis, smileys, decorative symbols, or pictograms (for "
    "example: \U0001f98a ✓ ⚠ \U0001f30c \U0001f514 ✨ ➡ "
    "❌). Never use them for emphasis, section headers, bullet decoration, "
    "or visual flair. This includes code comments and docstrings.\n"
    "- Do NOT use em dashes (—) or en dashes (–). Use a regular hyphen "
    "(-), a comma, a semicolon, or parentheses for parenthetical insertions. A "
    "sentence that would normally use an em dash should use a comma or "
    "parentheses instead.\n"
    "- Stick to plain ASCII punctuation for body text: . , : ; ! ? ' \" - ( ) / "
    "[ ].\n"
    "- Markdown headers (##), lists (-), fenced code blocks, and bold (**) ARE "
    "allowed because they are structural, not decorative.\n"
    "- URLs in PROSE (not inside code blocks) MUST be wrapped in markdown link "
    "syntax `[label](url)`. NEVER write a bare URL on its own line, and NEVER "
    "write a `**Label:** url` pattern; bare URLs do not render as clickable in "
    "our frontend. This applies to download links, DOIs (link as "
    "`[10.1234/example](https://doi.org/10.1234/example)`), arxiv IDs, and any "
    "other URL. Inside fenced code blocks and code comments, raw URLs are fine.\n"
    "  - WRONG (in prose): `**Download URL:** {{MUNIN_PUBLIC_URL}}/paper/...`\n"
    "  - RIGHT (in prose): `[Download PDF]({{MUNIN_PUBLIC_URL}}/paper/...)`\n\n"
    "This rule applies to ALL output: explanations, summaries, tables, inline "
    "text, code comments, and file contents.\n\n"
    "=== END OUTPUT STYLE ==="
)

FRAME_PREFIX = IDENTITY + "\n\n" + CORE_RULES + "\n\n" + OUTPUT_STYLE

# SUFFIX: chat's (TASK PLANNING + DISCOVERING TOOLS) with the example hidden-tool
# list as the superset (chat already lists the broad set; add "citation export").
FRAME_SUFFIX = chat_suf.replace(
    "equation transcription, and more",
    "equation transcription, citation export, and more",
)

# --- research integrity block (relocated from research's CORE RULES) -------
RESEARCH_INTEGRITY = (
    "\n\n=== RESEARCH INTEGRITY ===\n\n"
    "This is a research task; accuracy and intellectual honesty come first.\n"
    "- NEVER answer a substantive research question (about papers, findings, or "
    "the state of a field) from general knowledge alone when a tool-grounded "
    "answer is possible. Search FIRST, then answer; every factual claim should "
    "be traceable to a tool result.\n"
    "- Do NOT fabricate paper titles, authors, abstracts, or DOIs. When "
    "summarising a paper, use ONLY information from the actual tool response.\n"
    "- It is better to say \"I could not find this\" than to provide an "
    "unsourced or invented answer.\n\n"
    "=== END RESEARCH INTEGRITY ==="
)
research_frag = RESEARCH_INTEGRITY + research_frag

# --- assemble + write ------------------------------------------------------
PROFILES = [("chat", chat, chat_frag), ("code", code, code_frag),
            ("research", research, research_frag)]
for pid, src, frag in PROFILES:
    out = dict(src)
    out["name"] = "Munin"
    params = dict(src["params"])
    params["system"] = FRAME_PREFIX + frag + FRAME_SUFFIX
    out["params"] = params
    (P / f"{pid}.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"wrote {pid}.json  (name=Munin, system {len(params['system'])} chars)")

print("done. Filenames stay chat/code/research.json (== internal routing ids); "
      "only the identity is now unified to Munin.")
