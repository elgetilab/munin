"""
MCP Tool Definitions - JSON Schema format for MCP protocol.

Each tool has:
- name: Unique identifier
- description: What the tool does
- inputSchema: JSON Schema for parameters
"""

# P1 #7 — deferred tool schema. Only CORE_TOOLS ship in the vLLM `tools`
# array by default; everything else is discoverable at runtime via
# `tool_search`, which unlocks matches into the per-request schema. This
# keeps the schema small (~9 tools vs 39) so prefill stays well clear of
# the hang cliff. CORE is intersected with the persona allowlist in
# `_openai_tools_schema`, so a persona that lacks a core tool simply
# doesn't get it. Membership rationale: control-flow tools
# (ask_clarification, tool_search) must always be present; the rest are
# the high-frequency workhorses.
CORE_TOOLS = frozenset({
    # Specialised agents (the primary read/find/compute surface). `source`
    # supersedes read_paper+compare_papers (both retired as tools; source
    # covers them via mode=summary / mode=compare); `search` consolidates the
    # three search tools; `compute` is spec->verified-code.
    # paper_search/web_search were REMOVED from core 2026-08-27. They stayed
    # visible next to the very tool whose description says it supersedes them,
    # so the model had four overlapping discovery tools and chained them by
    # hand: the iLOV reproducer used search x1, paper_search x1,
    # semantic_scholar_search x1, web_search x8, web_fetch x8, source x7 for one
    # factual question. Both remain reachable through `tool_search`, and every
    # internal caller (search_agent, source, research) invokes them as Python
    # functions rather than as tools, so nothing internal changes. `web_search`
    # is now resident on the CHAT profile only, where a light one-shot web
    # lookup is the right primitive and a three-tier `search` is overkill.
    "source",
    "search",
    "compute",
    # web_search was removed from core 2026-08-27 and RESTORED the same day.
    # Removing it did not stop the model using it: across 3 reproducer runs it
    # was still called 7, 7 and 11 times, reached through `tool_search`, so the
    # only effect was extra discovery calls. The tool menu was never the
    # constraint; ~60% of the calls in every run are READS (web_fetch + source),
    # which is what `search` could not do. Escalation now lives inside `search`.
    "web_search",
    "run_python",
    "create_artifact",
    "calculate",
    "ask_clarification",
    "tool_search",
    # P2 #24 Phase 1: plan-mode tools. Always visible to every
    # persona so the model can call set_plan at turn start without
    # first discovering the tool via tool_search. Cost: ~600 extra
    # prefill tokens; worth it for a high-frequency scaffolding tool.
    "set_plan",
    "update_plan_item",
})

MCP_TOOLS = {
    "web_search": {
        "name": "web_search",
        "description": "Search the web using SearXNG meta-search engine with multi-query fan-out. For the best coverage, pass a `queries` array of 3-5 varied phrasings instead of a single `query` - the tool runs them in parallel, dedupes by URL, and returns results ranked by how many queries surfaced each URL. If you pass only `query` (a single string), the backend will expand it into 3-5 variants for you automatically.\n\nDEGRADED-BACKEND HANDLING: the result dict may contain an `engines_unresponsive` field listing engines that failed on this call (rate-limited, blocked, or behind a CAPTCHA). When ALL engines fail and `total_hits` is 0, the result also includes a `warning` field. If you see `warning`, do NOT conclude that the topic is obscure or doesn't exist - the tool is broken. Surface the degradation to the user (something like 'my web search backend is currently unable to reach its engines') and ask them for a direct URL, DOI, or arxiv ID if they have one. Do not retry the same call expecting a different outcome; if you must retry, wait or try a paper_search / semantic_scholar_search instead.",
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
        "description": "Search the LOCAL curated paper corpus (fast; the corpus is deliberately curated and INCOMPLETE, so branch out to semantic_scholar_search when hits are thin or the topic is off-corpus) using SPECTER semantic search with multi-query fan-out. Pass `queries` as an array of 3-5 varied phrasings for best coverage; the tool runs them in parallel and dedupes by DOI. Passing a single `query` string triggers automatic expansion into 3-5 variants. Returns papers with titles, authors, years, DOIs, scores, and a short abstract excerpt you can ground claims on and use to judge relevance before calling source to read the full text.",
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
        "description": "Search the EXTERNAL Semantic Scholar index (200M+ papers across all fields; broader coverage than the local corpus but slower and it uses the S2 quota, so prefer paper_search first and branch here when local hits are thin or off-corpus) with multi-query fan-out. This is for SEARCH by topic. When the user hands you a specific DOI to read, use source (mode='summary') - do NOT search here for a paper you can already identify. Returns papers with titles, authors, DOIs, citation counts, abstracts, and AI-generated TLDRs. Pass `queries` as a list of 3-5 varied search phrasings for broad coverage, or a single `query` string which will be auto-expanded. Takes an optional `year` filter applied to every query.",
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
    "source": {
        "name": "source",
        "description": "Read 1..N documents and return a GROUNDED result with a stated outcome. This is the unified read agent: it supersedes read_paper (use mode='summary') and compare_papers (use mode='compare'), and adds two modes those lacked. mode='qa' answers a SPECIFIC question from the paper's FULL text (not a lossy summary) - use this when the user wants a precise value, number, or result that lives in the body, a table, or a figure caption ('what buried surface area did they report?', 'what was the fold-change in condition X?'); it quotes the exact supporting sentence or returns outcome='not_found' rather than guessing. mode='extract' pulls structured rows against a schema and returns a data HANDLE + a 5-row preview (never the full rows) for the compute agent to plot. `refs` is a list of tagged references - {\"doi\":\"...\"}, {\"arxiv\":\"...\"}, {\"url\":\"...\"}, or {\"title\":\"...\"} (fuzzy; may return outcome='ambiguous' with candidates). Every call returns an `outcome` (resolved | not_found | ambiguous | extraction_failed | out_of_scope | unresolved) so you always know WHICH way it went. Prefer this over read_paper/compare_papers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tagged references to read, e.g. [{\"doi\": \"10.1038/nature12373\"}] or [{\"title\": \"AlphaFold structure prediction\"}]. Bare DOI strings are accepted too."
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "qa", "findings", "extract", "compare"],
                    "description": "summary = narrative + key findings (like read_paper); qa = answer `question` from full text; findings = up to 4 distinct grounded findings (claim + verbatim quote) for `question`; extract = structured rows against `schema`, handle-only; compare = side-by-side markdown over 2-5 refs.",
                    "default": "summary"
                },
                "question": {
                    "type": "string",
                    "description": "Required for mode='qa' (the specific question to answer from the full text); optional for mode='extract' to steer what to pull."
                },
                "focus": {
                    "type": "string",
                    "description": "Optional topic bias for summary/compare, e.g. 'statistical methods', 'the mouse cohort'."
                },
                "schema": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional column list for mode='extract'. Omit on the first call to get a proposed schema + preview, then call again with the finalized columns."
                }
            },
            "required": ["refs"]
        }
    },
    "search": {
        "name": "search",
        "description": "Find and rank the evidence relevant to a topic across three tiers in one call: the local curated corpus (corpus_paper), external open-access papers via Semantic Scholar (oa_paper), and the web (web). Supersedes paper_search + semantic_scholar_search + web_search for the 'find me sources' intent. Every hit carries a `source_type` so you can judge trust (a Nature paper and an SEO listicle are not comparable on score); the ranker prefers the corpus, then OA, and caps web. Deduplicates across tiers on the alias set (DOI / arXiv / title), so the preprint and the published version collapse to one. Returns {ranked: [{ref, title, snippet, score, source_type, ...}], coverage_note, thin_evidence, counts}. If a sub-corpus scope is active (e.g. a #group tag), `coverage_note` reports 'X in scope, Y consortium-wide' so a scoping gap is not misread as a missing paper. `thin_evidence: true` warns that few strong scholarly hits were found rather than padding with weak ones. Use depth='deep' to include the web tier. THIS IS THE PRIMARY RESEARCH TOOL: use it for any 'find sources / what does the literature say / look this up' intent. It already runs the corpus, Semantic Scholar and web tiers for you and expands your query into variants internally, so do NOT hand-chain paper_search, semantic_scholar_search or web_search after it, and do NOT pass a 'queries' list (it takes a single natural-language 'query').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic or question to find evidence for. Automatically expanded into query variants across each tier."
                },
                "depth": {
                    "type": "string",
                    "enum": ["normal", "deep"],
                    "description": "normal = start local (corpus, then Semantic Scholar) and escalate to the web only if those come up short; deep = include the web from the start. Default normal, which is usually right: the tool escalates on its own.",
                    "default": "normal"
                },
                "read": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3,
                    "description": "Open up to N of the best hits and answer the query FROM THEIR FULL TEXT, not just the snippet. Use read=2 whenever you need a specific value, number, measurement or property (an extinction coefficient, a Kd, a rate constant): those live in the body of a paper and are never in the snippet. Reads run one at a time and stop at the first document that actually answers, so this usually costs one extra call, not N. Returns an `answers` array alongside `ranked`. Prefer this over calling source or web_fetch yourself afterwards.",
                    "default": 0
                },
                "filters": {
                    "type": "object",
                    "description": "Optional {year: '2020-2024'} applied to the scholarly tier, and {tags: [...]} to override the active sub-corpus scope."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max ranked results to return. Default 10.",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    "compute": {
        "name": "compute",
        "description": "Turn a natural-language spec into code, run it in the network-off sandbox, and return a reproducible bundle (code + data + figure) with a stated verification level. Best for plots and quick analyses ('plot the fold-change by condition', 'fit a curve and show residuals'). Routing is deterministic: language='python' runs in the sandbox (verify_level='executed' - it actually ran and produced a figure); other languages come back generated but not executed. If the data comes from a paper, pass the `data_handle` from source(mode='extract') - never paste the numbers yourself; the agent materialises the rows into the sandbox so provenance is preserved. Returns {code (byte-identical to what ran, reads data.csv from its own dir, seeded RNG), language, verify_level (executed|returned|failed), ran, has_figure, artifacts (rendered figure comes back for a glance-check), attempts, errors}. A bounded repair loop fixes runtime errors and stops if it gets stuck. Note: 'runs' is not 'correct' - eyeball the returned figure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "What to compute or plot, in natural language."
                },
                "data_handle": {
                    "type": "string",
                    "description": "Optional handle from source(mode='extract') whose rows should be materialised as data.csv in the sandbox. Do NOT transcribe values into the spec."
                },
                "language": {
                    "type": "string",
                    "description": "Programming language. 'python' (default) executes in the sandbox; others are generated but not run.",
                    "default": "python"
                },
                "budget": {
                    "type": "string",
                    "enum": ["quick", "compute"],
                    "description": "quick (~15s, default, the plot case) or compute (longer, for heavier numerics).",
                    "default": "quick"
                }
            },
            "required": ["spec"]
        }
    },
    "get_citations": {
        "name": "get_citations",
        "description": "Get papers that cite a given paper. Queries the LOCAL Neo4j citation graph built from the curated paper corpus - fast but only covers papers in our corpus. If the user wants broader coverage across the whole Semantic Scholar graph (~200M papers), use `s2_get_citations` instead.",
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
        "description": "Get papers cited by a given paper (its references). Queries the LOCAL Neo4j citation graph built from the curated paper corpus - fast but only covers papers in our corpus. If the user wants broader coverage across the whole Semantic Scholar graph (~200M papers), use `s2_get_references` instead.",
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
    "s2_get_citations": {
        "name": "s2_get_citations",
        "description": "Papers that CITE a given paper, from the full Semantic Scholar graph (~200M papers). Use this when the user asks 'who cites this?', 'what papers build on X?', 'what's the follow-up work?'. Broader than `get_citations` (which only sees our curated local corpus) but slower (two S2 API calls per invocation). Returns `total_citations` (the full S2 count) plus a list of `citations` with DOI, title, authors, year, citation_count, and openAccessPdf URL. (Note: S2's /citations endpoint does not serve tldr summaries - call paper_lookup(doi) on specific entries if you need the tldr.) Any citation whose DOI is in our local corpus also gets a `download_url` + `local_pdf_available: True` so you can direct the user to the local PDF. Pass `year_from` to filter to recent citers ('papers since 2023 that cite X'). Pass `include_contexts: true` to get the actual sentences where each citing paper references the original (useful for 'how is this cited' questions). Max `limit` is 100.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper whose citations you want."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of citing papers to return (default 50, hard cap 100).",
                    "default": 50
                },
                "year_from": {
                    "type": "integer",
                    "description": "Optional: only return citing papers published in this year or later."
                },
                "include_contexts": {
                    "type": "boolean",
                    "description": "If true, each result includes the `contexts` field with the actual sentence(s) where the citing paper references the original. Costs a little more payload but is valuable for 'how is X cited' questions.",
                    "default": False
                }
            },
            "required": ["doi"]
        }
    },
    "s2_get_references": {
        "name": "s2_get_references",
        "description": "Papers that the given paper CITES (its reference list), from the full Semantic Scholar graph. Use when the user asks 'what does this paper cite?', 'what did the authors build on?', 'what's the foundational work for paper X?'. Broader than `get_references` (which only sees our curated local corpus). Returns `references` with DOI, title, authors, year, citation_count, openAccessPdf, and `download_url` + `local_pdf_available: True` for any reference that happens to be in the local corpus. (Note: S2's /references endpoint does not serve tldr summaries - call paper_lookup(doi) on specific entries if you need the tldr.) `include_contexts: true` includes the sentence(s) where each reference is cited. Max `limit` is 100.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper whose references you want."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of references to return (default 50, hard cap 100).",
                    "default": 50
                },
                "include_contexts": {
                    "type": "boolean",
                    "description": "If true, each result includes the `contexts` field with the sentence(s) where the source paper references it.",
                    "default": False
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
    "list_documents": {
        "name": "list_documents",
        "description": "List, enumerate, browse or inventory ALL documents the user has uploaded, without a search query. Answers 'how many papers did I upload', 'list my documents', 'what is in my knowledge base', 'catalog my files', 'show everything I have uploaded'. Use this INSTEAD of guessing topics and running search_user_docs repeatedly: that is a semantic search, so zero results mean the query missed, whereas an empty result here means the store really is empty. Returns filename, document_id, chunk count, upload time and a `status` per document plus a `total`. status 'embedded' means search_user_docs can reach it; status 'stored' means the file exists but has no embeddings, which is normal for images and indicates failed text extraction for anything else. When `total` exceeds what is returned, say so rather than presenting the page as the complete list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum documents to return (default: 50, max: 200). `total` always reports the true count.",
                    "default": 50
                }
            }
        }
    },
    "browse_tag_papers": {
        "name": "browse_tag_papers",
        "description": "List, enumerate, browse or catalog the papers in a corpus collection - a research group, a topic cluster, or one contributor - with no search query and no ranking. Answers 'list the first 10 papers in the Deibel group', 'what is in the attached knowledge', 'show me everything this group has contributed', 'inventory this topic'. Use this INSTEAD of paper_search whenever the user wants the CONTENTS of a collection rather than papers relevant to a question: a similarity search can only return a sample and presenting that as a list is wrong. `kind` is topic/group/contributor and `slug` is the tag value; BOTH may be omitted when exactly one #tag scope is attached to the conversation, in which case that collection is browsed. Paginate with `offset`. Returns paper stubs (title, doi, year, authors, contributors, download_url) plus `total`, the real size of the collection - always report the page size against that total rather than implying you saw everything.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["topic", "group", "contributor"],
                    "description": "Which kind of collection. Omit to use the conversation's single attached scope tag."
                },
                "slug": {
                    "type": "string",
                    "description": "The tag value: a topic_slug, a research-group slug (e.g. 'deibel'), or a contributor username. Omit to use the conversation's single attached scope tag."
                },
                "offset": {
                    "type": "integer",
                    "description": "Papers to skip, for paging (default: 0)",
                    "default": 0
                },
                "limit": {
                    "type": "integer",
                    "description": "Papers per page (default: 10, max: 200)",
                    "default": 10
                },
                "sort": {
                    "type": "string",
                    "enum": ["year_desc", "year_asc", "upload_desc"],
                    "description": "Ordering within the page (default: year_desc). Sorting applies to the page, not corpus-wide.",
                    "default": "year_desc"
                }
            }
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
        "is_concurrency_safe": False,
        "description": "Start a new versioned document in the current conversation (§22 artifacts). Use this when the user asks you to write something longer than a short reply that they'll want to ITERATE on: a paper abstract, a grant proposal section, a LaTeX manuscript, a python script, an SVG diagram, a reviewer-response letter, a bibliography entry. The artifact appears in the side panel on the right of the chat and the user can edit it directly. Subsequent edits from you go through update_artifact; subsequent user edits come back to you via the next turn's === ACTIVE ARTIFACTS === block (which is already in your system prompt). Do NOT use create_artifact for quick inline answers, short code snippets that the user just wants to copy once, or anything the user did not ask you to PRODUCE as a document. **Do NOT call create_artifact speculatively or as a \"general-purpose template\" when a critical parameter of what the user wants is missing or unclear.** If you don't know the programming language, the input format, which variant of an ambiguous acronym the user means (EPR? HMM? MD?), or what specific analysis/section/structure they want, call `ask_clarification` FIRST on its own turn and wait for the user's answer; only THEN create the artifact with the clarified scope. Writing a speculative template artifact AND then asking clarifying questions at the end is a failure mode: the user did not want a template, they wanted the right thing. content_type should be one of: text/markdown, text/latex, text/plain, text/html, application/python, application/json, image/svg+xml (SVG source as XML, not a binary image). language is optional syntax-highlighting hint (e.g. 'python', 'latex'). Returns {id, version: 1, title, content_type, ...}. 500 KB byte cap per version. Not available in ephemeral chats.",
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
        "is_concurrency_safe": False,
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
        "is_concurrency_safe": False,
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
        "is_concurrency_safe": False,
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
        "is_concurrency_safe": False,
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
    "edit_python": {
        "name": "edit_python",
        "is_concurrency_safe": False,
        "description": "Change part of the code you last ran, then re-run it, without re-sending the whole script. Use this whenever you have already called run_python and want to fix a bug, tweak a parameter, or add a few lines - it is the normal way to iterate on code. Each edit is a literal search-and-replace against the current source: `old` must appear EXACTLY ONCE and match character-for-character including indentation, and `new` replaces it (pass an empty string to delete). No line numbers, no diff syntax. Send several edits in one call when you are changing several places. The edited source runs in the same persistent kernel, so variables and imports from earlier calls are still live. If an `old` does not match you get an error naming the closest lines - fix the edit and retry rather than falling back to re-sending the whole script. Returns the normal run_python result plus edits_applied, lines_added, lines_removed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "Replacements to apply in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old": {
                                "type": "string",
                                "description": "Exact snippet to replace. Must occur exactly once in the current source; include surrounding lines if it would otherwise be ambiguous."
                            },
                            "new": {
                                "type": "string",
                                "description": "Replacement text. Empty string deletes the snippet."
                            }
                        },
                        "required": ["old", "new"]
                    }
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Per-execution wall clock timeout in seconds (default 30, max 120).",
                    "default": 30
                }
            },
            "required": ["edits"]
        }
    },
    "run_python": {
        "name": "run_python",
        "is_concurrency_safe": False,
        "description": "Execute Python code in a sandboxed Jupyter kernel scoped to the current conversation. Use this whenever the user asks you to compute, plot, analyse data, generate a spreadsheet, or otherwise do something a Python script could do better than prose. IMPORTANT - the kernel is PERSISTENT and you should iterate incrementally: variables, imports and dataframes stay live between calls, so send only new or changed code. Once you have run something, use `edit_python` to change part of it rather than re-sending the whole script. Files written to the current working directory become artifacts the user can download, and matplotlib figures created with `plt.show()` or display() are auto-captured as PNG artifacts. When mentioning a produced file in your prose, link to it using the `external_url` from the tool result; do NOT construct URLs yourself. The sandbox has no internet, no GPU, no host filesystem; pre-installed packages: numpy, scipy, pandas, matplotlib, seaborn, scikit-learn, sympy, networkx, openpyxl, Pillow, pyyaml, requests. Resource caps: 30 s default wall clock, 2 GB memory, 100 MB max file size. Do NOT call this tool for trivial arithmetic - use `calculate` instead.",
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
        "is_concurrency_safe": False,
        "description": "Restart the current conversation's Jupyter kernel, wiping all in-memory state (variables, imports, open files). Files in the conversation's scratch directory survive the reset. Use this when previous code left the kernel in a bad state, when you want a clean namespace, or when the user explicitly asks you to start over.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    "compile_latex": {
        "name": "compile_latex",
        "is_concurrency_safe": False,
        "description": (
            "Compile a LaTeX document with pdflatex inside the sandbox "
            "and return both the source .tex file and the compiled .pdf "
            "as downloadable artifacts, plus structured compile errors "
            "when the build fails. Use this ANY time you write LaTeX for "
            "the user - a full article, a Beamer deck, a standalone "
            "figure, a table, or even just an equation wrapped in a "
            "minimal document - so you can verify it compiles before "
            "handing it over.\n\n"
            "**Three input modes — pick the cheapest one that fits the "
            "request:**\n"
            "1. `source` (full text): brand-new document. Pass the "
            "complete LaTeX in `source`.\n"
            "2. `artifact_id` (unchanged recompile): the user wants the "
            "exact same artifact recompiled (rare). Pass the id from a "
            "previous compile_latex result; the tool reads its latest "
            "version content as the source.\n"
            "3. `artifact_id` + `diff` (THE ITERATION FLOW — use this "
            "for almost every follow-up turn like \"make it 16:9\", "
            "\"change the colour to red\", \"fix this typo\"): pass "
            "the .tex artifact id from the previous compile_latex "
            "result plus a unified diff in `diff`. The tool resolves "
            "the source server-side, applies the diff, then compiles. "
            "This saves thousands of tokens vs re-emitting the full "
            "body — a one-line aspectratio tweak becomes a ~150-byte "
            "diff. Diff format mirrors the standard unified diff "
            "(`@@ -old_start,old_len +new_start,new_len @@` hunks "
            "with space/minus/plus-prefixed lines); context and "
            "removal lines must match the source EXACTLY (no fuzz). "
            "If you're not sure of the exact line content, call "
            "read_artifact first to see the latest version, then "
            "build the diff against it.\n\n"
            "Iterate on failure: read the `errors` list (structured "
            "file/line/message entries) or `log_tail` (last 50 lines "
            "of main.log), fix the source, and call compile_latex "
            "again. The sandbox has texlive-latex-base + latex-extra + "
            "latex-recommended + science + bibtex-extra + "
            "fonts-recommended, so amsmath, siunitx, physics, tikz/pgf, "
            "beamer, biblatex, and the usual class/style packages are "
            "all available. Shell-escape is disabled (`\\write18` will "
            "not work) and there is no network. Bibliography: pass a "
            "full .bib file as `bibliography` and the tool will run "
            "the pdflatex → bibtex → pdflatex → pdflatex cycle "
            "automatically. Extra files: pass `extra_files` as a "
            "filename→content dict for .cls/.bst/.sty helpers or "
            "\\includegraphics targets. Text files go as plain "
            "strings; binary files (images) must be base64 with a "
            "'base64:' prefix, e.g. {'logo.png': 'base64:iVBOR...'}. "
            "The .tex source artifact is ALWAYS returned (even on "
            "failure) so the user can download and fix it manually; "
            "the .pdf is only returned on success. Timeout default "
            "60s, max 120s. Unavailable in ephemeral chats.\n\n"
            "IMPORTANT - artifact download links: the tool result "
            "includes an `external_url` field on each artifact (tex "
            "and pdf). When you mention the PDF or .tex in your prose "
            "response, you MUST use this `external_url` value as the "
            "link target. Format: `[Download PDF](EXTERNAL_URL)`. "
            "Do NOT construct URLs yourself - do NOT use "
            "`search.muninai.org/paper/...` or any other URL pattern. "
            "The `external_url` is the ONLY correct path to the file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Full contents of main.tex. Must be a complete, compilable document (\\documentclass, body, \\end{document}). Use this for brand-new documents only — for follow-up edits to an existing artifact, use `artifact_id` + `diff` instead so you don't burn tokens re-emitting the body.",
                },
                "artifact_id": {
                    "type": "string",
                    "description": "Id of an existing .tex artifact (returned by a previous compile_latex call as `tex_artifact.registered_artifact_id`, or visible in the === ACTIVE ARTIFACTS === block). The tool reads its latest version content as the base source. Pass alone for an unchanged recompile, or pair with `diff` to apply a unified-diff edit before compiling.",
                },
                "diff": {
                    "type": "string",
                    "description": "Optional unified diff to apply to the artifact's latest version before compiling. Only valid alongside `artifact_id`. Standard format: `@@ -old_start,old_len +new_start,new_len @@` hunks with space/minus/plus-prefixed lines. Context and removal lines must match the source EXACTLY — no fuzz. If a hunk fails to apply, read_artifact the latest version and rebuild the diff. Saves thousands of tokens vs re-emitting the full body for small edits.",
                },
                "bibliography": {
                    "type": "string",
                    "description": "Optional full contents of refs.bib. If provided, the tool runs the standard pdflatex → bibtex → pdflatex → pdflatex cycle so citations resolve.",
                },
                "extra_files": {
                    "type": "object",
                    "description": "Optional filename → content map for auxiliary files shipped alongside main.tex in the same working directory (e.g. custom .cls/.sty, .bst styles, or images). Values are plain text by default; values prefixed with 'base64:' are decoded as binary. Max 20 files, 10 MB each.",
                    "additionalProperties": {"type": "string"},
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Per-pass timeout in seconds (default 60, max 120). Counts against each pdflatex invocation individually, so a .bib document can burn up to 3× this.",
                    "default": 60,
                    "minimum": 5,
                    "maximum": 120,
                },
            },
            "oneOf": [
                {"required": ["source"]},
                {"required": ["artifact_id"]},
            ],
        },
    },
    "ask_clarification": {
        "name": "ask_clarification",
        "description": (
            "Pause and ask the user 1-5 structured multiple-choice questions "
            "when their request is too ambiguous to act on confidently. Always "
            "provide a short restatement of what you think they're asking in "
            "`what_i_understood`, then offer each question with 2-6 plausible "
            "`options` the user can tap on. Leave `allow_custom: true` so they "
            "can type a free-form answer if none fit.\n\n"
            "CRITICAL CALLING RULE: this tool MUST be called ALONE on its own "
            "turn. Do NOT combine it with `create_artifact`, `run_python`, "
            "`deep_research`, `paper_search`, `web_search`, or ANY other tool "
            "in the same response. Do NOT write any prose answer alongside "
            "the tool call - the backend short-circuits the turn as soon as "
            "you emit this call, so other tool calls and trailing text are "
            "silently dropped. Writing a speculative 'starting-point' artifact "
            "AND then asking clarifying questions is the single most common "
            "failure mode of this tool - it wastes tool calls and produces "
            "code/results the user did not ask for. If you are unsure whether "
            "to ask, ask FIRST (alone) and wait for the answer; never ask "
            "AFTER doing speculative work.\n\n"
            "USE THIS when the request is so vague that multiple very different "
            "plans would all be reasonable: 'help me with my paper', 'what's "
            "new?', 'look into photosynthesis', single-word messages like 'fix "
            "it', ambiguous domain acronyms (EPR, NMR, MD...), or when a "
            "critical parameter (programming language, input file format, time "
            "window, scope, target, output format) is missing.\n\n"
            "DO NOT USE for clear requests even if they're broad: 'find recent "
            "papers on polymer crystallization', 'what day is it?', 'review "
            "this code', 'summarise this paper'. Do not use it as a stalling "
            "tactic or to second-guess reasonable defaults - pick sensible "
            "defaults and proceed if a single clarification wouldn't change "
            "your whole plan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "what_i_understood": {
                    "type": "string",
                    "description": "One-sentence restatement of the user's request. Shown prominently at the top of the clarification card so the user can confirm or correct your interpretation. Keep under 500 characters."
                },
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 5,
                    "description": "1 to 5 clarifying sub-questions, each with its own set of multiple-choice options.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable identifier for this sub-question (e.g. 'year_range', 'scope'). Optional - auto-assigned as q1/q2/... if omitted."
                            },
                            "text": {
                                "type": "string",
                                "description": "The question itself. Keep under 300 characters."
                            },
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 6,
                                "items": {"type": "string"},
                                "description": "2 to 6 plausible answers for the user to choose from. Each under 100 characters."
                            },
                            "allow_custom": {
                                "type": "boolean",
                                "description": "Whether to show a 'type your own' text field beside the preset options. Default: true.",
                                "default": True
                            }
                        },
                        "required": ["text", "options"]
                    }
                }
            },
            "required": ["what_i_understood", "questions"]
        }
    },
    "faq": {
        "name": "faq",
        "description": "Look up admin-curated answers to user-facing how-to questions. Use this ONLY when the user asks how the Munin interface works ('how do I upload a document?', 'what is incognito mode?', 'what's the difference between the personas?', 'how do I start a project?'). Do NOT use it for research questions - those go through deep_research / paper_search / web_search / source / etc. The list of available topic ids is in the === CAPABILITIES === block of your system prompt under 'FAQ topics'. Three call modes: (1) faq(topic='upload_documents') returns the full answer for one topic; (2) faq(search='upload') does substring matching and returns a list of previews; (3) faq() with no arguments returns the table of contents (all topics with their questions, no answer bodies). Start with mode (1) when you know the exact topic; fall back to (2) or (3) when you don't.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Exact snake_case topic id (e.g. 'upload_documents', 'personas', 'projects'). Returns the full answer. See the === CAPABILITIES === FAQ topics line for valid ids."
                },
                "search": {
                    "type": "string",
                    "description": "Case-insensitive substring to match against topic ids, questions, and answers. Returns a list of matches with answer previews."
                }
            },
            "required": []
        }
    },
    "tool_search": {
        "name": "tool_search",
        "description": (
            "Discover tools that are NOT in your current tool list. Your "
            "schema only carries a small core set (paper/web search, "
            "source, run_python, create_artifact, calculate, "
            "ask_clarification). Many other "
            "capabilities exist but are hidden until you search for them: "
            "citation-graph traversal, Semantic Scholar lookups, LaTeX "
            "compilation, artifact editing, conversation memory, project "
            "tools, equation transcription, citation export, and more. "
            "Call tool_search with a short description of the capability "
            "you need (e.g. 'find papers that cite a DOI', 'compile LaTeX "
            "to PDF', 'remember a fact about the user', 'list the user's "
            "uploaded documents'). It returns the matching tools' full "
            "schemas and makes them callable on your following turns. "
            "Use this whenever the obvious tool for a task isn't in your "
            "list rather than assuming the capability is missing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short natural-language description of the capability or tool you are looking for."
                }
            },
            "required": ["query"]
        }
    },
    "set_plan": {
        "name": "set_plan",
        "description": (
            "REPLACE the current task list for this conversation with `items`. "
            "Use this at the START of any multi-step request to break the work "
            "into 2-20 short action-oriented checkboxed steps the user can see. "
            "Always set the FIRST item's status to 'in_progress' when you "
            "immediately start working on it; subsequent items stay 'pending' "
            "until you start them.\n\n"
            "CALLING RULES:\n"
            "1. Call this BEFORE any other tool on a multi-step task. The user "
            "sees the list and uses it to follow your progress.\n"
            "2. To FLIP a single item's status (pending -> in_progress -> "
            "done), use `update_plan_item` instead — it's cheaper than "
            "retyping the whole list.\n"
            "3. Keep titles SHORT and action-oriented ('Search arxiv for X' "
            "not 'I will search arxiv for X and then read the abstracts').\n"
            "4. At most 20 items, 200 chars per title, 500 chars per notes.\n\n"
            "DO NOT USE for one-step requests (a single web_search, a single "
            "calculate, a single paper_lookup, 'what's the weather'). The list "
            "is scaffolding for multi-step work, not a ceremony for every turn.\n\n"
            "Item ids are auto-assigned 'p-1', 'p-2', ... if you omit them; "
            "you can also pass your own stable ids (snake_case recommended). "
            "Status values: 'pending' | 'in_progress' | 'done' | 'cancelled'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable id (e.g. 'p-1' or 'search_arxiv'). Auto-assigned 'p-1'/'p-2'/... if omitted."
                            },
                            "title": {
                                "type": "string",
                                "maxLength": 200,
                                "description": "One-line action ('Search arxiv for X'). Max 200 chars."
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "cancelled"],
                                "default": "pending"
                            },
                            "notes": {
                                "type": "string",
                                "maxLength": 500,
                                "description": "Optional context block, max 500 chars."
                            }
                        },
                        "required": ["title"]
                    }
                },
                "requires_approval": {
                    "type": "boolean",
                    "default": False,
                    "description": "Phase 2 (audit row #24): when true AND the persona declares plan_approval tools, the next gated tool call blocks until the user approves. Leave false on routine multi-step work."
                }
            },
            "required": ["items"]
        },
        "is_concurrency_safe": False
    },
    "update_plan_item": {
        "name": "update_plan_item",
        "description": (
            "Flip ONE plan item's status by id, or update its notes. Use this "
            "between tool calls so the user sees progress as you go. Cheaper "
            "than re-sending the whole list via `set_plan`.\n\n"
            "TYPICAL PATTERN:\n"
            "1. set_plan with full list at turn start (first item "
            "'in_progress')\n"
            "2. ...do the first step...\n"
            "3. update_plan_item('p-1', status='done')\n"
            "4. update_plan_item('p-2', status='in_progress')\n"
            "5. ...do the second step...\n"
            "6. update_plan_item('p-2', status='done')\n"
            "...and so on.\n\n"
            "If you change the structure of the plan (add/remove/reorder "
            "items), use set_plan instead; this tool only flips a single "
            "item's status or notes. Returns an error if the id doesn't exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The item id to update (matches an id from a prior set_plan call)."
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": "New status. Omit to keep current."
                },
                "notes": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Replace notes (max 500 chars). Pass empty string to clear; omit to keep current."
                }
            },
            "required": ["id"]
        },
        "is_concurrency_safe": False
    }
}
