"""
MCP tool: compare_papers (§8).

A thin composite on top of §7 ``read_paper``. Fans out read_paper
calls for N DOIs in parallel, then feeds the resulting summaries
into a single LLM call that produces a side-by-side markdown
comparison.

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
MIN_PAPERS = 1


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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def compare_papers(
    dois: list[str],
    focus: Optional[str] = None,
    max_papers: int = HARD_MAX_PAPERS,
) -> dict:
    """
    Fan out ``read_paper`` for each DOI in parallel, then produce a
    single side-by-side markdown comparison via one extra LLM call.
    See module docstring for the full contract.
    """
    from .read_paper import read_paper
    from .llm import llm_summarize

    # Validate / normalise the DOI list.
    if not isinstance(dois, list):
        return {"error": "dois must be a list of strings"}
    cleaned: list[str] = []
    seen: set[str] = set()
    for d in dois:
        if not isinstance(d, str):
            continue
        d = d.strip()
        if not d:
            continue
        key = d.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(d)
    if not cleaned:
        return {"error": "dois must contain at least one non-empty DOI"}

    try:
        limit = int(max_papers)
    except (TypeError, ValueError):
        limit = HARD_MAX_PAPERS
    limit = max(MIN_PAPERS, min(limit, HARD_MAX_PAPERS))
    cleaned = cleaned[:limit]

    # Parallel read_paper fan-out.
    read_results = await asyncio.gather(
        *(read_paper(doi=d, focus=focus) for d in cleaned),
        return_exceptions=True,
    )

    successful: list[dict] = []
    failed: list[dict] = []
    all_sources_used: set[str] = set()

    for doi, result in zip(cleaned, read_results):
        if isinstance(result, Exception):
            failed.append({
                "doi": doi,
                "error": f"{type(result).__name__}: {result}",
            })
            continue
        if not isinstance(result, dict):
            failed.append({
                "doi": doi,
                "error": f"read_paper returned unexpected {type(result).__name__}",
            })
            continue
        if result.get("error"):
            failed.append({
                "doi": doi,
                "error": result["error"],
            })
            continue
        for src in (result.get("sources_used") or []):
            all_sources_used.add(src)
        successful.append(result)

    # If nothing succeeded there's nothing to compare.
    if not successful:
        return {
            "focus": focus,
            "papers": [],
            "comparison": None,
            "failed": failed,
            "error": (
                "compare_papers could not read any of the requested "
                "DOIs. See `failed` for per-DOI reasons."
            ),
        }

    # Trim what we echo back to the caller to keep the response
    # bounded. Callers that need full details can call read_paper
    # on a specific DOI afterwards.
    papers_metadata: list[dict] = []
    for p in successful:
        papers_metadata.append({
            "doi": p.get("doi"),
            "title": p.get("title"),
            "authors": (p.get("authors") or [])[:10],
            "summary": p.get("summary"),
            "key_findings": p.get("key_findings") or [],
            "sources_used": p.get("sources_used") or [],
        })

    # Single papers take a shortcut - no "comparison" to produce.
    if len(successful) == 1:
        lone = successful[0]
        single_title = lone.get("title") or "the paper"
        return {
            "focus": focus,
            "papers": papers_metadata,
            "comparison": (
                f"Only one paper was available to read ({single_title}). "
                f"Its summary is above under `papers[0].summary` and "
                f"its key findings under `papers[0].key_findings`; "
                f"there is nothing to compare."
            ),
            "failed": failed,
            "sources_used": sorted(all_sources_used),
            "n_compared": 1,
        }

    # Build the comparison prompt and fire the single LLM call.
    system_instruction, user_text = _build_comparison_prompt(
        successful, focus
    )
    llm_result = await llm_summarize(
        text=user_text,
        instruction=system_instruction,
        max_tokens=3000,
    )
    if not isinstance(llm_result, dict) or llm_result.get("error"):
        error_msg = (
            llm_result.get("error")
            if isinstance(llm_result, dict)
            else "unknown LLM failure"
        )
        return {
            "focus": focus,
            "papers": papers_metadata,
            "comparison": None,
            "failed": failed,
            "error": f"comparison LLM call failed: {error_msg}",
            "sources_used": sorted(all_sources_used),
        }

    comparison_md = (llm_result.get("summary") or "").strip()
    if not comparison_md:
        comparison_md = (
            "The comparison LLM call returned an empty response. "
            "Please retry or fall back to reading each paper "
            "individually via read_paper."
        )

    return {
        "focus": focus,
        "papers": papers_metadata,
        "comparison": comparison_md,
        "failed": failed,
        "sources_used": sorted(all_sources_used),
        "n_compared": len(successful),
    }
