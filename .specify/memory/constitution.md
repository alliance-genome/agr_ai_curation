# AGR AI Curation Constitution

## Core Principles

### I. Library-First Architecture

Every feature must start as a standalone, reusable library before integration into the main application:

- Libraries must be self-contained with clear interfaces and dependencies
- Each library requires independent unit tests with >80% coverage
- Documentation must include purpose, API reference, and usage examples
- No organizational-only libraries - each must solve a specific technical problem

### II. CLI Interface Requirement

All backend services and libraries must expose functionality via command-line interface:

- Text in/out protocol: stdin/args → stdout, errors → stderr
- Support both JSON and human-readable output formats
- CLI commands must be discoverable via --help flags
- Integration points must be testable via CLI before GUI implementation

### III. Test-First Development (NON-NEGOTIABLE)

Test-Driven Development is mandatory for all new features and bug fixes:

- Tests written first → User approval → Tests fail → Then implement
- Red-Green-Refactor cycle must be strictly enforced
- Frontend: Vitest for React components, integration tests for user flows
- Backend: Pytest with fixtures for FastAPI endpoints, SQLAlchemy models
- No merge without passing tests and code review

### IV. Integration Testing Priority

Integration tests required for critical system boundaries:

- AI service integrations (OpenAI, Gemini API calls)
- Database transactions and migrations
- PDF processing and annotation persistence
- WebSocket/SSE communication for real-time features
- Docker compose service interactions

### V. Observability & Monitoring

All services must be observable and debuggable in production:

- Structured logging using Python logging/JavaScript console with correlation IDs
- Health check endpoints for all services (/health, /readiness)
- Performance metrics for AI API calls and database queries
- Error tracking with full stack traces and request context
- Debug mode toggles for development troubleshooting

### VI. Versioning & Breaking Changes

Semantic versioning with careful migration planning:

- MAJOR.MINOR.PATCH format for all releases
- Breaking changes require migration scripts and deprecation notices
- Database schema changes via Alembic migrations only
- API versioning strategy: URL path versioning (/v1/, /v2/)
- Frontend/Backend compatibility matrix maintained

### VII. Simplicity & YAGNI

Start simple, iterate based on real user needs:

- No premature optimization or abstraction
- Maximum 3 levels of component nesting in React
- Avoid complex state management until proven necessary
- Prefer composition over inheritance
- Document why complexity is added when unavoidable

### VIII. Tech Stack Integration First

Prioritize integration with existing technology stack before introducing new dependencies:

- **Existing stack preference**: React + MUI, FastAPI, PostgreSQL, Docker
- **New technology requires**: Documented justification, user approval, migration plan
- **Evaluate existing tools**: Check if React/MUI components, FastAPI extensions, or PostgreSQL features can solve the problem
- **Compatibility check**: Ensure new dependencies work with Python 3.11+, Node 20+, and existing Docker setup
- **Security review**: All new dependencies must pass security audit (npm audit, pip-audit)

## Security Requirements

### Data Protection

- No API keys or secrets in code (use environment variables)
- Pre-commit hooks for secret detection (gitleaks)
- Sanitize all user inputs before database operations
- CORS configuration restricted to known origins
- Rate limiting on AI API endpoints

### Authentication & Authorization

- API key management through secure environment configuration
- Service-to-service authentication for internal APIs
- Audit logging for all data modifications
- Role-based access control preparation (future implementation)

## Development Workflow

### Code Review Requirements

- All PRs require at least one approval
- Automated checks must pass: linting, tests, security scans
- Documentation updates for API changes
- Performance impact assessment for database/AI changes

### Deployment Standards

- Docker images must be multi-stage builds for size optimization
- Health checks required before traffic routing
- Database migrations tested in staging first
- Rollback plan documented for each release

### Quality Gates

- Frontend: ESLint, Prettier, TypeScript strict mode
- Backend: Black, isort, mypy, flake8
- Test coverage must not decrease
- No console errors in production builds

## Governance

The Constitution supersedes all other development practices and guidelines:

- All code reviews must verify constitutional compliance
- Amendments require: Written proposal, team discussion, 2/3 approval, migration plan
- Violations must be documented with remediation timeline
- Use /CLAUDE.md for runtime development guidance
- Review constitution quarterly for relevance

**Version**: 1.0.0 | **Ratified**: 2025-01-13 | **Last Amended**: 2025-01-13
