FROM python:3.12.15-slim
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY main.py models.py ./
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8070"]
