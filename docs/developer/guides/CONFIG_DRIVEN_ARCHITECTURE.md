# Config-Driven Architecture Guide

This guide explains the config-driven architecture for AGR AI Curation, where **YAML is the source of truth** and the database serves as a runtime cache populated at startup.

> **Last Updated:** February 24, 2026 (Added LLM provider/model config system, unified agents table, tool policies)
>
> **Scope**: This guide describes the repository source layout used by core
> package and runtime maintainers. Standard installs load package-owned agents,
> tools, and defaults from `~/.agr_ai_curation/runtime/packages/` plus
> deployment overrides under `~/.agr_ai_curation/runtime/config/`. Public/custom
> deployments should not edit repo `config/` or `backend/src/` paths directly.
> For the public runtime contract, see
> [Modular Packages and Upgrades](../../deployment/modular-packages.md).

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Core Concepts](#core-concepts)
4. [Configuration Files](#configuration-files)
5. [LLM Provider Configuration](#llm-provider-configuration)
6. [Unified Agents Table](#unified-agents-table)
7. [Adding a New Agent](#adding-a-new-agent)
8. [Adding a New Tool](#adding-a-new-tool)
9. [Configuring Groups](#configuring-groups)
10. [Adding External Connections](#adding-external-connections)
11. [Deployment](#deployment)
12. [Environment Variables](#environment-variables)
13. [Testing](#testing)
14. [Troubleshooting](#troubleshooting)

---

## Overview

The config-driven architecture separates the **base product** (reusable by any organization) from **customizable components** (organization-specific agents, prompts, tools).

### Key Principles

1. **YAML is source of truth** - Configuration lives in YAML files, not code
2. **Database is runtime cache** - YAML is loaded into the database at startup
3. **Self-contained agents** - Each agent is a folder with all its configuration
4. **Environment variable substitution** - Secrets use `${VAR}` or `${VAR:-default}` syntax
5. **Thread-safe lazy loading** - Loaders initialize once, can force reload

### Benefits

- **Portable**: Easy to copy, version, and share agent configurations
- **Testable**: YAML can be validated before deployment
- **Customizable**: Organizations can maintain their own agent sets
- **Auditable**: All configuration is version-controlled

---

## Directory Structure

Two layouts matter in the modular system: the installed runtime under
`~/.agr_ai_curation/`, and the repository source tree used to build and maintain
the shipped `core` package.

```text
~/.agr_ai_curation/
├── .env
├── runtime/
│   ├── config/                          # Deployment override YAML
│   │   ├── connections.yaml
│   │   ├── groups.yaml
│   │   ├── maintenance_message.txt
│   │   ├── models.yaml
│   │   ├── overrides.yaml              # Optional package/tool selections
│   │   ├── providers.yaml
│   │   └── tool_policy_defaults.yaml
│   ├── packages/
│   │   ├── core/                        # Shipped AGR package
│   │   │   ├── package.yaml
│   │   │   ├── agents/
│   │   │   ├── config/
│   │   │   ├── python/src/...
│   │   │   ├── requirements/
│   │   │   └── tools/bindings.yaml
│   │   └── org-custom/                  # Your package(s)
│   └── state/
│       └── package_runner/              # Per-package virtualenvs and runtime state
└── data/                                # Mutable deployment data
```

```text
agr_ai_curation/
├── config/                              # Repo mirror of shipped core defaults
│   ├── models.yaml
│   ├── providers.yaml
│   ├── tool_policy_defaults.yaml
│   ├── groups.yaml.example
│   ├── connections.yaml.example
│   └── agents/                          # Source-development mirror of core agent bundles
│       ├── README.md
│       ├── _examples/
│       └── ...
├── packages/
│   └── core/
│       ├── package.yaml
│       ├── agents/
│       ├── config/
│       ├── python/src/agr_ai_curation_core/tools/
│       ├── requirements/
│       └── tools/bindings.yaml
├── backend/
│   ├── src/lib/config/                  # Runtime config/package loaders
│   ├── src/lib/packages/                # Package discovery, manifests, registry, runner
│   ├── src/lib/agent_studio/            # Agent Studio services and runtime catalog
│   └── src/models/sql/                  # Database models
└── scripts/
    └── deploy_alliance.sh               # Repo-local core-package sync helper
```

---

## Core Concepts

### Agent Folder Structure

Each package-owned agent is a self-contained folder:

```text
agents/my_agent/
├── agent.yaml        # Agent definition and metadata
├── prompt.yaml       # Base prompt instructions
├── schema.py         # Pydantic output schema
└── group_rules/      # Organization-specific rules (optional)
    ├── fb.yaml       # FlyBase-specific rules
    ├── wb.yaml       # WormBase-specific rules
    └── ...
```

For standalone installs, that folder lives under
`~/.agr_ai_curation/runtime/packages/<package>/agents/`. In this repository,
`config/agents/` is the source-development mirror for the shipped `core`
package.

### Loading Order

At system startup:

1. Package manifests are discovered from `runtime/packages/`.
2. `connections.yaml` and `groups.yaml` are loaded from `runtime/config/`.
3. Package-backed provider/model/tool policy defaults are loaded from
   `runtime/packages/*/config/`.
4. The deployment override files in `runtime/config/` are merged on top.
5. Package-owned agent bundles are discovered from `runtime/packages/*/agents/`.
6. Group rules are associated with agents based on `group_id` matching.
7. Schemas are dynamically imported from each bundle's `schema.py`.
8. Provider runtime contracts are validated (cross-checks providers, models,
   and API keys).
9. System agents are seeded into the unified `agents` database table.
10. Tool policies are loaded from the `tool_policies` table.

### Thread Safety

All loaders use `threading.Lock()` for thread-safe initialization:

```python
from backend.src.lib.config import load_agent_definitions

# First call loads from YAML
agents = load_agent_definitions()

# Subsequent calls return cached data
agents = load_agent_definitions()

# Force reload if needed
agents = load_agent_definitions(force_reload=True)
```

---

## Configuration Files

### agent.yaml

Defines the agent's metadata, tools, and model configuration:

```yaml
# Agent identifier (must match folder name)
agent_id: gene_validation

# Human-readable name
name: "Gene Validation Agent"

# Description for UI and documentation
description: "Validates gene symbols and IDs against the Alliance database"

# Supervisor routing - tells supervisor when to use this agent
supervisor_routing:
  description: "Use for validating genes, looking up gene symbols, or finding gene information"

# Tools this agent can use (must exist in the merged runtime tool registry)
tools:
  - agr_curation_query
  - alliance_api_call

# Output schema class name (from schema.py)
output_schema: GeneValidationEnvelope

# Model configuration
model_config:
  model: "${AGENT_GENE_MODEL:-gpt-4o}"
  temperature: 0.1
  reasoning: "medium"

# Whether to inject group-specific rules
group_rules_enabled: true
```

### prompt.yaml

Contains the agent's base prompt:

```yaml
agent_id: gene_validation

content: |
  You are a Gene Validation Specialist for the Alliance of Genome Resources.

  ## Your Role

  Validate gene symbols and identifiers against the Alliance database.

  ## Tools Available

  - **agr_curation_query**: Query the Alliance curation database
  - **alliance_api_call**: Call the Alliance REST API

  ## Instructions

  1. Parse the user's query to identify gene symbols or IDs
  2. Query the database to validate each gene
  3. Return structured results with validation status

  ## Output Format

  Always return results using the GeneValidationEnvelope schema.
```

### schema.py

Defines the Pydantic output schema:

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class GeneResult(BaseModel):
    """A single gene validation result."""
    symbol: str = Field(description="Gene symbol")
    primary_id: str = Field(description="Alliance CURIE (e.g., FB:FBgn0000001)")
    species: str = Field(description="Species taxon")
    valid: bool = Field(description="Whether the gene was found")

class GeneValidationEnvelope(BaseModel):
    """Container for gene validation results."""
    results: List[GeneResult] = Field(default_factory=list)
    not_found: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
```

### Group Rules (group_rules/*.yaml)

Organization-specific behavior:

```yaml
# agents/gene/group_rules/fb.yaml
group_id: FB

content: |
  ## FlyBase-Specific Rules

  When validating genes for FlyBase:
  - Use the FB: prefix for FlyBase identifiers
  - Check both current symbols and synonyms
  - Include CG numbers as alternative identifiers
```

---

## LLM Provider Configuration

The LLM provider system uses two YAML files to separate **what models are available** from **how to reach each provider backend**.

### models.yaml

Defines the catalog of models that curators can select in the UI. Each entry declares capabilities, guidance text, and which provider handles it.

Located at: `config/models.yaml`

```yaml
models:
  - model_id: gpt-5-mini
    name: GPT-5 Mini
    provider: openai                    # Must match a key in providers.yaml
    description: Fast default model for day-to-day drafting and light extraction.
    guidance: Start here for most tasks.
    default: true                       # Exactly one model should be default
    supports_reasoning: false
    supports_temperature: false
    recommended_for:
      - Interactive drafting and quick iterations
      - Routine extraction and formatting
    avoid_for:
      - Deep multi-step adjudication with conflicting evidence

  - model_id: gpt-5.4
    name: GPT-5.4
    provider: openai
    description: Highest-quality model for complex reasoning.
    supports_reasoning: true
    reasoning_options: [low, medium, high]
    default_reasoning: medium
    reasoning_descriptions:
      low: Fastest mode. Good for quick checks.
      medium: Recommended default for curation.
      high: Deepest reasoning. Use sparingly.

  - model_id: openai/gpt-oss-120b
    name: GPT-OSS 120B
    provider: groq                      # Routed through Groq via LiteLLM
    description: Ultra-fast open-weight model on Groq.
    supports_reasoning: false
    supports_temperature: true
```

#### Model Definition Fields

| Field | Required | Description |
|-------|----------|-------------|
| `model_id` | Yes | Unique identifier passed to the LLM API |
| `name` | No | Human-readable display name (defaults to model_id) |
| `provider` | No | Provider key from providers.yaml (defaults to `openai`) |
| `description` | No | Short description for the UI |
| `guidance` | No | Curator-facing usage guidance |
| `default` | No | Whether this is the default model (`true`/`false`) |
| `curator_visible` | No | Show in curator model picker (default: `true`) |
| `supports_reasoning` | No | Whether the model supports reasoning levels |
| `supports_temperature` | No | Whether the model supports temperature control |
| `reasoning_options` | No | List of reasoning levels (e.g., `[low, medium, high]`) |
| `default_reasoning` | No | Default reasoning level (must be in `reasoning_options`) |
| `reasoning_descriptions` | No | Map of reasoning level to description text |
| `recommended_for` | No | List of use cases where the model excels |
| `avoid_for` | No | List of use cases where another model is preferred |

### providers.yaml

Defines how to reach each LLM backend. Supports two driver types: `openai_native` for direct OpenAI API access and `litellm` for third-party providers routed through LiteLLM.

Located at: `config/providers.yaml`

```yaml
providers:
  openai:
    driver: openai_native               # Direct OpenAI Agents SDK
    api_key_env: OPENAI_API_KEY          # Env var holding the API key
    base_url_env: OPENAI_BASE_URL        # Optional env var for base URL override
    default_base_url: ""                 # Empty = use OpenAI default
    api_mode: responses                  # "responses" or "chat_completions"
    default_for_runner: true             # Exactly one provider must be true
    supports:
      parallel_tool_calls: true

  gemini:
    driver: litellm                      # Route through LiteLLM
    api_key_env: GEMINI_API_KEY
    base_url_env: GEMINI_BASE_URL
    default_base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
    litellm_prefix: gemini               # Required for litellm driver
    drop_params: true                    # Drop unsupported params for this provider
    supports:
      parallel_tool_calls: false

  groq:
    driver: litellm
    api_key_env: GROQ_API_KEY
    base_url_env: GROQ_BASE_URL
    default_base_url: "https://api.groq.com/openai/v1"
    litellm_prefix: groq
    drop_params: true
    supports:
      parallel_tool_calls: true
```

#### Provider Definition Fields

| Field | Required | Description |
|-------|----------|-------------|
| `driver` | Yes | `openai_native` or `litellm` |
| `api_key_env` | Yes | Name of the environment variable holding the API key |
| `base_url_env` | No | Env var for base URL override |
| `default_base_url` | No | Fallback base URL when env var is not set |
| `litellm_prefix` | Conditional | Required when `driver: litellm` (e.g., `gemini`, `groq`) |
| `drop_params` | No | Drop unsupported params for non-OpenAI providers (default: true for litellm) |
| `api_mode` | No | `responses` or `chat_completions` (default: `responses`) |
| `default_for_runner` | No | Whether this provider is the default runner (exactly one must be `true`) |
| `supports.parallel_tool_calls` | No | Whether parallel tool calls are supported (default: `true`) |

### Provider Validation

At startup, `provider_validation.py` cross-validates the loaded providers and models:

- Every model's `provider` field must reference a key defined in `providers.yaml`
- Providers used by at least one model (or marked `default_for_runner`) must have their API key set in the environment
- Exactly one provider must have `default_for_runner: true`

The validation runs in **strict mode** by default (`LLM_PROVIDER_STRICT_MODE=true`), which causes the backend to fail fast on startup if any required API key is missing. Set `LLM_PROVIDER_STRICT_MODE=false` to downgrade missing keys to warnings instead of errors.

```python
from src.lib.config import validate_and_cache_provider_runtime_contracts

# Runs at startup; raises RuntimeError if validation fails in strict mode
report = validate_and_cache_provider_runtime_contracts()

# Later, retrieve the cached report
from src.lib.config import get_startup_provider_validation_report
report = get_startup_provider_validation_report()
```

### Adding a New LLM Provider

1. Add the provider definition to `config/providers.yaml`:

```yaml
providers:
  # ... existing providers ...

  my_provider:
    driver: litellm
    api_key_env: MY_PROVIDER_API_KEY
    default_base_url: "https://api.myprovider.com/v1"
    litellm_prefix: my_provider
    drop_params: true
    supports:
      parallel_tool_calls: true
```

2. Add models that use the provider to `config/models.yaml`:

```yaml
models:
  # ... existing models ...

  - model_id: my-model-7b
    name: My Model 7B
    provider: my_provider
    description: Fast model from My Provider.
    supports_reasoning: false
    supports_temperature: true
```

3. Set the API key in your environment:

```bash
export MY_PROVIDER_API_KEY=your-key-here
```

4. Restart the backend. The provider validation will confirm the new provider is reachable.

---

## Unified Agents Table

All agents (system and custom) are stored in a single `agents` PostgreSQL table. This replaces the previous architecture where system agents were hardcoded Python files and custom agents lived in a separate `custom_agents` table.

### Agent Visibility Model

Each agent row has a `visibility` field controlling who can see and use it:

| Visibility | Scope | Created By |
|------------|-------|------------|
| `system` | Visible to all users | Seeded from YAML at startup |
| `private` | Visible only to the owner | Created via Agent Workshop UI |
| `project` | Visible to project members | Shared within a project |

### Database Schema (key columns)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `agent_key` | String(100) | Unique identifier (e.g., `gene_validation`) |
| `user_id` | Integer | Owner (NULL for system agents) |
| `name` | String(255) | Display name |
| `instructions` | Text | Full prompt text |
| `model_id` | String(100) | LLM model identifier (from models.yaml) |
| `model_temperature` | Float | Temperature setting |
| `model_reasoning` | String(20) | Reasoning level (low/medium/high or NULL) |
| `tool_ids` | JSONB | List of tool keys the agent can use |
| `output_schema_key` | String(100) | Pydantic schema class name |
| `visibility` | String(20) | `private`, `project`, or `system` |
| `project_id` | UUID | Project scope (for `project` visibility) |
| `supervisor_enabled` | Boolean | Whether supervisor can route to this agent |
| `show_in_palette` | Boolean | Whether to show in the Flow Builder palette |
| `is_active` | Boolean | Soft-delete flag |

### Supporting Tables

- **`projects`** - Sharing boundaries for agent visibility
- **`project_members`** - Membership mapping (user to project, with role)
- **`tool_policies`** - Runtime policy controls for each tool (visibility, attach/execute permissions)
- **`tool_idea_requests`** - Curator-submitted requests for new tool capabilities

### Agent Service

The `agent_service.py` module provides the data access layer:

```python
from src.lib.agent_studio.agent_service import (
    list_agents_visible_to_user,
    get_agent_by_key,
    agent_to_execution_spec,
)

# List all agents a user can see (system + their private + project agents)
agents = list_agents_visible_to_user(db, user_id=42)

# Get a specific agent with visibility enforcement
agent = get_agent_by_key(db, agent_key="gene_validation", user_id=42)

# Convert to runtime execution spec
spec = agent_to_execution_spec(agent)
```

### Database Migrations

The unified agents schema was introduced through a series of Alembic migrations:

1. `u3v4w5x6y7z8` - Create `agents` and `projects` tables
2. `v4w5x6y7z8a9` - Seed system agents from YAML into the `agents` table
3. `w5x6y7z8a9b0` - Point custom agent foreign keys to the new `agents` table
4. `x6y7z8a9b0c1` - Drop the legacy `custom_agents` table
5. `y7z8a9b0c1d2` - Add unique index for active custom agent names per user
6. `z8a9b0c1d2e3` - Create `tool_policies` table
7. `a9b0c1d2e3f4` - Create `tool_idea_requests` table

Migrations run automatically on container startup via `alembic upgrade head`.

---

## Adding a New Agent

Choose the path that matches your goal:

- **Standalone install or org customization** -- Create the bundle inside a
  package under `~/.agr_ai_curation/runtime/packages/<package>/agents/`.
- **Shipped core package maintenance** -- Update the repo mirror under
  `config/agents/` and keep `packages/core/agents/` aligned before shipping.

### Step 1: Create or choose a package

```bash
mkdir -p ~/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent
```

### Step 2: Update agent.yaml

Create `agents/my_agent/agent.yaml` inside the package:

```yaml
agent_id: my_agent
name: "My Agent"
description: "Does something useful"

supervisor_routing:
  description: "Use when the user asks about [specific domain]"

tools:
  - agr_curation_query    # Existing tool
  # - my_custom_tool      # Add custom tools if needed

output_schema: MyAgentEnvelope

model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-4o}"
  temperature: 0.2
  reasoning: "low"

group_rules_enabled: false
```

### Step 3: Update prompt.yaml and schema.py

Create `agents/my_agent/prompt.yaml`:

```yaml
agent_id: my_agent

content: |
  You are a specialist agent for [domain].

  ## Your Role
  [Describe the agent's purpose]

  ## Tools Available
  [List and describe available tools]

  ## Instructions
  [Step-by-step instructions]
```

Create `agents/my_agent/schema.py`:

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class MyResult(BaseModel):
    """Single result item."""
    id: str
    name: str
    valid: bool

class MyAgentEnvelope(BaseModel):
    """Container for results."""
    results: List[MyResult] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
```

### Step 4: Add Group Rules (Optional)

If your agent needs organization-specific behavior:

```bash
mkdir -p ~/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent/group_rules
```

Create `agents/my_agent/group_rules/fb.yaml`:

```yaml
group_id: FB
content: |
  ## FlyBase-Specific Behavior

  When processing FlyBase data:
  - [Specific instructions]
```

### Step 5: Export and install the package

Ensure the package manifest exports the bundle. The shorthand form is the
easiest option:

```yaml
agent_bundles:
  - name: my_agent
    has_schema: true
    group_rules: [fb]
```

If you are maintaining the shipped core package from this repository, keep the
same bundle aligned in `packages/core/agents/my_agent/` and the repo mirror
under `config/agents/my_agent/`.

### Step 6: Restart and test

```bash
# Restart the standalone backend to pick up package changes
docker compose --env-file ~/.agr_ai_curation/.env \
  -f docker-compose.production.yml restart backend
```

---

## Adding a New Tool

Tools are Python functions that agents can call. In the modular runtime, public
and organization-specific tools live in a package's Python source tree and are
exported through `tools/bindings.yaml`.

### Step 1: Choose a package layout

```text
~/.agr_ai_curation/runtime/packages/org-custom/
├── package.yaml
├── requirements/runtime.txt
├── python/src/org_custom/tools/
└── tools/bindings.yaml
```

### Step 2: Create the Tool

Create `python/src/org_custom/tools/my_tool.py`:

```python
"""My custom tool for [purpose]."""
import logging
from typing import Optional
from agents import function_tool

logger = logging.getLogger(__name__)


@function_tool
def my_custom_tool(
    query: str,
    limit: int = 10,
) -> dict:
    """
    Search for [something] in [data source].

    Args:
        query: The search query
        limit: Maximum results to return (default: 10)

    Returns:
        Dictionary with 'results' list and 'total' count
    """
    try:
        # Your implementation here
        results = []

        return {
            "status": "success",
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        logger.error(f"Tool error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "results": [],
        }
```

### Step 3: Export the tool binding

Declare the tool in `tools/bindings.yaml`:

```yaml
package_id: org.custom
bindings_api_version: 1.0.0
tools:
  - tool_id: my_custom_tool
    binding_kind: static
    callable: org_custom.tools.my_tool:my_custom_tool
    required_context: []
    description: Search for [something] in [data source]
```

### Step 4: Reference in Agent

Add the tool ID to a package-owned agent bundle:

```yaml
tools:
  - my_custom_tool
```

### Tool Guidelines

- Use `@function_tool` decorator
- Return `dict` or Pydantic `BaseModel`
- Handle errors gracefully (return error dict, don't raise)
- Add clear docstrings (used by LLM for tool selection)
- Keep functions focused on one task

### Repo-maintainer note

If you are updating the shipped core package from this repository:

- put the tool implementation in
  `packages/core/python/src/agr_ai_curation_core/tools/`,
- export it from `packages/core/tools/bindings.yaml`, and
- only edit `backend/src/lib/agent_studio/catalog_service.py` or the package
  runtime modules when you need new resolver behavior rather than a normal
  package-declared tool.

---

## Configuring Groups

Groups map authentication provider groups (e.g., AWS Cognito) to internal group IDs.

### Step 1: Copy Template

```bash
cp config/groups.yaml.example config/groups.yaml
```

### Step 2: Configure Identity Provider

```yaml
identity_provider:
  type: cognito              # cognito, okta, auth0, custom
  group_claim: "cognito:groups"  # JWT claim containing groups
```

### Step 3: Add Group Mappings

```yaml
groups:
  # Map external group name to internal ID
  FlyBase:
    group_id: FB             # Used in agent group_rules/
    display_name: "FlyBase"
    description: "Drosophila genome database"

  WormBase:
    group_id: WB
    display_name: "WormBase"
    description: "C. elegans genome database"
```

### Step 4: Configure Admin Groups (Optional)

```yaml
admin_groups:
  - Alliance_Admins
```

### Using Groups in Agents

Create `group_rules/{group_id}.yaml` in the package-owned agent folder:

```yaml
# agents/gene/group_rules/fb.yaml
group_id: FB

content: |
  ## FlyBase-Specific Rules

  For FlyBase genes:
  - Use FB: prefix for identifiers
  - Check for CG numbers as alternatives
```

---

## Adding External Connections

External connections define databases, APIs, and caches the system uses.

### Step 1: Copy Template

```bash
cp config/connections.yaml.example config/connections.yaml
```

### Step 2: Configure Databases

```yaml
databases:
  primary:
    type: postgresql
    host: "${DB_HOST:-localhost}"
    port: ${DB_PORT:-5432}
    database: "${DB_NAME:-ai_curation}"
    username: "${DB_USER:-postgres}"
    password: "${DB_PASSWORD}"  # No default - must be set!

    pool:
      min_size: 5
      max_size: 20

    health_check:
      enabled: true
      query: "SELECT 1"
      interval_seconds: 30
      timeout_seconds: 5
```

### Step 3: Configure APIs

```yaml
apis:
  openai:
    type: openai
    base_url: "${OPENAI_BASE_URL:-https://api.openai.com/v1}"
    api_key: "${OPENAI_API_KEY}"

    rate_limit:
      requests_per_minute: 60
      tokens_per_minute: 90000

    health_check:
      enabled: true
      endpoint: "/models"
      interval_seconds: 60
```

### Step 4: Configure Caches (Optional)

```yaml
caches:
  redis:
    enabled: ${REDIS_ENABLED:-false}
    host: "${REDIS_HOST:-localhost}"
    port: ${REDIS_PORT:-6379}
    password: "${REDIS_PASSWORD:-}"
```

### Step 5: Set Required Services

```yaml
health_check:
  aggregate:
    required_services:
      - databases.primary
      - apis.openai
    optional_services:
      - caches.redis
```

---

## Deployment

For standalone installs, deliver custom behavior by copying a versioned package
into `~/.agr_ai_curation/runtime/packages/` and restarting the stack. The
commands below are repo-maintainer helpers for syncing shipped Alliance content
inside a source checkout.

### Alliance Deployment

In the repo-maintainer workflow, Alliance agents are developed in
`alliance_agents/` and synced into the repo mirror at `config/agents/`:

```bash
# Preview changes (dry run)
./scripts/deploy_alliance.sh --dry-run --verbose

# Deploy Alliance agents
make deploy-alliance

# Or use the script directly
./scripts/deploy_alliance.sh --verbose
```

### Deployment Script Options

```bash
./scripts/deploy_alliance.sh [OPTIONS]

Options:
  --dry-run      Show what would be deployed without making changes
  --clean        Remove existing agents before deploying
  --verbose      Show detailed output
  --force        Skip confirmation prompts
```

### Docker Deployment

```bash
# Rebuild with new configuration
docker compose down
docker compose up -d --build

# Just restart backend
docker compose restart backend
```

---

## Environment Variables

### Syntax

YAML files support environment variable substitution:

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Use value of VAR, empty string if not set |
| `${VAR:-default}` | Use value of VAR, or "default" if not set |

### Common Variables

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ai_curation
DB_USER=postgres
DB_PASSWORD=secret
DATABASE_URL=postgresql://postgres:secret@localhost:5432/ai_curation

# LLM Provider API Keys
OPENAI_API_KEY=sk-...              # Required (default runner provider)
GEMINI_API_KEY=...                 # Optional (for Gemini provider)
GROQ_API_KEY=gsk_...               # Optional (for Groq provider)

# LLM Provider Base URL Overrides (optional)
OPENAI_BASE_URL=https://api.openai.com/v1
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
GROQ_BASE_URL=https://api.groq.com/openai/v1

# Provider Validation
LLM_PROVIDER_STRICT_MODE=true      # Fail startup if required keys missing (default: true)

# Per-agent model overrides (in agent.yaml via ${VAR:-default})
AGENT_GENE_MODEL=gpt-4o
AGENT_SUPERVISOR_MODEL=gpt-4o

# Config paths (optional, defaults to config/)
CONFIG_PATH=/app/config
MODELS_CONFIG_PATH=/app/config/models.yaml
PROVIDERS_CONFIG_PATH=/app/config/providers.yaml
ALLIANCE_AGENTS_PATH=/app/alliance_agents

# Tool policy cache
TOOL_POLICY_CACHE_TTL_SECONDS=30   # How long to cache tool policies (default: 30)
```

### Security

- **Never commit secrets** - Use environment variables for passwords/keys
- **No defaults for passwords** - Use `${DB_PASSWORD}` not `${DB_PASSWORD:-secret}`
- **Use .env files** - Keep secrets in `.env` (gitignored)

---

## Testing

### Run Config Loader Tests

```bash
# All config loader tests
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  python -m pytest tests/unit/test_config_loaders.py -v

# Specific test class
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  python -m pytest tests/unit/test_config_loaders.py::TestGroupsLoader -v
```

### Validate Configuration

```bash
# Check agent YAML is valid (run from within Docker container)
docker compose exec backend python -c "from src.lib.config import load_agent_definitions; print(load_agent_definitions())"

# Check groups configuration
docker compose exec backend python -c "from src.lib.config import load_groups; print(load_groups())"

# Check connections configuration
docker compose exec backend python -c "from src.lib.config import load_connections; print(load_connections())"

# Check models.yaml is valid
docker compose exec backend python -c "from src.lib.config import load_models; print(load_models())"

# Check providers.yaml is valid
docker compose exec backend python -c "from src.lib.config import load_providers; print(load_providers())"

# Run full provider/model cross-validation report
docker compose exec backend python -c "
from src.lib.config import build_provider_runtime_report
import json
report = build_provider_runtime_report(strict_mode=False)
print(json.dumps(report, indent=2))
"
```

### Test Deployment Script

```bash
# Dry run to see what would change
./scripts/deploy_alliance.sh --dry-run --verbose
```

---

## Troubleshooting

### Agent Not Loading

| Symptom | Check |
|---------|-------|
| Agent missing from supervisor | Folder name starts with `_`? Those are skipped. |
| YAML parse error | Run `python -c "import yaml; yaml.safe_load(open('<agent_root>/agent.yaml'))"` where `<agent_root>` is your package bundle directory. |
| Schema not found | Check `output_schema` matches class name in `schema.py` |
| Tools not found | Verify the package exports the tool ID in `tools/bindings.yaml` and the callable import path is valid. |

### Group Rules Not Applied

| Symptom | Check |
|---------|-------|
| Rules not injected | `group_rules_enabled: true` in agent.yaml? |
| Wrong rules applied | File name matches `group_id` from groups.yaml? |
| No rules for user | User's Cognito group mapped in groups.yaml? |

### Environment Variables Not Substituted

| Symptom | Check |
|---------|-------|
| Empty value | Variable not set in environment or .env |
| Literal `${VAR}` appears | Check for typos in syntax |
| Default not used | Correct syntax is `${VAR:-default}` with `:-` |

### LLM Provider Issues

| Symptom | Check |
|---------|-------|
| Startup crash with "LLM provider validation failed" | Required API key env var is not set. Check `providers.yaml` for which env var is expected. |
| Model references unknown provider | The `provider` field in `models.yaml` doesn't match a key in `providers.yaml`. |
| "must define exactly one provider with default_for_runner=true" | Check `providers.yaml` - exactly one provider needs `default_for_runner: true`. |
| LiteLLM provider not routing correctly | Verify `litellm_prefix` is set and `drop_params: true` for non-OpenAI providers. |
| Provider validation warnings but no errors | Set `LLM_PROVIDER_STRICT_MODE=false` if unused providers missing keys is acceptable. |

### Health Checks Failing

| Symptom | Check |
|---------|-------|
| Database unhealthy | Connection string correct? Port open? |
| API unhealthy | API key valid? Rate limited? |
| Timeout errors | Increase `timeout_seconds` in health_check |

### Unified Agents Table Issues

| Symptom | Check |
|---------|-------|
| System agents missing after restart | Check `alembic upgrade head` ran successfully in startup logs. |
| Custom agent not visible | Verify the agent's `visibility` and `is_active` flags; check user's project membership. |
| Duplicate agent_key error | Agent keys must be unique. Check for conflicts between YAML-defined and custom agents. |

---

## Resources

- [config/README.md](../../../config/README.md) - Configuration directory overview
- [config/agents/README.md](../../../config/agents/README.md) - Agent configuration details
- [alliance_agents/README.md](../../../alliance_agents/README.md) - Alliance-specific agents
