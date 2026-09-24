"""All constants for the eval suite. No logic beyond the two request-field
helpers at the bottom, which mirror the backend's byte for byte.

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
# Points at papers_bge since the 2026-07 encoder cutover: scorecard headers
# were still counting the retired 768-d `papers` corpus, so reported corpus
# sizes lagged what the arms actually searched.
PAPERS_COLLECTION = "papers_bge"

# 1024-d BGE collection, kept as its own name because the migration pipelines
# below address source and destination separately. Same collection as
# PAPERS_COLLECTION now that the cutover is live.
PAPERS_BGE_COLLECTION = "papers_bge"

# The retired 768-d SPECTER-v1 corpus. Only the migration/pre-cutover tooling
# reads it (as the copy source and the rollback target); no arm searches it.
PAPERS_LEGACY_COLLECTION = "papers"

# Chunk-level collection behind source(mode=evidence) (2026-08-30). The agentic
# arm reads it at every egress setting, so it is half the corpus an arm actually
# searched and belongs in the provenance stamp next to PAPERS_COLLECTION. Name
# mirrors the backend's own default (mcp/tools/source.py: CHUNKS_COLLECTION).
CHUNKS_COLLECTION = "papers_chunks"

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

# BGE-large-en-v1.5 (encoder-migration candidate, 1024d). Query instruction is
# applied to QUERIES ONLY (docs embedded raw) — matching the bake-off config, or
# the recall gain shrinks. See ENCODER-MIGRATION-PLAN.md.
BGE_LARGE_PATH = os.getenv("BGE_LARGE_PATH", "/opt/munin/data/models/bge-large")
BGE_LARGE_HF_ID = "BAAI/bge-large-en-v1.5"
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# Faithfulness judge (Track B). MiniCheck-Flan-T5-Large (<1B) predicts
# support(doc, claim) -> [0,1] at the sentence level (Tang et al. 2024,
# arXiv 2404.10774). Local path first, else the HF id. Runs on GPU when free
# (env MUNIN_BENCH_ENTAILMENT_DEVICE) else CPU.
MINICHECK_PATH = os.getenv("MINICHECK_PATH", "/opt/munin/data/models/minicheck-flan-t5-large")
MINICHECK_HF_ID = "lytang/MiniCheck-Flan-T5-Large"
# The 7B escalation variant (spec fallback if RAGTruth AUROC is poor).
MINICHECK_7B_HF_ID = "bespokelabs/Bespoke-MiniCheck-7B"

# Encoder presets for the eval runners: label -> (model path/id, collection,
# query_prefix). "specter-v1" is the production baseline; "bge-large" is Phase A.
ENCODER_PRESETS = {
    "specter-v1": {"model": SPECTER_MODEL_PATH, "hf": SPECTER_HF_ID,
                   "collection": PAPERS_COLLECTION, "query_prefix": "", "dim": 768},
    "bge-large": {"model": BGE_LARGE_PATH, "hf": BGE_LARGE_HF_ID,
                  "collection": PAPERS_BGE_COLLECTION,
                  "query_prefix": BGE_QUERY_INSTRUCTION, "dim": 1024},
}

# --- vLLM (query expansion for the frozen variant set) ----------------------
VLLM_URL = os.getenv("VLLM_URL", "http://127.0.0.1:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3.8-27b")

# --- Arm-matching constants (Track D) ---------------------------------------
# The bare and RAG arms call vLLM DIRECTLY, bypassing the retrieval service, so
# anything the backend applies to the agentic arm has to be restated here or the
# arms differ by more than the harness. These three MUST track their backend
# counterparts; they use the same env var names so one export matches both.
#
#   LLM_REASONING_EFFORT   <- backend database.LLM_REASONING_EFFORT
#   VLLM_MAX_OUTPUT_TOKENS <- backend chat_context.DEFAULT_MAX_OUTPUT_TOKENS
#
# Why this matters concretely: Qwen3.8 defaults to reasoning_effort "xhigh",
# where one measured question consumed 11,374 completion tokens and returned an
# EMPTY answer at an 8K cap. Left unset, the bare/RAG arms would run at xhigh
# against the old 4096-token default and return empty content on a substantial
# fraction of questions, which the scorer counts as unparseable/abstain. That
# depresses the bare arm and INFLATES the headline harness delta for a reason
# that has nothing to do with the harness.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "medium").strip()
MAX_OUTPUT_TOKENS = int(os.getenv("VLLM_MAX_OUTPUT_TOKENS", "16384"))

# How a mechanical sub-task turns reasoning OFF for the served backbone. Mirrors
# backend database.LLM_THINKING_MODE (same env name, same values):
#   enable_thinking  -> {"enable_thinking": false}   Qwen3 family
#   effort_low       -> {"reasoning_effort": "low"}  gpt-oss (reasoning cannot
#                                                     be disabled, only shortened)
#   none             -> send nothing                  a model with no reasoning mode
# Before 2026-09-15 four bench scripts hardcoded the Qwen form, which gpt-oss
# silently ignores: the judge / expander / router then reasons at full length
# and the call looks fine while costing several times more.
LLM_THINKING_MODE = os.getenv("LLM_THINKING_MODE", "enable_thinking").strip() or "enable_thinking"


def thinking_off_fields() -> dict:
    """Request fields that disable (or minimise) the reasoning trace, or `{}`."""
    if LLM_THINKING_MODE == "enable_thinking":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if LLM_THINKING_MODE == "effort_low":
        return {"chat_template_kwargs": {"reasoning_effort": "low"}}
    if LLM_THINKING_MODE == "none":
        return {}
    raise ValueError(f"unknown LLM_THINKING_MODE {LLM_THINKING_MODE!r}")


def reasoning_effort_fields() -> dict:
    """Request fields pinning a user-facing turn's reasoning budget, or `{}`."""
    if not LLM_REASONING_EFFORT or LLM_REASONING_EFFORT.lower() == "default":
        return {}
    return {"chat_template_kwargs": {"reasoning_effort": LLM_REASONING_EFFORT}}

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
