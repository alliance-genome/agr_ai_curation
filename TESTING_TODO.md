# Testing TODO

This document tracks testing improvements needed for the AI Curation project.

## Current State

The project has existing tests in `backend/tests/` organized as:
- `unit/` - Unit tests (~180 passing)
- `contract/` - API contract tests (partial coverage)
- `integration/` - Integration tests (need environment setup)

Run tests via Docker:
```bash
./scripts/testing/run-tests.sh all      # Run all tests
./scripts/testing/run-tests.sh unit     # Unit tests only
./scripts/testing/run-tests.sh contract # Contract tests only
```

## Tests to Add/Improve

### High Priority

- [ ] **End-to-end pipeline test** - Upload PDF → Process → Query chunks → Verify results
- [ ] **Agent routing tests** - Verify supervisor routes to correct specialist agents
- [ ] **Ontology API tests** - Upload, streaming progress, status, deletion lifecycle
- [ ] **Authentication flow tests** - Cognito login/logout/session management

### Medium Priority

- [ ] **Weaviate integration tests** - Chunk storage, retrieval, reranking
- [ ] **Chat streaming tests** - SSE event format, cancellation, error handling
- [ ] **File output tests** - CSV/TSV/JSON generation from flows
- [ ] **Curation flow tests** - Flow creation, execution, batch processing

### Low Priority

- [ ] **Agent Studio tests** - Trace analysis, prompt browsing
- [ ] **Maintenance mode tests** - Banner display, service behavior
- [ ] **Multi-tenant isolation tests** - User data separation

## Known Issues

See `docs/developer/TEST_HEALTH_REPORT.md` for:
- List of currently passing tests
- Known broken tests (removed imports)
- Contract test failures needing investigation

## Contributing Tests

When adding tests:
1. Place in appropriate directory (`backend/tests/unit/`, `integration/`, or `contract/`)
2. Follow existing patterns (pytest fixtures, async where needed)
3. Mock external services (Weaviate, Langfuse) in unit tests
4. Use `docker-compose.test.yml` for integration tests requiring services
