"""
MCP Tool implementations.

Each module provides functions that implement MCP tools:
- web.py: web_search, web_fetch
- papers.py: paper_search, semantic_scholar_search, paper_lookup, get_citations, get_references, get_author_papers, get_paper_pdf, check_papers_availability
- llm.py: llm_summarize
- documents.py: search_user_docs
"""

from .web import web_search, web_fetch_content
from .papers import (
    paper_search,
    semantic_scholar_search,
    paper_lookup,
    get_citations,
    get_references,
    get_author_papers,
    get_paper_pdf,
    check_papers_availability,
)
from .llm import llm_summarize
from .documents import search_user_docs, view_attachment
from .equation import transcribe_equation
from .faq import faq
from .read_paper import read_paper
from .compare_papers import compare_papers
from .s2_citations import s2_get_citations, s2_get_references
from .chats import search_past_conversations
from .memory import remember, forget, recall
from .artifacts import (
    create_artifact,
    read_artifact,
    update_artifact,
    list_artifacts,
    save_artifact_to_documents,
)
from .projects import list_projects, get_current_project
from .agents import invoke_agent
from .research import deep_research
from .citations import export_citations
from .calculator import calculate
from .sandbox import run_python, sandbox_reset, sandbox_shutdown
from .latex import compile_latex
from .clarification import (
    ask_clarification,
    validate_clarification_payload,
    render_markdown_fallback,
)

__all__ = [
    "web_search",
    "web_fetch_content",
    "paper_search",
    "semantic_scholar_search",
    "paper_lookup",
    "read_paper",
    "compare_papers",
    "get_citations",
    "get_references",
    "s2_get_citations",
    "s2_get_references",
    "get_author_papers",
    "get_paper_pdf",
    "check_papers_availability",
    "llm_summarize",
    "search_user_docs",
    "view_attachment",
    "transcribe_equation",
    "faq",
    "search_past_conversations",
    "remember",
    "forget",
    "recall",
    "create_artifact",
    "read_artifact",
    "update_artifact",
    "list_artifacts",
    "save_artifact_to_documents",
    "list_projects",
    "get_current_project",
    "invoke_agent",
    "deep_research",
    "export_citations",
    "calculate",
    "run_python",
    "sandbox_reset",
    "sandbox_shutdown",
    "compile_latex",
    "ask_clarification",
    "validate_clarification_payload",
    "render_markdown_fallback",
]
