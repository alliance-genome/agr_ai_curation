FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app/backend

# Install build dependencies first (rarely changes)
# Also install Docker CLI for accessing container logs
RUN apt-get update && apt-get install -y \
    gcc \
    git \
    curl \
    ca-certificates \
    gnupg \
    lsb-release \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first for better caching
COPY requirements.txt .

# Configure pip to use PyPI directly and upgrade pip
RUN pip config set global.index-url https://pypi.org/simple && \
    pip install --no-cache-dir --upgrade pip setuptools wheel

# Install Python dependencies with cache mount for faster rebuilds
# Note: Removed --index-url override to allow git+ URLs in requirements.txt to work correctly
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt --retries 3

# Copy source code
COPY src/ ./src/
COPY main.py .
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY pytest.ini .
# Set Python path
ENV PYTHONPATH=/app/backend:$PYTHONPATH

# Expose the API port
EXPOSE 8000

# Run the FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
