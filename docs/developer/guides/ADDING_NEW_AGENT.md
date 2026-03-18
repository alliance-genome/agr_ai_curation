# Adding a New Agent

Step-by-step guide to adding a new agent to the AI Curation system.

> **Time**: 15-30 minutes for YAML-only agents, 5 minutes via Agent Studio UI
> **Prerequisites**: Docker running, backend accessible
>
> **Scope**: Public or organization-specific customization for a standard
> install should be packaged under `~/.agr_ai_curation/runtime/packages/`.
> The repo-local `config/agents/` paths in this guide are for source checkout
> work and shipped `agr.alliance` maintenance. For the public runtime contract,
> see [Modular Packages and Upgrades](../../deployment/modular-packages.md).

---

## Overview

Choose the path that matches your goal:

1. **Runtime package authoring** -- For standalone installs and org-specific customization, add the agent bundle under `~/.agr_ai_curation/runtime/packages/<package>/agents/<agent>/`, update that package's manifest, and install the package.
2. **Source checkout maintenance** -- For shipped `agr.alliance` work in this repository, use the repo-local `config/agents/` mirror and keep `packages/alliance/agents/` aligned.
3. **Agent Studio UI** -- For personal or project-scoped agents, use the browser and skip file edits entirely.

Agents are defined through two complementary paths:

1. **Package-backed agent bundles** (system agents) -- Standalone installs keep system-agent YAML under `~/.agr_ai_curation/runtime/packages/<package>/agents/<agent>/`. In this repository, `config/agents/` is the source-development mirror for the shipped `agr.alliance` package while `packages/alliance/agents/` is the package-owned source tree.
2. **Agent Studio UI** (custom agents) -- Curators create personal or project-scoped agents through the browser. These are stored directly in the `agents` table with `visibility='private'` or `visibility='project'`.

Both paths produce rows in the same `agents` table. At runtime, the supervisor discovers all active, supervisor-enabled agents from the database and creates streaming tool wrappers for them dynamically. **No Python agent files are needed.**

```text
~/.agr_ai_curation/runtime/packages/org-custom/
  package.yaml
  agents/
    my_agent/
      agent.yaml               # Agent definition and metadata
      prompt.yaml              # Base instructions
      schema.py                # Output schema
      group_rules/             # Optional: org-specific behavior
        fb.yaml
        wb.yaml
```

---

## Path A: Add a System Agent via a Package Bundle

This is the primary path for standalone installs and reusable organization
packages. If you are maintaining the shipped `agr.alliance` package from a source
checkout, use the same bundle structure and keep the repo mirror aligned rather
than teaching installed users to edit `config/agents/` directly.

System agents ship with the product and are visible to all users. In the modular
runtime, the public authoring unit is a package-owned agent bundle.

### Step 1: Create or choose a package

```bash
mkdir -p ~/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent
```

If you are maintaining the shipped `agr.alliance` package from this repository, keep
the repo mirror in `config/agents/my_agent/` aligned with the package-owned
bundle in `packages/alliance/agents/my_agent/`.

### Step 2: Define Your Agent (agent.yaml)

Create `agents/my_agent/agent.yaml` inside your package:

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

# Tools this agent can use (must exist in the merged runtime tool registry)
tools:
  - agr_curation_query

# Output schema class name (from agents/my_agent/schema.py)
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

Create `agents/my_agent/prompt.yaml`:

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

For package-authored agents, output schemas usually live next to the bundle in
`agents/my_agent/schema.py`:

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

The `output_schema` value in `agent.yaml` must match the class name exactly.
Runtime schema discovery resolves it from the installed bundle (or from the
shipped `agr.alliance` package source when you are working in a source checkout).

#### Schema Rules

| Do | Don't |
|-----|-------|
| Use `Field(default_factory=list)` for lists | Use `Field(...)` (required) for envelope lists |
| Add `description` to every field | Leave fields undocumented |
| Use `Optional[X] = Field(default=None)` | Make optional fields required |
| Keep schemas flat | Create deeply nested structures |

---

### Step 5: Add Group Rules (Optional)

If different organizations need different behavior, create
`agents/my_agent/group_rules/`:

```bash
mkdir -p ~/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent/group_rules
```

Create `agents/my_agent/group_rules/fb.yaml` for FlyBase:

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

**Important**: The `group_id` must match a key in the active groups
configuration (`~/.agr_ai_curation/runtime/config/groups.yaml` for a standalone
install). Set `group_rules_enabled: true` in your `agent.yaml` to activate rule
injection.

---

### Step 6: Export the bundle and reload the runtime

Declare the bundle in your package manifest so the runtime can discover it:

```yaml
agent_bundles:
  - name: my_agent
    has_schema: true
    group_rules: [fb]
```

Then install or update the package under `~/.agr_ai_curation/runtime/packages/`
and restart the backend:

```bash
docker compose --env-file ~/.agr_ai_curation/.env \
  -f docker-compose.production.yml restart backend
```

Repo-maintainer note:

- Keep `packages/alliance/agents/my_agent/` and `config/agents/my_agent/` aligned.
- If the shipped `agr.alliance` catalog changes require migration-time seed adjustments,
  update the relevant Alembic/bootstrap flow in the repository rather than
  telling installed users to edit repo-local YAML directly.

---

### Step 7: Verify and Test

#### Check YAML Syntax

```bash
python3 -c "import yaml; yaml.safe_load(open('$HOME/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent/agent.yaml'))"
python3 -c "import yaml; yaml.safe_load(open('$HOME/.agr_ai_curation/runtime/packages/org-custom/agents/my_agent/prompt.yaml'))"
```

#### Restart Backend

```bash
docker compose --env-file ~/.agr_ai_curation/.env \
  -f docker-compose.production.yml restart backend
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
3. **Missing tools** -- Add tools to the `tools` list and verify the tool ID is exported from a package `tools/bindings.yaml`
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

1. **Package discovery**: `load_agent_definitions()` resolves agent bundles from the runtime packages root (or an explicit legacy override path).
2. **Registry build**: `registry_builder.py` reads those bundle files and builds `AGENT_REGISTRY` metadata for the catalog UI.
3. **Prompt + schema loading**: prompt/schema/group-rule loaders read `prompt.yaml`, `schema.py`, and `group_rules/*.yaml` from the same bundle.
4. **Supervisor creation**: `supervisor_agent.py` queries the `agents` table for rows where `visibility='system'` AND `supervisor_enabled=true` AND `is_active=true`.
5. **Tool wrapping**: For each discovered agent, `get_agent_by_id()` builds a runtime `Agent` instance from the database row (instructions, model, tools, schema) and wraps it as a streaming tool.
6. **Prompt injection**: Group rules and document context are injected into instructions at build time based on the agent's configuration.

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

- `packages/alliance/agents/gene/agent.yaml` -- Package-owned agent definition with batching, group rules, and model config
- `packages/alliance/agents/gene/prompt.yaml` -- Detailed prompt with search strategies and output format
- `packages/alliance/agents/gene/group_rules/fb.yaml` -- FlyBase-specific rules
- `packages/alliance/agents/gene/group_rules/wb.yaml` -- WormBase-specific rules
- `config/agents/gene/` -- Repo mirror used when maintaining the shipped `agr.alliance` package from source

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Agent not appearing in UI | Check that the agent exists in the `agents` table with `is_active=true`. Run `docker compose exec postgres psql -U postgres ai_curation -c "SELECT agent_key, is_active, visibility FROM agents;"` |
| Supervisor not routing to agent | Verify `supervisor_enabled=true` and `supervisor_description` is clear in the DB row. Check logs: `docker compose logs backend \| grep ask_my_agent` |
| "Unknown agent_id" error | The bundle was not loaded into the runtime. Verify the package manifest exports the agent, confirm it is installed under `runtime/packages/`, then restart the backend. |
| Schema not found | Verify `output_schema_key` matches a class name in the installed bundle's `schema.py` (or the shipped core schema module when developing from source) |
| Tools not resolving | Verify the tool ID is exported from a package `tools/bindings.yaml` and survived merged-registry validation |
| Group rules not injected | Check `group_rules_enabled=true` and `group_rules_component` points to a valid prompt cache key |
| Prompt changes not reflected | Refresh cache: `curl -X POST http://localhost:8000/api/admin/prompts/cache/refresh` |

---

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](./CONFIG_DRIVEN_ARCHITECTURE.md) -- Full architecture guide
- [AGENTS_DEVELOPMENT_GUIDE.md](./AGENTS_DEVELOPMENT_GUIDE.md) -- Comprehensive agent development reference
- [ADDING_NEW_TOOL.md](./ADDING_NEW_TOOL.md) -- Adding custom tools
- [config/agents/_examples/](../../../config/agents/_examples/) -- Template files
