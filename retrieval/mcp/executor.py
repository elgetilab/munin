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
    read_paper,
    get_citations,
    get_references,
    s2_get_citations,
    s2_get_references,
    get_author_papers,
    get_paper_pdf,
    check_papers_availability,
    llm_summarize,
    search_user_docs,
    view_attachment,
    transcribe_equation,
    search_past_conversations,
    remember,
    forget,
    recall,
    create_artifact,
    read_artifact,
    update_artifact,
    list_artifacts,
    save_artifact_to_documents,
    list_projects,
    get_current_project,
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

        elif tool_name == "read_paper":
            return await read_paper(
                doi=arguments.get("doi", ""),
                focus=arguments.get("focus"),
            )

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

        elif tool_name == "s2_get_citations":
            return await s2_get_citations(
                doi=arguments.get("doi", ""),
                limit=arguments.get("limit", 50),
                year_from=arguments.get("year_from"),
                include_contexts=bool(arguments.get("include_contexts", False)),
            )

        elif tool_name == "s2_get_references":
            return await s2_get_references(
                doi=arguments.get("doi", ""),
                limit=arguments.get("limit", 50),
                include_contexts=bool(arguments.get("include_contexts", False)),
            )

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
            # Pass project_id through only if the caller actually provided
            # it; otherwise let the tool's contextvar default apply.
            if "project_id" in arguments:
                return await search_user_docs(
                    query=query,
                    top_k=top_k,
                    project_id=arguments.get("project_id"),
                )
            return await search_user_docs(query=query, top_k=top_k)

        elif tool_name == "view_attachment":
            return await view_attachment(
                document_id=arguments.get("document_id", ""),
            )

        elif tool_name == "transcribe_equation":
            return await transcribe_equation(
                image_ref=arguments.get("image_ref", ""),
            )

        elif tool_name == "create_artifact":
            return await create_artifact(
                title=arguments.get("title", ""),
                content=arguments.get("content", ""),
                content_type=arguments.get("content_type", ""),
                language=arguments.get("language"),
                change_summary=arguments.get("change_summary"),
            )

        elif tool_name == "read_artifact":
            return await read_artifact(
                artifact_id=arguments.get("artifact_id", ""),
                version=arguments.get("version"),
            )

        elif tool_name == "update_artifact":
            return await update_artifact(
                artifact_id=arguments.get("artifact_id", ""),
                content=arguments.get("content", ""),
                change_summary=arguments.get("change_summary"),
                is_diff=bool(arguments.get("is_diff", False)),
                base_version=arguments.get("base_version"),
            )

        elif tool_name == "list_artifacts":
            return await list_artifacts()

        elif tool_name == "save_artifact_to_documents":
            return await save_artifact_to_documents(
                artifact_id=arguments.get("artifact_id", ""),
                filename=arguments.get("filename"),
            )

        elif tool_name == "remember":
            return await remember(
                key=arguments.get("key", ""),
                value=arguments.get("value", ""),
            )

        elif tool_name == "forget":
            return await forget(key=arguments.get("key", ""))

        elif tool_name == "recall":
            return await recall(search=arguments.get("search"))

        elif tool_name == "list_projects":
            return await list_projects()

        elif tool_name == "get_current_project":
            return await get_current_project()

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
