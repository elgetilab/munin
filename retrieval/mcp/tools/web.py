"""
Web-related MCP tools.

Provides:
- web_search: Search the web using SearXNG
- web_fetch_content: Fetch and extract content from URLs
"""

import httpx

from database import SEARXNG_URL
from .llm import llm_summarize


async def web_search(query: str, top_k: int = 10) -> dict:
    """
    Search the web using SearXNG and return results.

    Args:
        query: Search query
        top_k: Number of results to return

    Returns:
        Dict with 'results' list containing title, url, snippet
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                    "engines": "google,duckduckgo,brave",
                    "language": "en"
                }
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("results", [])[:top_k]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", "")[:500]
                })

            return {"results": results}

    except Exception as e:
        return {"error": f"Web search failed: {str(e)}"}


async def web_fetch_content(url: str, summarize: bool = False,
                            summary_instruction: str = "Summarize the main points") -> dict:
    """
    Fetch webpage content and optionally summarize using LLM.

    Args:
        url: The URL to fetch
        summarize: Whether to summarize the content
        summary_instruction: Instructions for summarization

    Returns:
        Dict with 'content' (or 'summary') and metadata, or 'error'
    """
    try:
        import trafilatura

        # Fetch the URL
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; MuninBot/1.0; +https://muninai.org)"
            })
            response.raise_for_status()
            html = response.text

        # Extract main content using trafilatura
        content = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True
        )

        if not content:
            return {"error": "Could not extract content from URL", "url": url}

        result = {
            "url": url,
            "content_length": len(content),
            "content": content[:50000] if len(content) > 50000 else content  # Limit raw content
        }

        # Optionally summarize
        if summarize:
            summary_result = await llm_summarize(content, summary_instruction)
            if "error" in summary_result:
                result["summary_error"] = summary_result["error"]
            else:
                result["summary"] = summary_result["summary"]

        return result

    except httpx.TimeoutException:
        return {"error": f"Request timed out for URL: {url}"}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code} for URL: {url}"}
    except Exception as e:
        return {"error": f"Failed to fetch URL: {str(e)}"}
