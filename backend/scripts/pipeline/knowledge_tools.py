#!/usr/bin/env python3
"""
==============================================================================
MUNIN Knowledge Base Tools
==============================================================================
Tools for LLM function calling to query the knowledge base.

Available Tools:
    - search_papers: Semantic search over papers
    - find_citing_papers: Find papers that cite a given paper
    - find_author_papers: Find papers by an author
    - get_paper_details: Get full details of a paper
    - web_search: Search the web for current information

Usage:
    from knowledge_tools import execute_tool, TOOLS

    result = execute_tool("search_papers", {"query": "transformer models"})
==============================================================================
"""

import json
import os
from typing import Optional

# ==============================================================================
# Configuration
# ==============================================================================
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "__NEO4J_PASSWORD__")
COLLECTION_NAME = "papers"

# Lazy-loaded clients
_qdrant = None
_neo4j = None
_embedder = None


# ==============================================================================
# Client Initialization
# ==============================================================================
def get_qdrant():
    """Get or initialize Qdrant client"""
    global _qdrant
    if _qdrant is None:
        try:
            from qdrant_client import QdrantClient
            _qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        except Exception as e:
            print(f"Qdrant connection failed: {e}")
    return _qdrant


def get_neo4j():
    """Get or initialize Neo4j driver"""
    global _neo4j
    if _neo4j is None and not NEO4J_PASSWORD.startswith("__"):
        try:
            from neo4j import GraphDatabase
            _neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
        except Exception as e:
            print(f"Neo4j connection failed: {e}")
    return _neo4j


def get_embedder():
    """Get or initialize SPECTER embedder"""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            # Use same model as paper_pipeline.py for consistent embeddings
            specter_path = "/opt/munin/data/models/specter"
            if os.path.exists(specter_path):
                _embedder = SentenceTransformer(specter_path)
            else:
                _embedder = SentenceTransformer("sentence-transformers/allenai-specter")
        except Exception as e:
            print(f"Embedder initialization failed: {e}")
    return _embedder


# ==============================================================================
# Tool Definitions (for LLM function calling)
# ==============================================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search the scientific paper database using semantic similarity. Returns papers matching the query with titles, authors, and abstracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g., 'transformer models for medical imaging')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 20)",
                        "default": 5
                    },
                    "year_min": {
                        "type": "integer",
                        "description": "Minimum publication year filter"
                    },
                    "year_max": {
                        "type": "integer",
                        "description": "Maximum publication year filter"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_citing_papers",
            "description": "Find papers that cite a given paper (by DOI). Useful for finding follow-up work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doi": {
                        "type": "string",
                        "description": "The DOI of the paper to find citations for"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of citing papers to return (default: 10)",
                        "default": 10
                    }
                },
                "required": ["doi"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_author_papers",
            "description": "Find all papers by a specific author in the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "author_name": {
                        "type": "string",
                        "description": "The author's name (partial match supported)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of papers to return (default: 20)",
                        "default": 20
                    }
                },
                "required": ["author_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_details",
            "description": "Get full details of a specific paper by its DOI or paper ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doi": {
                        "type": "string",
                        "description": "The DOI of the paper"
                    },
                    "paper_id": {
                        "type": "string",
                        "description": "The internal paper ID"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information not in the paper database. Use for recent news, current events, or topics not covered by academic papers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ==============================================================================
# Tool Implementations
# ==============================================================================
def search_papers(query: str, top_k: int = 5,
                  year_min: Optional[int] = None,
                  year_max: Optional[int] = None) -> str:
    """Search papers using semantic similarity"""
    qdrant = get_qdrant()
    embedder = get_embedder()

    if not qdrant or not embedder:
        return json.dumps({"error": "Knowledge base not available"})

    # Generate query embedding
    vector = embedder.encode(query).tolist()

    # Build filter (qdrant-client 1.7+ API)
    query_filter = None
    if year_min or year_max:
        from qdrant_client.models import Filter, FieldCondition, Range
        conditions = []
        if year_min:
            conditions.append(FieldCondition(key="year", range=Range(gte=year_min)))
        if year_max:
            conditions.append(FieldCondition(key="year", range=Range(lte=year_max)))
        query_filter = Filter(must=conditions)

    # Search (qdrant-client 1.7+ uses query_points instead of search)
    results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=min(top_k, 20),
        query_filter=query_filter
    )

    papers = []
    for hit in results.points:
        papers.append({
            "title": hit.payload.get("title"),
            "authors": hit.payload.get("authors", []),
            "year": hit.payload.get("year"),
            "doi": hit.payload.get("doi"),
            "journal": hit.payload.get("journal"),
            "abstract": hit.payload.get("abstract", "")[:500] + "...",
            "relevance_score": round(hit.score, 3)
        })

    return json.dumps({
        "query": query,
        "num_results": len(papers),
        "papers": papers
    }, indent=2)


def find_citing_papers(doi: str, limit: int = 10) -> str:
    """Find papers that cite a given paper"""
    neo4j = get_neo4j()

    if not neo4j:
        return json.dumps({"error": "Graph database not available"})

    with neo4j.session() as session:
        result = session.run("""
            MATCH (citing:Paper)-[:CITES]->(p:Paper {doi: $doi})
            RETURN citing.title as title,
                   citing.doi as doi,
                   citing.year as year,
                   citing.paper_id as paper_id
            ORDER BY citing.year DESC
            LIMIT $limit
        """, doi=doi, limit=limit)

        papers = [dict(r) for r in result]

    return json.dumps({
        "cited_doi": doi,
        "num_citing": len(papers),
        "citing_papers": papers
    }, indent=2)


def find_author_papers(author_name: str, limit: int = 20) -> str:
    """Find papers by an author"""
    neo4j = get_neo4j()

    if not neo4j:
        return json.dumps({"error": "Graph database not available"})

    with neo4j.session() as session:
        result = session.run("""
            MATCH (a:Author)-[:AUTHORED]->(p:Paper)
            WHERE toLower(a.name) CONTAINS toLower($name)
            RETURN p.title as title,
                   p.doi as doi,
                   p.year as year,
                   p.journal as journal,
                   a.name as author_name
            ORDER BY p.year DESC
            LIMIT $limit
        """, name=author_name, limit=limit)

        papers = [dict(r) for r in result]

    return json.dumps({
        "author_query": author_name,
        "num_papers": len(papers),
        "papers": papers
    }, indent=2)


def get_paper_details(doi: str = None, paper_id: str = None) -> str:
    """Get full details of a paper"""
    qdrant = get_qdrant()

    if not qdrant:
        return json.dumps({"error": "Knowledge base not available"})

    if not doi and not paper_id:
        return json.dumps({"error": "Either doi or paper_id must be provided"})

    # Search by payload filter
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    filter_key = "doi" if doi else "paper_id"
    filter_value = doi if doi else paper_id

    results = qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key=filter_key, match=MatchValue(value=filter_value))]
        ),
        limit=1,
        with_payload=True
    )

    if results[0]:
        paper = results[0][0].payload
        return json.dumps(paper, indent=2)
    else:
        return json.dumps({"error": f"Paper not found with {filter_key}={filter_value}"})


def web_search(query: str, num_results: int = 5) -> str:
    """Search the web using DuckDuckGo"""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))

        return json.dumps({
            "query": query,
            "num_results": len(results),
            "results": [
                {
                    "title": r["title"],
                    "url": r["href"],
                    "snippet": r["body"]
                }
                for r in results
            ]
        }, indent=2)
    except ImportError:
        return json.dumps({"error": "Web search not available. Install: pip install duckduckgo-search"})
    except Exception as e:
        return json.dumps({"error": f"Web search failed: {str(e)}"})


# ==============================================================================
# Tool Executor
# ==============================================================================
def execute_tool(tool_name: str, arguments: dict) -> str:
    """Execute a tool by name with given arguments"""
    tools = {
        "search_papers": search_papers,
        "find_citing_papers": find_citing_papers,
        "find_author_papers": find_author_papers,
        "get_paper_details": get_paper_details,
        "web_search": web_search
    }

    if tool_name not in tools:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        return tools[tool_name](**arguments)
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {str(e)}"})


# ==============================================================================
# CLI for Testing
# ==============================================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python knowledge_tools.py <tool_name> <json_args>")
        print("\nAvailable tools:")
        for tool in TOOLS:
            name = tool["function"]["name"]
            desc = tool["function"]["description"]
            print(f"  - {name}: {desc[:60]}...")
        sys.exit(1)

    tool_name = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    result = execute_tool(tool_name, args)
    print(result)
