# Munin Knowledge Tools

This directory contains the agentic workflow tools for Munin's RAG (Retrieval-Augmented Generation) system.

## Overview

The agentic workflow allows the LLM to autonomously call tools to search knowledge bases, find citations, and retrieve information before answering user queries.

```
User Query
    │
    ▼
┌─────────────────┐
│   LLM decides   │◄──────────────────────────────┐
│  which tool to  │                               │
│      call       │                               │
└────────┬────────┘                               │
         │                                        │
         ▼                                        │
┌─────────────────┐     ┌─────────────────┐      │
│  Execute Tool   │────▶│  Tool Result    │──────┘
│  (search, etc.) │     │  (JSON data)    │
└─────────────────┘     └─────────────────┘
                               │
                               │ (after max_tool_calls
                               │  or LLM stops calling)
                               ▼
                        ┌─────────────────┐
                        │  Final Answer   │
                        │  with citations │
                        └─────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `knowledge_tools.py` | Tool definitions and implementations |
| `paper_pipeline.py` | PDF processing pipeline (GROBID → Qdrant) |
| `paper_crawler.py` | Citation-based paper acquisition |
| `notion_sync.py` | Notion workspace sync to Qdrant |

## How to Add a New Tool

### Step 1: Define the Tool Schema

Add your tool to the `TOOLS` list in `knowledge_tools.py`:

```python
# In knowledge_tools.py, add to TOOLS list:

{
    "type": "function",
    "function": {
        "name": "my_new_tool",
        "description": "Clear description of what this tool does. Be specific - the LLM reads this to decide when to use it.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What this parameter is for"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default: 10)",
                    "default": 10
                }
            },
            "required": ["query"]  # Only truly required params
        }
    }
}
```

### Step 2: Implement the Tool Function

Add your implementation function:

```python
def my_new_tool(query: str, limit: int = 10) -> str:
    """
    Your tool implementation.

    IMPORTANT: Always return a JSON string, not a dict or other type.
    The LLM parses this output to understand the results.
    """
    try:
        # Your implementation here
        results = do_something(query, limit)

        return json.dumps({
            "query": query,
            "num_results": len(results),
            "results": results
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})
```

### Step 3: Register the Tool

Add your function to the `execute_tool` dispatcher:

```python
def execute_tool(tool_name: str, arguments: dict) -> str:
    tools = {
        "search_papers": search_papers,
        "find_citing_papers": find_citing_papers,
        "find_author_papers": find_author_papers,
        "get_paper_details": get_paper_details,
        "web_search": web_search,
        "my_new_tool": my_new_tool,  # Add here
    }
    # ...
```

### Step 4: Test Your Tool

```bash
# Test from command line
python knowledge_tools.py my_new_tool '{"query": "test", "limit": 5}'

# Expected output: JSON result
```

## Existing Tools

### search_papers
Semantic search over the paper database using SPECTER embeddings.

```python
# Usage
execute_tool("search_papers", {
    "query": "transformer attention mechanisms",
    "top_k": 5,
    "year_min": 2020,
    "year_max": 2024
})
```

### find_citing_papers
Find papers that cite a given paper using Neo4j graph database.

```python
execute_tool("find_citing_papers", {
    "doi": "10.1234/example.2023",
    "limit": 10
})
```

### find_author_papers
Find all papers by a specific author.

```python
execute_tool("find_author_papers", {
    "author_name": "Vaswani",
    "limit": 20
})
```

### get_paper_details
Get full metadata for a specific paper.

```python
execute_tool("get_paper_details", {
    "doi": "10.1234/example.2023"
})
# or
execute_tool("get_paper_details", {
    "paper_id": "abc123"
})
```

### web_search
Search the web using SearXNG meta-search engine.

```python
execute_tool("web_search", {
    "query": "latest transformer research 2024",
    "num_results": 5
})
```

## Modifying Tool Behavior

### Change How Many Tools Can Be Called

In `scripts/examples/munin_client.py`:

```python
def research(self, query, use_tools=True, max_tool_calls=3):
    # Change max_tool_calls for more complex multi-step reasoning
    # Higher = more thorough but slower and more tokens
```

### Change Tool Selection Behavior

The LLM decides which tools to call based on:
1. The tool's `description` field
2. The user's query
3. Previous tool results

To make the LLM prefer certain tools, improve their descriptions or add more context to the system prompt.

### Force Specific Tools

In `munin_client.py`, change `tool_choice`:

```python
response = self.client.chat.completions.create(
    model="llama-3.1-8b",
    messages=messages,
    tools=TOOLS,
    tool_choice="auto"  # LLM decides
    # tool_choice="required"  # Must call at least one tool
    # tool_choice={"type": "function", "function": {"name": "search_papers"}}  # Force specific tool
)
```

## Adding New Knowledge Sources

### Example: Add a Zotero Collection Search

```python
# 1. Add to TOOLS list
{
    "type": "function",
    "function": {
        "name": "search_zotero",
        "description": "Search your personal Zotero library for papers and references.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "collection": {"type": "string", "description": "Zotero collection name (optional)"}
            },
            "required": ["query"]
        }
    }
}

# 2. Implement
def search_zotero(query: str, collection: str = None) -> str:
    from pyzotero import zotero

    zot = zotero.Zotero(ZOTERO_LIBRARY_ID, 'user', ZOTERO_API_KEY)
    items = zot.items(q=query, limit=10)

    results = []
    for item in items:
        results.append({
            "title": item["data"].get("title"),
            "authors": [a.get("lastName") for a in item["data"].get("creators", [])],
            "year": item["data"].get("date", "")[:4],
            "doi": item["data"].get("DOI")
        })

    return json.dumps({"query": query, "results": results}, indent=2)

# 3. Register in execute_tool
tools["search_zotero"] = search_zotero
```

## Debugging

### Enable Verbose Tool Logging

In `munin_client.py`, tool calls are logged:

```python
print(f"  [Tool] {tool_name}({tool_args})")
```

To see full responses, add:

```python
result = execute_tool(tool_name, tool_args)
print(f"  [Result] {result[:500]}...")  # First 500 chars
```

### Test Tools Independently

```bash
# Set up environment
export QDRANT_HOST=localhost
export NEO4J_PASSWORD=your_password

# Run tool directly
python -c "
from knowledge_tools import execute_tool
result = execute_tool('search_papers', {'query': 'attention mechanism', 'top_k': 3})
print(result)
"
```

## Best Practices

1. **Tool descriptions matter**: The LLM reads these to decide when to use each tool. Be specific and clear.

2. **Return structured JSON**: Makes it easy for the LLM to parse and cite.

3. **Include metadata**: Return source, confidence scores, and identifiers so the LLM can cite properly.

4. **Handle errors gracefully**: Return `{"error": "message"}` instead of raising exceptions.

5. **Keep tools focused**: One tool should do one thing well. The LLM can chain multiple tools.

6. **Limit result size**: Return concise summaries, not full documents. The LLM has context limits.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         TOOL LAYER                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  knowledge_tools.py                                              │
│  ├── TOOLS[] ─────────────────► OpenAI function calling schema  │
│  ├── search_papers() ─────────► Qdrant + SPECTER                │
│  ├── find_citing_papers() ────► Neo4j graph queries             │
│  ├── find_author_papers() ────► Neo4j graph queries             │
│  ├── get_paper_details() ─────► Qdrant payload lookup           │
│  ├── web_search() ────────────► SearXNG meta-search             │
│  └── execute_tool() ──────────► Dispatcher                      │
│                                                                  │
│  munin_client.py                                                 │
│  └── research() ──────────────► Multi-step tool calling loop    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                       DATA LAYER                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Qdrant (vectors)          Neo4j (graph)        SearXNG (web)   │
│  ├── papers collection     ├── Paper nodes      └── meta-search │
│  └── notion collection     ├── Author nodes                     │
│                            └── CITES edges                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```
