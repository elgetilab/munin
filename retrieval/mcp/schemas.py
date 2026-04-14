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
        "description": "Search the authenticated user's uploaded documents (PDFs, notes, DOCX) using semantic search over BGE-base embeddings. The user is determined automatically from the request context — do not pass a user id. When this chat is filed into a project (§21), the search automatically scopes to the project's docs first, and falls back to the user's global docs only if the project-scoped search returned zero hits; the response includes a `sources_used` field (`[\"project\"]`, `[\"project\", \"global\"]`, or `[\"global\"]`) so you can tell the user honestly whether the result came from their project or from the broader corpus. Pass `project_id=null` to force a user-global search ignoring the project. Returns matching text chunks with filename, chunk index, score, and content.",
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
                },
                "project_id": {
                    "type": ["string", "null"],
                    "description": "Override the auto-scoped project. Pass null to force a user-global search; pass a specific project id to search only that project; omit entirely to inherit the current conversation's project (the common case)."
                }
            },
            "required": ["query"]
        }
    },
    "transcribe_equation": {
        "name": "transcribe_equation",
        "description": "Transcribe an equation from an uploaded image as LaTeX source. Use this when the user attaches a screenshot/photograph of an equation from a paper and wants it in editable LaTeX form - 'give me the LaTeX for this equation', 'transcribe this formula', 'copy this into my notes'. Pass the document_id of an image the user has uploaded in this conversation (you can find document_ids in the '[Attachments on this message: ...]' markers on earlier turns, or in the result of list_projects / list_attachments). Returns {latex: '...', image_ref: '...'}. If the image contains no mathematics, latex will be null and a message field will explain. Only image documents (png/jpeg/webp) are supported.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_ref": {
                    "type": "string",
                    "description": "The document_id of an uploaded image to transcribe."
                }
            },
            "required": ["image_ref"]
        }
    },
    "view_attachment": {
        "name": "view_attachment",
        "description": "Re-attach an image the user uploaded earlier in this conversation so you can look at it again. Use this when the user asks a follow-up about a previously-attached image ('what colour was the shape in the screenshot I sent?', 'look at that figure again and tell me...'). You learn the document_id of past attachments from the inline '[Attachments on this message: doc_xxx (filename, mime)]' markers that appear in earlier turns' content. The image will be shown to you as a multimodal user message on the NEXT tool-loop iteration (not in this tool's return value, which is just a metadata marker). Only works for image attachments; non-image docs return an error. The tool is always available in persistent chats and not in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The document id of the attachment you want to see again. Copy it from the '[Attachments on this message: ...]' marker on an earlier turn."
                }
            },
            "required": ["document_id"]
        }
    },
    "create_artifact": {
        "name": "create_artifact",
        "description": "Start a new versioned document in the current conversation (§22 artifacts). Use this when the user asks you to write something longer than a short reply that they'll want to ITERATE on: a paper abstract, a grant proposal section, a LaTeX manuscript, a python script, an SVG diagram, a reviewer-response letter, a bibliography entry. The artifact appears in the side panel on the right of the chat and the user can edit it directly. Subsequent edits from you go through update_artifact; subsequent user edits come back to you via the next turn's === ACTIVE ARTIFACTS === block (which is already in your system prompt). Do NOT use create_artifact for quick inline answers, short code snippets that the user just wants to copy once, or anything the user did not ask you to PRODUCE as a document. content_type should be one of: text/markdown, text/latex, text/plain, text/html, application/python, application/json, image/svg+xml (SVG source as XML, not a binary image). language is optional syntax-highlighting hint (e.g. 'python', 'latex'). Returns {id, version: 1, title, content_type, ...}. 500 KB byte cap per version. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short human-readable title for the artifact (max 200 chars), e.g. 'Kinase inhibitor abstract' or 'Figure 3 plot script'."
                },
                "content": {
                    "type": "string",
                    "description": "The initial content of the document. Max 500 KB of UTF-8 text."
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type: text/markdown, text/latex, text/plain, text/html, application/python, application/json, image/svg+xml."
                },
                "language": {
                    "type": "string",
                    "description": "Optional syntax-highlighting hint for the frontend (e.g. 'python', 'latex', 'markdown'). Can be omitted."
                },
                "change_summary": {
                    "type": "string",
                    "description": "Optional short description of what this initial version contains. Max 500 chars."
                }
            },
            "required": ["title", "content", "content_type"]
        }
    },
    "read_artifact": {
        "name": "read_artifact",
        "description": "Load the full current (or a specific past) version of an artifact in the current conversation. Use this when you need to see the actual content of an artifact to reason about it, quote it, or decide how to update it. The === ACTIVE ARTIFACTS === block in your system prompt shows only titles and word counts to keep context overhead bounded - this tool is the on-demand fetch for the content itself. For the current draft, omit the version argument; for a historical version, pass version=N where N is between 1 and the artifact's latest_version.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The id of the artifact to read, taken from the === ACTIVE ARTIFACTS === block or from a previous create_artifact/list_artifacts result."
                },
                "version": {
                    "type": "integer",
                    "description": "Optional specific version number. Omit to get the latest."
                }
            },
            "required": ["artifact_id"]
        }
    },
    "update_artifact": {
        "name": "update_artifact",
        "description": "Append a new version to an existing artifact. Two modes: (1) default full-content mode sends the COMPLETE new document in `content` - use this for new drafts, heavy restructures, or small documents where the whole thing is cheap to re-send; (2) `is_diff=True` mode sends a unified diff in `content` that the backend applies to `base_version` - use this for small edits to long documents to save tokens (e.g. a one-paragraph tweak to a 50 KB paper draft becomes a ~1 KB diff). In diff mode, the diff must be a standard unified diff (`@@ -old,len +new,len @@` hunks with space/minus/plus-prefixed lines), line numbers must be correct, and context/removal lines must match the source exactly - strict matching only, no fuzz. Always pass `base_version` to whichever version you just read via read_artifact; if someone else has updated the artifact since then, you'll get a clear stale-base error and should re-read and retry instead of clobbering. change_summary is a short description of WHAT changed in this version. Returns {id, version, base_version, applied_hunks, lines_added, lines_removed, ...}. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The id of the artifact to update."
                },
                "content": {
                    "type": "string",
                    "description": "Either the full new content (default, `is_diff=False`) OR a unified diff to apply to the base version (`is_diff=True`). Max 500 KB either way; in diff mode the cap is checked against the RESULT of applying the diff, not the diff text itself."
                },
                "is_diff": {
                    "type": "boolean",
                    "description": "If true, `content` is a unified diff to apply; if false (default), `content` is the complete replacement text.",
                    "default": False
                },
                "base_version": {
                    "type": "integer",
                    "description": "Optional but strongly recommended: the version number you just read and are basing this update on. If this differs from the artifact's current latest_version, the call is rejected with a stale-base error so you can re-read and retry instead of silently overwriting a concurrent edit. In diff mode, the diff is applied against this version's content."
                },
                "change_summary": {
                    "type": "string",
                    "description": "Short one-line description of what changed in this version (max 500 chars). Helps the user and future-you navigate the version history."
                }
            },
            "required": ["artifact_id", "content"]
        }
    },
    "list_artifacts": {
        "name": "list_artifacts",
        "description": "List all artifacts in the current conversation. Usually you do NOT need this - the === ACTIVE ARTIFACTS === block in your system prompt already shows the same summary. Use it only when the user asks 'what documents am I working on?' or 'show me everything you have open' and you want to return a fresh list. Returns {artifacts: [{id, title, content_type, source, latest_version, word_count, filename, external_url, ...}], total}. The `source` field is either 'model_written' (you created it via create_artifact/update_artifact) or 'sandbox_generated' (run_python produced it as a file output).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    "save_artifact_to_documents": {
        "name": "save_artifact_to_documents",
        "description": "Promote an artifact (model-written OR sandbox-generated) into the user's persistent documents store so it can be RAG-searched in future conversations and referenced later via `document:<doc_id>` image attachments. Use this when the user says things like 'save this as a note', 'keep this for later', 'add this to my documents', or when you've produced a figure/spreadsheet they're likely to reference in another chat. Does NOT copy artifacts between conversations on its own - it puts them in the global user_docs store where the user can then reference them anywhere. `filename` is optional and will be derived from the artifact title + content_type if omitted (e.g. 'Kinase abstract v1' with text/markdown becomes 'Kinase abstract v1.md'). Returns {saved, artifact_id, source, document_id, filename, status}. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The id of the artifact to promote. Can be either a model-written artifact (from create_artifact) or a sandbox-generated file (from run_python)."
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename for the saved document. If omitted, derived from the artifact's title and content_type. Must NOT contain slashes."
                }
            },
            "required": ["artifact_id"]
        }
    },
    "remember": {
        "name": "remember",
        "description": "Store a persistent fact about the user across conversations. Use this sparingly and only for facts that will matter in FUTURE chats: who the user is (field, role, affiliation), their long-term preferences (citation style, language, tone, units), ongoing projects they'll want you to recall next session, and anything they explicitly ask you to 'remember'. Do NOT use it for ephemeral conversation context, one-off questions, or things that belong in the current chat only. Keys should be short snake_case labels (e.g. 'research_area', 'citation_style', 'preferred_plot_style'). Values are capped at 200 characters. The store is bounded at 20 entries per user - if full, the oldest entry is auto-evicted and its key is returned in the 'evicted' field. Complementary to the user profile (user-curated via settings); memory is model-curated via this tool. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Short snake_case label for the fact (max 100 chars), e.g. 'research_area' or 'preferred_citation_style'."
                },
                "value": {
                    "type": "string",
                    "description": "The fact itself, at most 200 characters. Concise and factual, not a full sentence of explanation."
                }
            },
            "required": ["key", "value"]
        }
    },
    "forget": {
        "name": "forget",
        "description": "Delete a specific remembered fact about the user. Use this when the user says something that contradicts an existing memory, when they explicitly ask you to forget something, or when a fact has become stale (e.g. they changed jobs). Returns {forgotten: bool, key: ..., total_memories: N}. No-op if the key doesn't exist. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The exact key of the memory to delete."
                }
            },
            "required": ["key"]
        }
    },
    "recall": {
        "name": "recall",
        "description": "Retrieve remembered facts about the user. All existing memories are already included in your system prompt under '=== WHAT YOU REMEMBER ABOUT THIS USER ===' so you rarely need this tool - use it only when the user asks 'what do you remember about me?' and you want to show them the full list, or when you need to search memories by substring. Pass a 'search' string to filter by case-insensitive substring match against keys and values; omit for the full list. Returns {memories: [...], total: N, search: ...}. Not available in ephemeral chats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Optional case-insensitive substring to filter by. Matches both keys and values."
                }
            },
            "required": []
        }
    },
    "list_projects": {
        "name": "list_projects",
        "description": "List the user's projects (workspaces that group conversations + docs + instructions together). Returns names, descriptions, conversation counts, and default personas. Use this when the user asks 'what projects do I have?' or when deciding which project to suggest filing a conversation into. Archived projects are excluded.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    "get_current_project": {
        "name": "get_current_project",
        "description": "Return the project the current conversation is filed into, or {\"project\": null} if this chat is unfiled. Use when the user asks 'what project am I in?', 'what are this project's instructions?', or when you want to reference the workspace explicitly. The project's instructions and description are already injected into your system prompt when a project is active, but this tool lets you quote them back verbatim if needed.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
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
    },
    "run_python": {
        "name": "run_python",
        "description": "Execute Python code in a sandboxed Jupyter kernel scoped to the current conversation. Use this whenever the user asks you to compute, plot, analyse data, generate a spreadsheet, or otherwise do something a Python script could do better than prose. State persists between calls in the same chat (variables, imports, dataframes), and files written to the current working directory become artifacts the user can download. The sandbox has no internet, no GPU, no host filesystem; pre-installed packages: numpy, scipy, pandas, matplotlib, seaborn, scikit-learn, sympy, networkx, openpyxl, Pillow, pyyaml, requests. Resource caps: 30 s default wall clock, 2 GB memory, 100 MB max file size. matplotlib figures created with `plt.show()` or display() are auto-captured as PNG artifacts. Do NOT call this tool for trivial arithmetic - use `calculate` instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python source to execute. Multi-line is fine."
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Per-execution wall clock timeout in seconds (default 30, max 120). Increase only when you genuinely need it.",
                    "default": 30
                }
            },
            "required": ["code"]
        }
    },
    "sandbox_reset": {
        "name": "sandbox_reset",
        "description": "Restart the current conversation's Jupyter kernel, wiping all in-memory state (variables, imports, open files). Files in the conversation's scratch directory survive the reset. Use this when previous code left the kernel in a bad state, when you want a clean namespace, or when the user explicitly asks you to start over.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}
