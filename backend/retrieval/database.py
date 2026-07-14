"""
Database connections and embedder initialization for the Munin Retrieval Service.
"""

import logging
import os

logger = logging.getLogger(__name__)

# ==============================================================================
# Configuration
# ==============================================================================
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888")
SPECTER_MODEL_PATH = os.getenv("SPECTER_MODEL_PATH", "/models/specter")
BGE_MODEL_PATH = os.getenv("BGE_MODEL_PATH", "/models/bge-base")
PAPERS_PDF_DIR = os.getenv("PAPERS_PDF_DIR", "/opt/munin/data/papers/pdf")

# --- Paper encoder selection (encoder migration; see
# todo_v2/done/ENCODER-MIGRATION-PLAN.md). Defaults preserve today's behaviour, so
# deploying the code is a no-op; cutover is an env flip + restart, rollback is
# the reverse. PAPER_ENCODER and PAPERS_COLLECTION MUST be set together (a
# BGE encoder implies the 1024d papers_bge collection, else dim mismatch).
PAPER_ENCODER = os.getenv("PAPER_ENCODER", "specter")            # specter | bge-large
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers")     # papers | papers_bge
BGE_LARGE_MODEL_PATH = os.getenv("BGE_LARGE_MODEL_PATH", "/models/bge-large")
# BGE query instruction applied to QUERIES ONLY (docs embedded raw). Empty for
# SPECTER. Must match the eval bake-off or the recall gain shrinks.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
PAPER_QUERY_PREFIX = _BGE_QUERY_INSTRUCTION if PAPER_ENCODER == "bge-large" else ""

# Deep Research configuration
# Submission is disabled by default while the MiroThinker research model is
# under review for retirement. Read endpoints (status/output/jobs/queue) stay
# up so past reports remain downloadable. Re-enable with DEEPRESEARCH_ENABLED=1
# AND `systemctl enable --now deepresearch-daemon` on the cluster head.
DEEPRESEARCH_ENABLED = os.getenv("DEEPRESEARCH_ENABLED", "0") == "1"
DEEPRESEARCH_QUEUE_DIR = os.getenv("DEEPRESEARCH_QUEUE_DIR", "/deepresearch/queue")
DEEPRESEARCH_JOBS_DIR = os.getenv("DEEPRESEARCH_JOBS_DIR", "/deepresearch/jobs")
SLURM_QUEUE_FILE = os.getenv("SLURM_QUEUE_FILE", "/deepresearch/slurm_queue.json")

# vLLM configuration (for MCP tools)
VLLM_URL = os.getenv("VLLM_URL", "http://127.0.0.1:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3.6-35b-a3b")

# Lazy-loaded clients and models
_qdrant = None
_neo4j = None
_specter = None
_bge = None
_paper_encoder = None


# ==============================================================================
# Embedder Initialization
# ==============================================================================
def get_specter():
    """Get or initialize SPECTER embedder for papers."""
    global _specter
    if _specter is None:
        from sentence_transformers import SentenceTransformer

        # Try local path first, then fall back to HuggingFace
        # Must match the model used in paper_pipeline.py for consistent embeddings
        model_options = [
            (SPECTER_MODEL_PATH, "local path"),
            ("sentence-transformers/allenai-specter", "HuggingFace"),
        ]

        for model_path, description in model_options:
            if model_path == SPECTER_MODEL_PATH and not os.path.exists(model_path):
                continue  # Skip if local path doesn't exist
            try:
                logger.info("Trying to load SPECTER from %s...", description)
                _specter = SentenceTransformer(model_path)
                logger.info("SPECTER model loaded from %s", description)
                return _specter
            except Exception as e:
                logger.warning("Failed to load from %s: %s", description, e)
                continue

        logger.error("Could not load SPECTER model")
    return _specter


def get_paper_encoder():
    """Encoder for the PAPERS corpus, selected by PAPER_ENCODER (specter |
    bge-large). Kept DISTINCT from get_specter(), which also serves the 768d
    ``notion`` collection and must not change under the migration."""
    global _paper_encoder
    if _paper_encoder is None:
        if PAPER_ENCODER == "bge-large":
            from sentence_transformers import SentenceTransformer
            model = (BGE_LARGE_MODEL_PATH if os.path.exists(BGE_LARGE_MODEL_PATH)
                     else "BAAI/bge-large-en-v1.5")
            _paper_encoder = SentenceTransformer(model)   # 1024d
            logger.info("Paper encoder: BGE-large (%s)", model)
        else:
            _paper_encoder = get_specter()                # 768d
            logger.info("Paper encoder: SPECTER-v1")
    return _paper_encoder


def encode_paper_query(text: str) -> list[float]:
    """Embed a QUERY against the papers corpus (applies the BGE query prefix if
    the active encoder needs one; empty for SPECTER)."""
    return get_paper_encoder().encode(PAPER_QUERY_PREFIX + text).tolist()


def encode_paper_doc(text: str) -> list[float]:
    """Embed a DOCUMENT (title\\n\\nabstract) for the papers corpus (raw, no
    prefix)."""
    return get_paper_encoder().encode(text).tolist()


def get_bge():
    """Get or initialize BGE embedder for general content."""
    global _bge
    if _bge is None:
        try:
            from sentence_transformers import SentenceTransformer
            if os.path.exists(BGE_MODEL_PATH):
                _bge = SentenceTransformer(BGE_MODEL_PATH)
            else:
                _bge = SentenceTransformer("BAAI/bge-base-en-v1.5")
            logger.info("BGE model loaded")
        except Exception:
            logger.exception("Failed to load BGE")
    return _bge


def is_specter_loaded() -> bool:
    """Return True if SPECTER model has been lazy-loaded (does not trigger load)."""
    return _specter is not None


def is_bge_loaded() -> bool:
    """Return True if BGE model has been lazy-loaded (does not trigger load)."""
    return _bge is not None


def get_qdrant():
    """Get or initialize Qdrant client."""
    global _qdrant
    if _qdrant is None:
        try:
            from qdrant_client import QdrantClient
            _qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
            logger.info("Connected to Qdrant at %s:%d", QDRANT_HOST, QDRANT_PORT)
        except Exception:
            logger.exception("Failed to connect to Qdrant")
    return _qdrant


def get_neo4j():
    """Get or initialize Neo4j driver."""
    global _neo4j
    if _neo4j is None:
        if not NEO4J_PASSWORD:
            logger.warning("NEO4J_PASSWORD not set, Neo4j features disabled")
            return None
        try:
            from neo4j import GraphDatabase
            _neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            # Test connection
            with _neo4j.session() as session:
                session.run("RETURN 1")
            logger.info("Connected to Neo4j at %s", NEO4J_URI)
        except Exception:
            logger.exception("Failed to connect to Neo4j")
            _neo4j = None
    return _neo4j
