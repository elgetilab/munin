"""
MCP tool dispatchers (P2 #19).

One ``@register_tool("name")`` per MCP tool. Each function is a thin
shim: pull arguments off the dict and forward them as explicit
kwargs to the implementation in ``mcp/tools/*.py``. Argument shapes
are preserved verbatim from the old if/elif chain in ``executor.py``.

Adding a new tool: write the implementation in ``mcp/tools/...``,
add the schema entry to ``mcp/schemas.py::MCP_TOOLS``, and add one
``@register_tool("...")`` function here. The startup consistency
check (``verify_dispatch_registry`` in ``_dispatch.py``) raises if
any of the three are missing.
"""

from __future__ import annotations

from ._dispatch import register_tool
from .tools import (
    ask_clarification,
    calculate,
    check_papers_availability,
    compile_latex,
    compute,
    create_artifact,
    deep_research,
    export_citations,
    faq,
    forget,
    get_author_papers,
    get_citations,
    get_current_project,
    get_paper_pdf,
    get_references,
    invoke_agent,
    list_artifacts,
    list_projects,
    llm_summarize,
    paper_lookup,
    paper_search,
    read_artifact,
    edit_python,
    recall,
    remember,
    run_python,
    s2_get_citations,
    s2_get_references,
    sandbox_reset,
    save_artifact_to_documents,
    search_past_conversations,
    search_user_docs,
    search,
    semantic_scholar_search,
    source,
    tool_search,
    transcribe_equation,
    update_artifact,
    view_attachment,
    web_fetch_content,
    web_search,
)


# ---------------------------------------------------------------------------
# Web + paper search
# ---------------------------------------------------------------------------


@register_tool("web_search")
async def _web_search(arguments: dict) -> dict:
    return await web_search(
        query=arguments.get("query"),
        queries=arguments.get("queries"),
        top_k=arguments.get("top_k", 10),
    )


@register_tool("paper_search")
async def _paper_search(arguments: dict) -> dict:
    return await paper_search(
        query=arguments.get("query"),
        queries=arguments.get("queries"),
        top_k=arguments.get("top_k", 5),
    )


@register_tool("semantic_scholar_search")
async def _semantic_scholar_search(arguments: dict) -> dict:
    return await semantic_scholar_search(
        query=arguments.get("query"),
        queries=arguments.get("queries"),
        top_k=arguments.get("top_k", 10),
        year=arguments.get("year", ""),
    )


@register_tool("paper_lookup")
async def _paper_lookup(arguments: dict) -> dict:
    return await paper_lookup(arguments.get("doi", ""))


@register_tool("source")
async def _source(arguments: dict) -> dict:
    return await source(
        refs=arguments.get("refs", []),
        mode=arguments.get("mode", "summary"),
        question=arguments.get("question"),
        focus=arguments.get("focus"),
        schema=arguments.get("schema"),
    )


@register_tool("search")
async def _search(arguments: dict) -> dict:
    return await search(
        query=arguments.get("query"),
        filters=arguments.get("filters"),
        depth=arguments.get("depth", "normal"),
        top_k=arguments.get("top_k", 10),
    )


@register_tool("compute")
async def _compute(arguments: dict) -> dict:
    return await compute(
        spec=arguments.get("spec", ""),
        data_handle=arguments.get("data_handle"),
        language=arguments.get("language", "python"),
        budget=arguments.get("budget", "quick"),
    )


@register_tool("llm_summarize")
async def _llm_summarize(arguments: dict) -> dict:
    return await llm_summarize(
        arguments.get("text", ""),
        arguments.get("instruction", "Summarize"),
        arguments.get("max_tokens", 16384),
    )


@register_tool("web_fetch")
async def _web_fetch(arguments: dict) -> dict:
    # Backwards-compat: older callers may still pass ``summarize``; we
    # ignore it because web_fetch always summarises now.
    return await web_fetch_content(
        arguments.get("url", ""),
        arguments.get(
            "summary_instruction",
            "Summarize the main points and key findings, focusing on factual content.",
        ),
    )


# ---------------------------------------------------------------------------
# Citation graph
# ---------------------------------------------------------------------------


@register_tool("get_citations")
async def _get_citations(arguments: dict) -> dict:
    return await get_citations(
        arguments.get("doi", ""),
        arguments.get("limit", 20),
    )


@register_tool("get_references")
async def _get_references(arguments: dict) -> dict:
    return await get_references(
        arguments.get("doi", ""),
        arguments.get("limit", 50),
    )


@register_tool("s2_get_citations")
async def _s2_get_citations(arguments: dict) -> dict:
    return await s2_get_citations(
        doi=arguments.get("doi", ""),
        limit=arguments.get("limit", 50),
        year_from=arguments.get("year_from"),
        include_contexts=bool(arguments.get("include_contexts", False)),
    )


@register_tool("s2_get_references")
async def _s2_get_references(arguments: dict) -> dict:
    return await s2_get_references(
        doi=arguments.get("doi", ""),
        limit=arguments.get("limit", 50),
        include_contexts=bool(arguments.get("include_contexts", False)),
    )


@register_tool("get_author_papers")
async def _get_author_papers(arguments: dict) -> dict:
    return await get_author_papers(
        arguments.get("author_name", ""),
        arguments.get("limit", 50),
    )


@register_tool("get_paper_pdf")
async def _get_paper_pdf(arguments: dict) -> dict:
    return await get_paper_pdf(arguments.get("doi", ""))


@register_tool("check_papers_availability")
async def _check_papers_availability(arguments: dict) -> dict:
    return await check_papers_availability(arguments.get("dois", []))


# ---------------------------------------------------------------------------
# User docs / attachments / equations
# ---------------------------------------------------------------------------


@register_tool("search_user_docs")
async def _search_user_docs(arguments: dict) -> dict:
    # Pass project_id through only if the caller actually provided it;
    # otherwise let the tool's contextvar default apply. Behavioural
    # difference matters because search_user_docs(project_id=None)
    # explicitly scopes to "no project", while omitting the kwarg
    # falls back to the current_project_id ContextVar.
    if "project_id" in arguments:
        return await search_user_docs(
            query=arguments.get("query", ""),
            top_k=arguments.get("top_k", 5),
            project_id=arguments.get("project_id"),
        )
    return await search_user_docs(
        query=arguments.get("query", ""),
        top_k=arguments.get("top_k", 5),
    )


@register_tool("view_attachment")
async def _view_attachment(arguments: dict) -> dict:
    return await view_attachment(document_id=arguments.get("document_id", ""))


@register_tool("transcribe_equation")
async def _transcribe_equation(arguments: dict) -> dict:
    return await transcribe_equation(image_ref=arguments.get("image_ref", ""))


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@register_tool("create_artifact")
async def _create_artifact(arguments: dict) -> dict:
    return await create_artifact(
        title=arguments.get("title", ""),
        content=arguments.get("content", ""),
        content_type=arguments.get("content_type", ""),
        language=arguments.get("language"),
        change_summary=arguments.get("change_summary"),
    )


@register_tool("read_artifact")
async def _read_artifact(arguments: dict) -> dict:
    return await read_artifact(
        artifact_id=arguments.get("artifact_id", ""),
        version=arguments.get("version"),
    )


@register_tool("update_artifact")
async def _update_artifact(arguments: dict) -> dict:
    return await update_artifact(
        artifact_id=arguments.get("artifact_id", ""),
        content=arguments.get("content", ""),
        change_summary=arguments.get("change_summary"),
        is_diff=bool(arguments.get("is_diff", False)),
        base_version=arguments.get("base_version"),
    )


@register_tool("list_artifacts")
async def _list_artifacts(arguments: dict) -> dict:
    return await list_artifacts()


@register_tool("save_artifact_to_documents")
async def _save_artifact_to_documents(arguments: dict) -> dict:
    return await save_artifact_to_documents(
        artifact_id=arguments.get("artifact_id", ""),
        filename=arguments.get("filename"),
    )


# ---------------------------------------------------------------------------
# Memory + projects + history
# ---------------------------------------------------------------------------


@register_tool("remember")
async def _remember(arguments: dict) -> dict:
    return await remember(
        key=arguments.get("key", ""),
        value=arguments.get("value", ""),
    )


@register_tool("forget")
async def _forget(arguments: dict) -> dict:
    return await forget(key=arguments.get("key", ""))


@register_tool("recall")
async def _recall(arguments: dict) -> dict:
    return await recall(search=arguments.get("search"))


@register_tool("list_projects")
async def _list_projects(arguments: dict) -> dict:
    return await list_projects()


@register_tool("get_current_project")
async def _get_current_project(arguments: dict) -> dict:
    return await get_current_project()


@register_tool("search_past_conversations")
async def _search_past_conversations(arguments: dict) -> dict:
    return await search_past_conversations(
        query=arguments.get("query", ""),
        limit=arguments.get("limit", 5),
        persona=arguments.get("persona"),
        include_current=arguments.get("include_current", False),
    )


# ---------------------------------------------------------------------------
# Agents + research orchestration
# ---------------------------------------------------------------------------


@register_tool("invoke_agent")
async def _invoke_agent(arguments: dict) -> dict:
    return await invoke_agent(
        arguments.get("agent", ""),
        arguments.get("query", ""),
    )


@register_tool("deep_research")
async def _deep_research(arguments: dict) -> dict:
    return await deep_research(
        arguments.get("question", ""),
        arguments.get("depth", "medium"),
    )


@register_tool("export_citations")
async def _export_citations(arguments: dict) -> dict:
    return await export_citations(
        dois=arguments.get("dois", []),
        format=arguments.get("format", "bibtex"),
    )


# ---------------------------------------------------------------------------
# Calculator + sandbox + LaTeX
# ---------------------------------------------------------------------------


@register_tool("calculate")
async def _calculate(arguments: dict) -> dict:
    return await calculate(
        expression=arguments.get("expression", ""),
        mode=arguments.get("mode", "numeric"),
    )


@register_tool("run_python")
async def _run_python(arguments: dict) -> dict:
    return await run_python(
        code=arguments.get("code", ""),
        timeout_s=arguments.get("timeout_s", 30),
    )


@register_tool("edit_python")
async def _edit_python(arguments: dict) -> dict:
    return await edit_python(
        edits=arguments.get("edits") or [],
        timeout_s=arguments.get("timeout_s", 30),
    )


@register_tool("sandbox_reset")
async def _sandbox_reset(arguments: dict) -> dict:
    return await sandbox_reset()


@register_tool("compile_latex")
async def _compile_latex(arguments: dict) -> dict:
    return await compile_latex(
        source=arguments.get("source"),
        artifact_id=arguments.get("artifact_id"),
        diff=arguments.get("diff"),
        bibliography=arguments.get("bibliography"),
        extra_files=arguments.get("extra_files"),
        timeout_s=arguments.get("timeout_s", 60),
    )


# ---------------------------------------------------------------------------
# FAQ + clarification + tool discovery
# ---------------------------------------------------------------------------


@register_tool("faq")
async def _faq(arguments: dict) -> dict:
    return await faq(
        topic=arguments.get("topic"),
        search=arguments.get("search"),
    )


@register_tool("ask_clarification")
async def _ask_clarification(arguments: dict) -> dict:
    return await ask_clarification(
        what_i_understood=arguments.get("what_i_understood", ""),
        questions=arguments.get("questions", []),
    )


@register_tool("tool_search")
async def _tool_search(arguments: dict) -> dict:
    return await tool_search(query=arguments.get("query", ""))


# ---------------------------------------------------------------------------
# Plan mode (P2 #24 Phase 1)
# ---------------------------------------------------------------------------
#
# Both dispatchers read user_email + conversation_id from the active
# ContextVars (same pattern as artifacts, memory, etc.) and emit a
# `plan_updated` SSE event so the inline PlanCard UI updates in real
# time. Tool results are deliberately compact (an `ok` boolean + ids
# + counts) so the model's next-turn prefill isn't bloated by the
# full plan payload — the model already has the canonical version in
# the system prompt block.


@register_tool("set_plan")
async def _set_plan(arguments: dict) -> dict:
    # Lazy imports: dispatchers.py is imported by executor.py at
    # module top, before any FastAPI startup; pulling chat_service
    # internals here would create an import cycle.
    from mcp.context import (
        current_conversation_id,
        current_sse_emitter,
        current_user_email,
    )
    import plan_store

    user_email = current_user_email.get()
    conversation_id = current_conversation_id.get()
    if not user_email or not conversation_id:
        return {
            "error": (
                "set_plan requires a persistent authenticated chat "
                "(no plan on ephemeral / unauthenticated turns)"
            )
        }
    try:
        plan = await plan_store.set_plan(
            user_email=user_email,
            conversation_id=conversation_id,
            items=arguments.get("items") or [],
            requires_approval=bool(arguments.get("requires_approval", False)),
        )
    except plan_store.PlanError as e:
        return {"error": str(e)}

    emit = current_sse_emitter.get()
    if emit is not None:
        try:
            emit("plan_updated", plan)
        except Exception:
            # Per-tool emitter is best-effort; the canonical state is
            # in the DB and the system-prompt block on the next turn.
            pass

    return {
        "ok": True,
        "item_count": len(plan["items"]),
        "ids": [it["id"] for it in plan["items"]],
    }


@register_tool("update_plan_item")
async def _update_plan_item(arguments: dict) -> dict:
    from mcp.context import (
        current_conversation_id,
        current_sse_emitter,
        current_user_email,
    )
    import plan_store

    user_email = current_user_email.get()
    conversation_id = current_conversation_id.get()
    if not user_email or not conversation_id:
        return {
            "error": (
                "update_plan_item requires a persistent authenticated chat"
            )
        }
    try:
        plan = await plan_store.update_item(
            user_email=user_email,
            conversation_id=conversation_id,
            item_id=arguments.get("id") or "",
            status=arguments.get("status"),
            notes=arguments.get("notes"),
        )
    except plan_store.PlanError as e:
        return {"error": str(e)}

    emit = current_sse_emitter.get()
    if emit is not None:
        try:
            emit("plan_updated", plan)
        except Exception:
            pass

    # Return the single touched item plus a compact summary of the
    # other ids — saves prefill tokens vs returning the full plan.
    touched = next(
        (it for it in plan["items"] if it["id"] == arguments.get("id")),
        None,
    )
    return {
        "ok": True,
        "item": touched,
        "ids": [it["id"] for it in plan["items"]],
    }
