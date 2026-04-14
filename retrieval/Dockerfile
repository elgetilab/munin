# ==============================================================================
# MUNIN RETRIEVAL SERVICE - Dockerfile
# ==============================================================================
# Multi-stage build for the RAG retrieval service
# ==============================================================================

FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ==============================================================================
# Final image
# ==============================================================================
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application
COPY main.py .
COPY database.py .
COPY models.py .
COPY chat_store.py .
COPY chat_context.py .
COPY chat_service.py .
COPY personas.py .
COPY document_store.py .
COPY user_profile_store.py .
COPY vision.py .

# Copy MCP module
COPY mcp/ /app/mcp/

# Copy in-container test scripts (stress-test.py exec's them via
# docker exec; see §5.8).
COPY tests/ /app/tests/

# Copy agents module
COPY agents/ /app/agents/

# Copy static files (search UI)
COPY static/ /app/static/

# Persona definitions and agent config are mounted at runtime
# (see docker-compose.yml): /app/personas and /app/config.

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

# Run
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
