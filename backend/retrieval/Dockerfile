# ==============================================================================
# MUNIN RETRIEVAL SERVICE - Dockerfile
# ==============================================================================
# Multi-stage build for the RAG retrieval service
# ==============================================================================

FROM python:3.11.14-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# constraints.txt pins every package, transitive ones included, to the versions
# the reference deployment runs; requirements.txt only says what is needed.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt -c constraints.txt

# ==============================================================================
# Final image
# ==============================================================================
FROM python:3.11.14-slim

WORKDIR /app

# OCR for uploaded PDFs that carry no text layer. A PDF printed from a browser
# or scanned to "Microsoft: Print To PDF" has images and nothing else, so GROBID
# and pypdf both extract zero characters and the upload lands unsearchable
# (2 such documents were sitting in the store on 2026-09-01). ocrmypdf is the
# same tool scripts/pipeline/paper_pipeline.py already uses for scanned papers,
# with the same --skip-text invocation, so there is one OCR behaviour to reason
# about rather than two. Costs ~248 MB on an 8.7 GB image; the English language
# pack alone is installed, since the corpus is English and each extra language
# is another ~15 MB for no current gain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ocrmypdf \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application — every top-level module. A glob, not a per-file
# list: a per-file list silently drops any new module (vllm_client,
# usage_tracker, logging_config, tool_result, maintenance, ... were all
# missed this way), and the build still succeeds, so the breakage only
# surfaces as an ImportError at container startup.
COPY *.py ./

# A3 router: the labelled example set is a data file (JSON), so `COPY *.py`
# above does NOT pick it up. Without it the router index fails to build and
# routing silently falls back to the pin. Ship it explicitly.
COPY router_examples.json ./

# Copy MCP module
COPY mcp /app/mcp/

# Copy in-container test scripts (stress-test.py exec's them via
# docker exec; see §5.8).
COPY tests /app/tests/

# Copy agents module
COPY agents /app/agents/

# Copy hooks module (P2 #23). The auto-loader walks this directory
# at startup; missing it would silently disable every hook.
COPY hooks /app/hooks/

# Copy the behaviour-eval harness (on-demand, run via docker exec).
# Not imported by the service; ships so it's available in the container.
COPY evals /app/evals/

# Persona definitions and agent config are mounted at runtime
# (see docker-compose.yml): /app/personas and /app/config.

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

# Run
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
