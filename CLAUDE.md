# CLAUDE.md - Runtime Development Guidance for AGR AI Curation

## 🚨 IMPORTANT: Documentation Access via Context7 MCP

**When you need up-to-date documentation for any library (especially PydanticAI, FastAPI, React, etc.), use the Context7 MCP tools:**

1. First use `mcp__context7__resolve-library-id` to find the library
2. Then use `mcp__context7__get-library-docs` to get the latest documentation

This ensures you're always working with the most current API references and best practices, particularly important for:

- **PydanticAI** - For AI agent development and structured outputs
- **FastAPI** - For backend API development
- **React/MUI** - For frontend components
- **SQLAlchemy** - For database models
- Any other library where you're unsure about the latest patterns or APIs

## Project Overview

AGR AI Curation is an advanced three-panel interface for AI-assisted biocuration, featuring real-time streaming chat with multiple AI models, PDF annotation with multi-color highlighting, and comprehensive curation tools.

**Project Structure**:

```
ai_curation/
├── backend/            # FastAPI backend application
│   ├── app/           # Main application code
│   │   ├── models/    # SQLAlchemy models (11 entities for PDF Q&A)
│   │   ├── api/       # API endpoints
│   │   └── agents/    # PydanticAI agents
│   ├── tests/         # Test suite (unit, integration, contract)
│   └── requirements.txt
├── frontend/          # React frontend application
├── docker/            # Docker configurations
│   ├── postgres/      # PostgreSQL with pgvector setup
│   └── *.Dockerfile   # Service dockerfiles
├── docker-compose.yml # Orchestrates all services
└── specs/            # Feature specifications and planning docs
    └── 002-pdf-document-q/  # Current feature: PDF Document Q&A
```

## Technology Stack

- **Frontend**: React 18, Material-UI v5, Vite, TypeScript
- **Backend**: FastAPI (Python 3.11+), SQLAlchemy, Pydantic v2
- **Database**: PostgreSQL 16 with Alembic migrations
- **AI Services**: OpenAI SDK, Google Generative AI (Gemini)
- **Testing**: Vitest (Frontend), Pytest (Backend)
- **Containerization**: Docker Compose with multi-stage builds

## Constitutional Principles (v1.0.0)

Refer to `.specify/memory/constitution.md` for full details. Key principles:

1. **Library-First**: Every feature starts as a standalone library
2. **CLI Interface**: All services must expose CLI functionality
3. **Test-First (NON-NEGOTIABLE)**: TDD mandatory, Red-Green-Refactor
4. **Integration Testing**: Required for AI services, DB, WebSocket/SSE
5. **Observability**: Structured logging, health checks, metrics
6. **Versioning**: Semantic versioning, Alembic for DB migrations
7. **Simplicity**: YAGNI, max 3 levels React nesting
8. **Tech Stack First**: Prefer existing stack, new tech needs approval

## Development Commands

### Frontend Development

```bash
cd frontend
npm install
npm run dev              # Start dev server at http://localhost:3000
npm run test            # Run Vitest tests
npm run lint            # ESLint + Prettier check
npm run build           # Production build
```

### Backend Development

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002  # Start dev server
pytest                  # Run all tests
pytest -v --cov=app     # With coverage
black .                 # Format code
flake8 .               # Lint code
mypy .                 # Type checking
```

### Docker Development

```bash
docker compose up -d    # Start all services (uses docker compose v2)
docker compose logs -f  # View logs
docker compose down     # Stop services
docker compose build    # Rebuild images
docker compose ps       # Show running containers
```

### Docker Testing

**IMPORTANT**: All backend testing is done via Docker Compose to ensure proper PostgreSQL with pgvector support.

```bash
# Run tests in the backend container
docker compose exec backend pytest tests/unit/test_models.py -v

# Run specific test class
docker compose exec backend pytest tests/unit/test_models.py::TestPDFDocument -v

# Run with coverage
docker compose exec backend pytest tests/ --cov=app --cov-report=term-missing

# Run tests and stay in container for debugging
docker compose exec backend bash
# Then inside container:
cd /app && pytest tests/unit/test_models.py -xvs
```

**Test Database Configuration**:

- Main DB: `ai_curation_db` on port 5432
- Test DB: `ai_curation_db_test` on port 5433 (if configured)
- Both databases have pgvector and necessary extensions pre-installed
- Tests use PostgreSQL, NOT SQLite, due to PostgreSQL-specific features (UUID, JSONB, pgvector, tsvector)

### Database Operations

**Note: This project uses a fresh-start approach - no migrations needed during development**

```bash
# Create/recreate database schema from models
docker compose exec backend python -c "
from app.models import Base
from app.database import engine
Base.metadata.drop_all(engine)  # Clean slate
Base.metadata.create_all(engine)  # Create all tables
"

# For production (when data preservation is needed):
cd backend
alembic revision --autogenerate -m "Description"  # Create migration
alembic upgrade head    # Apply migrations
alembic downgrade -1    # Rollback one migration
```

## Testing Requirements

### Frontend Testing

- Component tests with Vitest and React Testing Library
- Integration tests for user flows
- Mock API calls with MSW or similar
- Coverage target: >80%

### Backend Testing

- Unit tests with Pytest fixtures
- FastAPI endpoint tests with TestClient
- SQLAlchemy model tests with test database
- Integration tests for AI services (use test keys)
- Coverage target: >80%

## Security Checklist

- [ ] No API keys in code (use .env)
- [ ] Pre-commit hooks installed (`./setup-pre-commit.sh`)
- [ ] Input sanitization for all user data
- [ ] CORS configured for production origins only
- [ ] Rate limiting on AI endpoints
- [ ] Dependencies audited (`npm audit`, `pip-audit`)

## Pre-commit Hook Policy

**NEVER skip pre-commit hooks without investigation.** When a pre-commit hook fails:

1. **STOP and investigate** the failure reason
2. **Inform the user** about what failed and why
3. **Fix the issue** if possible (formatting, linting, etc.)
4. **Ask for guidance** if the fix is unclear or risky
5. **Document the resolution** in commit message if needed

Common pre-commit hooks and how to handle failures:

- **detect-secrets**: May indicate hardcoded secrets - investigate and remove
- **black/prettier**: Auto-formatting issues - run the formatter
- **flake8/eslint**: Code quality issues - fix the violations
- **mypy**: Type checking errors - correct type annotations

Only proceed without hooks in exceptional cases WITH explicit user approval and clear documentation of why.

## AI Service Integration

### OpenAI Configuration

```python
# Use environment variable
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Models: gpt-4o, gpt-4o-mini, gpt-3.5-turbo
```

### Gemini Configuration

```python
# Use environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Models: gemini-2.0-flash-exp, gemini-1.5-pro, gemini-1.5-flash
```

## Common Tasks

### Getting Documentation When Stuck

**Always use Context7 MCP for the latest documentation:**

```
# Example: Getting PydanticAI docs
1. Use mcp__context7__resolve-library-id with "pydanticai"
2. Use mcp__context7__get-library-docs with the resolved ID
```

This is especially important when:

- Working with new libraries or unfamiliar APIs
- Implementing AI agents with PydanticAI
- Unsure about the latest patterns or best practices
- Getting errors that might be due to API changes

### Adding a New Feature

1. Create feature spec in `/specs/[feature-name]/spec.md`
2. Run planning: Follow constitutional principles
3. Write tests first (TDD)
4. Implement as library with CLI
5. Add integration tests
6. Update documentation

### Debugging

- Frontend: Use React DevTools + browser console
- Backend: Set `DEBUG=true` in .env for detailed logs
- Database: Use pgAdmin or `psql` for queries
- AI Services: Check rate limits and API keys

### Performance Optimization

- Frontend: React.memo, useMemo, virtualization for lists
- Backend: Database query optimization, caching
- AI: Batch requests, streaming responses
- Docker: Multi-stage builds, layer caching

## Recent Changes (Last 5)

1. **2025-01-15**: Added Docker Compose testing documentation and project structure
2. **2025-01-15**: Fixed SQLAlchemy models (metadata → meta_data, SQLAlchemy 2.0 style)
3. **2025-01-15**: Started PDF Document Q&A implementation (T001-T005)
4. **2025-01-13**: Added Context7 MCP documentation guidance
5. **2025-01-13**: Created constitution v1.0.0 with 8 core principles

## Troubleshooting

### Common Issues

- **CORS errors**: Check backend CORS_ORIGINS in config.py
- **WebSocket disconnects**: Verify nginx proxy configuration
- **Database connection**: Check DATABASE_URL in .env
- **AI rate limits**: Implement exponential backoff

### Getting Help

- Internal docs: `/docs` directory
- API docs: http://localhost:8002/docs (Swagger UI)
- Constitution: `.specify/memory/constitution.md`
- Project README: `/README.md`

---

_Last updated: 2025-01-13 | Constitution v1.0.0_
