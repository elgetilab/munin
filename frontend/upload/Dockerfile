FROM python:3.12-slim
WORKDIR /app
RUN pip install fastapi uvicorn httpx --no-cache-dir
COPY hook_service.py .
CMD ["uvicorn", "hook_service:app", "--host", "0.0.0.0", "--port", "18088", "--workers", "1"]
