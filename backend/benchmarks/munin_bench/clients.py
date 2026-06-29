"""Client + model factories for the eval suite.

Kept out of config.py (which is constants-only) and out of the retriever
classes (which receive their dependencies injected, so they stay unit-testable
without live infra). A runner builds one Qdrant client, one Neo4j driver, and
one SPECTER model here and shares them across retrievers.
"""

from __future__ import annotations

import logging
import os

from . import config

logger = logging.getLogger(__name__)


def get_qdrant():
    """Construct a QdrantClient with the same kwargs as production
    (database.py: host/port, no API key on the internal network)."""
    from qdrant_client import QdrantClient

    return QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)


def get_neo4j():
    """Construct a Neo4j driver. Raises if NEO4J_PASSWORD is unset so a
    misconfigured run fails loudly instead of silently returning empty
    citation data (which would make citation_rerank degenerate to dense)."""
    from neo4j import GraphDatabase

    password = os.getenv("NEO4J_PASSWORD", config.NEO4J_PASSWORD)
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD is unset. Export it before running graph "
            "retrievers (citation_rerank, rrf_hybrid, two_hop_check)."
        )
    return GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USER, password)
    )


def load_specter():
    """Load the SPECTER embedder, local path first then HF id — mirrors
    database.get_specter()."""
    from sentence_transformers import SentenceTransformer

    candidates = [
        (config.SPECTER_MODEL_PATH, "local path"),
        (config.SPECTER_HF_ID, "HuggingFace"),
    ]
    for path, desc in candidates:
        if path == config.SPECTER_MODEL_PATH and not os.path.exists(path):
            continue
        try:
            logger.info("Loading SPECTER from %s (%s)", path, desc)
            return SentenceTransformer(path)
        except Exception as e:  # pragma: no cover - load-time/env dependent
            logger.warning("SPECTER load from %s failed: %s", desc, e)
    raise RuntimeError("Could not load SPECTER model from local path or HF")
