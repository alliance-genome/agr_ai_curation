# Agent Development Guide

Comprehensive reference for the AGR AI Curation multi-agent architecture. Covers the full lifecycle from agent definition through runtime execution, including the unified database model, dynamic supervisor discovery, tool bindings, prompt management, and observability.

> **Last Updated:** February 2026 (Unified agents table, dynamic supervisor discovery, YAML + DB architecture)

---

## Table of Contents

1. [Quick Start: Environment & CLI](#quick-start-environment--cli)
2. [Architecture Overview](#architecture-overview)
3. [Critical Patterns (Read First)](#critical-patterns-read-first)
4. [Agent Lifecycle](#agent-lifecycle)
5. [Unified Agents Table](#unified-agents-table)
6. [YAML Configuration](#yaml-configuration)
7. [Tool System](#tool-system)
8. [Prompt Management](#prompt-management)
9. [Supervisor and Routing](#supervisor-and-routing)
10. [Multi-Provider Support](#multi-provider-support)
11. [Output Schemas](#output-schemas)
12. [Frontend Integration](#frontend-integration)
13. [Configuration & Environment Variables](#configuration--environment-variables)
14. [Testing & Validation](#testing--validation)
15. [Current Agents & Tools](#current-agents--tools)
16. [Troubleshooting](#troubleshooting)
17. [Resources](#resources)

---

## Quick Start: Environment & CLI

### Local stack & data access

- **Environment file** -- Run `make setup` once. This copies `.env.example` to `~/.agr_ai_curation/.env` (600 permissions) so secrets stay outside git.
- **Start everything** -- `make dev` (foreground) or `make dev-detached`. Services come up on:
  - Backend FastAPI: `http://localhost:8000`
  - Agent Studio (React): `http://localhost:3002/agent-studio` (set `DEV_MODE=true` in your env to bypass Cognito locally)
  - Langfuse: `http://localhost:3000`
  - Trace Review API (optional): `http://localhost:8001`
- **Data services** -- `docker compose` brings up Postgres (AI curation DB + prompt tables + unified agents table) on `localhost:5434`, Redis, Weaviate, ClickHouse, MinIO, and Langfuse worker. Use `docker compose logs -f backend` to watch agent telemetry.
- **Inspect agents** -- `docker compose exec postgres psql -U postgres ai_curation -c "SELECT agent_key, name, visibility, supervisor_enabled, is_active FROM agents ORDER BY agent_key;"`.
- **Inspect prompts** -- `docker compose exec postgres psql -U postgres ai_curation -c "SELECT agent_name, prompt_type, version, is_active FROM prompt_templates ORDER BY updated_at DESC LIMIT 20;"`.

---

## Architecture Overview

The system uses a **config-driven, database-backed** architecture where YAML files define the initial state and the database is the runtime authority.

### Key Components

1. **YAML Config Layer** (`config/agents/*/agent.yaml`, `prompt.yaml`, `group_rules/`) -- Source of truth for system agent definitions. Read at startup by `registry_builder.py` for UI metadata and by Alembic migrations for database seeding.

2. **Unified Agents Table** (`agents` in Postgres) -- Single table for both system agents (seeded from YAML) and custom agents (created via UI). Contains all runtime fields: instructions, model settings, tool IDs, output schema key, group rules config, supervisor routing, and visibility.

3. **Catalog Service** (`backend/src/lib/agent_studio/catalog_service.py`) -- Central runtime factory. `get_agent_by_id()` reads a row from the `agents` table and builds a live OpenAI Agents SDK `Agent` object with resolved tools, injected group rules, document context, and output schema.

4. **Registry Builder** (`backend/src/lib/agent_studio/registry_builder.py`) -- Builds `AGENT_REGISTRY` from YAML at import time. Provides metadata for the Agent Studio UI (categories, icons, documentation) without touching the database.

5. **Supervisor Agent** (`backend/src/lib/openai_agents/agents/supervisor_agent.py`) -- Dynamically discovers supervisor-enabled agents from the `agents` table and creates streaming tool wrappers. No hardcoded specialist imports.

6. **Agent Service** (`backend/src/lib/agent_studio/agent_service.py`) -- Database access layer for agent CRUD. Handles visibility rules (system/private/project), user scoping, and execution spec materialization.

7. **Config Loaders** (`backend/src/lib/config/`) -- Thread-safe loaders for agents, schemas, groups, connections, models, and providers. All support `force_reload` for cache invalidation.

8. **Tool Bindings** (`TOOL_BINDINGS` in `catalog_service.py`) -- Declarative mapping from tool IDs to runtime resolver functions. Each binding declares its required execution context (document_id, database_url, etc.).

### Data Flow

```
config/agents/*.yaml ──→ registry_builder.py ──→ AGENT_REGISTRY (UI metadata)
                     └──→ Alembic migration ──→ agents table (runtime authority)
                                                      │
                                                      ▼
                                              get_agent_by_id()
                                                      │
                                      ┌───────────────┼───────────────┐
                                      ▼               ▼               ▼
                              resolve_tools()   build_runtime    resolve_output
                              (TOOL_BINDINGS)   _instructions()   _schema()
                                      │               │               │
                                      ▼               ▼               ▼
                                  Agent(tools=..., instructions=..., output_type=...)
```

---

## Critical Patterns (Read First)

1. **Database is runtime authority** -- The `agents` table is what `get_agent_by_id()` reads at runtime. YAML files are only read during migrations and for UI metadata. Editing a YAML file alone will not change agent behavior until the database row is updated.

2. **No hardcoded agent files** -- Individual Python agent files (`gene_agent.py`, `disease_agent.py`, etc.) have been removed. All agents are built generically by `_create_db_agent()` in `catalog_service.py` from database rows.

3. **Dynamic supervisor discovery** -- The supervisor queries the `agents` table for `visibility='system'` + `supervisor_enabled=true` records and creates streaming tools dynamically. No imports or explicit wiring needed.

4. **Tool bindings are declarative** -- Tools are resolved via `TOOL_BINDINGS` in `catalog_service.py`. Each entry declares a resolver function and required execution context. The `resolve_tools()` function materializes tool instances at runtime.

5. **Prompt cache for group rules** -- Base prompts come from the `agents.instructions` column. Group rules come from the `prompt_templates` table via the prompt cache. `_inject_group_rules_with_overrides()` merges them at runtime.

6. **Envelope outputs with safe defaults** -- Output models (`*Envelope`) must allow empty responses: every field either `Optional[...] = None` or `Field(default_factory=list, ...)`. Missing defaults cause structured output failures.

7. **Structured-output enforcement** -- `inject_structured_output_instruction()` is called automatically by `_build_runtime_instructions()` when `output_schema_key` is set. This injects the "CRITICAL: ALWAYS PRODUCE ..." block.

8. **Context via closures, not params** -- API layers set `trace_id`, `session_id`, and `curator_id` using `src/lib/context`. Tools should capture context either when the tool is created or right before use. Do **not** add these as function parameters.

9. **Streaming tools for audit** -- Specialists are wrapped with `_create_streaming_tool()` so tool calls become audit events, batching nudges work, and prompts are logged only when the specialist actually runs.

---

## Agent Lifecycle

### System Agents (YAML-defined)

1. Developer creates `config/agents/my_agent/agent.yaml` and `prompt.yaml`
2. An Alembic migration reads the YAML and inserts a row into `agents` with `visibility='system'`
3. At backend startup, `registry_builder.py` reads the YAML to build `AGENT_REGISTRY` for UI metadata
4. The prompt cache loads active prompts from `prompt_templates` for group rule injection
5. When the supervisor is created, it queries the `agents` table and builds streaming tools for each enabled agent
6. When a user query matches the agent's routing description, the supervisor calls the streaming tool
7. `get_agent_by_id()` builds a fresh `Agent` instance from the DB row with resolved tools, injected group rules, and output schema

### Custom Agents (UI-created)

1. Curator creates an agent via the Agent Studio UI
2. A row is inserted into `agents` with `visibility='private'` and `supervisor_enabled=false`
3. The agent is immediately available for flow execution and direct invocation
4. No YAML files, no migrations, no code changes required

---

## Unified Agents Table

The `agents` table (`backend/src/models/sql/agent.py`) stores all agent records:

### Key Columns

| Column | Type | Purpose |
|--------|------|---------|
| `agent_key` | String(100) | Unique identifier, used as lookup key |
| `name` | String(255) | Display name |
| `instructions` | Text | Full prompt text |
| `model_id` | String(100) | LLM model identifier (e.g., `gpt-4o`) |
| `model_temperature` | Float | Temperature setting |
| `model_reasoning` | String(20) | Reasoning effort level |
| `tool_ids` | JSONB | List of tool IDs (e.g., `["agr_curation_query"]`) |
| `output_schema_key` | String(100) | Pydantic class name from `models.py` |
| `group_rules_enabled` | Boolean | Whether to inject group-specific rules |
| `group_rules_component` | String(100) | Prompt cache key for group rule lookup |
| `mod_prompt_overrides` | JSONB | Per-group prompt overrides (custom agents) |
| `visibility` | String(20) | `system`, `private`, or `project` |
| `user_id` | Integer | Owner (NULL for system agents) |
| `project_id` | UUID | Project scope (for `project` visibility) |
| `supervisor_enabled` | Boolean | Whether the supervisor can route to this agent |
| `supervisor_description` | Text | Routing hint for the supervisor |
| `supervisor_batchable` | Boolean | Whether batching nudges apply |
| `show_in_palette` | Boolean | Whether to show in Flow Builder palette |
| `is_active` | Boolean | Soft delete flag |

### Visibility Rules

- **system**: Visible to all users. Created from YAML via migration.
- **private**: Visible only to the owner (`user_id`). Created via Agent Studio.
- **project**: Visible to all members of the associated project. Created via Agent Studio.

---

## YAML Configuration

### agent.yaml Reference

See the `config/agents/_examples/basic_agent/agent.yaml` template for all fields. Key sections:

- **Basic info**: `agent_id`, `name`, `description`, `category`, `subcategory`
- **Supervisor routing**: `supervisor_routing.enabled`, `.description`, `.batchable`, `.batching_entity`
- **Tools**: List of tool IDs that map to `TOOL_BINDINGS`
- **Output schema**: Class name from `backend/src/lib/openai_agents/models.py`
- **Model config**: `model`, `temperature`, `reasoning` (supports `${ENV_VAR:-default}` syntax)
- **Frontend**: `icon`, `show_in_palette`
- **Group rules**: `group_rules_enabled`

### prompt.yaml Reference

```yaml
agent_id: my_agent
content: |
  [Full prompt text with markdown formatting]
```

The prompt content is seeded into the `prompt_templates` table by the Alembic migration. At runtime, the prompt is stored directly in the `agents.instructions` column. Group rules are fetched from the prompt cache and injected dynamically.

### Group Rules

Group rules are YAML files under `config/agents/my_agent/group_rules/`. Each file has:

```yaml
group_id: FB          # Must match config/groups.yaml
content: |
  [Organization-specific instructions]
```

These are seeded into `prompt_templates` with `prompt_type='group_rules'` and injected into the agent's instructions at runtime when the user belongs to that group.

---

## Tool System

### Available Tools

Tools are Python functions decorated with `@function_tool` in `backend/src/lib/openai_agents/tools/`. Each tool that agents can reference must have a `TOOL_BINDINGS` entry in `catalog_service.py`.

| Tool ID | Category | Description |
|---------|----------|-------------|
| `agr_curation_query` | Database | Multi-method AGR curation DB access (genes, alleles, anatomy, life stages, GO terms) |
| `curation_db_sql` | Database | Parameterized SQL against curation DB (disease ontology) |
| `search_document` | PDF | Weaviate hybrid search over uploaded PDFs |
| `read_section` | PDF | Read full text of a document section |
| `read_subsection` | PDF | Read full text of a subsection |
| `alliance_api_call` | API | Alliance REST API (orthology) |
| `chebi_api_call` | API | ChEBI chemical database API |
| `quickgo_api_call` | API | QuickGO Gene Ontology API |
| `go_api_call` | API | GO Consortium annotations API |
| `save_csv_file` | Output | Persist data as downloadable CSV |
| `save_tsv_file` | Output | Persist data as downloadable TSV |
| `save_json_file` | Output | Persist data as downloadable JSON |

### Tool Bindings

Each tool has a binding in `TOOL_BINDINGS` that declares:

- **`binding`**: `"static"` (no context needed) or `"context_factory"` (needs runtime context)
- **`required_context`**: List of execution context fields needed (e.g., `["document_id", "user_id"]`)
- **`resolver`**: Function that returns the tool instance

```python
TOOL_BINDINGS = {
    "agr_curation_query": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_agr_curation_tool,
    },
    "search_document": {
        "binding": "context_factory",
        "required_context": ["document_id", "user_id"],
        "resolver": _resolve_search_document_tool,
    },
}
```

### Adding a New Tool Binding

When adding a new tool that agents reference:

1. Create the tool in `backend/src/lib/openai_agents/tools/` (see [ADDING_NEW_TOOL.md](./ADDING_NEW_TOOL.md))
2. Add a resolver function in `catalog_service.py`
3. Add the binding to `TOOL_BINDINGS`
4. Optionally add to `TOOL_REGISTRY` for UI documentation

### Tool Resolution at Runtime

When `get_agent_by_id()` builds an agent, it calls `resolve_tools()` which:

1. Reads the `tool_ids` JSONB array from the agent's DB row
2. Canonicalizes method-level aliases back to parent tool IDs
3. Looks up each tool in `TOOL_BINDINGS`
4. Validates that required execution context is present
5. Calls the resolver function to get the tool instance

---

## Prompt Management

### Prompt Sources

- **System agent instructions**: Stored in `agents.instructions` column, seeded from `prompt.yaml` during migration
- **Group rules**: Stored in `prompt_templates` table with `prompt_type='group_rules'`, accessed via the prompt cache
- **Custom agent instructions**: Written directly to `agents.instructions` by the Agent Studio UI

### Prompt Cache

The prompt cache (`backend/src/lib/prompts/cache.py`) loads active prompts from `prompt_templates` at startup. Group rules are fetched from this cache during agent construction.

```bash
# Refresh cache after editing prompts
curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh
```

### Editing Prompts

**For system agents** -- Update via the Admin API:

```bash
curl -X POST http://localhost:8000/api/admin/prompts \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_name": "gene",
    "prompt_type": "system",
    "content": "Updated prompt text...",
    "change_notes": "Improved search strategy",
    "activate": true
  }'

# Refresh cache
curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh
```

**For custom agents** -- Edit through the Agent Studio UI, which updates `agents.instructions` directly.

### Prompt Execution Logging

When a specialist agent runs, `commit_pending_prompts()` records which prompt versions were used in `prompt_execution_log`. This provides an audit trail for prompt changes.

---

## Supervisor and Routing

### Dynamic Agent Discovery

The supervisor no longer hardcodes specialist imports. Instead, `_create_dynamic_specialist_tools()` in `supervisor_agent.py`:

1. Queries `agents` table: `visibility='system'` AND `supervisor_enabled=true` AND `is_active=true`
2. For each row, calls `get_agent_by_id()` to build a runtime `Agent` instance
3. Wraps each agent with `_create_streaming_tool()` for audit visibility
4. Document-dependent agents are skipped if no document is loaded

### Routing Table

The supervisor's routing instructions include a dynamically generated markdown table from `generate_routing_table()`:

```
| Tool | When to Use |
|------|-------------|
| ask_gene_specialist | Use for validating genes against the database... |
| ask_allele_specialist | Use for validating allele/variant identifiers... |
```

This table is built from `supervisor_description` fields in the `agents` table.

### Batching

Agents with `supervisor_batchable=true` and `supervisor_batching_entity` set will receive batching nudges from the supervisor after multiple sequential calls.

---

## Multi-Provider Support

### Model Catalog

Models are defined in `config/models.yaml` and loaded by `backend/src/lib/config/models_loader.py`. The catalog provides:

- Model selection UI for curators (via Agent Studio)
- Capability metadata (reasoning support, temperature support)
- Provider routing (OpenAI, Groq, etc.)

### Provider Configuration

Providers are defined in `config/providers.yaml` and loaded by `backend/src/lib/config/providers_loader.py`. Each provider declares:

- API endpoint and authentication
- Capability flags (parallel tool calls, reasoning support)
- Model-to-provider mapping

### Per-Agent Model Override

Set environment variables to override an agent's default model:

```bash
AGENT_GENE_MODEL=gpt-5-mini
AGENT_SUPERVISOR_MODEL=gpt-5.4
AGENT_PDF_MODEL=gpt-5.4
```

---

## Output Schemas

All output schemas live in `backend/src/lib/openai_agents/models.py`. The `output_schema_key` in the `agents` table maps to a class name in this module.

### Rules

- Every field has a `description` (feeds structured output instructions)
- Never use `Field(...)` in envelopes; defaults are mandatory
- Keep names snake_case; prefer flat structures
- The catalog service calls `_resolve_output_schema()` to look up the class by name

### Current Schemas

| Schema | Used By |
|--------|---------|
| `GeneValidationEnvelope` | Gene agent |
| `AlleleValidationEnvelope` | Allele agent |
| `DiseaseResultEnvelope` | Disease agent |
| `ChemicalResultEnvelope` | Chemical agent |
| `GeneOntologyResultEnvelope` | Gene ontology agent |
| `GOAnnotationResultEnvelope` | GO annotations agent |
| `OrthologResultEnvelope` | Orthologs agent |
| `OntologyMappingResultEnvelope` | Ontology mapping agent |

---

## Frontend Integration

No hardcoded icon maps or agent lists. Agent Studio fetches metadata dynamically:

- `GET /api/agent-studio/registry/metadata` -- Returns `AGENT_REGISTRY` data (built from YAML)
- `GET /api/agent-studio/catalog` -- Returns prompt catalog with version info
- Agent creation/editing via `POST /api/agent-studio/agents`

The Flow Builder palette, trace badges, and Tool Inspector all derive from registry metadata.

---

## Configuration & Environment Variables

### Per-Agent Overrides

```bash
AGENT_MY_AGENT_MODEL=gpt-5-mini
AGENT_MY_AGENT_REASONING=low
AGENT_MY_AGENT_TEMPERATURE=0.2
```

### Global Settings

```bash
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openai
AGENT_MAX_TURNS=20
```

### Database Connections

```bash
CURATION_DB_URL=postgresql://user:pass@host:port/db
DATABASE_URL=postgresql://user:pass@host:port/ai_curation
```

### Docker Compose

Environment variables are passed through for every `AGENT_*` value, so you usually don't need to edit `docker-compose.yml` unless the agent needs a brand-new service or secret.

---

## Testing & Validation

### Static Checks

```bash
# Validate YAML syntax
python3 -c "import yaml; yaml.safe_load(open('config/agents/my_agent/agent.yaml'))"

# Run unit tests
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  python -m pytest tests/unit/ -v
```

### Runtime Checks

```bash
# Rebuild backend
docker compose up -d backend --build

# Refresh prompt cache
curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh

# Chat via Agent Studio and confirm routing
docker compose logs backend | grep my_agent

# Inspect Langfuse for traces
# http://localhost:3000
```

### Database Inspection

```bash
# Check agent exists and is active
docker compose exec postgres psql -U postgres ai_curation -c \
  "SELECT agent_key, name, supervisor_enabled, is_active FROM agents WHERE agent_key = 'my_agent';"

# Check prompts
docker compose exec postgres psql -U postgres ai_curation -c \
  "SELECT agent_name, prompt_type, version, is_active FROM prompt_templates WHERE agent_name = 'my_agent';"
```

---

## Current Agents & Tools

### System Agents

| Agent Key | Category | Description |
|-----------|----------|-------------|
| `supervisor` | Routing | Routes chat queries to specialist tools |
| `pdf` | Extraction | Document extraction with hybrid search |
| `gene_expression` | Extraction | Gene expression annotation from PDFs |
| `gene` | Validation | Gene symbol/ID validation against AGR |
| `allele` | Validation | Allele/variant validation against AGR |
| `disease` | Validation | Disease Ontology (DOID) mapping |
| `chemical` | Validation | ChEBI chemical compound lookup |
| `gene_ontology` | Validation | GO term lookup via QuickGO |
| `go_annotations` | Validation | GO annotations via GO Consortium API |
| `orthologs` | Validation | Cross-species ortholog lookup |
| `ontology_mapping` | Validation | Free-text to ontology term mapping |
| `chat_output` | Output | Final answer formatting |
| `csv_formatter` | Output | CSV file output |
| `tsv_formatter` | Output | TSV file output |
| `json_formatter` | Output | JSON file output |

### Tools

See the `TOOL_REGISTRY` and `TOOL_BINDINGS` in `catalog_service.py` for the complete list. Key tools:

- `agr_curation_query` -- Multi-method AGR database access
- `search_document`, `read_section`, `read_subsection` -- Weaviate PDF search
- `curation_db_sql` -- Direct SQL for disease ontology
- REST API wrappers: `alliance_api_call`, `chebi_api_call`, `quickgo_api_call`, `go_api_call`
- File output: `save_csv_file`, `save_tsv_file`, `save_json_file`

---

## Troubleshooting

| Symptom | Checks |
|---------|--------|
| Agent not in supervisor | `supervisor_enabled=true` in DB? `is_active=true`? `visibility='system'`? |
| Agent not in Flow Builder palette | `show_in_palette=true` in DB? Agent in `AGENT_REGISTRY`? |
| "Unknown agent_id" error | Agent row missing from `agents` table. Run migration or insert manually. |
| "Unknown tool binding" error | Tool not in `TOOL_BINDINGS`. Add resolver + binding entry in `catalog_service.py`. |
| Schema not found | `output_schema_key` doesn't match a class in `models.py`. Check exact spelling. |
| Prompt changes not reflected | Refresh cache: `curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh`. For `agents.instructions`, restart backend. |
| Group rules not injected | `group_rules_enabled=true`? `group_rules_component` set? Active group rule prompt in `prompt_templates`? |
| Tool errors at runtime | Check `TOOL_BINDINGS.required_context` -- missing `document_id` or `database_url`? |
| Supervisor routing failures | Improve `supervisor_description` in DB. Check logs: `docker compose logs backend \| grep ask_my_agent` |
| Langfuse missing traces | Verify Langfuse credentials in `.env`. Check `LANGFUSE_HOST`, `LANGFUSE_SECRET_KEY`. |

---

## Resources

- `README.md` (repo root) -- Platform overview and entry points
- `Makefile` -- `dev`, `dev-build`, `rebuild-backend`, `trace-review`, `test-*` commands
- `docs/developer/README.md` -- Map of available developer docs
- `scripts/README.md` -- Utility scripts documentation
- `backend/src/lib/agent_studio/catalog_service.py` -- Central agent factory and tool resolution
- `backend/src/lib/agent_studio/registry_builder.py` -- YAML-to-registry conversion
- `backend/src/lib/agent_studio/agent_service.py` -- Database agent CRUD
- `backend/src/models/sql/agent.py` -- Unified agent SQL model
- `backend/src/lib/openai_agents/agents/supervisor_agent.py` -- Dynamic supervisor with tool discovery
- `backend/src/lib/config/` -- All configuration loaders
- `config/models.yaml` -- Model catalog
- `config/agents/` -- Agent YAML definitions
- Langfuse docs: https://langfuse.com/docs
- OpenAI Agents SDK: https://github.com/openai/openai-agents-python
