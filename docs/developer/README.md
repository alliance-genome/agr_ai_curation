# Developer Documentation

Documentation for the AI Curation Platform developers.

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `api/` | API reference documentation |
| `guides/` | Developer guides and how-tos |
| `traces/` | Langfuse trace analysis tools |

## Quick Navigation

### Getting Started

Start here for new developers:

1. **[CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md)** -- Overview of the YAML + database architecture
2. **[ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md)** -- Step-by-step guide to create a new agent (YAML or UI)
3. **[ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md)** -- How to add tools that agents can use
4. **[AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md)** -- Comprehensive agent architecture reference
5. **[UPLOAD_RUNTIME_CONTRACT.md](guides/UPLOAD_RUNTIME_CONTRACT.md)** -- Upload runtime contract (status/cancellation/rollback/idempotency; implementation in ALL-23)

### Developer Guides

| Guide | Description |
|-------|-------------|
| [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) | Full architecture guide -- YAML source of truth, database runtime, loaders, deployment |
| [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) | Create agents via YAML config or Agent Studio UI -- no Python agent files needed |
| [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) | Add Python tools with `@function_tool` and register tool bindings |
| [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) | Comprehensive reference: unified agents table, dynamic supervisor, tool bindings, prompt management |
| [UPLOAD_RUNTIME_CONTRACT.md](guides/UPLOAD_RUNTIME_CONTRACT.md) | Upload runtime behavioral contract: status precedence, cancellation, rollback matrix, and idempotency expectations (implementation tracked in ALL-23) |

### API Reference

- **[API_USAGE.md](api/API_USAGE.md)** -- Complete HTTP reference with streaming, auth, and workflows

### Trace Analysis

- **[TRACE_REVIEW_API.md](traces/TRACE_REVIEW_API.md)** -- Trace review service API documentation
- **[TRACE_REVIEW_SPECIFICATION.md](traces/TRACE_REVIEW_SPECIFICATION.md)** -- Trace review system specification

## Configuration Reference

For configuration files, see:

| File | Description |
|------|-------------|
| [config/README.md](../../config/README.md) | Configuration directory overview |
| [config/agents/README.md](../../config/agents/README.md) | Agent configuration reference |
| [config/models.yaml](../../config/models.yaml) | Model catalog (curator-selectable LLMs) |
| [config/groups.yaml.example](../../config/groups.yaml.example) | Group/Cognito mapping template |
| [config/connections.yaml.example](../../config/connections.yaml.example) | External connections template |

## Common Tasks

### Add a New Agent

Agents are defined via YAML config and stored in the unified `agents` database table. No Python agent files are needed.

```bash
# Copy template
cp -r config/agents/_examples/basic_agent config/agents/my_agent

# Edit files
# - config/agents/my_agent/agent.yaml    (agent definition)
# - config/agents/my_agent/prompt.yaml   (instructions)

# Seed into database (via migration or manual insert)
# Then restart backend
docker compose restart backend
```

See [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) for details. Custom agents can also be created directly via the Agent Studio UI without any file changes.

### Add a New Tool

```bash
# Create tool file
# backend/src/lib/openai_agents/tools/my_tool.py

# Register tool binding in catalog_service.py
# Add to TOOL_BINDINGS dict

# Reference in agent.yaml
# tools:
#   - my_tool

# Restart backend
docker compose restart backend
```

See [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) for details.

### Configure Groups

```bash
# Copy template
cp config/groups.yaml.example config/groups.yaml

# Edit to map your identity provider groups
# to internal group IDs
```

### Configure External Connections

```bash
# Copy template
cp config/connections.yaml.example config/connections.yaml

# Edit to configure databases, APIs, caches
```

## Architecture Overview

The system uses a **config-driven, database-backed architecture** where:

1. **YAML defines initial state** -- Agent metadata, prompts, and group rules in `config/agents/`
2. **Database is runtime authority** -- The unified `agents` table stores all agent records (system + custom)
3. **Dynamic discovery** -- The supervisor queries the database for enabled agents and builds streaming tools at runtime
4. **Declarative tool bindings** -- Tools are resolved via `TOOL_BINDINGS` with explicit context requirements
5. **No hardcoded agent files** -- All 16 original Python agent files have been replaced by generic construction from database rows

```
config/agents/          # YAML source of truth (system agents)
  supervisor/
  gene/
  disease/
  [your_agent]/
    agent.yaml          # Agent definition
    prompt.yaml         # Instructions
    group_rules/        # Org-specific behavior

          |
          | Alembic migration seeds into:
          v

agents table            # Unified database (runtime authority)
  (system agents from YAML + custom agents from UI)

          |
          | get_agent_by_id() reads from DB
          v

Runtime Agent           # OpenAI Agents SDK Agent instance
  (tools resolved via TOOL_BINDINGS)
  (group rules injected from prompt cache)
  (output schema resolved from models.py)
```

See [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) and [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) for the complete reference.
