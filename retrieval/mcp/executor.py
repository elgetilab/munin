"""
MCP Tool Executor.

Maps tool names to their implementations and handles execution.
"""

from .tools import (
    web_search,
    web_fetch_content,
    paper_search,
    semantic_scholar_search,
    paper_lookup,
    get_citations,
    get_references,
    get_author_papers,
    get_paper_pdf,
    check_papers_availability,
    llm_summarize,
    search_user_docs,
    search_past_conversations,
    invoke_agent,
    deep_research,
    export_citations,
    calculate,
    run_python,
    sandbox_reset,
)


async def execute_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """
    Execute an MCP tool and return the result.

    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments

    Returns:
        Tool result as a dictionary
    """
    try:
        if tool_name == "web_search":
            return await web_search(
                query=arguments.get("query"),
                queries=arguments.get("queries"),
                top_k=arguments.get("top_k", 10),
            )

        elif tool_name == "paper_search":
            return await paper_search(
                query=arguments.get("query"),
                queries=arguments.get("queries"),
                top_k=arguments.get("top_k", 5),
            )

        elif tool_name == "semantic_scholar_search":
            return await semantic_scholar_search(
                query=arguments.get("query"),
                queries=arguments.get("queries"),
                top_k=arguments.get("top_k", 10),
                year=arguments.get("year", ""),
            )

        elif tool_name == "paper_lookup":
            doi = arguments.get("doi", "")
            return await paper_lookup(doi)

        elif tool_name == "llm_summarize":
            text = arguments.get("text", "")
            instruction = arguments.get("instruction", "Summarize")
            max_tokens = arguments.get("max_tokens", 16384)
            return await llm_summarize(text, instruction, max_tokens)

        elif tool_name == "web_fetch":
            url = arguments.get("url", "")
            # Backwards-compat shim: older callers may still pass `summarize`;
            # we ignore it because web_fetch always summarises now.
            summary_instruction = arguments.get(
                "summary_instruction",
                "Summarize the main points and key findings, focusing on factual content.",
            )
            return await web_fetch_content(url, summary_instruction)

        elif tool_name == "get_citations":
            doi = arguments.get("doi", "")
            limit = arguments.get("limit", 20)
            return await get_citations(doi, limit)

        elif tool_name == "get_references":
            doi = arguments.get("doi", "")
            limit = arguments.get("limit", 50)
            return await get_references(doi, limit)

        elif tool_name == "get_author_papers":
            author_name = arguments.get("author_name", "")
            limit = arguments.get("limit", 50)
            return await get_author_papers(author_name, limit)

        elif tool_name == "get_paper_pdf":
            doi = arguments.get("doi", "")
            return await get_paper_pdf(doi)

        elif tool_name == "check_papers_availability":
            dois = arguments.get("dois", [])
            return await check_papers_availability(dois)

        elif tool_name == "search_user_docs":
            query = arguments.get("query", "")
            top_k = arguments.get("top_k", 5)
            return await search_user_docs(query, top_k)

        elif tool_name == "search_past_conversations":
            return await search_past_conversations(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 5),
                persona=arguments.get("persona"),
                include_current=arguments.get("include_current", False),
            )

        elif tool_name == "invoke_agent":
            agent = arguments.get("agent", "")
            query = arguments.get("query", "")
            return await invoke_agent(agent, query)

        elif tool_name == "deep_research":
            question = arguments.get("question", "")
            depth = arguments.get("depth", "medium")
            return await deep_research(question, depth)

        elif tool_name == "export_citations":
            dois = arguments.get("dois", [])
            fmt = arguments.get("format", "bibtex")
            return await export_citations(dois=dois, format=fmt)

        elif tool_name == "calculate":
            expression = arguments.get("expression", "")
            mode = arguments.get("mode", "numeric")
            return await calculate(expression=expression, mode=mode)

        elif tool_name == "run_python":
            return await run_python(
                code=arguments.get("code", ""),
                timeout_s=arguments.get("timeout_s", 30),
            )

        elif tool_name == "sandbox_reset":
            return await sandbox_reset()

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {"error": f"Tool execution failed: {str(e)}"}
