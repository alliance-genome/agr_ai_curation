# Adding New Agents to the Multi-Agent System

This guide explains how to add and maintain specialist agents inside the AGR AI Curation Platform. The platform runs a multi-service stack (FastAPI backend + React Agent Studio + Langfuse + Postgres + Weaviate) so every agent change must stay in sync with the runtime environment, database-backed prompts, and the developer tooling described below.

> **Last Updated:** January 26, 2026 (OpenAI Agents SDK stack with database prompts & Agent Studio flows)

---

## Table of Contents

1. [Quick Start: Environment & CLI](#quick-start-environment--cli)
   1. [Local stack & data access](#local-stack--data-access)
   2. [CLI scaffolding tool](#cli-scaffolding-tool)
2. [Architecture Overview](#architecture-overview)
3. [Critical Patterns (Read First)](#critical-patterns-read-first)
4. [Step-by-Step Guide](#step-by-step-guide)
5. [Complete Checklist](#complete-checklist)
6. [Key Patterns & Gotchas](#key-patterns--gotchas)
7. [Multi-Provider Support](#multi-provider-support)
8. [Database-Backed Prompts](#database-backed-prompts)
9. [Advanced Topics](#advanced-topics)
10. [Current Specialists & Tools](#current-specialists--tools)
11. [Troubleshooting](#troubleshooting)
12. [Resources](#resources)

---

## Quick Start: Environment & CLI

### Local stack & data access

- **Environment file** – Run `make setup` once. This copies `.env.example` to `~/.agr_ai_curation/.env` (600 permissions) so secrets stay outside git.
- **Start everything** – `make dev` (foreground) or `make dev-detached`. Services come up on:
  - Backend FastAPI: `http://localhost:8000`
  - Agent Studio (React): `http://localhost:3002/agent-studio` (set `DEV_MODE=true` in your env to bypass Cognito locally)
  - Langfuse: `http://localhost:3000`
  - Trace Review API (optional): `http://localhost:8001`
- **Data services** – `docker compose` brings up Postgres (AI curation DB + prompt tables) on `localhost:5434`, Redis, Weaviate, ClickHouse, MinIO, and Langfuse worker. Use `docker compose logs -f backend` to watch agent telemetry.
- **Prompts live in Postgres** – Inspect with `docker compose exec postgres psql -U postgres ai_curation -c "select agent_name, prompt_type, version, is_active from prompt_templates order by updated_at desc limit 20;"`.
- **Agent Studio entry points** – Prompt Explorer (catalog, prompt history, group rules), Flows tab (visual builder), Trace context panel (pulls Langfuse + prompt execution logs) all read from the backend's catalog and prompt cache.

### CLI scaffolding tool

`scripts/create_agent.py` generates new agent files, registry entries, and prompt stubs with live validation against the real registries.

```bash
# Preview (no files touched)
python3 scripts/create_agent.py my_new_agent \
  --name "My New Agent" \
  --description "Validates my data" \
  --category "Validation" \
  --tools "agr_curation_query,save_csv_file" \
  --icon "🧪" \
  --dry-run

# Create with confirmation prompt
docker compose exec backend python scripts/create_agent.py my_new_agent \
  --name "My New Agent" \
  --description "Validates my data" \
  --category "Validation" \
  --tools "agr_curation_query" \
  --icon "🧪"
```

**Flags you will actually use**

| Flag | Purpose |
|------|---------|
| `--tools` | Comma-separated tool IDs validated against `TOOL_REGISTRY` (see `catalog_service.py`). |
| `--subcategory` | Optional Flow Builder grouping label. |
| `--icon` | Emoji for Agent Studio (stored with the registry metadata). |
| `--requires-document` | Adds `document_id` & `user_id` to `required_params` for PDF-aware agents. |
| `--create-prompt` | Prints a ready-to-run Python snippet that inserts the initial prompt via `PromptService`. |
| `--force` | Allows unknown tools (you must add them to `TOOL_REGISTRY` later). |
| `--yes / -y` | Skip the interactive confirmation. |

**What the CLI edits**
1. `backend/src/lib/openai_agents/agents/{agent_id}_agent.py` – scaffolded factory using the database prompt cache and Langfuse logging.
2. `backend/src/lib/openai_agents/agents/__init__.py` – import + `__all__` export.
3. `backend/src/lib/agent_studio/catalog_service.py` – adds the `AGENT_REGISTRY` entry (including frontend + supervisor metadata) and wires the factory import.

---

## Architecture Overview

1. **Frontend (Agent Studio)** – React (Vite) UI served through Nginx (`frontend` container). Provides Prompt Explorer, Flow Builder, Opus 4.5 chat, Langfuse trace context, and a palette of registry-driven agents.
2. **Backend (FastAPI)** – `backend/src/main.py` hosts OpenAI Agents chat endpoints, Agent Studio APIs, admin prompt management, and document ingestion endpoints. Startup tasks initialize the prompt cache and Langfuse client.
3. **OpenAI Agents SDK layer** – Lives in `backend/src/lib/openai_agents`. Key modules:
   - `agents/` – Supervisor + specialist factories (OpenAI Agents SDK `Agent` objects only). No HTTP/glue here.
   - `config.py` – Resolves per-agent settings via env overrides and registry defaults.
   - `models.py` – Structured output envelopes shared by all specialists.
   - `runner.py` – Streaming execution + Langfuse instrumentation + prompt logging.
   - `streaming_tools.py` – Wraps specialists as streaming tools with audit events and batching nudges.
   - `tools/` – `@function_tool` implementations (AGR database, SQL, Weaviate search, REST API wrappers, file outputs, etc.).
4. **Prompt Catalog & Flow services** – `backend/src/lib/agent_studio/` loads prompt metadata from the database, exposes `/api/agent-studio` endpoints, and registers flow-editing tools for Opus.
5. **Databases**
   - **Postgres (`postgres` service)** – Application DB + `prompt_templates` + `prompt_execution_log` tables + flow definitions.
   - **Alliance data sources** – `agr_curation_query` uses the AGR curation database via `CURATION_DB_URL`; disease agent also uses direct SQL (`curation_db_sql`). Keep VPN tunnels up when hitting internal DBs.
6. **Observability** – Langfuse (web + worker + ClickHouse + MinIO + Redis) captures traces. `langfuse_client.py` queues agent configs until the trace exists; `runner.py` flushes them.
7. **Agent Studio Trace Review** – Optional `trace_review_backend` container (host network) pulls Langfuse traces across VPN for the Trace Review UI.

---

## Critical Patterns (Read First)

1. **Prompt cache or bust** – Every specialist must fetch prompts via `src/lib/prompts/cache.get_prompt()` and register them with `set_pending_prompts()`. Runtime systems never touch disk files for prompts.
2. **Registry-driven config** – Use `get_agent_config(agent_id)` inside factories. If you need different defaults, set `"config_defaults"` in the agent’s `AGENT_REGISTRY` entry instead of creating bespoke config functions.
3. **Context via closures, not params** – API layers set `trace_id`, `session_id`, and `curator_id` using `src/lib/context`. Tools should capture context either when the tool is created (most cases) or right before use (file outputs). Do **not** add these as function parameters.
4. **Envelope outputs with safe defaults** – Output models (`*ResultEnvelope`) must allow empty responses: every field either `Optional[...] = None` or `Field(default_factory=list, ...)`. Missing defaults → Agents can’t emit structured output when nothing is found.
5. **Structured-output enforcement** – Call `inject_structured_output_instruction()` when your agent returns structured data. This injects the “CRITICAL: ALWAYS PRODUCE …” block so the SDK retries instead of silently finishing with `final_output=None`.
6. **Streaming tools** – Specialists should be wrapped with `_create_streaming_tool()` so tool calls become audit events, batching nudges work, and prompts are logged only when the specialist actually runs.
7. **Langfuse logging** – Each factory must call `log_agent_config()` with instructions, model settings, and tool list *before* returning the `Agent`. The runner flushes queued configs into the trace once OpenAI Agents emits the trace ID.
8. **Prompt execution logging** – Factory: `set_pending_prompts(agent.name, prompts_used)`. Runner: `commit_pending_prompts()` when the specialist actually executes, then `PromptService.log_all_used_prompts()` writes to `prompt_execution_log`.
9. **Tool docstrings are UX** – `function_tool` docstrings become the tool description for the LLM and appear in Agent Studio’s Tool Inspector. Describe inputs/outputs crisply and keep return types JSON serializable (dicts or Pydantic models).

---

## Step-by-Step Guide

### Step 1: Define output models (single + envelope)

File: `backend/src/lib/openai_agents/models.py`

```python
class MyAgentResult(BaseModel):
    """Single result."""
    primary_id: str = Field(..., description="Alliance-style CURIE")
    label: Optional[str] = Field(None, description="Human-readable label")
    synonyms: List[str] = Field(default_factory=list, description="Alternative labels")
    provenance: Optional[str] = Field(None, description="How we located this record")


class MyAgentResultEnvelope(BaseModel):
    """Always return this wrapper."""
    results: List[MyAgentResult] = Field(default_factory=list, description="Zero or more results")
    query_summary: Optional[str] = Field(None, description="What we searched for")
    not_found: List[str] = Field(default_factory=list, description="Identifiers with no matches")
    warnings: List[str] = Field(default_factory=list, description="Soft validation issues")
```

Rules:
- Every field has a description (the model feeds the structured output instructions).
- Never use `Field(...)` in envelopes; defaults are mandatory.
- Keep names snake_case; prefer flat structures unless you absolutely need nested objects.

### Step 2: Plan configuration

- Inside the factory call `config = get_agent_config("my_agent")`.
- Set defaults inside `AGENT_REGISTRY["my_agent"]["config_defaults"]`:

```python
"config_defaults": {
    "model": "gpt-5-mini",
    "reasoning": "low",
    "temperature": 0.2,
    "tool_choice": "auto",
}
```

This makes `AGENT_MY_AGENT_MODEL`, `AGENT_MY_AGENT_REASONING`, etc. optional overrides via the env. No code changes required when toggling providers.

### Step 3: Choose or create tools

**Built-in tools** (see `TOOL_REGISTRY` in `catalog_service.py`):

| Tool ID | Module | Highlights |
|---------|--------|------------|
| `agr_curation_query` | `backend/src/lib/openai_agents/tools/agr_curation.py` | Primary access to AGR curation DB for genes, alleles, anatomy, life stages, GO terms. Returns `AgrQueryResult`. |
| `curation_db_sql` | `backend/src/lib/openai_agents/tools/sql_query.py` (bound in `disease_agent`) | Direct read-only SQL access for ontology/disease lookups (requires `CURATION_DB_URL`). |
| `search_document` / `read_section` / `read_subsection` | `tools/weaviate_search.py` | Weaviate hybrid search + deterministic section readers (document-aware agents). |
| `create_rest_api_tool` | `tools/rest_api.py` | Generates domain-restricted REST callers (used for ChEBI, QuickGO, etc.). |
| `create_sql_query_tool` | `tools/sql_query.py` | Creates a named SQL read tool for any database URL. |
| `create_csv_tool` / `create_tsv_tool` / `create_json_tool` | `tools/file_output_tools.py` | Persist structured data and return download metadata (uses context vars at invocation time to capture trace/session/user). |

When creating a brand new tool:
1. Generate scaffolding with `python3 scripts/create_tool.py my_tool --name "..." --description "..." --params "query:str" --return-type "MyToolResult"`.
2. Add it to `backend/src/lib/openai_agents/tools/` and export it in `tools/__init__.py` if it should be importable by other modules.
3. Document it inside `TOOL_REGISTRY` so Agent Studio, the CLI, and diagnostic tools understand the parameters and usage.

Guidelines:
- Use `@function_tool`. Sync functions are fine for CPU-bound tasks; use `async` when hitting network IO.
- Return `dict` or `BaseModel`; the SDK will JSON-serialize automatically.
- Handle errors gently (return `status="error"` JSON instead of raising) so LLMs can self-correct.
- Capture context via closures or at invocation (see `file_output_tools`). Do **not** add `trace_id` parameters.

### Step 4: Build the factory

File: `backend/src/lib/openai_agents/agents/my_agent.py`

```python
"""My Agent."""
import logging
from typing import List, Optional

from agents import Agent

from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts
from ..config import build_model_settings, get_agent_config, get_model_for_agent
from ..langfuse_client import log_agent_config as log_to_langfuse
from ..models import MyAgentResultEnvelope
from ..prompt_utils import inject_structured_output_instruction

logger = logging.getLogger(__name__)


def create_my_agent(active_groups: Optional[List[str]] = None) -> Agent:
    from ..tools.agr_curation import agr_curation_query

    config = get_agent_config("my_agent")

    base_prompt = get_prompt("my_agent", "system")
    prompts_used = [base_prompt]
    instructions = inject_structured_output_instruction(
        base_prompt.content,
        output_type=MyAgentResultEnvelope,
    )

    if active_groups:
        try:
            from config.group_rules import inject_group_rules
            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="my_agent",
                prompts_out=prompts_used,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Group rule injection failed: %s", exc)

    model = get_model_for_agent(config.model)
    model_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice,
        parallel_tool_calls=True,
    )

    log_to_langfuse(
        agent_name="My Agent Specialist",
        instructions=instructions,
        model=config.model,
        tools=["agr_curation_query"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
            "active_groups": active_groups,
        },
    )

    agent = Agent(
        name="My Agent Specialist",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=[agr_curation_query],
        output_type=MyAgentResultEnvelope,
    )

    set_pending_prompts(agent.name, prompts_used)
    return agent
```

### Step 4b: Prompts in the database

Pick one of these options:

1. **Admin API** (recommended for iterative work)

```bash
curl -X POST http://localhost:8000/api/admin/prompts \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <dev token if DEV_MODE=false>' \
  -d '{
    "agent_name": "my_agent",
    "prompt_type": "system",
    "content": "You are the My Agent Specialist...",
    "change_notes": "Initial version",
    "activate": true
  }'

# Refresh in-memory cache after writing
docker compose exec backend curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh
```

Make sure `ADMIN_EMAILS` in your `.env` includes your login so the admin endpoints authorize you.

2. **PromptService helper** (non-interactive pipelines)

```bash
docker compose exec backend python - <<'PY'
from src.models.sql.database import SessionLocal
from src.lib.prompts.service import PromptService

db = SessionLocal()
svc = PromptService(db)
prompt = svc.create_version(
    agent_name="my_agent",
    content="You are the My Agent Specialist...",
    prompt_type="system",
    created_by="dev@example.org",
    change_notes="Initial",
    activate=True,
)
db.commit()
print("Created", prompt.id)
PY

# Refresh cache afterwards
curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh
```

3. **Alembic migration** – still an option for one-off seeding, but favor the API so prompt revisions stay out of code deployments.

The prompt history (all versions + group rules) shows up automatically inside Agent Studio → Prompt Explorer once the cache refresh completes.

### Step 5: Wire exports & registry entries

1. `agents/__init__.py` – import your factory and append to `__all__`.
2. `backend/src/lib/agent_studio/catalog_service.py`
   - Add the factory import near the top.
   - Create the `AGENT_REGISTRY["my_agent"]` entry:

```python
"my_agent": {
    "name": "My Agent",
    "description": "Validates ...",
    "category": "Validation",
    "subcategory": "Data Validation",
    "has_group_rules": True,
    "tools": ["agr_curation_query"],
    "factory": create_my_agent,
    "requires_document": False,
    "required_params": [],
    "batch_capabilities": [],
    "config_defaults": {
        "model": "gpt-5-mini",
        "reasoning": "low",
        "temperature": 0.2,
    },
    "frontend": {
        "icon": "🧪",
        "show_in_palette": True,
    },
    "supervisor": {
        "enabled": True,
        "tool_name": "ask_my_agent_specialist",
        "tool_description": "Ask the My Agent Specialist ...",
    },
    "batching": {
        "entity": "items",
        "example": 'ask_my_agent_specialist("Check: foo, bar, baz")',
    },
    "documentation": {
        "summary": "Two-sentence curator summary",
        "capabilities": [
            {
                "name": "Exact identifier lookup",
                "description": "...",
                "example_query": "",
                "example_result": "",
            }
        ],
        "data_sources": [
            {
                "name": "Alliance Curation DB",
                "description": "",
                "species_supported": ["WB", "FB"],
            }
        ],
        "limitations": ["Needs validated symbols"]
    },
},
```

3. **Tool metadata** – if you added a brand new tool, also register it inside `TOOL_REGISTRY` (document parameters + allowed domains) so the CLI, Agent Studio Tool Inspector, and diagnostics stay consistent.

### Step 6: Register with the supervisor

File: `backend/src/lib/openai_agents/agents/supervisor_agent.py`

1. Import the factory inside `create_supervisor_agent()`.
2. Instantiate your specialist right next to similar agents.
3. Wrap it with `_create_streaming_tool(...)`:

```python
my_agent = create_my_agent(active_groups=active_groups)
specialist_tools.append(_create_streaming_tool(
    agent=my_agent,
    tool_name="ask_my_agent_specialist",
    tool_description="Ask the My Agent Specialist about ...",
    specialist_name="My Agent Specialist",
))
```

4. Update `SUPERVISOR_INSTRUCTIONS` (markdown table) so routing has clear triggers.
5. If your agent can make repeated batched lookups, keep the `batching` entry in the registry—`streaming_tools.get_batching_config()` reads it automatically and nudges the supervisor after three sequential calls.

### Step 7: Frontend surfacing

No hard-coded icon map anymore. Agent Studio fetches metadata via `GET /api/agent-studio/registry/metadata`, so once the registry entry contains `frontend.icon`, the Flow Builder palette, trace badges, and Tool Inspector all pick it up automatically.

To verify:
1. Start the stack (`make dev`), log into Agent Studio, and open the Agents tab. Use the search box to locate your agent; confirm the description, icon, and category look correct.
2. Use the Prompt Explorer to ensure the base prompt and any group rules show up (and include version metadata from the DB).

### Step 8: Configuration & environment variables

1. **Local overrides** – add optional settings to `~/.agr_ai_curation/.env`:

```bash
# My Agent overrides
AGENT_MY_AGENT_MODEL=gpt-5-mini
AGENT_MY_AGENT_REASONING=low
# AGENT_MY_AGENT_TEMPERATURE=0.2
# AGENT_MY_AGENT_TOOL_CHOICE=auto
```

2. **docker-compose** – environment variables are already passed through for every `AGENT_*` value, so you usually don’t need to edit `docker-compose.yml` unless the agent needs a brand-new service or secret.
3. **Production (EC2)** – `.env` on the server is not deployed automatically. SSH in, edit `/home/ubuntu/ai_curation_prototype/.env`, restart the backend (`docker compose up -d backend --build`), rerun migrations if needed, and refresh the prompt cache.
4. **External databases** – if a tool needs Alliance DB access, ensure `CURATION_DB_URL` / `LITERATURE_DB_URL` env vars point to your SSH tunnel endpoints (`host.docker.internal` + forwarded port) before hitting them.

### Step 9: Test + validate

1. **Static checks**
   - `python scripts/validate_registry.py`
   - `python scripts/validate_current_agents.py`
   - `pytest backend/tests/unit/test_routing_consistency.py` (ensures every registry entry is reachable)
2. **Runtime**
   - `docker compose up -d backend --build` (or `make rebuild-backend`).
   - Refresh prompt cache: `curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh`.
   - Hit `POST /api/agent-studio/catalog/refresh` so the Prompt Explorer sees the new agent.
   - Chat via Agent Studio or `curl http://localhost:8000/api/chat` and confirm the supervisor tool shows up (watch `docker compose logs backend | grep my_agent`).
   - Inspect Langfuse for the new agent name, prompt versions, and structured output payload.
   - Save a flow in the Flow Builder with your agent to ensure `get_agent_by_id()` resolves all required params.
3. **Trace Review** – Run `make trace-review` if you need the dedicated trace analysis backend for debugging streaming transcripts.

---

## Complete Checklist

### Foundation
- [ ] Output model + envelope added to `backend/src/lib/openai_agents/models.py` (with defaults and descriptions).
- [ ] `inject_structured_output_instruction()` called when applicable.

### Tooling
- [ ] Reuse existing tools or create a new one via `scripts/create_tool.py` and `TOOL_REGISTRY` docs.
- [ ] Tool gracefully handles errors and returns JSON-serializable shapes.

### Factory
- [ ] Uses `get_agent_config`, `get_prompt`, `set_pending_prompts`, `build_model_settings`, and `log_agent_config`.
- [ ] Supports optional `active_groups` with `inject_group_rules`.
- [ ] Registers the correct `output_type` (envelope) and any output guardrails if needed.

### Registry + supervisor + frontend
- [ ] Factory exported in `agents/__init__.py`.
- [ ] `AGENT_REGISTRY` entry includes `frontend`, `supervisor`, `batching`, `config_defaults`, and optional `documentation` for Prompt Explorer.
- [ ] Supervisor imports + `_create_streaming_tool` wrapper added.
- [ ] `SUPERVISOR_INSTRUCTIONS` table updated.

### Prompts
- [ ] Prompt inserted via Admin API or PromptService and activated.
- [ ] `POST /api/admin/prompts/cache/refresh` run after insertion.
- [ ] Agent Studio Prompt Explorer shows the new prompt + version metadata.

### Config & deployment
- [ ] Local `.env` updated if the agent needs overrides or new secrets.
- [ ] Production `.env` + Docker restart plan documented (manual step!).
- [ ] Any new DB connections or HTTP allowlists captured in runbooks.

### Validation
- [ ] `scripts/validate_registry.py` + `scripts/validate_current_agents.py` pass.
- [ ] `make dev` → local chat run hits the agent and emits Langfuse traces.
- [ ] Flow Builder palette lists the agent under the right category.
- [ ] Batch nudge triggered (if configured) after ≥3 consecutive calls.
- [ ] Unit/integration tests updated if applicable.

---

## Key Patterns & Gotchas

- **Prompt cache lifecycle** – `backend/src/lib/prompts/cache.initialize()` runs once at startup. If you edit prompts directly in the DB, nothing changes until you call the cache refresh endpoint or restart the backend.
- **Prompt execution logging** – Only log prompts for agents that *actually run*. That’s why we store them via `set_pending_prompts()` and let `run_specialist_with_events()` call `commit_pending_prompts()`.
- **Batching nudges** – Don’t edit `BATCHING_NUDGE_CONFIG` unless absolutely necessary; attach `"batching"` metadata to the registry entry and the runtime automatically learns the tool name and sample string.
- **Document-aware agents** – `pdf` and `gene_expression` factories expect `document_id` + `user_id`. Set `"requires_document": True` and list the params under `required_params` so `get_agent_by_id()` knows what to forward.
- **Flow Builder compatibility** – Flows call `get_agent_by_id()` with a superset of kwargs. If your factory needs new kwargs, update `required_params` and make sure Flow Builder can capture them (usually by adding custom node fields in the frontend).
- **Tool guardrails** – Use `ToolCallTracker` + `create_tool_required_output_guardrail()` when your agent must call a tool before responding (PDF specialist already does this). Guardrails live in `backend/src/lib/openai_agents/guardrails.py`.
- **Langfuse queue** – `log_agent_config()` only queues metadata. Don’t expect to see it instantly in Langfuse until the trace exists and `flush_agent_configs()` runs.
- **Context capture timing** – Most tools capture context on creation (when the agent is instantiated). File-output tools are the exception—they take context at invocation because trace IDs don’t exist yet during agent creation.
- **Tunneled DBs** – `CURATION_DB_URL` and friends often reference `host.docker.internal:<forwarded-port>`. Keep your SSH tunnel alive before running tests that touch AGR data.

---

## Multi-Provider Support

`LLM_PROVIDER` (default `openai`) selects the backend implementation.

| Provider | Models | Notes |
|----------|--------|-------|
| `openai` | `gpt-5`, `gpt-5-mini`, `gpt-4o` | Reasoning enabled; temperature ignored on GPT-5. Requires `OPENAI_API_KEY`. |
| `gemini` | `gemini-3-pro-preview` | Uses LiteLLM compatibility layer (`LitellmModel`). `build_model_settings()` disables parallel tool calls. Requires `GEMINI_API_KEY` and sets `base_url` to Google’s OpenAI-compatible endpoint. |

`build_model_settings()` automatically maps reasoning levels and disables unsupported parameters. Use registry `config_defaults` for per-agent tuning; override with env vars as needed.

---

## Database-Backed Prompts

- **Tables** – `prompt_templates` (versioned prompts) + `prompt_execution_log` (audit trail). Schemas are defined in `backend/src/models/sql/prompts.py`.
- **PromptService** – Located in `backend/src/lib/prompts/service.py`. Handles creating versions, activating them, logging usage, and refreshing the cache.
- **Admin API** – `backend/src/api/admin/prompts.py` exposes `GET /api/admin/prompts`, `POST` to create versions, `POST /cache/refresh`, etc. Authorization uses `ADMIN_EMAILS` + Cognito unless `DEV_MODE=true` and no admins are set.
- **Prompt Catalog** – `PromptCatalogService` (singleton) merges the database prompts with `AGENT_REGISTRY` metadata so Agent Studio displays categories, documentation, and MOD rules.
- **Group rules** – Stored in the same table with `prompt_type="group_rules"`. `inject_group_rules()` fetches them via the cache. Legacy YAML files under `backend/config/group_rules/agents/*` document the intended rules but the live source of truth is the database entry.
- **Manual inspection** – `docker compose exec postgres psql -U postgres ai_curation -c "select agent_name, prompt_type, mod_id, version, is_active from prompt_templates where agent_name='my_agent' order by version;"`

---

## Advanced Topics

- **Structured output injection** – `backend/src/lib/openai_agents/prompt_utils.py` contains `inject_structured_output_instruction()` plus document-context helpers (`format_document_context_for_prompt`, `fetch_document_hierarchy_sync`). Use them to append standardized instructions without copying boilerplate.
- **Document context** – PDF-aware agents should fetch document hierarchy + abstract via `fetch_document_hierarchy_sync()` and append the formatted context so the LLM knows what sections exist.
- **Live audit events** – `streaming_tools.run_specialist_with_events()` emits `TOOL_START`, `TOOL_COMPLETE`, `SPECIALIST_SUMMARY`, etc., into either a live queue (for streaming UI) or the request-scoped buffer. Hook additional UI features into `_live_event_list` with care.
- **Flow tools** – `backend/src/lib/agent_studio/flow_tools.py` registers create/validate/get_template tools for Opus. They rely on `set_workflow_user_context()` to know which user is editing a flow. If your agent needs new flow inputs, update the Flow Builder node config + flow validation accordingly.
- **Langfuse flushing** – Call `flush_langfuse()` at request teardown (already done in the runner) to avoid missing spans when the app exits quickly.
- **Max turns** – `AGENT_MAX_TURNS` (default 20) limits specialist inner loops. Override via env if a specialist requires more tool calls.

---

## Current Specialists & Tools

### Agents (`AGENT_REGISTRY` excerpt)

| Agent ID | Category → Subcategory | File | Notes |
|----------|-----------------------|------|-------|
| `task_input` | Input → Input | N/A | Virtual node for flow instructions. |
| `supervisor` | Routing → System | `agents/supervisor_agent.py` | Routes chat queries and exposes streaming tool wrappers. |
| `pdf` | Extraction → PDF Extraction | `openai_agents/pdf_agent.py` | Document-aware specialist (search/read tools, guardrail enforced). |
| `gene_expression` | Extraction → PDF Extraction | `agents/gene_expression_agent.py` | Pulls expression annotations from PDFs. |
| `gene` | Validation → Data Validation | `agents/gene_agent.py` | AGR gene validation. |
| `allele` | Validation → Data Validation | `agents/allele_agent.py` | AGR allele validation with fullname attribution heuristics. |
| `disease` | Validation → Data Validation | `agents/disease_agent.py` | DOID mapping via `curation_db_sql`. |
| `chemical` | Validation → Data Validation | `agents/chemical_agent.py` | ChEBI API wrapper. |
| `gene_ontology` | Validation → Data Validation | `agents/gene_ontology_agent.py` | QuickGO term lookup. |
| `go_annotations` | Validation → Data Validation | `agents/go_annotations_agent.py` | QuickGO annotations search. |
| `orthologs` | Validation → Data Validation | `agents/orthologs_agent.py` | Alliance orthology API. |
| `ontology_mapping` | Validation → Data Validation | `agents/ontology_mapping_agent.py` | Maps free-text to anatomy/life-stage terms via AGR search. |
| `chat_output` | Output → Output | `agents/chat_output_agent.py` | Final answer formatting. |
| `csv_formatter` / `tsv_formatter` / `json_formatter` | Output → Output | `agents/*_formatter_agent.py` | File output specialists calling file-output tools. |

### Tools (`TOOL_REGISTRY` highlights)

- `agr_curation_query` – Multi-method AGR access (genes, alleles, anatomy, life stages, GO terms, species catalogs).
- `search_document`, `read_section`, `read_subsection` – Weaviate hybrid search + deterministic readers for PDF content.
- `curation_db_sql` – Parameterized SQL query tool bound to the AGR curation database.
- `alliance_api_call`, `chebi_api_call`, `quickgo_api_call`, `go_api_call` – REST wrappers with domain allowlists.
- `save_csv_file`, `save_tsv_file`, `save_json_file` – Persist structured agent outputs and return download metadata.
- `transfer_to_*` tools – Supervisor-only control tools (not exposed to Flow Builder).

Run `python scripts/validate_registry.py --show-tools` (see script help) if you need a printable dump of current tool metadata.

---

## Troubleshooting

| Symptom | Checks |
|---------|--------|
| Agent never appears in palette | `AGENT_REGISTRY` entry missing `frontend.show_in_palette=True`? Did you run `POST /api/agent-studio/catalog/refresh`? Any React caching (hard refresh Agent Studio). |
| Supervisor never routes to your agent | Ensure `_create_streaming_tool` block is added, the supervisor instructions table mentions your tool, and `tool_description` has clear triggers. Inspect logs: `docker compose logs backend | grep ask_my_agent_specialist`. |
| Prompt changes not reflected | Did you call `POST /api/admin/prompts/cache/refresh`? Confirm new version is `is_active=true` in `prompt_templates`. Restart backend if cache fails to refresh (check logs for `Prompt cache initialized`). |
| Structured output parsing failed | Recheck envelope defaults, run `inject_structured_output_instruction`, and confirm `output_type` references the envelope not the single result. Langfuse trace will include `SPECIALIST_RETRY` events if retries happened. |
| Tool never executes | If `tool_choice="required"`, ensure the instructions explicitly tell the LLM to call it. For optional tools, verify docstring + guardrails encourage usage. Batching nudges only fire when the registry’s `batching` entry exists. |
| SQL/AGR connection errors | Confirm tunnels/environment variables. Use `docker compose exec backend python - <<'PY'` to open the same SQLAlchemy engine your tool uses. |
| Flow execution errors | `get_agent_by_id()` raises `MissingRequiredParamError` if `required_params` are missing. Update Flow Builder node schema (frontend) to capture new inputs. |
| Langfuse missing agent configs | Ensure `log_agent_config` is called *before* `Agent` is returned, and that the backend has valid Langfuse credentials (`LANGFUSE_HOST`, etc.). |
| Trace Review empty | Service needs Langfuse credentials—including local fallback if you’re not on VPN. Run `make trace-review` with `TRACE_REVIEW_*` overrides in `.env`. |

---

## Resources

- `README.md` (repo root) – platform overview and entry points.
- `Makefile` – `dev`, `dev-build`, `rebuild-backend`, `trace-review`, `test-*` commands.
- `docs/developer/README.md` – map of available developer docs.
- `scripts/README.md` – agent & tool CLI docs, validation utilities, maintenance scripts.
- `backend/src/lib/openai_agents/` – canonical source for every agent, model, tool, and runner helper.
- `backend/src/lib/agent_studio/` – prompt catalog service, flow tools, suggestion service, trace context.
- `backend/src/lib/prompts/` – cache + service + execution logging APIs.
- `docs/plans/2026-01-24-agent-simplification.md` – rationale behind the registry simplification.
- Langfuse docs: https://langfuse.com/docs
- OpenAI Agents SDK: https://github.com/openai/openai-agents-python
- LiteLLM docs (Gemini adapter): https://docs.litellm.ai/

Keep this guide updated whenever the stack changes (new services, new registries, or different tooling). Treat the `AGENT_REGISTRY` + prompt database as the source of truth—Agent Studio, CLI tooling, and Flow Builder all derive their behavior from those two places.
