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


def load_encoder(model, device: str | None = None, hf_fallback: str | None = None):
    """Load any SentenceTransformer encoder (SPECTER, BGE, ...): local path first,
    then an optional HF id fallback.

    ``device`` defaults to the env ``MUNIN_BENCH_SPECTER_DEVICE`` or "cpu". CPU is
    the safe default on hugin (GPUs saturated by vLLM); pass "cuda" only when a
    card is free."""
    from sentence_transformers import SentenceTransformer

    if device is None:
        device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")

    candidates = [(model, "local path or id")]
    if hf_fallback and hf_fallback != model:
        candidates.append((hf_fallback, "HuggingFace fallback"))
    last = None
    for path, desc in candidates:
        # skip a LOCAL path that doesn't exist (leading /, ., ~); HF ids like
        # "org/model" contain a slash but aren't local paths, so always attempt.
        if str(path).startswith((os.sep, ".", "~")) and not os.path.exists(path):
            continue
        try:
            logger.info("Loading encoder from %s (%s) on %s", path, desc, device)
            return SentenceTransformer(path, device=device)
        except Exception as e:  # pragma: no cover - load-time/env dependent
            logger.warning("encoder load from %s failed: %s", desc, e)
            last = e
    raise RuntimeError(f"Could not load encoder {model!r}: {last}")


def load_specter(device: str | None = None):
    """Backward-compatible SPECTER loader (thin wrapper over load_encoder)."""
    return load_encoder(config.SPECTER_MODEL_PATH, device=device,
                        hf_fallback=config.SPECTER_HF_ID)
