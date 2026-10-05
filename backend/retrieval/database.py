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
# docs/paper-track/done/ENCODER-MIGRATION-PLAN.md). The defaults ARE production: the
# SPECTER-v1 -> BGE-large cutover completed 2026-07, so an unconfigured run
# (local dev, eval harness, fresh container) now matches the live stack
# instead of silently benchmarking the retired one. Rollback is the env flip
# in the other direction: PAPER_ENCODER=specter + PAPERS_COLLECTION=papers.
# The two MUST move together (a BGE encoder implies the 1024d papers_bge
# collection); verify_paper_space() below enforces that at startup.
PAPER_ENCODER = os.getenv("PAPER_ENCODER", "bge-large")            # bge-large | specter
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")   # papers_bge | papers
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

# ------------------------------------------------------------------------------
# Language-model endpoint.
#
# Munin speaks plain OpenAI-compatible HTTP, so the endpoint does not have to be
# vLLM: Ollama, llama.cpp's server, or a hosted API all work. The reference
# deployment is vLLM and the published results were measured on it.
#
# LLM_BASE_URL / LLM_MODEL_NAME are the names to use. VLLM_URL / VLLM_MODEL_NAME
# remain as aliases because they are what the cluster's cluster.env and every
# vLLM launch script already export; dropping them would be a silent
# misconfiguration on the next deploy. The internal symbols keep the old names
# so the 43 call sites across 14 modules do not churn.
# ------------------------------------------------------------------------------
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_LLM_MODEL_NAME = "qwen3.8-27b"


def _env_flag(name: str, default: bool, env=None) -> bool:
    raw = (os.environ if env is None else env).get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def resolve_llm_endpoint(env=None) -> tuple:
    """(base_url, model_name) from the environment, new names winning.

    A PURE function of `env` on purpose. These used to be inline `os.getenv`
    calls at module scope, which made them untestable without reimporting the
    module -- and module reload does not reliably re-read the environment once
    something else in the process has already imported it, so the tests that
    tried were passing alone and failing in the suite. Keeping the resolution
    in a function that takes a mapping makes it directly testable and is why
    the empty-string case below is pinned by a test rather than assumed.

    An EMPTY value falls through to the next candidate rather than winning:
    `LLM_BASE_URL=` in a .env file means "not set", not "use a blank base URL",
    which would otherwise send every request to a relative path.
    """
    env = os.environ if env is None else env
    url = env.get("LLM_BASE_URL") or env.get("VLLM_URL") or DEFAULT_LLM_BASE_URL
    model = (env.get("LLM_MODEL_NAME") or env.get("VLLM_MODEL_NAME")
             or DEFAULT_LLM_MODEL_NAME)
    return url, model


# Does the endpoint accept `chat_template_kwargs`?
#
# Munin disables model "thinking" for mechanical sub-tasks (summarise, expand a
# query, transcribe an equation, extract a memory) because a long <think> trace
# eats a small token budget and produces nothing useful. vLLM exposes that as
# `chat_template_kwargs: {"enable_thinking": false}`, which is a passthrough to
# the chat template and NOT part of the OpenAI schema.
#
# Strict servers reject unknown top-level request fields with a 400, so sending
# it blindly turns "point Munin at your own endpoint" into an immediate hard
# failure on every one of those sub-tasks. Gate it instead: on, the behaviour is
# byte-identical to before; off, the field is omitted and the only cost is that
# those sub-tasks may emit reasoning the caller then discards.
#
# Left ON by default so the reference deployment is unchanged. Turn it off for
# an endpoint that 400s (the symptom is every summarise/expand call failing
# while plain chat works).
LLM_THINKING_TOGGLE = _env_flag("LLM_THINKING_TOGGLE", True)

# How hard the model thinks on a USER-FACING turn.
#
# Qwen3.8 reads `reasoning_effort` out of `chat_template_kwargs` and accepts
# exactly "xhigh" (its default), "medium" or "low"; anything else makes the chat
# template raise, which surfaces as a 400 on every chat turn. It is only
# consulted when thinking is ON, so this is a no-op for the mechanical
# sub-tasks above, which disable thinking outright.
#
# Default "medium" rather than the model's "xhigh". Measured on the 2026-08-25
# swap: at xhigh a single moderate reasoning question burned 11,374 completion
# tokens to produce 672 characters of answer, and at an 8K cap the same question
# ran out of budget mid-<think> and returned an EMPTY answer (finish_reason
# "length", no content at all) - the "vLLM produced no output" signature. medium
# is also the only setting that injects no extra instruction into the system
# prompt; xhigh and low both prepend a nudge.
#
# Set to "" (or "default") to send nothing and let the model choose, which
# restores xhigh on Qwen3.8. Gated behind LLM_THINKING_TOGGLE because it rides
# the same non-OpenAI `chat_template_kwargs` passthrough: an endpoint strict
# enough to 400 on that field must not receive this one either.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "medium").strip()

# HOW a mechanical sub-task turns reasoning off, per backbone. The Qwen3
# family exposes `enable_thinking`; gpt-oss cannot disable reasoning at all
# and is told `reasoning_effort: low` instead; a model with no reasoning mode
# gets nothing. Set by the model profile (config/models/<slug>.env ->
# active-model.env -> compose). Default is the Qwen form so a container
# without the variable behaves exactly as every deployment before 2026-09-15.
THINKING_MODES = ("enable_thinking", "effort_low", "none")


def resolve_thinking_mode(env=None) -> str:
    """Pure function of `env` (see resolve_llm_endpoint for why)."""
    env = os.environ if env is None else env
    raw = (env.get("LLM_THINKING_MODE") or "enable_thinking").strip()
    if raw not in THINKING_MODES:
        raise ValueError(f"LLM_THINKING_MODE={raw!r}; expected one of {THINKING_MODES}")
    return raw


LLM_THINKING_MODE = resolve_thinking_mode()

VLLM_URL, VLLM_MODEL_NAME = resolve_llm_endpoint()


def thinking_off_fields(enabled: bool = None, mode: str = None) -> dict:
    """Request fields that disable (or minimise) the endpoint's reasoning
    trace, or `{}`.

    Splat into a request-body literal: `{..., **thinking_off_fields()}`.
    `enabled` / `mode` exist so a test can pin every branch without touching
    the process environment; callers pass nothing.
    """
    if not (LLM_THINKING_TOGGLE if enabled is None else enabled):
        return {}
    mode = LLM_THINKING_MODE if mode is None else mode
    if mode == "enable_thinking":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if mode == "effort_low":
        return {"chat_template_kwargs": {"reasoning_effort": "low"}}
    if mode == "none":
        return {}
    raise ValueError(f"unknown thinking mode {mode!r}")


def reasoning_effort_fields(effort: str = None, enabled: bool = None) -> dict:
    """Request fields pinning the reasoning budget of a user-facing turn, or `{}`.

    Splat into a request-body literal: `{..., **reasoning_effort_fields()}`.
    Returns `{}` when the passthrough is gated off or the effort is blank, so
    the default path for a non-vLLM endpoint is byte-identical to before.
    `effort` / `enabled` exist so a test can pin the branches without touching
    the process environment; callers pass nothing.
    """
    if not (LLM_THINKING_TOGGLE if enabled is None else enabled):
        return {}
    value = (LLM_REASONING_EFFORT if effort is None else effort).strip()
    if not value or value.lower() == "default":
        return {}
    return {"chat_template_kwargs": {"reasoning_effort": value}}


def thinking_off(body: dict, enabled: bool = None) -> dict:
    """Mutating form of `thinking_off_fields`, for a body built up in steps."""
    body.update(thinking_off_fields(enabled))
    return body


# ------------------------------------------------------------------------------
# Sampling profile: the backbone's recommended sampling, per persona class.
#
# Until 2026-09-15 the five sampling numbers lived in shared/personas/*.json,
# which meant they were Qwen3's recommended set baked into files that are
# shared by every backbone the stack serves. They now come from the model
# profile (config/models/<slug>.env -> SAMPLING_DEFAULT / SAMPLING_CODE, JSON
# objects) and a persona names only its CLASS (`params.sampling_class`,
# "default" or "code"). Two instances on two backbones can then run off one
# persona directory with each model sampled the way its vendor recommends.
#
# The built-in defaults below ARE the values the personas carried, so a
# container with neither variable set sends byte-identical bodies to before.
# tests/test_sampling_profile.py pins that against the persona files.
# ------------------------------------------------------------------------------
SAMPLING_CLASSES = ("default", "code")
_QWEN3_SAMPLING_DEFAULT = {"temperature": 1.0, "top_p": 0.95, "top_k": 20,
                           "min_p": 0.0, "presence_penalty": 1.5}
_QWEN3_SAMPLING_CODE = {"temperature": 0.6, "top_p": 0.95, "top_k": 20,
                        "min_p": 0.0, "presence_penalty": 0.0}
_SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty")


def resolve_sampling_profile(env=None) -> dict:
    """{"default": {...}, "code": {...}} from SAMPLING_DEFAULT / SAMPLING_CODE.

    Pure function of `env`. Each variable is a JSON object holding any subset
    of temperature / top_p / top_k / min_p / presence_penalty; an unset or
    blank variable falls back to the Qwen3 set for that class, an unknown key
    or a non-object raises at import so a typo fails the boot, not the turn.
    """
    import json as _json
    env = os.environ if env is None else env
    out = {}
    for cls, fallback in (("default", _QWEN3_SAMPLING_DEFAULT),
                          ("code", _QWEN3_SAMPLING_CODE)):
        raw = (env.get(f"SAMPLING_{cls.upper()}") or "").strip()
        if not raw:
            out[cls] = dict(fallback)
            continue
        try:
            val = _json.loads(raw)
        except ValueError as e:
            raise ValueError(f"SAMPLING_{cls.upper()} is not JSON: {e}") from e
        if not isinstance(val, dict):
            raise ValueError(f"SAMPLING_{cls.upper()} must be a JSON object")
        bad = set(val) - set(_SAMPLING_KEYS)
        if bad:
            raise ValueError(f"SAMPLING_{cls.upper()}: unknown keys {sorted(bad)}")
        out[cls] = dict(val)
    return out


SAMPLING_PROFILE = resolve_sampling_profile()


def model_sampling(sampling_class: str = "default", profile: dict = None) -> dict:
    """The backbone's sampling for one persona class. A copy, safe to mutate."""
    profile = SAMPLING_PROFILE if profile is None else profile
    if sampling_class not in SAMPLING_CLASSES:
        raise ValueError(f"unknown sampling class {sampling_class!r}; "
                         f"expected one of {SAMPLING_CLASSES}")
    return dict(profile[sampling_class])

# Lazy-loaded clients and models
_qdrant = None
_neo4j = None
_specter = None
_bge = None
_paper_encoder = None
_papers_collection_dim = None   # memoised width of PAPERS_COLLECTION


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
            if (model_path == SPECTER_MODEL_PATH
                    and local_model_or_hub(model_path, "") != model_path):
                continue  # no model in the local directory (or no directory)
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


def local_model_or_hub(path: str, hub_name: str) -> str:
    """The local model directory if it really holds a model, else the hub name.

    `os.path.exists` is not enough: compose bind-mounts a models directory for
    every encoder, and Docker creates a missing source as an EMPTY directory.
    That directory exists, SentenceTransformer refuses it ("Unrecognized
    model"), and on a fresh install the Hugging Face fallback never ran, so
    paper search and document embedding were dead until someone staged the
    weights by hand. A sentence-transformers export has modules.json; a plain
    transformers one has config.json.
    """
    if any(os.path.isfile(os.path.join(path, f)) for f in ("modules.json", "config.json")):
        return path
    return hub_name


def get_paper_encoder():
    """Encoder for the PAPERS corpus, selected by PAPER_ENCODER (specter |
    bge-large). Kept DISTINCT from get_specter(), which also serves the 768d
    ``notion`` collection and must not change under the migration."""
    global _paper_encoder
    if _paper_encoder is None:
        if PAPER_ENCODER == "bge-large":
            from sentence_transformers import SentenceTransformer
            model = local_model_or_hub(BGE_LARGE_MODEL_PATH, "BAAI/bge-large-en-v1.5")
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
            _bge = SentenceTransformer(
                local_model_or_hub(BGE_MODEL_PATH, "BAAI/bge-base-en-v1.5"))
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


def paper_space() -> dict:
    """Describe the active paper vector space (encoder, collection, dims).

    ``encoder_dim`` is the loaded model's real output width, so it reflects
    what we actually embed with rather than what the flag claims.
    ``collection_dim`` is None when Qdrant is unreachable or the collection
    does not exist yet (fresh cluster, before the first ingest).
    """
    encoder_dim = None
    try:
        enc = get_paper_encoder()
        if enc is not None:
            encoder_dim = enc.get_sentence_embedding_dimension()
    except Exception:
        logger.exception("Could not determine paper encoder dimension")

    # /api/status is polled by the frontend, so the collection width is
    # memoised after the first successful read (it cannot change without a
    # restart, since the collection name is env-fixed).
    global _papers_collection_dim
    collection_dim = _papers_collection_dim
    try:
        client = get_qdrant()
        if collection_dim is None and client is not None \
                and client.collection_exists(PAPERS_COLLECTION):
            params = client.get_collection(PAPERS_COLLECTION).config.params.vectors
            # Unnamed vectors give a VectorParams; named ones give a dict.
            collection_dim = (params.size if hasattr(params, "size")
                              else next(iter(params.values())).size)
            _papers_collection_dim = collection_dim
    except Exception:
        logger.exception("Could not read %s vector size from Qdrant",
                         PAPERS_COLLECTION)

    return {
        "encoder": PAPER_ENCODER,
        "collection": PAPERS_COLLECTION,
        "encoder_dim": encoder_dim,
        "collection_dim": collection_dim,
        "query_prefix": bool(PAPER_QUERY_PREFIX),
    }


def verify_paper_space() -> dict:
    """Fail startup when the encoder and the papers collection disagree.

    PAPER_ENCODER and PAPERS_COLLECTION are a pair: flipping one without the
    other produces a dimension mismatch where every paper search fails at
    query time, which historically surfaced as unexplained empty results
    rather than as a config error. Raising here turns a half-flip into a
    loud, immediate boot failure.

    A missing collection is NOT fatal: a fresh cluster has no papers until
    the pipeline creates it. Unreachable Qdrant is likewise non-fatal, since
    /api/status already reports that.
    """
    space = paper_space()
    enc_dim, coll_dim = space["encoder_dim"], space["collection_dim"]

    if enc_dim is not None and coll_dim is not None and enc_dim != coll_dim:
        raise RuntimeError(
            f"Paper vector space mismatch: PAPER_ENCODER={PAPER_ENCODER} emits "
            f"{enc_dim}d but collection {PAPERS_COLLECTION} stores {coll_dim}d. "
            "These two env vars must be set together "
            "(bge-large + papers_bge, or specter + papers)."
        )

    if coll_dim is None:
        logger.warning(
            "Paper space unverified: collection %s not readable (fresh cluster "
            "or Qdrant down). Encoder=%s (%sd).",
            PAPERS_COLLECTION, PAPER_ENCODER, enc_dim)
    else:
        logger.info("Paper space OK: encoder=%s (%sd) collection=%s (%sd)",
                    PAPER_ENCODER, enc_dim, PAPERS_COLLECTION, coll_dim)
    return space


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


# ==============================================================================
# Paper PDF resolution
# ==============================================================================
# Single resolver for "which file on disk holds this DOI's PDF".
#
# Until 2026-08-18 there were two copies: main.get_pdf_path (exact
# os.path.exists only) and mcp.tools.papers.get_pdf_path (exact, plus a
# legacy no-prefix form, plus a case-insensitive listdir scan). They drifted,
# and the drift was user-visible: the pipeline lowercases the DOI when it
# names the file while the crawler kept the registrant's original case, so
# GET /paper/{doi}/pdf returned 404 on 22 of one contributor's papers whose
# PDFs were sitting right there under a different case. read_paper found them
# because the MCP copy had the fallback. See UPLOAD-INGEST-REPAIR-PLAN.md
# defect 3.
#
# The case-insensitive step is a lowercase index rather than the old
# per-call os.listdir: the HTTP route hits this on every miss and the corpus
# is ~66k files. Invalidated on the directory's mtime, which changes whenever
# a file is added or removed, so an ingest is picked up on the next call.
_pdf_index: dict[str, str] | None = None
_pdf_index_mtime: float = -1.0
_pdf_index_dir: str | None = None


def _pdf_name_candidates(doi: str) -> list[str]:
    """Filenames that could hold this DOI, most canonical first.

    `doi_<doi with / -> _>.pdf` is what paper_pipeline writes today. The
    `:`-substituted variant matches paper_crawler's sanitisation, and the
    bare forms are a pre-`doi_` legacy layout still present in the corpus.
    """
    slash = doi.replace("/", "_")
    both = slash.replace(":", "_")
    names = [f"doi_{slash}.pdf", f"doi_{both}.pdf", f"{slash}.pdf", f"{both}.pdf"]
    # dict.fromkeys: dedupe (slash == both for most DOIs) but keep order.
    return list(dict.fromkeys(names))


def _pdf_lower_index() -> dict[str, str]:
    """Lowercased filename -> real filename, rebuilt when the dir changes."""
    global _pdf_index, _pdf_index_mtime, _pdf_index_dir
    try:
        mtime = os.stat(PAPERS_PDF_DIR).st_mtime
    except OSError:
        return {}
    if (
        _pdf_index is not None
        and mtime == _pdf_index_mtime
        and _pdf_index_dir == PAPERS_PDF_DIR
    ):
        return _pdf_index
    try:
        index = {
            n.lower(): n
            for n in os.listdir(PAPERS_PDF_DIR)
            if n.lower().endswith(".pdf")
        }
    except OSError as e:
        logger.warning("PDF index rebuild failed for %s: %s", PAPERS_PDF_DIR, e)
        return {}
    _pdf_index, _pdf_index_mtime, _pdf_index_dir = index, mtime, PAPERS_PDF_DIR
    return index


def get_pdf_path(doi: str) -> "str | None":
    """Absolute path to this DOI's PDF, or None when the corpus lacks it.

    Tries each candidate filename exactly, then once more against a
    case-insensitive index. Case differences are common: the pipeline
    lowercases DOIs, publishers and the crawler do not.
    """
    if not doi:
        return None
    candidates = _pdf_name_candidates(doi)
    for name in candidates:
        path = os.path.join(PAPERS_PDF_DIR, name)
        if os.path.exists(path):
            return path
    index = _pdf_lower_index()
    for name in candidates:
        real = index.get(name.lower())
        if real:
            return os.path.join(PAPERS_PDF_DIR, real)
    return None
