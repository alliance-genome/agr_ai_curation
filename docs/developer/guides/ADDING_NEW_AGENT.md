# Adding a New Agent

Step-by-step guide to adding a new agent to the AI Curation system.

> **Time**: 15-30 minutes for YAML-only agents, 5 minutes via Agent Studio UI
> **Prerequisites**: Docker running, backend accessible

---

## Overview

Agents are defined through two complementary paths:

1. **YAML config files** (system agents) -- Folders under `config/agents/` define agent metadata, prompts, and group rules. An Alembic migration seeds them into the unified `agents` database table at startup.
2. **Agent Studio UI** (custom agents) -- Curators create personal or project-scoped agents through the browser. These are stored directly in the `agents` table with `visibility='private'` or `visibility='project'`.

Both paths produce rows in the same `agents` table. At runtime, the supervisor discovers all active, supervisor-enabled agents from the database and creates streaming tool wrappers for them dynamically. **No Python agent files are needed.**

```
config/agents/my_agent/        # YAML source of truth
  agent.yaml                   # Agent definition and metadata
  prompt.yaml                  # Base instructions
  group_rules/                 # Optional: org-specific behavior
    fb.yaml
    wb.yaml
```

---

## Path A: Add a System Agent via YAML

System agents ship with the product and are visible to all users. They are seeded into the database from YAML during migrations.

### Step 1: Copy the Template

```bash
cd config/agents
cp -r _examples/basic_agent my_agent
```

### Step 2: Define Your Agent (agent.yaml)

Edit `my_agent/agent.yaml`:

```yaml
# Must match folder name
agent_id: my_agent

# Display name in UI
name: "My Agent"

# Brief description
description: "Validates and processes [domain] data"

# Category for UI grouping
category: "Validation"
subcategory: "Data Validation"

# CRITICAL: Tells supervisor when to route to this agent
# Be specific - vague descriptions cause routing errors
supervisor_routing:
  enabled: true
  description: "Use for validating [specific data type], querying [specific domain], or looking up [specific information]"
  batchable: true
  batching_entity: "items"
  batching_instructions: >
    When looking up multiple items, combine them into a single request.
    Example: "Look up these items: foo, bar, baz"

# Tools this agent can use (must exist in TOOL_BINDINGS in catalog_service.py)
tools:
  - agr_curation_query

# Output schema class name (from backend/src/lib/openai_agents/models.py)
output_schema: MyAgentEnvelope

# LLM settings (supports environment variables)
model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-4o}"
  temperature: 0.1
  reasoning: "medium"

# Execution requirements
requires_document: false
required_params: []
batch_capabilities: []

# Frontend display
frontend:
  icon: "🧪"
  show_in_palette: true

# Set true if different orgs need different behavior
group_rules_enabled: false
```

#### Supervisor Routing Tips

| Good Description | Bad Description |
|-----------------|-----------------|
| "Use for validating gene symbols, looking up gene IDs, or checking gene existence" | "Use for gene stuff" |
| "Use for extracting chemical compounds from documents" | "Handles chemicals" |
| "Use for mapping free-text to ontology terms" | "Does ontology" |

---

### Step 3: Write the Prompt (prompt.yaml)

Edit `my_agent/prompt.yaml`:

```yaml
agent_id: my_agent

content: |
  You are a [Domain] Specialist for the AI Curation system.

  ## Your Role

  [Clear description of what this agent does and why it exists]

  ## Capabilities

  You can help users with:
  - [Capability 1]
  - [Capability 2]
  - [Capability 3]

  ## Tools Available

  - **agr_curation_query**: Query the Alliance database for genes, alleles, etc.
    - Returns structured results with identifiers and metadata
    - Supports filtering by species, symbol, ID

  ## Instructions

  When handling a request:
  1. Parse the user's query to identify what they're looking for
  2. Call the appropriate tool(s) to gather data
  3. Validate and format the results
  4. Return a structured response using the schema

  ## Output Format

  Always return results using the MyAgentEnvelope schema:
  - `results`: List of validated items
  - `not_found`: Items that couldn't be validated
  - `warnings`: Any issues encountered

  ## Constraints

  - Only return items that exist in the database
  - Never fabricate or guess identifiers
  - Clearly distinguish between "not found" and "error"

  ## GROUP-SPECIFIC RULES

  [Group rules are automatically injected here at runtime if group_rules_enabled is true]
```

#### Prompt Writing Tips

- Be specific about what the agent can and cannot do
- Describe each tool and what it returns
- Give clear instructions for the workflow
- Define the output format explicitly
- Include constraints to prevent hallucination

---

### Step 4: Define the Output Schema

Output schemas live in `backend/src/lib/openai_agents/models.py` (the shared models module):

```python
class MyResult(BaseModel):
    """A single result item."""
    id: str = Field(description="Unique identifier (CURIE format)")
    name: str = Field(description="Human-readable name")
    valid: bool = Field(description="Whether the item was found/validated")
    species: Optional[str] = Field(default=None, description="Species (if applicable)")

class MyAgentEnvelope(BaseModel):
    """
    Container for results.

    This is the class referenced in agent.yaml's output_schema field.
    All list fields MUST have default_factory to handle empty results.
    """
    results: List[MyResult] = Field(
        default_factory=list,
        description="Successfully validated items"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="Identifiers that could not be found"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered"
    )
    query_summary: Optional[str] = Field(
        default=None,
        description="Summary of what was searched"
    )
```

The `output_schema` value in `agent.yaml` must match the class name exactly. The catalog service resolves it from the shared models module at runtime.

#### Schema Rules

| Do | Don't |
|-----|-------|
| Use `Field(default_factory=list)` for lists | Use `Field(...)` (required) for envelope lists |
| Add `description` to every field | Leave fields undocumented |
| Use `Optional[X] = Field(default=None)` | Make optional fields required |
| Keep schemas flat | Create deeply nested structures |

---

### Step 5: Add Group Rules (Optional)

If different organizations need different behavior, create `my_agent/group_rules/`:

```bash
mkdir -p my_agent/group_rules
```

Create `my_agent/group_rules/fb.yaml` for FlyBase:

```yaml
group_id: FB

content: |
  ## FlyBase-Specific Rules

  When handling FlyBase data:
  - Use the FB: prefix for FlyBase identifiers
  - Check for CG numbers as alternative identifiers
  - FlyBase genes may have multiple valid symbols (synonyms)
```

Create similar files for other groups (`wb.yaml`, `mgi.yaml`, etc.).

**Important**: The `group_id` must match a key in `config/groups.yaml`. Set `group_rules_enabled: true` in your `agent.yaml` to activate rule injection.

---

### Step 6: Seed into the Database

System agents are seeded via Alembic migration. The existing seed migration (`v4w5x6y7z8a9_seed_unified_agents.py`) reads `config/agents/*/agent.yaml` and `prompt.yaml` at migration time and inserts rows into the `agents` table with `visibility='system'`.

For a **new** agent added after the initial migration, you have two options:

**Option A: Create a new Alembic migration** (recommended for production):

```bash
docker compose exec backend alembic revision --autogenerate -m "seed_my_agent"
```

Then add seed logic similar to the existing seed migration.

**Option B: Manual database insert** (quick iteration during development):

```bash
docker compose exec backend python - <<'PY'
from src.models.sql.database import SessionLocal
from src.models.sql.agent import Agent

db = SessionLocal()
# Read your YAML and insert -- or just restart with a fresh DB
db.close()
PY
```

After seeding, restart the backend to pick up the new agent:

```bash
docker compose restart backend
```

---

### Step 7: Verify and Test

#### Check YAML Syntax

```bash
python3 -c "import yaml; yaml.safe_load(open('config/agents/my_agent/agent.yaml'))"
python3 -c "import yaml; yaml.safe_load(open('config/agents/my_agent/prompt.yaml'))"
```

#### Restart Backend

```bash
docker compose restart backend
```

#### Test in Chat

Open Agent Studio and ask a question that should route to your agent:

> "Can you validate these [domain items]: item1, item2, item3"

Check the logs:

```bash
docker compose logs backend | grep my_agent
```

---

### Step 8: Refine

Based on testing:

1. **Routing issues** -- Improve `supervisor_routing.description` in `agent.yaml`
2. **Wrong output format** -- Check schema matches what LLM returns
3. **Missing tools** -- Add tools to the `tools` list (must have a `TOOL_BINDINGS` entry)
4. **Group-specific issues** -- Add/update group rules

After modifying prompts in the database, refresh the prompt cache:

```bash
curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh
```

---

## Path B: Add a Custom Agent via Agent Studio

Custom agents are created through the Agent Studio UI and stored directly in the `agents` table without requiring any YAML files or code changes.

### Step 1: Open Agent Studio

Navigate to Agent Studio in the browser and select the agent creation workflow.

### Step 2: Fill in Agent Details

Provide:
- **Name** and **description**
- **Instructions** (the prompt)
- **Model** selection (from `config/models.yaml` catalog)
- **Tools** to enable
- **Visibility**: `private` (only you) or `project` (shared with team)

### Step 3: Save and Test

The agent is immediately available. No restart needed. Custom agents with `supervisor_enabled=false` (the default for custom agents) are available in flows and direct execution but not in supervisor chat routing.

---

## How Agents Are Discovered at Runtime

Understanding the discovery flow helps with debugging:

1. **Startup**: `registry_builder.py` reads all `config/agents/*/agent.yaml` files and builds `AGENT_REGISTRY` (metadata for the catalog UI)
2. **Migration**: The seed migration reads the same YAML files and inserts rows into the `agents` table with `visibility='system'`
3. **Supervisor creation**: `supervisor_agent.py` queries the `agents` table for rows where `visibility='system'` AND `supervisor_enabled=true` AND `is_active=true`
4. **Tool wrapping**: For each discovered agent, `get_agent_by_id()` builds a runtime `Agent` instance from the database row (instructions, model, tools, schema) and wraps it as a streaming tool
5. **Prompt injection**: Group rules and document context are injected into instructions at build time based on the agent's configuration

Key source files:

| File | Role |
|------|------|
| `backend/src/lib/agent_studio/catalog_service.py` | `get_agent_by_id()` builds agents from DB, `AGENT_REGISTRY` provides UI metadata |
| `backend/src/lib/agent_studio/registry_builder.py` | Builds `AGENT_REGISTRY` from YAML at import time |
| `backend/src/lib/agent_studio/agent_service.py` | `get_agent_by_key()` queries the `agents` table |
| `backend/src/models/sql/agent.py` | `Agent` SQLAlchemy model (unified table) |
| `backend/src/lib/openai_agents/agents/supervisor_agent.py` | Dynamic specialist discovery and streaming tool creation |

---

## Complete Example: Gene Validation Agent

The gene agent demonstrates the full pattern. See these files:

- `config/agents/gene/agent.yaml` -- Agent definition with batching, group rules, and model config
- `config/agents/gene/prompt.yaml` -- Detailed prompt with search strategies and output format
- `config/agents/gene/group_rules/fb.yaml` -- FlyBase-specific rules
- `config/agents/gene/group_rules/wb.yaml` -- WormBase-specific rules

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Agent not appearing in UI | Check that the agent exists in the `agents` table with `is_active=true`. Run `docker compose exec postgres psql -U postgres ai_curation -c "SELECT agent_key, is_active, visibility FROM agents;"` |
| Supervisor not routing to agent | Verify `supervisor_enabled=true` and `supervisor_description` is clear in the DB row. Check logs: `docker compose logs backend \| grep ask_my_agent` |
| "Unknown agent_id" error | Agent not in the `agents` table. Run the seed migration or insert manually |
| Schema not found | Verify `output_schema_key` matches a class name in `backend/src/lib/openai_agents/models.py` |
| Tools not resolving | Tool must have a `TOOL_BINDINGS` entry in `catalog_service.py`. Check error: "Unknown tool binding" |
| Group rules not injected | Check `group_rules_enabled=true` and `group_rules_component` points to a valid prompt cache key |
| Prompt changes not reflected | Refresh cache: `curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh` |

---

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](./CONFIG_DRIVEN_ARCHITECTURE.md) -- Full architecture guide
- [AGENTS_DEVELOPMENT_GUIDE.md](./AGENTS_DEVELOPMENT_GUIDE.md) -- Comprehensive agent development reference
- [ADDING_NEW_TOOL.md](./ADDING_NEW_TOOL.md) -- Adding custom tools
- [config/agents/_examples/](../../../config/agents/_examples/) -- Template files
