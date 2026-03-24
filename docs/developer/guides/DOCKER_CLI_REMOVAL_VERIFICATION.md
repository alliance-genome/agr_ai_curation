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
- `docker compose build backend` completed successfully on this tree. The build had no Docker-related errors, but the build still reflects a dev Dockerfile that contains Docker CLI install steps.
- Appendix A contains the exhaustive broad-sweep inventory of all current `backend/` matches for `docker`, `COMPOSE_PROJECT_NAME`, `DOCKER_HOST`, and `/var/run/docker.sock`.

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

- `backend/src/api/logs.py:4-5`
  - Module docstring says the endpoint provides Docker container logs and is used by the `get_docker_logs` tool.
- `backend/src/api/logs.py:45`
  - Handler docstring says it returns Docker container logs.
- `backend/src/api/logs.py:67-77`
  - Reads `COMPOSE_PROJECT_NAME`.
  - Builds a container name and executes `docker logs`.
- `backend/src/api/logs.py:104-107`
  - Returns a Docker-CLI-specific error message when the binary is missing.

This is the expected out-of-scope implementation owned by ALL-144.

### 3. Indirect runtime exposure of Docker log access

- `backend/src/api/agent_studio.py:1333-1369`
  - Defines the `get_docker_logs` tool contract and exports it in the workflow tool registry.
- `backend/src/api/agent_studio.py:1468`
  - Includes `GET_DOCKER_LOGS_TOOL` in the tool list.
- `backend/src/api/agent_studio.py:1717`
  - Imports `get_docker_logs`.
- `backend/src/api/agent_studio.py:1823-1827`
  - Dispatches tool calls to `get_docker_logs()`.
- `backend/src/api/agent_studio_system_prompt.md:209`
  - Documents the `get_docker_logs(container, lines)` tool for the agent.
- `backend/src/lib/agent_studio/tools.py:4-16`
  - Module docstring describes Docker log retrieval as part of the workflow analysis tool surface.
- `backend/src/lib/agent_studio/tools.py:38`
  - `get_trace_review_url()` comment references the shared Compose network for local Docker development.
- `backend/src/lib/agent_studio/tools.py:685-770`
  - `get_docker_logs()` still exists and forwards requests to `/api/logs/{container}`.

These are not new direct CLI calls, but they still keep Docker-log access reachable through the Agent Studio surface until ALL-145 removes or rewires the tool.

### 4. Other backend source and build references that mention Docker or Compose

- `backend/Dockerfile:1`
  - Uses the `public.ecr.aws/docker/library/python:3.11-slim` base image path.
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
- `backend/src/lib/logging_config.py:4`
  - Logging docstring mentions the Docker GELF driver.
- `backend/src/lib/openai_agents/langfuse_client.py:97`
  - Comment says the internal host is used for Docker container networking.
- `backend/src/lib/weaviate_client/connection.py:62`
  - Comment says `weaviate` is the Docker container hostname for local connections.
- `backend/Dockerfile.prod:1,23`
  - Uses `public.ecr.aws/docker/library/python:3.11-slim` in the production multi-stage build.
- `backend/Dockerfile.unit-test:1,5,7`
  - Unit-test build file comments mention Docker image usage and the `docker build` example.

These references do not invoke Docker CLI.

### 5. Backend docs and test references

- `backend/README.md:64`
  - Directory tree documents the `Dockerfile`.
- `backend/README.md:187-191`
  - Development section tells users to build and run with Docker Compose.
- `backend/README.md:280-282`
  - Docker configuration section describes the backend container and Dockerfile.
- `backend/README.md:191`
  - `docker-compose up backend`.
- `backend/README.md:311`
  - `docker-compose logs -f backend`.
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
- `backend/tests/integration/test_document_isolation.py:36`
  - Docstring says the test uses PostgreSQL from Docker Compose.
- `backend/tests/integration/test_document_isolation.py:125-126`
  - Uses `/.dockerenv` and Docker-specific host selection.
- `backend/tests/integration/test_login_provisioning.py:27`
  - Docstring says the test uses PostgreSQL from Docker Compose.
- `backend/tests/integration/test_logout.py:36`
  - Docstring says the test uses PostgreSQL from Docker Compose.
- `backend/tests/integration/test_logout.py:149`
  - Comment explains Docker containers on EC2 during test isolation.
- `backend/tests/unit/test_config_loaders.py:4`
  - Docstring shows a `docker compose ... backend-unit-tests` command.
- `backend/tests/unit/test_config_loaders.py:14,996`
  - Comments mention Docker container mounts and Docker-specific defaults.
- `backend/tests/fixtures/generate_pdfx_fixture.py:7-8`
  - Docstring shows a `docker compose ...` command.
- `backend/tests/fixtures/generate_pdfx_fixture.py:4`
  - Docstring says the script can run inside the backend Docker container.
- `backend/tests/unit/test_config.py:233-240`
  - Test docstrings/comments mention Docker Compose env overrides.
- `backend/tests/unit/test_config.py:241,245`
  - Test body uses the `docker_override` fixture data and asserts the Docker service hostname.
- `backend/tests/integration/conftest.py:24`
  - Checks `/.dockerenv`.
- `backend/tests/integration/conftest.py:33`
  - Comment explains host-vs-Docker integration test execution.
- `backend/tests/integration/persistence/conftest.py:31,56`
  - Uses `/.dockerenv` and a docker-compose service hint.
- `backend/tests/conftest.py:19`
  - Comment says the scripts directory is mounted inside the Docker container.
- `backend/tests/conftest.py:25-51`
  - Helper/commentary for Docker-vs-host test execution.
- `backend/tests/unit/lib/test_weaviate_connection.py:36,56`
  - Mentions local Docker hostnames and docker-compose env defaults.
- `backend/tests/unit/test_docker_compose_production.py:11,152`
  - Production compose test references.
- `backend/tests/unit/lib/packages/__init__.py:28`
  - Uses `docker-compose.test.yml` existence as a repository-root marker.
- `backend/tests/contract/test_health.py:241,247`
  - Contract test verifies Docker environment reporting from `/api/health`.
- `backend/tests/live_integration/test_backend_chat_live_pdf_qa.py:153`
  - Comment mentions cleanup from previous Docker runs.
- `backend/tests/live_integration/test_backend_flow_live_llm.py:168`
  - Comment mentions cleanup from previous Docker runs.
- `backend/tests/live_integration/test_backend_pdfx_live_pipeline.py:95`
  - Comment mentions cleanup from previous Docker runs.

## Exact grep outcomes

### Docker CLI command patterns

- `docker logs`
  - `backend/src/api/logs.py:71`
  - `backend/src/lib/agent_studio/tools.py:4` (docstring mention only via "Docker logs")
- `docker ps`
  - No matches.
- `docker exec`
  - No matches.
- `docker compose`
  - Present in docs, tests, and comments; the only live runtime Docker CLI execution remains `backend/src/api/logs.py:71-77`.

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
rg -n -i "(docker|COMPOSE_PROJECT_NAME|DOCKER_HOST|/var/run/docker\\.sock)" backend
docker compose build backend
```

Build result:

- `docker compose build backend` exited successfully.
- The build emitted the existing Docker Compose unset-environment warnings but no Docker-related build errors.
- The current dev Dockerfile still contains the Docker CLI install step at `backend/Dockerfile:6-18`, so this build confirms the current tree is runnable rather than confirming post-removal behavior.

## Appendix A. Exhaustive broad-sweep inventory

Raw output from `rg -n -i "(docker|COMPOSE_PROJECT_NAME|DOCKER_HOST|/var/run/docker\\.sock)" backend`:

```text
backend/README.md:64:└── Dockerfile                      # Container definition
backend/README.md:187:### Docker Development
backend/README.md:189:Build and run with Docker Compose from the root directory:
backend/README.md:191:docker-compose up backend
backend/README.md:280:## Docker Configuration
backend/README.md:282:The backend runs on port 8000 inside the container. The Dockerfile includes:
backend/README.md:311:docker-compose logs -f backend
backend/Dockerfile.prod:1:FROM public.ecr.aws/docker/library/python:3.11-slim AS builder
backend/Dockerfile.prod:23:FROM public.ecr.aws/docker/library/python:3.11-slim AS runtime
backend/Dockerfile.unit-test:1:# Lightweight Docker image for unit tests only
backend/Dockerfile.unit-test:5:# NOTE: This Dockerfile expects to be built from the PROJECT ROOT context
backend/Dockerfile.unit-test:7:# Example: docker build -f backend/Dockerfile.unit-test .
backend/Dockerfile:1:FROM public.ecr.aws/docker/library/python:3.11-slim
backend/Dockerfile:6:# Also install Docker CLI for accessing container logs
backend/Dockerfile:15:    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
backend/Dockerfile:16:    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
backend/Dockerfile:18:    && apt-get install -y docker-ce-cli \
backend/tests/integration/conftest.py:24:    if os.path.exists("/.dockerenv"):
backend/tests/integration/conftest.py:33:# Integration tests run from the host (.venv) should target the Docker-mapped
backend/tests/integration/test_login_provisioning.py:27:    """Use actual PostgreSQL database from Docker Compose.
backend/tests/integration/persistence/conftest.py:31:default_weaviate_host = "weaviate-test" if os.path.exists("/.dockerenv") else "127.0.0.1"
backend/tests/integration/persistence/conftest.py:56:            "Ensure docker-compose.test.yml weaviate-test service is running."
backend/tests/integration/test_document_isolation.py:36:    """Use actual PostgreSQL database from Docker Compose."""
backend/tests/integration/test_document_isolation.py:125:    in_docker = os.path.exists("/.dockerenv")
backend/tests/integration/test_document_isolation.py:126:    monkeypatch.setenv("WEAVIATE_HOST", "weaviate-test" if in_docker else "127.0.0.1")
backend/tests/integration/test_quickstart_validation.py:613:            result = subprocess.run(['docker', 'compose', 'ps'], capture_output=True, text=True, check=False)
backend/tests/integration/test_logout.py:36:    """Use actual PostgreSQL database from Docker Compose.
backend/tests/integration/test_logout.py:149:    # Disable EC2 detection for test isolation.  Docker containers on EC2
backend/alembic/versions/z8a9b0c1d2e3_add_tool_policies_table.py:85:        if (candidate / "docker-compose.test.yml").exists():
backend/src/api/logs.py:4:Provides access to Docker container logs for troubleshooting.
backend/src/api/logs.py:5:Used by Opus Workflow Analysis feature's get_docker_logs tool.
backend/src/api/logs.py:45:    Get Docker container logs.
backend/src/api/logs.py:67:    project_name = os.getenv("COMPOSE_PROJECT_NAME", "ai_curation_prototype")
backend/src/api/logs.py:71:        # Execute docker logs command (not compose logs, since we're inside a container)
backend/src/api/logs.py:72:        cmd = ["docker", "logs", "--tail", str(lines), container_name]
backend/src/api/logs.py:107:            detail="Docker CLI not found. Ensure Docker is installed and socket is mounted."
backend/tests/unit/test_config.py:232:    def test_configuration_with_docker_compose_override(self):
backend/tests/unit/test_config.py:233:        """Test that Docker Compose environment variables override .env file."""
backend/tests/unit/test_config.py:234:        # Simulate Docker Compose override
backend/tests/unit/test_config.py:235:        docker_override = {
backend/tests/unit/test_config.py:236:            'WEAVIATE_HOST': 'weaviate',  # Docker service name
backend/tests/unit/test_config.py:240:        # Docker Compose values should take precedence
backend/tests/unit/test_config.py:241:        with patch.dict(os.environ, docker_override):
backend/tests/unit/test_config.py:245:            assert url == "http://weaviate:8080"  # Uses Docker service name
backend/tests/unit/test_config_loaders.py:4:Run with: docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
backend/tests/unit/test_config_loaders.py:14:# In Docker, backend is mounted at /app/backend, so parent is /app.
backend/tests/unit/test_config_loaders.py:996:        # Note: This will use DEFAULT_AGENTS_PATH which may differ in Docker
backend/tests/unit/test_docker_compose_production.py:11:COMPOSE_PATH = WORKSPACE_ROOT / "docker-compose.production.yml"
backend/tests/unit/test_docker_compose_production.py:152:    assert "docker-compose.production.yml" in start_verify_script
backend/src/api/agent_studio.py:1333:GET_DOCKER_LOGS_TOOL = {
backend/src/api/agent_studio.py:1334:    "name": "get_docker_logs",
backend/src/api/agent_studio.py:1335:    "description": "Retrieve Docker container logs for troubleshooting. Use this when curators report errors or unexpected behavior to help diagnose issues.",
backend/src/api/agent_studio.py:1369:    "get_docker_logs",
backend/src/api/agent_studio.py:1468:        GET_DOCKER_LOGS_TOOL,
backend/src/api/agent_studio.py:1717:        get_docker_logs,
backend/src/api/agent_studio.py:1823:    elif tool_name == "get_docker_logs":
backend/src/api/agent_studio.py:1827:        result = await get_docker_logs(container=container, lines=lines)
backend/tests/conftest.py:19:# The scripts directory is mounted at /app/scripts in the Docker container
backend/tests/conftest.py:25:def _running_in_docker() -> bool:
backend/tests/conftest.py:27:    return Path("/.dockerenv").exists()
backend/tests/conftest.py:35:    if _running_in_docker():
backend/tests/conftest.py:44:# Keep backend tests deterministic across host `.venv` runs and Docker CI runs.
backend/tests/conftest.py:45:# Host runs should target docker-compose published ports; container runs should
backend/tests/conftest.py:51:    os.environ["WEAVIATE_HOST"] = "weaviate-test" if _running_in_docker() else "127.0.0.1"
backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:179:async def test_get_docker_logs_success_and_error_branches(monkeypatch):
backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:186:    success = await tools.get_docker_logs(container="backend", lines=50)
backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:192:    bad_container = await tools.get_docker_logs(container="unknown", lines=200)
backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:197:    timeout = await tools.get_docker_logs(container="backend", lines=200)
backend/tests/unit/lib/agent_studio/test_trace_review_tools.py:203:    connect = await tools.get_docker_logs(container="backend", lines=200)
backend/tests/unit/api/test_logs_api.py:41:    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "myproj")
backend/tests/unit/api/test_logs_api.py:49:    assert captured["cmd"] == ("docker", "logs", "--tail", "120", "myproj-backend-1")
backend/tests/unit/api/test_logs_api.py:89:async def test_get_container_logs_handles_missing_docker_cli(monkeypatch):
backend/tests/unit/api/test_logs_api.py:90:    async def _missing_docker(*_cmd, **_kwargs):
backend/tests/unit/api/test_logs_api.py:91:        raise FileNotFoundError("docker not found")
backend/tests/unit/api/test_logs_api.py:93:    monkeypatch.setattr(logs_api.asyncio, "create_subprocess_exec", _missing_docker)
backend/tests/unit/api/test_logs_api.py:98:    assert "Docker CLI not found" in exc.value.detail
backend/src/api/health.py:65:        "docker": os.path.exists("/.dockerenv"),
backend/tests/unit/lib/test_weaviate_connection.py:36:def test_connect_uses_local_docker_hostname(monkeypatch):
backend/tests/unit/lib/test_weaviate_connection.py:56:    # Ensure service-level defaults from docker-compose env do not leak into this unit expectation.
backend/tests/fixtures/generate_pdfx_fixture.py:4:Usage (from inside the backend Docker container):
backend/tests/fixtures/generate_pdfx_fixture.py:7:Or via docker compose:
backend/tests/fixtures/generate_pdfx_fixture.py:8:    docker compose -f docker-compose.test.yml run --rm \
backend/src/api/maintenance.py:15:# Path to the maintenance message file (mounted via docker-compose)
backend/src/api/agent_studio_system_prompt.md:209:- **`get_docker_logs(container, lines)`** - System logs. Use only for failed calls or reported errors. Containers: backend, weaviate, postgres.
backend/tests/contract/test_health.py:241:    def test_health_check_docker_environment(self, client, mock_weaviate_connection):
backend/tests/contract/test_health.py:247:            assert response.json()["details"]["environment"]["docker"] is True
backend/src/lib/agent_studio/tools.py:4:Provides tool functions for Opus to dynamically query trace data and Docker logs.
backend/src/lib/agent_studio/tools.py:16:- get_docker_logs: Container log retrieval
backend/src/lib/agent_studio/tools.py:38:    on the shared Compose network. For local development outside Docker, set
backend/src/lib/agent_studio/tools.py:685:async def get_docker_logs(container: str = "backend", lines: int = 2000) -> Dict[str, Any]:
backend/src/lib/agent_studio/tools.py:687:    Retrieve Docker container logs for troubleshooting.
backend/src/lib/agent_studio/tools.py:748:                    "help": "Check Docker service and container status"
backend/src/lib/agent_studio/tools.py:770:            "help": "Verify Docker is accessible and container name is correct"
backend/src/lib/logging_config.py:4:when sent via Docker GELF driver, while remaining human-readable in local development.
backend/tests/live_integration/test_backend_chat_live_pdf_qa.py:153:    # (for example from previous Docker runs). Force an isolated writable path.
backend/tests/live_integration/test_backend_flow_live_llm.py:168:    # from previous Docker runs. Force an isolated writable storage root.
backend/tests/live_integration/test_backend_pdfx_live_pipeline.py:95:    # from previous Docker runs. Force an isolated writable storage root.
backend/tests/unit/lib/packages/__init__.py:28:        if (candidate / "docker-compose.test.yml").exists():
backend/src/lib/config/package_default_sources.py:42:        if (candidate / "docker-compose.test.yml").exists():
backend/src/lib/config/agent_sources.py:23:        if (candidate / "docker-compose.test.yml").exists():
backend/src/lib/runtime_entrypoint.py:191:    # Docker Compose project-scoped Postgres containers are typically named
backend/src/lib/openai_agents/langfuse_client.py:97:        # Use internal host for Docker container networking
backend/src/lib/weaviate_client/connection.py:62:                    # Docker container hostname
```

## Follow-up ownership

- ALL-144: remove the direct `docker logs` dependency from `backend/src/api/logs.py`.
- ALL-145: remove or rewire the `get_docker_logs` Agent Studio tool path in `backend/src/lib/agent_studio/tools.py` and `backend/src/api/agent_studio.py`.
- A separate implementation change is still required to remove the Docker CLI install steps from `backend/Dockerfile`.
