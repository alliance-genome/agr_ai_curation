# Docker CLI Removal Verification

Audit date: 2026-03-24

This note records the ALL-148 verification pass for backend Docker CLI hardening after the litellm 1.82.8 supply-chain incident.

## Summary

- The hardening is not complete in the current tree. `backend/Dockerfile` still installs `curl`, `gnupg`, `lsb-release`, Docker's apt repository metadata, and `docker-ce-cli` at `backend/Dockerfile:6-18`.
- No `backend/` references to `/var/run/docker.sock` were found.
- No `backend/` references to `DOCKER_HOST` were found.
- `COMPOSE_PROJECT_NAME` remains in `backend/src/api/logs.py:67` and the matching unit test at `backend/tests/unit/api/test_logs_api.py:41`.
- Direct Docker CLI execution remains limited to `backend/src/api/logs.py:71-77`, which is the expected out-of-scope endpoint for ALL-144.
- The Agent Studio surface still exposes Docker log retrieval indirectly through `backend/src/api/agent_studio.py` and `backend/src/lib/agent_studio/tools.py`, which matches the out-of-scope follow-up for ALL-145.
- `docker compose build backend` completed successfully on this tree. The build had no Docker-related errors, but it succeeded because the current `backend/Dockerfile` still installs Docker CLI support.

## Scope Checklist

- [ ] Confirm `curl`, `docker-ce-cli`, `gnupg`, `lsb-release` are removed from `backend/Dockerfile`.
- [x] Grep `backend/` for Docker CLI references.
- [x] Grep `backend/` for `/var/run/docker.sock`.
- [x] Grep `backend/` for `DOCKER_HOST` and `COMPOSE_PROJECT_NAME`.
- [x] Check for API/tool dependencies beyond `logs.py` and `get_docker_logs()`.
- [x] Build the dev image with `docker compose build backend`.
- [x] Document remaining Docker references with file paths and line numbers.

## Findings

### 1. Blocking hardening gap

- `backend/Dockerfile:6-18`
  - Still installs `curl`, `gnupg`, `lsb-release`, sets up Docker's apt repo, and installs `docker-ce-cli`.
  - This means the primary hardening objective has not landed in the current branch snapshot yet.

### 2. Direct runtime Docker CLI dependency

- `backend/src/api/logs.py:67-77`
  - Reads `COMPOSE_PROJECT_NAME`.
  - Builds a container name and executes `docker logs`.
- `backend/src/api/logs.py:104-107`
  - Returns a Docker-CLI-specific error message when the binary is missing.

This is the expected out-of-scope implementation owned by ALL-144.

### 3. Indirect runtime exposure of Docker log access

- `backend/src/lib/agent_studio/tools.py:685-770`
  - `get_docker_logs()` still exists and forwards requests to `/api/logs/{container}`.
- `backend/src/api/agent_studio.py:1333-1369`
  - Registers `get_docker_logs` as an available tool.
- `backend/src/api/agent_studio.py:1468`
  - Includes `GET_DOCKER_LOGS_TOOL` in the tool list.
- `backend/src/api/agent_studio.py:1716-1723`
  - Imports `get_docker_logs`.
- `backend/src/api/agent_studio.py:1823-1827`
  - Dispatches tool calls to `get_docker_logs()`.
- `backend/src/api/agent_studio_system_prompt.md:209`
  - Documents the `get_docker_logs(container, lines)` tool for the agent.

These are not new direct CLI calls, but they still keep Docker-log access reachable through the Agent Studio surface until ALL-145 removes or rewires the tool.

### 4. Other backend source references that mention Docker or Compose

- `backend/src/lib/runtime_entrypoint.py:191`
  - Comment describing Docker Compose Postgres container naming.
- `backend/src/api/health.py:65`
  - Reports whether the app is running inside Docker via `/.dockerenv`.
- `backend/src/api/maintenance.py:15`
  - Comment says the maintenance message file is mounted via docker-compose.
- `backend/src/lib/config/agent_sources.py:23`
  - Detects repository root by looking for `docker-compose.test.yml`.
- `backend/src/lib/config/package_default_sources.py:42`
  - Detects repository root by looking for `docker-compose.test.yml`.
- `backend/alembic/versions/z8a9b0c1d2e3_add_tool_policies_table.py:85`
  - Uses `docker-compose.test.yml` existence as a repository-root marker.

These references do not invoke Docker CLI.

### 5. Backend docs and test references

- `backend/README.md:191`
  - `docker-compose up backend`
- `backend/README.md:311`
  - `docker-compose logs -f backend`
- `backend/tests/unit/api/test_logs_api.py:41`
  - Sets `COMPOSE_PROJECT_NAME` in the logs API test.
- `backend/tests/unit/api/test_logs_api.py:49`
  - Expects the `docker logs` command tuple.
- `backend/tests/unit/api/test_logs_api.py:89-93`
  - Exercises the missing-Docker-CLI branch.
- `backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:179-203`
  - Tests `get_docker_logs()`.
- `backend/tests/integration/test_quickstart_validation.py:613`
  - Runs `docker compose ps` from an integration test.
- `backend/tests/unit/test_config_loaders.py:4`
  - Docstring shows a `docker compose ... backend-unit-tests` command.
- `backend/tests/fixtures/generate_pdfx_fixture.py:7-8`
  - Docstring shows a `docker compose ...` command.
- `backend/tests/integration/test_document_isolation.py:36`
  - Docstring mentions Docker Compose.
- `backend/tests/integration/test_login_provisioning.py:27`
  - Docstring mentions Docker Compose.
- `backend/tests/integration/test_logout.py:36`
  - Docstring mentions Docker Compose.
- `backend/tests/unit/test_config.py:233-240`
  - Test docstrings/comments mention Docker Compose env overrides.
- `backend/tests/integration/conftest.py:24`
  - Checks `/.dockerenv`.
- `backend/tests/integration/persistence/conftest.py:31,56`
  - Uses `/.dockerenv` and a docker-compose service hint.
- `backend/tests/conftest.py:25-51`
  - Helper/commentary for Docker-vs-host test execution.
- `backend/tests/unit/lib/test_weaviate_connection.py:36,56`
  - Mentions local Docker hostnames and docker-compose env defaults.
- `backend/tests/unit/test_docker_compose_production.py:11,152`
  - Production compose test references.
- `backend/tests/unit/lib/packages/__init__.py:28`
  - Uses `docker-compose.test.yml` existence as a repository-root marker.

## Exact grep outcomes

### Docker CLI command patterns

- `docker logs`
  - `backend/src/api/logs.py:71`
  - `backend/src/lib/agent_studio/tools.py:4` (docstring mention only)
- `docker ps`
  - No matches.
- `docker exec`
  - No matches.
- `docker compose`
  - Present only in docs, tests, and comments; no live backend source executes `docker compose`.

### Socket and environment variables

- `/var/run/docker.sock`
  - No matches in `backend/`.
- `DOCKER_HOST`
  - No matches in `backend/`.
- `COMPOSE_PROJECT_NAME`
  - `backend/src/api/logs.py:67`
  - `backend/tests/unit/api/test_logs_api.py:41`

## Validation

Commands run during this audit:

```bash
git fetch origin main
rg -n "curl|docker-ce-cli|gnupg|lsb-release" backend/Dockerfile
rg -n -S "docker logs|docker ps|docker exec|docker compose" backend
rg -n -S "/var/run/docker\\.sock" backend
rg -n -S "DOCKER_HOST|COMPOSE_PROJECT_NAME" backend
rg -n -i -S "docker|COMPOSE_PROJECT_NAME|DOCKER_HOST|/var/run/docker\\.sock" backend
docker compose build backend
```

Build result:

- `docker compose build backend` exited successfully.
- The build reused a cached layer for the Docker CLI install step from `backend/Dockerfile:7-18`.
- No Docker-related build errors were observed.

## Follow-up ownership

- ALL-144: remove the direct `docker logs` dependency from `backend/src/api/logs.py`.
- ALL-145: remove or rewire the `get_docker_logs` Agent Studio tool path in `backend/src/lib/agent_studio/tools.py` and `backend/src/api/agent_studio.py`.
- A separate implementation change is still required to remove the Docker CLI install steps from `backend/Dockerfile`.
