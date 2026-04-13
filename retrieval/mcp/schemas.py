"""
MCP Tool Definitions - JSON Schema format for MCP protocol.

Each tool has:
- name: Unique identifier
- description: What the tool does
- inputSchema: JSON Schema for parameters
"""

MCP_TOOLS = {
    "web_search": {
        "name": "web_search",
        "description": "Search the web using SearXNG meta-search engine with multi-query fan-out. For the best coverage, pass a `queries` array of 3-5 varied phrasings instead of a single `query` — the tool runs them in parallel, dedupes by URL, and returns results ranked by how many queries surfaced each URL. If you pass only `query` (a single string), the backend will expand it into 3-5 variants for you automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Single search query. Backend will auto-expand into 3-5 variants. Ignored when `queries` is provided."
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit list of 2-6 search queries to run in parallel. Takes precedence over `query` when provided. Use this when you have multiple specific angles to cover."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum deduped results to return (default: 10)",
                    "default": 10
                }
            }
        }
    },
    "paper_search": {
        "name": "paper_search",
        "description": "Search the local scientific paper corpus using SPECTER semantic search with multi-query fan-out. Pass `queries` as an array of 3-5 varied phrasings for best coverage; the tool runs them in parallel and dedupes by DOI. Passing a single `query` string triggers automatic expansion into 3-5 variants. Returns papers with titles, authors, years, DOIs, and scores.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Single search query. Backend will auto-expand into 3-5 variants. Ignored when `queries` is provided."
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit list of 2-6 paper search queries to run in parallel. Takes precedence over `query`."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum deduped results to return (default: 5)",
                    "default": 5
                }
            }
        }
    },
    "semantic_scholar_search": {
        "name": "semantic_scholar_search",
        "description": "Search Semantic Scholar (200M+ papers across all fields) with multi-query fan-out. Returns papers with titles, authors, DOIs, citation counts, abstracts, and AI-generated TLDRs. Pass `queries` as a list of 3-5 varied search phrasings for broad coverage, or a single `query` string which will be auto-expanded. Takes an optional `year` filter applied to every query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Single search query. Backend will auto-expand into 3-5 variants. Ignored when `queries` is provided."
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit list of 2-6 search queries to run in parallel. Takes precedence over `query`."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum deduped results to return (default: 10, max: 100)",
                    "default": 10
                },
                "year": {
                    "type": "string",
                    "description": "Optional year filter applied to every query (e.g., '2020-2024', '2024-', '2024')",
                    "default": ""
                }
            }
        }
    },
    "paper_lookup": {
        "name": "paper_lookup",
        "description": "Look up detailed information about a specific paper by DOI. Returns full metadata including title, authors, journal, year, abstract, and citation counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper to look up (e.g., '10.1038/s41586-021-03819-2')"
                }
            },
            "required": ["doi"]
        }
    },
    "llm_summarize": {
        "name": "llm_summarize",
        "description": "Use an LLM (Qwen3-30B) to summarize or extract information from text. Useful for processing long documents or extracting specific information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to process"
                },
                "instruction": {
                    "type": "string",
                    "description": "Instructions for how to process the text (e.g., 'Summarize in 3 bullet points', 'Extract key findings')"
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens in response (default: 16384)",
                    "default": 16384
                }
            },
            "required": ["text", "instruction"]
        }
    },
    "web_fetch": {
        "name": "web_fetch",
        "description": "Fetch a URL, extract its main text content, and return a CONDENSED SUMMARY (not the raw page). Long pages are automatically split into sections and summarised in parallel by multiple LLM calls, then composed into one coherent summary — so raw web content never bloats the conversation context. Use this whenever you need to read the content of a web page. Pass `summary_instruction` to bias what the summariser focuses on (e.g. 'focus on methodology', 'extract only the conclusions about X').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch and summarise"
                },
                "summary_instruction": {
                    "type": "string",
                    "description": "Optional bias for the summariser: what to focus on or extract. Applies to both per-section and final summaries.",
                    "default": "Summarize the main points and key findings, focusing on factual content."
                }
            },
            "required": ["url"]
        }
    },
    "get_citations": {
        "name": "get_citations",
        "description": "Get papers that cite a given paper. Useful for finding follow-up research.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper to find citations for"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of citing papers to return (default: 20)",
                    "default": 20
                }
            },
            "required": ["doi"]
        }
    },
    "get_references": {
        "name": "get_references",
        "description": "Get papers cited by a given paper (its references). Useful for finding foundational research.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of references to return (default: 50)",
                    "default": 50
                }
            },
            "required": ["doi"]
        }
    },
    "get_author_papers": {
        "name": "get_author_papers",
        "description": "Get all papers by a given author. Supports partial name matching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "author_name": {
                    "type": "string",
                    "description": "The author's name (partial match supported)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of papers to return (default: 50)",
                    "default": 50
                }
            },
            "required": ["author_name"]
        }
    },
    "get_paper_pdf": {
        "name": "get_paper_pdf",
        "description": "Check if a PDF is available for a paper and get the download URL. If not available, provides alternative sources like Sci-Hub.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper (e.g., '10.1038/nature12373')"
                }
            },
            "required": ["doi"]
        }
    },
    "check_papers_availability": {
        "name": "check_papers_availability",
        "description": "Check PDF availability for multiple papers at once. Returns lists of available and not available papers with download URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dois": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of DOIs to check (max 20)"
                }
            },
            "required": ["dois"]
        }
    },
    "search_user_docs": {
        "name": "search_user_docs",
        "description": "Search the authenticated user's uploaded documents (PDFs, notes, DOCX) using semantic search over BGE-base embeddings. The user is determined automatically from the request context — do not pass a user id. Returns matching text chunks with filename, chunk index, score, and content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    "invoke_agent": {
        "name": "invoke_agent",
        "description": "Delegate a task to a named agent workflow defined in agents.yml. Agents run their own tool-calling loop with guardrails and return a structured answer. Use this when a request is substantially more work than a single tool call — deep literature review, structured code reviews, long-form writing with citations. The `agent` must be one of the registered agent names listed in the system prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Registered agent name (e.g. research_orchestrator)"
                },
                "query": {
                    "type": "string",
                    "description": "The task or question to hand to the agent"
                }
            },
            "required": ["agent", "query"]
        }
    }
}
