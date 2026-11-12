# ==============================================================================
# MUNIN LLM-FAILOVER - vLLM first, llm-claude when vLLM is down
# (docs/ops/HAIKU-FALLBACK.md)
# ==============================================================================
FROM python:3.12.15-slim

WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY main.py failover.py ./

RUN useradd --create-home --uid 1000 failover
USER failover

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"

EXPOSE 8000
CMD ["uvicorn", "main:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
