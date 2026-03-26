FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8001
ENV BACKEND_PORT=8001
CMD ["sh", "-c", "exec uvicorn src.main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8001} --reload"]
