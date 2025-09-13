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
docker-compose up -d    # Start all services
docker-compose logs -f  # View logs
docker-compose down     # Stop services
docker-compose build    # Rebuild images
```

### Database Operations

```bash
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

## Recent Changes (Last 3)

1. **2025-01-13**: Added Context7 MCP documentation guidance
2. **2025-01-13**: Created constitution v1.0.0 with 8 core principles
3. **2025-01-13**: Added Tech Stack Integration First principle

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
