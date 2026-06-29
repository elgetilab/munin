"""All constants for the eval suite. No logic.

Values mirror the production defaults in ``backend/retrieval/database.py`` and
``backend/retrieval/main.py``. Secrets (the Neo4j password) are read from the
environment at client-construction time (see ``clients.py``); nothing secret
lives here.
"""

from __future__ import annotations

import os

# --- Qdrant -----------------------------------------------------------------
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
# The PRODUCTION papers collection. Read-only for the eval harness; BEIR
# subsets get their own ``eval_*`` collections (Phase 3) and never touch this.
PAPERS_COLLECTION = "papers"

# --- Neo4j ------------------------------------------------------------------
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
# Read from env only; never hardcoded. clients.get_neo4j() raises if unset.
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# --- SPECTER ----------------------------------------------------------------
# Host path on hugin; falls back to the HF id inside clients.load_specter().
SPECTER_MODEL_PATH = os.getenv(
    "SPECTER_MODEL_PATH", "/opt/munin/data/models/specter"
)
SPECTER_HF_ID = "sentence-transformers/allenai-specter"

# --- vLLM (query expansion for the frozen variant set) ----------------------
VLLM_URL = os.getenv("VLLM_URL", "http://127.0.0.1:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3.6-35b-a3b")

# --- Retrieval ranking constants (copied from production) -------------------
# main.py: fetch_k = min(top_k * 3, 100)
FETCH_K_MULT = 3
FETCH_K_CAP = 100

# Citation re-rank weights. 0.7/0.3 is the HybridSearchRequest API default and
# the paper claim (KICKOFF Q1); 0.8/0.2 is the prior search-UI setting, kept
# as a sweep point. CitationRerankRetriever's own constructor default is
# 0.8/0.2 per RETRIEVAL-EVAL-SPEC Phase 2; runners pass explicit weights.
DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_CITATION_WEIGHT = 0.3

# AgentRetriever fan-out: production expands one query to n=5 (base + 4).
AGENT_VARIANT_N = 5
# paper_search uses per_query = max(top_k, 5).
AGENT_PER_QUERY_FLOOR = 5

# RRF hybrid (Cormack et al. 2009, SIGIR): k=60, expand top seed_n dense hits.
RRF_K = 60
RRF_SEED_N = 10
