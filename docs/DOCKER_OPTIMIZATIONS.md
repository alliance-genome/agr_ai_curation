# Docker Optimization Summary

## ✅ All Services Optimized

### 1. Backend Container

**Optimizations:**

- ✅ BuildKit cache mount for pip packages (~1.5GB cached)
- ✅ Layer ordering optimized (requirements before code)
- ✅ `.dockerignore` reduces build context
- ✅ Removed `--no-cache-dir` from pip

**Build command:**

```bash
DOCKER_BUILDKIT=1 docker compose build backend
```

### 2. Frontend Container

**Optimizations:**

- ✅ Multi-stage build (smaller final image)
- ✅ BuildKit cache mount for npm packages
- ✅ Layer ordering optimized (package.json before source)
- ✅ Production-only dependencies in final stage

**Build command:**

```bash
DOCKER_BUILDKIT=1 docker compose build frontend
```

### 3. Database Containers (postgres & postgres-test)

**Current setup:**

- Using official `pgvector/pgvector:pg16` image (no build needed)
- Data persisted in volumes (`postgres_data`, `postgres_test_data`)
- Initialization scripts mounted at startup

**Why no optimization needed:**

- Pre-built images (no build time)
- Data persistence via volumes (survives container restarts)
- Separate test database prevents test pollution

## Quick Commands

### Build Everything with Caching

```bash
# Enable BuildKit and build all services
export DOCKER_BUILDKIT=1
docker compose build
```

### Build Specific Service

```bash
# Backend only
DOCKER_BUILDKIT=1 docker compose build backend

# Frontend only
DOCKER_BUILDKIT=1 docker compose build frontend
```

### Start Services

```bash
# Start all services
docker compose up -d

# Start specific service
docker compose up -d backend
```

### Run Tests

```bash
# After backend is built and running
docker compose exec backend pytest tests/ -v
```

## Cache Statistics

| Service       | First Build     | With Cache | Cache Size |
| ------------- | --------------- | ---------- | ---------- |
| Backend       | 5-10 min        | 30 sec\*   | ~1.5GB     |
| Frontend      | 2-3 min         | 20 sec\*   | ~200MB     |
| Postgres      | N/A (pre-built) | N/A        | N/A        |
| Postgres-test | N/A (pre-built) | N/A        | N/A        |

\*When only application code changes

## Database Notes

### Why Two Postgres Containers?

1. **postgres** (port 5432): Production database
   - Used for development and manual testing
   - Data persists in `./postgres_data`

2. **postgres-test** (port 5433): Test database
   - Used for automated tests
   - Isolated from production data
   - Can be reset without affecting development

### Database Optimization

The databases are already optimized:

- ✅ Using volumes for data persistence
- ✅ Health checks ensure availability
- ✅ pgvector extension pre-installed
- ✅ No build step required (using official images)

## Docker Compose Best Practices

### ✅ What We're Doing Right:

1. **Separate test database** - Tests don't pollute dev data
2. **Health checks** - Services wait for dependencies
3. **Named volumes** - Data persists between container restarts
4. **Volume mounts for code** - Hot reload during development
5. **BuildKit caching** - Fast rebuilds

### 🚀 Performance Tips:

1. **Always use BuildKit:**

   ```bash
   export DOCKER_BUILDKIT=1  # Add to your .bashrc/.zshrc
   ```

2. **Parallel builds:**

   ```bash
   docker compose build --parallel
   ```

3. **Check what's cached:**

   ```bash
   docker compose build --progress=plain backend 2>&1 | grep CACHED
   ```

4. **Clean up when needed:**

   ```bash
   # Remove unused images
   docker image prune

   # Remove build cache
   docker builder prune

   # Full cleanup (careful!)
   docker system prune -a
   ```

## File Structure

```
ai_curation/
├── docker-compose.yml      # Main orchestration
├── .env.docker            # BuildKit environment variables
├── .dockerignore          # Exclude files from build context
├── docker/
│   ├── Dockerfile.backend # Optimized with caching
│   ├── Dockerfile.frontend # Multi-stage with caching
│   └── postgres/
│       └── init-pgvector.sql # Database initialization
└── DOCKER_CACHING.md      # Detailed caching documentation
```

## Summary

All Docker services are now optimized:

- **Backend**: BuildKit caching for Python packages
- **Frontend**: BuildKit caching for npm packages
- **Databases**: Using pre-built images with volumes

No shell scripts needed - everything works with standard `docker compose` commands!
