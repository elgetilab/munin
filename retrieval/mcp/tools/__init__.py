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
from .chats import search_past_conversations
from .projects import list_projects, get_current_project
from .agents import invoke_agent
from .research import deep_research
from .citations import export_citations
from .calculator import calculate
from .sandbox import run_python, sandbox_reset, sandbox_shutdown

__all__ = [
    "web_search",
    "web_fetch_content",
    "paper_search",
    "semantic_scholar_search",
    "paper_lookup",
    "get_citations",
    "get_references",
    "get_author_papers",
    "get_paper_pdf",
    "check_papers_availability",
    "llm_summarize",
    "search_user_docs",
    "view_attachment",
    "search_past_conversations",
    "list_projects",
    "get_current_project",
    "invoke_agent",
    "deep_research",
    "export_citations",
    "calculate",
    "run_python",
    "sandbox_reset",
    "sandbox_shutdown",
]
