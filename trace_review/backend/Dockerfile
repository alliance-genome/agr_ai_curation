FROM python:3.11-slim AS runtime-dependencies

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM runtime-dependencies AS test

COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

COPY src/ ./src/
COPY tests/ ./tests/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPYCACHEPREFIX=/tmp/trace-review-python-pycache

CMD ["python", "-m", "pytest", "tests", "-q", "-o", "cache_dir=/tmp/trace-review-pytest-cache"]


FROM runtime-dependencies AS development

COPY . .

EXPOSE 8001
ENV BACKEND_PORT=8001
CMD ["sh", "-c", "exec uvicorn src.main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8001} --reload"]
