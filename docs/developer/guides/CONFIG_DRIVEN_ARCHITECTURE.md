# Config-Driven Architecture Guide

This guide explains the config-driven architecture for AGR AI Curation, where **YAML is the source of truth** and the database serves as a runtime cache populated at startup.

> **Last Updated:** January 30, 2026 (Config-driven architecture with YAML-based agents)

---

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Core Concepts](#core-concepts)
4. [Configuration Files](#configuration-files)
5. [Adding a New Agent](#adding-a-new-agent)
6. [Adding a New Tool](#adding-a-new-tool)
7. [Configuring Groups](#configuring-groups)
8. [Adding External Connections](#adding-external-connections)
9. [Deployment](#deployment)
10. [Environment Variables](#environment-variables)
11. [Testing](#testing)
12. [Troubleshooting](#troubleshooting)

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

```
agr_ai_curation/
├── config/                          # Runtime configuration (source of truth)
│   ├── README.md                    # Configuration overview
│   ├── groups.yaml                  # Group/Cognito mapping (copy from .example)
│   ├── groups.yaml.example          # Template for groups
│   ├── connections.yaml             # External service connections (copy from .example)
│   ├── connections.yaml.example     # Template for connections
│   └── agents/                      # Agent definitions (loaded at runtime)
│       ├── README.md                # Agent configuration guide
│       ├── _examples/               # Template agents (not loaded)
│       │   └── basic_agent/         # Example agent structure
│       ├── supervisor/              # Core supervisor agent
│       ├── gene/                    # Gene validation agent
│       ├── disease/                 # Disease validation agent
│       └── [your_agent]/            # Your custom agents
│
├── alliance_agents/                 # Alliance-specific agents (development source)
│   ├── README.md                    # Alliance agents documentation
│   ├── gene/                        # Gene agent source
│   ├── allele/                      # Allele agent source
│   └── ...                          # Other Alliance agents
│
├── backend/
│   ├── src/lib/config/              # Configuration loaders
│   │   ├── __init__.py              # Public API exports
│   │   ├── agent_loader.py          # Loads agent.yaml files
│   │   ├── schema_discovery.py      # Discovers schema.py files
│   │   ├── groups_loader.py         # Loads groups.yaml
│   │   └── connections_loader.py    # Loads connections.yaml
│   │
│   └── tools/                       # Tool implementations
│       ├── core/                    # Base tools (ship with product)
│       └── custom/                  # Organization-specific tools
│
└── scripts/
    └── deploy_alliance.sh           # Syncs alliance_agents/ to config/agents/
```

---

## Core Concepts

### Agent Folder Structure

Each agent is a self-contained folder:

```
my_agent/
├── agent.yaml        # Agent definition and metadata
├── prompt.yaml       # Base prompt instructions
├── schema.py         # Pydantic output schema
└── group_rules/      # Organization-specific rules (optional)
    ├── fb.yaml       # FlyBase-specific rules
    ├── wb.yaml       # WormBase-specific rules
    └── ...
```

### Loading Order

At system startup:

1. `connections.yaml` is loaded to establish service connections
2. `groups.yaml` is loaded for authentication mapping
3. Agent folders in `config/agents/` are discovered and loaded
4. Group rules are associated with agents based on group_id matching
5. Schemas are dynamically imported from `schema.py` files

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

# Tools this agent can use (must exist in backend/tools/)
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
# group_rules/fb.yaml
group_id: FB

rules: |
  ## FlyBase-Specific Rules

  When validating genes for FlyBase:
  - Use the FB: prefix for FlyBase identifiers
  - Check both current symbols and synonyms
  - Include CG numbers as alternative identifiers
```

---

## Adding a New Agent

### Step 1: Copy the Template

```bash
cp -r config/agents/_examples/basic_agent config/agents/my_agent
```

### Step 2: Update agent.yaml

Edit `config/agents/my_agent/agent.yaml`:

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

### Step 3: Update prompt.yaml

Edit `config/agents/my_agent/prompt.yaml`:

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

### Step 4: Update schema.py

Edit `config/agents/my_agent/schema.py`:

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

### Step 5: Add Group Rules (Optional)

If your agent needs organization-specific behavior:

```bash
mkdir -p config/agents/my_agent/group_rules
```

Create `config/agents/my_agent/group_rules/fb.yaml`:

```yaml
group_id: FB

rules: |
  ## FlyBase-Specific Behavior

  When processing FlyBase data:
  - [Specific instructions]
```

### Step 6: Restart and Test

```bash
# Restart backend to pick up changes
docker compose restart backend

# Or rebuild if needed
make rebuild-backend
```

---

## Adding a New Tool

Tools are Python functions that agents can call. They live in `backend/tools/`.

### Step 1: Choose Location

- `backend/tools/core/` - Generic tools that ship with the base product
- `backend/tools/custom/` - Organization-specific tools

### Step 2: Create the Tool

Create `backend/tools/custom/my_tool.py`:

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

### Step 3: Export the Tool

Add to `backend/tools/custom/__init__.py`:

```python
from .my_tool import my_custom_tool

__all__ = [
    "my_custom_tool",
    # ... other tools
]
```

### Step 4: Reference in Agent

Add to your agent's `agent.yaml`:

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

Create `group_rules/{group_id}.yaml` in your agent folder:

```yaml
# config/agents/gene/group_rules/fb.yaml
group_id: FB

rules: |
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

### Alliance Deployment

Alliance agents are developed in `alliance_agents/` and deployed to `config/agents/`:

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

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1

# Per-agent model overrides
AGENT_GENE_MODEL=gpt-4o
AGENT_SUPERVISOR_MODEL=gpt-4o

# Config paths (optional, defaults to config/)
CONFIG_PATH=/app/config
ALLIANCE_AGENTS_PATH=/app/alliance_agents
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
| YAML parse error | Run `python -c "import yaml; yaml.safe_load(open('config/agents/my_agent/agent.yaml'))"` |
| Schema not found | Check `output_schema` matches class name in `schema.py` |
| Tools not found | Verify tool file exists in `backend/tools/custom/` with `@function_tool` decorator |

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

### Health Checks Failing

| Symptom | Check |
|---------|-------|
| Database unhealthy | Connection string correct? Port open? |
| API unhealthy | API key valid? Rate limited? |
| Timeout errors | Increase `timeout_seconds` in health_check |

---

## Resources

- [config/README.md](../../../config/README.md) - Configuration directory overview
- [config/agents/README.md](../../../config/agents/README.md) - Agent configuration details
- [alliance_agents/README.md](../../../alliance_agents/README.md) - Alliance-specific agents
- [CONFIG_DRIVEN_ARCHITECTURE_DESIGN.md](../../../CONFIG_DRIVEN_ARCHITECTURE_DESIGN.md) - Architecture design document
