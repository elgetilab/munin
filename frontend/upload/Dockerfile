FROM python:3.12.15-slim
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY hook_service.py .
CMD ["uvicorn", "hook_service:app", "--host", "0.0.0.0", "--port", "18088", "--workers", "1"]
