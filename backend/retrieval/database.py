"""
Database connections and embedder initialization for the Munin Retrieval Service.
"""

import os

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

# Deep Research configuration
DEEPRESEARCH_QUEUE_DIR = os.getenv("DEEPRESEARCH_QUEUE_DIR", "/deepresearch/queue")
DEEPRESEARCH_JOBS_DIR = os.getenv("DEEPRESEARCH_JOBS_DIR", "/deepresearch/jobs")
SLURM_QUEUE_FILE = os.getenv("SLURM_QUEUE_FILE", "/deepresearch/slurm_queue.json")

# vLLM configuration (for MCP tools)
VLLM_URL = os.getenv("VLLM_URL", "http://127.0.0.1:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3.5-35b-a3b")

# Lazy-loaded clients and models
_qdrant = None
_neo4j = None
_specter = None
_bge = None


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
                print(f"[INFO] Trying to load SPECTER from {description}...")
                _specter = SentenceTransformer(model_path)
                print(f"[OK] SPECTER model loaded from {description}")
                return _specter
            except Exception as e:
                print(f"[WARNING] Failed to load from {description}: {e}")
                continue

        print("[ERROR] Could not load SPECTER model")
    return _specter


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
            print("[OK] BGE model loaded")
        except Exception as e:
            print(f"[ERROR] Failed to load BGE: {e}")
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
            print(f"[OK] Connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
        except Exception as e:
            print(f"[ERROR] Failed to connect to Qdrant: {e}")
    return _qdrant


def get_neo4j():
    """Get or initialize Neo4j driver."""
    global _neo4j
    if _neo4j is None:
        if not NEO4J_PASSWORD:
            print("[WARNING] NEO4J_PASSWORD not set, Neo4j features disabled")
            return None
        try:
            from neo4j import GraphDatabase
            _neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            # Test connection
            with _neo4j.session() as session:
                session.run("RETURN 1")
            print(f"[OK] Connected to Neo4j at {NEO4J_URI}")
        except Exception as e:
            print(f"[ERROR] Failed to connect to Neo4j: {e}")
            _neo4j = None
    return _neo4j
