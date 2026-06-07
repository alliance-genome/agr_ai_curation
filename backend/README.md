# AI Curation Platform - Backend API

## Overview

Unified FastAPI backend serving both AI Chat and Weaviate Control Panel APIs. This service provides:
- AI-powered chat functionality using OpenAI Agents SDK with config-driven agents
- Agent Workshop for browsing, customizing, and managing agents
- Domain-envelope curation runtime: domain-pack metadata, persisted envelope checkpoints, validation findings/history, review-row materialization, and export/submission readiness
- Multi-provider LLM support (OpenAI, Gemini, Groq) via pluggable provider architecture
- Vector database management via Weaviate
- PDF document processing and chunking
- OpenTelemetry tracing with Langfuse

## Architecture

```
backend/
├── src/
│   ├── api/                        # API endpoints
│   │   ├── chat.py                 # AI chat endpoints
│   │   ├── agent_studio.py         # Agent Studio (catalog, Opus chat, traces)
│   │   ├── agent_studio_custom.py  # Custom agent CRUD endpoints
│   │   ├── documents.py            # Document management
│   │   ├── chunks.py               # Document chunking
│   │   ├── batch.py                # Batch processing endpoints
│   │   ├── flows.py                # Curation flow endpoints
│   │   ├── curation_workspace.py   # Curation review, envelope rows, field patches, export/submission
│   │   ├── processing.py           # PDF processing
│   │   ├── schema.py               # Schema management
│   │   ├── settings.py             # Settings endpoints
│   │   ├── strategies.py           # Processing strategies
│   │   └── health.py               # Health checks
│   ├── lib/                        # Core libraries
│   │   ├── agent_studio/           # Agent Studio services
│   │   │   ├── agent_service.py    # Unified agent CRUD and visibility
│   │   │   ├── catalog_service.py  # Prompt catalog builder
│   │   │   ├── registry_builder.py # YAML-to-registry bridge
│   │   │   ├── tool_policy_service.py  # Tool library policy cache
│   │   │   └── tool_idea_service.py    # Tool idea request workflow
│   │   ├── config/                 # Configuration loaders
│   │   │   ├── agent_loader.py     # Loads agent.yaml files
│   │   │   ├── models_loader.py    # Loads config/models.yaml
│   │   │   ├── providers_loader.py # Loads config/providers.yaml
│   │   │   ├── provider_validation.py  # Cross-validates providers and models
│   │   │   ├── schema_discovery.py # Discovers schema.py files
│   │   │   ├── groups_loader.py    # Loads groups.yaml
│   │   │   └── connections_loader.py   # Loads connections.yaml
│   │   ├── openai_agents/          # OpenAI Agents SDK integration
│   │   │   └── agents/
│   │   │       └── supervisor_agent.py # Supervisor (routes to config-driven agents)
│   │   ├── batch/                  # Batch processing engine
│   │   ├── flows/                  # Curation flow executor
│   │   ├── domain_packs/           # Domain-pack registry, validation, repair, materialization
│   │   ├── domain_envelopes/       # Envelope checkpoints, indexes, and field patches
│   │   ├── curation_workspace/     # Review sessions, projections, validation, export/submission
│   │   ├── weaviate_client/        # Weaviate integration
│   │   └── pipeline/               # Processing pipeline
│   └── models/                     # Data models
│       └── sql/
│           ├── agent.py            # Agent, Project, ProjectMember tables
│           ├── tool_policy.py      # ToolPolicy table
│           ├── tool_idea_request.py # ToolIdeaRequest table
│           ├── custom_agent.py     # Legacy custom agent (compatibility)
│           └── ...
├── alembic/                        # Database migrations
├── tests/                          # Test suite
├── main.py                         # FastAPI application
├── requirements.txt                # Python dependencies
└── Dockerfile                      # Container definition
```

### Config Directory (project root)

Agent definitions and LLM provider configuration live outside the backend in the
project-level `config/` directory, which is mounted read-only into the container:

```
config/
├── agents/                  # Agent definitions (loaded at startup)
│   ├── supervisor/          # Core supervisor agent
│   ├── gene/                # Gene validation agent
│   ├── disease/             # Disease validation agent
│   └── [your_agent]/        # Custom agents
├── models.yaml              # LLM model catalog (curator-selectable models)
├── providers.yaml           # LLM provider definitions (OpenAI, Gemini, Groq)
├── groups.yaml              # Group/Cognito mapping
├── connections.yaml         # External service connections
└── tool_policy_defaults.yaml # Default tool visibility policies
```

## API Endpoints

### Chat API (`/api`)
- `POST /api/chat` - Send a message and get a response
- `POST /api/chat/stream` - Stream responses via Server-Sent Events
- `GET /api/chat/status` - Check chat service status

### Agent Studio (`/api/agent-studio`)
- `GET /api/agent-studio/catalog` - Get all agent prompts organized by category
- `GET /api/agent-studio/registry/metadata` - Get agent metadata, including domain-envelope and validation attachment metadata for domain-pack agents
- `POST /api/agent-studio/chat` - Stream a conversation with Opus
- `GET /api/agent-studio/trace/{trace_id}/context` - Get enriched trace context
- `POST /api/agent-studio/suggestion` - Submit a prompt suggestion
- `GET /api/agent-studio/custom-agents` - List user's custom agents
- `POST /api/agent-studio/custom-agents` - Create a custom agent
- `PUT /api/agent-studio/custom-agents/{id}` - Update a custom agent
- `DELETE /api/agent-studio/custom-agents/{id}` - Delete a custom agent

### Curation Workspace (`/api/curation-workspace`)
- Review sessions materialize domain-envelope objects into curator-visible rows
- Field edits are bounded patches against envelope object field paths
- Validation summaries and evidence anchors are projected from persisted envelope data
- Export and submission previews enforce expected envelope revisions, validation findings, required/export-blocking field policy, and adapter readiness blockers

### Weaviate Control Panel (`/weaviate`)
- `GET /weaviate/documents` - List all documents
- `POST /weaviate/documents` - Upload a new document
- `DELETE /weaviate/documents/{id}` - Delete a document
- `GET /weaviate/chunks` - Get document chunks
- `POST /weaviate/processing/start` - Start processing pipeline
- `GET /weaviate/schema` - Get Weaviate schema
- `POST /weaviate/settings` - Update settings

### Health & Monitoring
- `GET /` - API information
- `GET /health` - Lightweight liveness probe
- `GET /health/live` - Explicit liveness alias for probes
- `GET /health/ready` - Strict Docker readiness probe for validation dependencies
- `GET /health/deep` - Comprehensive dependency health check
- `GET /docs` - Swagger UI documentation
- `GET /openapi.json` - OpenAPI specification

## Environment Variables

### Required
- `OPENAI_API_KEY` - OpenAI API key for AI agents
- `DATABASE_URL` - PostgreSQL connection string

### LLM Providers (set per provider in use)
- `OPENAI_API_KEY` - OpenAI API key (required, also used by default runner)
- `GEMINI_API_KEY` - Google Gemini API key (optional, for Gemini provider)
- `GROQ_API_KEY` - Groq API key (optional, for Groq provider)
- `LLM_PROVIDER_STRICT_MODE` - Fail startup if required provider keys missing (default: `true`)
- `AGENT_RUNTIME_STRICT_MODE` - Escalate critical template-tool drift warnings to startup errors (default: `false`)

### Config Paths (optional)
- `MODELS_CONFIG_PATH` - Override path to `models.yaml` (default: auto-detected)
- `PROVIDERS_CONFIG_PATH` - Override path to `providers.yaml` (default: auto-detected)
- `GROUPS_CONFIG_PATH` - Override path to `groups.yaml` (default: `/runtime/config/groups.yaml`, fallback: repo `config/groups.yaml`)
- `CONNECTIONS_CONFIG_PATH` - Override path to `connections.yaml` (default: `/runtime/config/connections.yaml`, fallback: repo `config/connections.yaml`)

### Optional
- `LANGFUSE_PUBLIC_KEY` - Langfuse public key for tracing
- `LANGFUSE_SECRET_KEY` - Langfuse secret key for tracing
- `WEAVIATE_HOST` - Weaviate host (default: `weaviate`)
- `WEAVIATE_PORT` - Weaviate port (default: `8080`)
- `WEAVIATE_SCHEME` - Weaviate scheme (default: `http`)
- `AGR_RUNTIME_ROOT` - Root for modular runtime config/packages/state (default: `/runtime`)
- `PDF_STORAGE_PATH` - Path for PDF storage (default: `/runtime/state/pdf_storage`)
- `FILE_OUTPUT_STORAGE_PATH` - Path for generated file outputs (default: `/runtime/state/file_outputs`)
- `IDENTIFIER_PREFIX_FILE_PATH` - Identifier prefix cache file (default: `/runtime/state/identifier_prefixes/identifier_prefixes.json`)
- `UNSTRUCTURED_API_URL` - Unstructured API URL for PDF processing
- `UNSTRUCTURED_API_KEY` - Unstructured API key
- `TOOL_POLICY_CACHE_TTL_SECONDS` - Tool policy cache lifetime (default: `30`)
- `DEBUG` - Enable debug mode (default: `false`)

## Maintenance Scripts

- Audit/backfill custom-agent tool drift from template defaults:
  - Dry-run: `.venv/bin/python scripts/audit_backfill_agent_tools.py`
  - Apply critical candidates: `.venv/bin/python scripts/audit_backfill_agent_tools.py --apply`
  - Include non-critical candidates: `.venv/bin/python scripts/audit_backfill_agent_tools.py --apply --include-noncritical`

## Development Setup

### Local Development

1. Create virtual environment:
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set environment variables:
```bash
export OPENAI_API_KEY="your-api-key"
export WEAVIATE_HOST="localhost"
export WEAVIATE_PORT="8080"
```

4. Run the development server:
```bash
uvicorn main:app --reload --port 8000
```

### Docker Development

Build and run with Docker Compose from the root directory:
```bash
docker-compose up backend
```

The API will be available at `http://localhost:8000`

## Testing

Run backend tests through the Docker test compose file from the repository root:
```bash
# Unit tests
docker compose -f docker-compose.test.yml run --rm backend-unit-tests

# Contract tests
docker compose -f docker-compose.test.yml run --rm backend-contract-tests

# All backend tests
docker compose -f docker-compose.test.yml run --rm backend-tests

# Specific file
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"
```

Domain-envelope release gates and live curation DB opt-in tests are documented in
[docs/developer/TEST_STRATEGY.md](../docs/developer/TEST_STRATEGY.md).

## API Documentation

When the server is running, interactive API documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Tracing & Monitoring

The backend integrates with Langfuse for distributed tracing of agent operations. When configured with Langfuse credentials, you can monitor:
- Agent execution traces
- Task completion times
- LLM API calls
- Error tracking

View traces at: `http://localhost:3000` (when Langfuse is running)

## Agent Architecture

Agents are defined entirely in YAML configuration files under `config/agents/`. The 16
hardcoded Python agent files that previously lived in `backend/src/lib/openai_agents/agents/`
have been replaced by this config-driven approach. The old `agent_factory.py` has also been
removed.

At startup the backend:

1. Loads agent definitions from `config/agents/*/agent.yaml` (via `agent_loader.py`)
2. Loads the LLM model catalog from `config/models.yaml` (via `models_loader.py`)
3. Loads LLM provider definitions from `config/providers.yaml` (via `providers_loader.py`)
4. Cross-validates that every model references a known provider and that required API keys
   are present (via `provider_validation.py`)
5. Seeds the unified `agents` table in PostgreSQL from the YAML definitions
6. Builds the supervisor agent dynamically from the loaded registry

The only remaining Python agent file is `supervisor_agent.py`, which is constructed at
runtime from the loaded agent registry rather than containing hardcoded routing logic.

Custom agents created through the Agent Workshop UI are stored directly in the `agents`
database table with `visibility = 'private'` or `'project'`, while system agents loaded
from YAML have `visibility = 'system'`.

See [CONFIG_DRIVEN_ARCHITECTURE.md](../docs/developer/guides/CONFIG_DRIVEN_ARCHITECTURE.md) for
the full guide on adding agents, configuring providers, and managing tool policies.

## Domain Envelopes

New domain-pack curation runs use persisted `DomainEnvelope` records as the
semantic source of truth. Extraction agents produce envelope objects with object
IDs or pending refs, relative field paths, validation findings, history, schema
refs, evidence metadata, and domain-pack metadata. The workspace still stores
candidate rows for review mechanics, but for domain-pack runs those rows are
materialized projections with `semantic_source: domain_envelope.objects`.

Automatic validation comes from domain-pack metadata. Active validator bindings
use package-scoped validator agent refs or produce explicit dispatch findings;
under-development bindings remain visible metadata. Repair, field edits, export,
and submission all operate against envelope object field paths and expected
revisions.

See [DOMAIN_ENVELOPES.md](../docs/developer/guides/DOMAIN_ENVELOPES.md) for the
full developer contract.

## Dependencies

### Core
- **FastAPI** - Web framework
- **Uvicorn** - ASGI server
- **Pydantic** - Data validation
- **SQLAlchemy** - ORM and database access
- **Alembic** - Database migrations
- **PyYAML** - YAML configuration loading

### AI & Chat
- **OpenAI Agents SDK** - AI agent framework
- **OpenAI** - LLM provider (native driver)
- **LiteLLM** - Multi-provider LLM gateway (Gemini, Groq)
- **Anthropic** - Opus chat in Agent Studio
- **Langfuse** - Observability
- **OpenInference** - Instrumentation

### Document Processing
- **Weaviate-Client** - Vector database client
- **Unstructured** - PDF processing
- **Pillow** - Image processing
- **PyTesseract** - OCR

## Docker Configuration

The backend runs on port 8000 inside the container. The Dockerfile includes:
- Python 3.11 slim base image
- OCR dependencies (Tesseract)
- PDF processing tools (Poppler)
- Health check endpoint

The `config/` directory from the project root is mounted read-only into the container at
`/app/config`, providing access to `models.yaml`, `providers.yaml`, agent definitions, and
other runtime configuration. Alembic migrations run automatically on container startup
(`alembic upgrade head`) before the application starts.

## Troubleshooting

### Common Issues

1. **Weaviate connection failed**: Ensure Weaviate is running and accessible
2. **OpenAI API errors**: Check your API key is valid
3. **PDF processing fails**: Verify Tesseract and Poppler are installed
4. **Langfuse tracing not working**: Check credentials and network connectivity

### Debug Mode

Enable debug logging:
```bash
export DEBUG=true
```

View logs:
```bash
docker-compose logs -f backend
```

## Contributing

1. Follow the existing code structure
2. Add tests for new features
3. Update API documentation
4. Run linters before committing:
   ```bash
   black src/
   flake8 src/
   mypy src/
   ```
