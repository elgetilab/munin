# Agentic Orchestration

## Overview

The retrieval service acts as the coordinator for agentic workflows. The main model (Qwen 3.5) decides when to invoke specialized sub-agents — each a constrained workflow that chains multiple tool calls and can iterate. Sub-agents run within the same request, streaming their progress via the task execution log (see CHAT-UI-TASK-LOG.md).

This is NOT a multi-model system. There is one model (Qwen 3.5) making all decisions. "Agents" here means predefined workflows with constrained permissions that the model can trigger, not separate LLM instances.

## Architecture

```
User message
    │
    ▼
Retrieval service receives /api/chat/completions
    │
    ▼
Assemble context (persona prompt + summary + recent messages + RAG)
    │
    ▼
Send to vLLM → model streams response
    │
    ├── Model outputs text → stream tokens to frontend
    │
    ├── Model calls a tool → execute, return result, model continues
    │
    └── Model invokes an agent → run agent workflow:
          │
          ├── Agent has a predefined sequence of tools + iteration logic
          ├── Each step streams as a task log entry
          ├── Agent can call vLLM for intermediate reasoning
          └── Agent returns structured result → model synthesizes final answer
```

## Agent Registry

Agents are defined as configuration, not code. Each agent has a name, description, permitted tools, a system prompt fragment, and a maximum iteration count.

**Config file:** `config/agents.yml`

```yaml
agents:
  research_orchestrator:
    name: Research Orchestrator
    description: >
      Deep investigation of a research question. Searches papers, explores
      citations, checks web sources, and synthesizes findings with sources.
    trigger: >
      Use when the user asks a complex research question that needs multiple
      sources, citation exploration, or comprehensive literature analysis.
      NOT for simple factual questions.
    tools:
      - paper_search
      - semantic_scholar_search
      - get_citations
      - get_references
      - get_author_papers
      - web_search
      - web_fetch
      - llm_summarize
      - search_user_docs
    max_iterations: 8
    max_tool_calls: 20
    system_fragment: >
      You are executing a research workflow. Plan your approach, then execute
      step by step. Search broadly first, then follow promising leads via
      citations. Synthesize findings with source attribution. If initial
      results are poor, try alternative queries.

  code_checker:
    name: Code Checker
    description: >
      Validates code output by running syntax checks and basic tests.
      Feeds errors back for automatic correction.
    trigger: >
      Automatically triggered when the Turing persona outputs a code block.
      Do not invoke explicitly.
    tools:
      - execute_code  # sandboxed code execution (future)
    max_iterations: 3
    max_tool_calls: 6
    system_fragment: >
      Check the code for syntax errors and obvious bugs. If errors are found,
      explain what's wrong and produce a corrected version. Iterate until
      the code passes or max iterations reached.

  writing_agent:
    name: Writing Agent
    description: >
      Structures and drafts longer documents by breaking them into sections,
      researching each section, and composing a coherent output.
    trigger: >
      Use when the user asks you to write a comprehensive document, report,
      literature review, or grant section that benefits from structured
      planning and per-section research.
    tools:
      - paper_search
      - semantic_scholar_search
      - web_search
      - web_fetch
      - llm_summarize
      - search_user_docs
    max_iterations: 10
    max_tool_calls: 30
    system_fragment: >
      Break the writing task into sections. For each section, gather relevant
      sources, then draft the section. After all sections are drafted,
      review for coherence and consistency. Produce the final document with
      proper citations.
```

## How Agents Are Invoked

### Model-Triggered (Reactive)

The model decides to use an agent based on the query. The agent descriptions are included in the system prompt so the model knows what's available:

```
You have access to these agent workflows:

- **Research Orchestrator**: For complex research questions needing multiple sources
  and citation exploration. Invoke with: {agent: "research_orchestrator", query: "..."}

- **Writing Agent**: For comprehensive documents that need structured research
  per section. Invoke with: {agent: "writing_agent", query: "...", outline: [...]}

When a query is simple (one search, one factual answer), use individual tools directly.
When a query is complex (multi-source, needs synthesis), invoke an agent workflow.
```

The model emits a special tool call:

```json
{
  "name": "invoke_agent",
  "arguments": {
    "agent": "research_orchestrator",
    "query": "Current state of cryo-EM for membrane protein structure determination"
  }
}
```

### User-Triggered (Proactive)

Users can explicitly invoke agent workflows from the chat:

- `/research What is the current state of CRISPR delivery mechanisms?`
- `/write-review A literature review on lipid raft dynamics, 2020-2026`

These are parsed by the frontend and sent as `invoke_agent` tool calls. The model doesn't need to decide — the user chose the workflow.

**Slash commands → agent mapping:**

| Slash command | Agent | Behavior |
|---------------|-------|----------|
| `/research <question>` | research_orchestrator | Deep multi-source investigation |
| `/write <description>` | writing_agent | Structured document drafting |
| `/analyze <DOI or title>` | research_orchestrator (with paper_lookup first) | Deep dive into a specific paper |

### Automatic (Code Checker)

The code checker triggers automatically when the Turing persona outputs a code block. The backend detects code blocks in the model output and invokes the checker without user action. The user sees it in the task log:

```
🧠 Reasoned for 1.2s                                        ▸
💻 Generated Python code (24 lines)                          ▸
🔍 Code checker: running syntax check                        ▸
   ✓ No errors found
```

Or if there's an error:

```
💻 Generated Python code (24 lines)                          ▸
🔍 Code checker: running syntax check                        ▾
   ✗ SyntaxError on line 12: unexpected indent
🧠 Reasoned for 0.8s                                        ▸
   "The indentation on line 12 needs fixing..."
💻 Corrected code (24 lines)                                 ▸
🔍 Code checker: running syntax check                        ▸
   ✓ No errors found
```

## Agent Execution Flow

When an agent is invoked:

1. **Load agent config** from registry (tools, max_iterations, system_fragment)
2. **Create agent context**: base system prompt + agent system_fragment + the query
3. **Execute loop:**
   ```
   for iteration in range(max_iterations):
       response = call_vllm(agent_context + accumulated_results)
       if response contains tool_calls:
           for each tool_call:
               if tool not in agent.tools: reject
               result = execute_tool(tool_call)
               stream task_log entry to frontend
               append result to accumulated_results
       elif response contains final_answer:
           break
       else:
           break  # model chose to stop
   ```
4. **Return** the agent's final output to the main model for synthesis into the response

### Parallel Tool Execution

When the model plans multiple independent tool calls in one step, the backend executes them in parallel:

```python
# Model outputs: "I need to search papers AND web simultaneously"
tool_calls = [
    {"name": "paper_search", "arguments": {"query": "cryo-EM membrane"}},
    {"name": "web_search", "arguments": {"query": "cryo-EM membrane 2026"}},
    {"name": "get_author_papers", "arguments": {"name": "Yifan Cheng"}}
]

# Execute all three concurrently
results = await asyncio.gather(*[
    execute_tool(tc) for tc in tool_calls
])
```

The task log shows them appearing nearly simultaneously rather than sequentially.

### Guardrails

- **Tool allowlist:** Each agent can only use its permitted tools. A research agent can't execute code; a code checker can't search the web.
- **Iteration limit:** Hard cap on loops to prevent runaway agents.
- **Tool call limit:** Hard cap on total tool calls per agent invocation.
- **Timeout:** 5 minutes per agent invocation (configurable).
- **Token budget:** Agent context is separate from the main conversation context. Each agent invocation gets a fresh context window with just its system prompt, query, and accumulated results.

## Task Log Integration

Agent workflows produce the same task log entries as direct tool calls (CHAT-UI-TASK-LOG.md), but nested under an agent header:

```
🔬 Research Orchestrator                                     ▾
│ 🧠 Planning research approach...                          ▸
│ 📚 Paper search (3 queries)                               ▾
│    "cryo-EM membrane protein"                 → 8 results ▸
│    "single particle analysis membrane"        → 5 results ▸
│    "detergent-free membrane structure"         → 4 results ▸
│ 🧠 Evaluating results, following citations... ▸
│ 🔗 Citation lookup: 10.1038/s41586-024...      → 15 refs  ▸
│ 🌐 Web search: "cryo-EM advances 2026"        → 6 results ▸
│ 🌐 Fetched nature.com/articles/...             → 2.1k words ▸
│ 📝 Summarized findings                        → 450 words ▸
│
│ Completed in 34s · 8 tool calls · 3 sources

[Model's synthesized response follows below]
```

The agent block is collapsible as a whole, and each step within it is individually expandable.

## SSE Events for Agents

New event types for the streaming protocol (extends `../../shared/docs/BACKEND-API.md` §5):

```
event: agent_start
data: {"agent": "research_orchestrator", "query": "Current state of cryo-EM..."}

event: agent_thinking
data: {"content": "Planning: search papers first, then follow citations..."}

event: agent_tool_call
data: {"id": "tc_1", "name": "paper_search", "arguments": {...}}

event: agent_tool_result
data: {"id": "tc_1", "name": "paper_search", "result": {...}}

event: agent_done
data: {"agent": "research_orchestrator", "tool_calls": 8, "duration_seconds": 34}
```

These nest under the existing event stream. The frontend distinguishes between direct tool calls (model acting alone) and agent tool calls (part of a workflow) by the event prefix.

## Web Search Routing

The model decides whether to web-search on each query. This is not a separate routing model — Qwen 3.5 itself decides:

**System prompt guidance:**
```
Use web_search when you need:
- Current information (news, recent events, latest publications)
- Statistics or data that change over time
- Information you're unsure about and want to verify

Do NOT use web_search for:
- Reasoning, creative, or code tasks
- Questions answerable from papers or your training data
- Follow-up questions in an ongoing conversation where context is already established
```

The model's reasoning (visible in the thinking block) shows its decision: "This is a factual question about a well-known concept, no web search needed" or "The user asks about 2026 developments, I should check the web."

## Implementation Priority

1. **Agent registry loader** — Parse `config/agents.yml`, make available to the retrieval service
2. **invoke_agent tool** — Register as an MCP tool, implement the execution loop
3. **Parallel tool execution** — `asyncio.gather` for concurrent tool calls
4. **Slash command parsing** — Frontend sends `/research ...` as an agent invocation
5. **Code checker** — Automatic triggering on code output (needs sandboxed execution — can start with syntax check only)
6. **Writing agent** — Most complex, implement after the research orchestrator works

## File Locations

```
backend/                        # cluster half of the monorepo
├── config/
│   └── agents.yml              # Agent definitions
├── retrieval/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── registry.py         # Load and validate agent configs
│   │   ├── executor.py         # Agent execution loop
│   │   └── parallel.py         # Parallel tool execution
│   └── mcp/
│       └── tools/
│           └── agents.py       # invoke_agent MCP tool
```
