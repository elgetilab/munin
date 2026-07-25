"""
Comparison-prompt building blocks (formerly the compare_papers MCP
tool, §8).

The compare_papers tool was retired once the `source` agent's
mode='compare' superseded it; this module is kept because `source`
(mcp/tools/source.py) reuses `_build_comparison_prompt` and
`HARD_MAX_PAPERS`. The top-level `compare_papers()` entry point (which
fanned out read_paper calls) is gone; the prompt builder below is the
surface.

Design decisions (locked 2026-04-14):

- **Free-form markdown output** in a single ``comparison`` field.
  The spec suggested structured ``comparison_table`` /
  ``disagreements`` / ``common_findings`` fields but parsing those
  reliably from LLM output is fragile. Markdown is easier to render
  and the model can still quote back specific sections when asked.
- **``focus`` is optional** - a focused comparison is much more
  useful than a generic one, but the tool still works without it.
- **Hard cap at 5 papers** regardless of what the caller passes
  for ``max_papers``. Past 5 the comparison starts being too wide
  to be useful in one LLM turn.
- **Partial success**: if one DOI can't be read, the tool continues
  with the rest and lists the failure in a ``failed`` field so the
  model can tell the user "I couldn't find X but here's what I
  found for Y and Z".
- **Latency**: ~6s for the parallel read_paper fan-out (gated by
  the slowest paper, not the sum) plus ~3s for the final
  comparison call. Call budget ~9s total for up to 5 papers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

HARD_MAX_PAPERS = 5


# ---------------------------------------------------------------------------
# Comparison prompt
# ---------------------------------------------------------------------------

_COMPARISON_SYSTEM_INSTRUCTION_BASE = (
    "You are comparing {n} research papers side by side. Produce a "
    "single markdown comparison with the following sections, in this "
    "order, each as a level-2 heading (## Header):\n"
    "\n"
    "## Methods / Approach\n"
    "  A short paragraph per paper contrasting their methodological "
    "approach. Use bullet points if it helps.\n"
    "\n"
    "## Results\n"
    "  A side-by-side summary of the headline results. Prefer "
    "quantitative statements (effect sizes, p-values, sample sizes) "
    "where the source summaries include them.\n"
    "\n"
    "## Scope and limitations\n"
    "  What each paper claims to cover and where the authors flag "
    "limitations. Keep it honest - if a paper doesn't mention "
    "limitations, say so.\n"
    "\n"
    "## Where they disagree\n"
    "  Explicit contradictions or tensions between the papers. If "
    "they genuinely don't disagree, say 'No direct disagreements "
    "in the provided summaries.'\n"
    "\n"
    "## Common ground\n"
    "  Findings or assumptions all of the papers share.\n"
    "\n"
    "## Verdict\n"
    "  A 2-4 sentence synthesis that directly addresses the user's "
    "question. Be specific; do not hedge with 'further research is "
    "needed' unless the sources explicitly say so.\n"
    "\n"
    "Do not invent content beyond what the summaries provide. If a "
    "summary doesn't cover a given axis, say so in that section "
    "rather than fabricating it. Refer to the papers by their "
    "shortened title (e.g. 'AlphaFold paper', 'Jumper et al.') "
    "rather than by DOI."
)


def _build_comparison_prompt(
    successful_papers: list[dict],
    focus: Optional[str],
) -> tuple[str, str]:
    """
    Return ``(system_instruction, user_text)`` for the comparison
    LLM call. system_instruction is the structured "produce a
    side-by-side comparison" directive; user_text is the
    concatenated paper summaries.
    """
    n = len(successful_papers)
    base = _COMPARISON_SYSTEM_INSTRUCTION_BASE.format(n=n)
    if focus:
        base += (
            f"\n\nThe user's specific question / axis of comparison is: "
            f"{focus}. Bias every section toward this angle - if a paper "
            f"doesn't cover the topic, say so explicitly in the "
            f"corresponding section."
        )
    else:
        base += (
            "\n\nNo specific focus was provided. Compare the papers on "
            "their own terms - treat the commonalities and differences "
            "in methods, results, and conclusions as the natural axes."
        )

    sections: list[str] = []
    for i, paper in enumerate(successful_papers, start=1):
        title = (paper.get("title") or "").strip() or "Untitled"
        authors = paper.get("authors") or []
        if isinstance(authors, list):
            authors_str = ", ".join(
                a if isinstance(a, str) else str(a) for a in authors[:6]
            )
            if len(authors) > 6:
                authors_str += ", et al."
        else:
            authors_str = str(authors)
        doi = paper.get("doi") or "?"
        summary = (paper.get("summary") or "").strip()
        key_findings = paper.get("key_findings") or []
        findings_block = ""
        if key_findings:
            findings_block = "\nKey findings:\n" + "\n".join(
                f"- {f}" for f in key_findings
            )
        section = (
            f"=== Paper {i}: {title} ===\n"
            f"DOI: {doi}\n"
            f"Authors: {authors_str or '(unknown)'}\n"
            f"\nSummary:\n{summary or '(no summary available)'}"
            f"{findings_block}"
        )
        sections.append(section)

    user_text = "\n\n".join(sections)
    return base, user_text

