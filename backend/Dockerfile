FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app/backend

# Install build dependencies first (rarely changes)
# Security hardening 2026-03-24: removed curl, docker-ce-cli, and Docker repo setup.
# The container no longer needs Docker CLI (docker.sock removed from compose).
# curl removed to reduce attack surface for supply-chain payloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    ripgrep \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements files first for better caching
# requirements.lock.txt is the pinned lockfile (exact versions, generated from clean image)
# requirements.txt is the human-readable spec (used only when regenerating the lockfile)
COPY requirements.lock.txt requirements.txt ./

# Configure pip to use PyPI directly and upgrade pip
RUN pip config set global.index-url https://pypi.org/simple && \
    pip install --no-cache-dir --upgrade pip setuptools wheel

# Install from lockfile for reproducible, pinned builds
# Security hardening 2026-03-24: use lockfile to prevent supply chain version drift
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.lock.txt --retries 3

# Post-install security check: only allow known .pth files (supply chain defense)
# The litellm 1.82.8 attack (2026-03-24) used a malicious .pth file to execute
# obfuscated code on every Python startup. This whitelist catches any new .pth files.
RUN KNOWN_PTH="a1_coverage.pth distutils-precedence.pth sphinxcontrib_jsmath-1.0.1-py3.7-nspkg.pth" && \
    UNKNOWN="" && \
    for pth in $(find /usr/local/lib/python3.11/site-packages/ -maxdepth 1 -name "*.pth" -exec basename {} \;); do \
      if ! echo "$KNOWN_PTH" | grep -qw "$pth"; then UNKNOWN="$UNKNOWN $pth"; fi; \
    done && \
    if [ -n "$UNKNOWN" ]; then \
      echo "SECURITY: Unknown .pth file(s) detected:$UNKNOWN" && \
      echo "If legitimate, add to KNOWN_PTH whitelist in Dockerfile." && exit 1; \
    else echo "SECURITY: .pth whitelist check passed ($(echo $KNOWN_PTH | wc -w) known files)"; fi

# Copy source code
COPY src/ ./src/
COPY main.py .
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY tests/ ./tests/
COPY pytest.ini .
# Set Python path
ENV PYTHONPATH=/app/backend:$PYTHONPATH

# Expose the API port
EXPOSE 8000

# Run the FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
