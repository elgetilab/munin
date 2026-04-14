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
        "description": "Look up detailed information about a specific paper by DOI. Cascades through three sources in order: (1) local Qdrant papers corpus — fastest, carries citation-graph counts from Neo4j, (2) Semantic Scholar — richest metadata with TLDR summaries and open-access PDF links when available, (3) Crossref — most reliable for recent DOIs. The `source` field in the response tells you which source the data came from. Only fails if all three miss. Safe to call on any DOI; no need to restrict to papers in the local corpus.",
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
    "search_past_conversations": {
        "name": "search_past_conversations",
        "description": "Full-text search over the authenticated user's OWN past conversations on this server. Use this when the user mentions something from a prior chat ('didn't we discuss X?', 'what was that paper I found last week?', 'I asked about Y earlier'). Returns matching message snippets with the conversation id, title, persona, role of the matching message, and a pinned flag. Pinned conversations are boosted in ranking. Does NOT search the current conversation by default — that is already in your context. The user is determined automatically from the request context; do not pass a user id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Plain words work; FTS5 syntax (phrase queries, NEAR, OR) also works."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of matching messages to return (default: 5, max: 20).",
                    "default": 5
                },
                "persona": {
                    "type": "string",
                    "description": "Optional persona id filter (e.g. 'research', 'chat'). Omit to search across all personas."
                },
                "include_current": {
                    "type": "boolean",
                    "description": "If true, allow results from the current conversation. Default false because the current chat is already in your context.",
                    "default": False
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
    },
    "calculate": {
        "name": "calculate",
        "description": "Evaluate a mathematical expression precisely. ALWAYS prefer this tool over computing in your head — LLMs make silent arithmetic errors and researchers notice. Three modes: 'numeric' for arithmetic / percentages / trig / logs / arbitrary precision (e.g. '17% of 450', 'sqrt(2)*pi/log(10)', '2**1024'); 'symbolic' for sympy operations like derivatives, integrals, equation solving, limits, simplification (e.g. 'diff(sin(x)**2, x)', 'integrate(exp(-x**2), (x, -oo, oo))', 'solve(x**2 - 4, x)'); 'physical' for unit-aware arithmetic with built-in physical constants and unit conversions via pint (e.g. '8.6 MJ to kcal', '1 eV to J', 'boltzmann_constant * 310 K to eV', 'planck_constant * speed_of_light / (500 nm) to eV'). Use 'physical' whenever units are involved. Use 'symbolic' when the user wants an exact answer or a symbolic operation. Otherwise use 'numeric'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The expression to evaluate. Format depends on mode — see tool description for examples per mode."
                },
                "mode": {
                    "type": "string",
                    "enum": ["numeric", "symbolic", "physical"],
                    "description": "Evaluation mode. Default: numeric.",
                    "default": "numeric"
                }
            },
            "required": ["expression"]
        }
    },
    "export_citations": {
        "name": "export_citations",
        "description": "Export formatted bibliographic citations for a list of DOIs. Hits doi.org content negotiation in parallel and returns the formatted strings, ready to drop into Zotero, Mendeley, EndNote, or a LaTeX bibliography. Use this whenever the user asks for citations in a specific format ('give me BibTeX for these papers', 'export these as RIS', 'format these in APA style'). Supported formats: bibtex, ris, csl-json, apa, chicago, nature, ieee, vancouver. Up to 50 DOIs per call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dois": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of DOI strings (without https://doi.org/ prefix). Max 50 per call."
                },
                "format": {
                    "type": "string",
                    "enum": [
                        "bibtex", "ris", "csl-json",
                        "apa", "chicago", "nature", "ieee", "vancouver"
                    ],
                    "description": "Citation format. Default: bibtex.",
                    "default": "bibtex"
                }
            },
            "required": ["dois"]
        }
    },
    "deep_research": {
        "name": "deep_research",
        "description": "One-shot composite research tool. Decomposes a research question into sub-questions, expands each into search query variants, fans out across the local paper corpus + Semantic Scholar + the web in parallel, and fetches + map-reduce-summarises the top web pages — all in a single deterministic call. Returns a structured dict with {sub_questions, queries_executed, papers, web_sources, web_summaries, sources_used}. USE THIS INSTEAD OF CALLING web_search / paper_search / web_fetch SEPARATELY when the user asks a substantive research question (\"tell me about X\", \"what's the state of the art in Y\", \"find papers about Z and summarise them\"). Much more efficient than firing individual tool calls because it reuses the same query pool across all three sources and runs everything in parallel. For short factual lookups or single-source queries, still prefer the individual search tools — deep_research is heavier. Use depth=\"deep\" for exhaustive coverage (5 sub-questions, 5 fetches); default \"medium\" (3 sub-questions, 3 fetches) is the right choice for most questions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The research question or topic to investigate. Should be a full question or topic, not a single search term."
                },
                "depth": {
                    "type": "string",
                    "enum": ["medium", "deep"],
                    "description": "Coverage level. 'medium' = 3 sub-questions, 3 web fetches (~15-30s). 'deep' = 5 sub-questions, 5 web fetches (~30-90s). Default: medium.",
                    "default": "medium"
                }
            },
            "required": ["question"]
        }
    }
}
