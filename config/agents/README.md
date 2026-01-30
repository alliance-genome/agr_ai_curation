# Agents Directory

This directory contains agent definitions that are loaded at runtime. Each agent is a self-contained folder with YAML configuration and Python schema.

## Directory Structure

```
agents/
├── README.md           # This file
├── _examples/          # Template agents (NOT loaded - underscore prefix)
│   └── basic_agent/    # Copy this to create new agents
├── supervisor/         # Core supervisor agent (ships with base)
├── gene/               # Gene validation agent
├── allele/             # Allele validation agent
├── disease/            # Disease validation agent
└── [your_agent]/       # Your custom agents
```

## Agent Folder Structure

Each agent is a self-contained folder:

```
my_agent/
├── agent.yaml        # Agent definition + supervisor routing
├── prompt.yaml       # Base prompt instructions
├── schema.py         # Pydantic output schema
└── group_rules/      # Organization-specific rules (optional)
    ├── fb.yaml       # FlyBase rules
    ├── wb.yaml       # WormBase rules
    └── ...
```

## Quick Start: Adding a New Agent

### Step 1: Copy the Template

```bash
cp -r _examples/basic_agent my_agent
```

### Step 2: Update agent.yaml

```yaml
agent_id: my_agent                    # Must match folder name
name: "My Agent"                      # Display name
description: "Validates something"    # Brief description

supervisor_routing:
  description: "Use when [specific triggers]"  # Supervisor routing hint

tools:
  - agr_curation_query               # Available tools

output_schema: MyAgentEnvelope       # Class name from schema.py

model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-4o}"  # Supports env vars
  temperature: 0.1
  reasoning: "medium"

group_rules_enabled: true            # Load group_rules/*.yaml
```

### Step 3: Update prompt.yaml

```yaml
agent_id: my_agent

content: |
  You are a specialist agent for [domain].

  ## Your Role
  [What this agent does]

  ## Tools Available
  - **agr_curation_query**: Query the database

  ## Instructions
  1. Parse the query
  2. Call appropriate tools
  3. Return structured results
```

### Step 4: Update schema.py

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class MyResult(BaseModel):
    """Single result item."""
    id: str = Field(description="Unique identifier")
    name: str = Field(description="Display name")
    valid: bool = Field(description="Validation status")

class MyAgentEnvelope(BaseModel):
    """Container for results - referenced in agent.yaml."""
    results: List[MyResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
```

### Step 5: Add Group Rules (Optional)

If your agent needs organization-specific behavior:

```bash
mkdir -p my_agent/group_rules
```

Create `my_agent/group_rules/fb.yaml`:

```yaml
group_id: FB

rules: |
  ## FlyBase-Specific Rules
  - Use FB: prefix for identifiers
  - Check for CG numbers
```

### Step 6: Restart

```bash
docker compose restart backend
```

## File Reference

### agent.yaml Fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Unique ID (must match folder name) |
| `name` | Yes | Human-readable display name |
| `description` | Yes | Brief description of agent purpose |
| `supervisor_routing.description` | Yes | Tells supervisor when to route to this agent |
| `tools` | Yes | List of tool names agent can use |
| `output_schema` | Yes | Pydantic class name from schema.py |
| `model_config.model` | No | LLM model (default: gpt-4o) |
| `model_config.temperature` | No | Response randomness 0.0-1.0 (default: 0.1) |
| `model_config.reasoning` | No | Thinking effort: disabled/low/medium/high |
| `group_rules_enabled` | No | Load group_rules/*.yaml (default: false) |

### prompt.yaml Fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Must match agent.yaml |
| `content` | Yes | The actual prompt text (YAML multiline `\|`) |

### schema.py Requirements

- Must define the envelope class referenced in `output_schema`
- Use `Field(default_factory=list)` for list fields (never required)
- Add `Field(description=...)` for all fields (helps LLM)
- Keep schemas flat - avoid deep nesting

### group_rules/*.yaml Fields

| Field | Required | Description |
|-------|----------|-------------|
| `group_id` | Yes | Must match filename and groups.yaml mapping |
| `rules` | Yes | Rules to inject into prompt (YAML multiline `\|`) |

## Loading Behavior

- **Loaded**: All folders without underscore prefix
- **Skipped**: `_examples/`, `_templates/`, `_deprecated/`, etc.
- **Timing**: Agents load at backend startup
- **Caching**: Loaded once, use `force_reload=True` to refresh

## Environment Variables

Model configuration supports environment variable substitution:

```yaml
model_config:
  model: "${AGENT_GENE_MODEL:-gpt-4o}"      # Use env var or default
  temperature: ${AGENT_GENE_TEMP:-0.1}       # Works for numbers too
```

Common pattern: `AGENT_{AGENT_ID}_MODEL`, `AGENT_{AGENT_ID}_TEMP`

## Tools Reference

Tools are defined in `backend/tools/`. They are auto-discovered from `core/` and `custom/` directories.

| Tool | Description |
|------|-------------|
| `agr_curation_query` | Query Alliance curation database |
| `alliance_api_call` | Call Alliance REST API |
| `weaviate_search` | Search documents in vector database |
| `search_document` | Search within a specific document |
| `read_section` | Read a document section |

To add a custom tool:
1. Create Python file in `backend/tools/custom/`
2. Use `@function_tool` decorator
3. Restart backend
4. Reference by name in agent.yaml

See `backend/tools/README.md` for details.

## Validation

The loader validates:

- YAML syntax and required fields
- Schema class exists and is importable
- Tools referenced exist in tool registry
- Group rule files match configured groups

Errors are logged with specific file and line information.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent not loading | Check folder name doesn't start with `_` |
| Schema not found | Verify `output_schema` matches class name exactly |
| Tool not available | Check tool file exists in `backend/tools/custom/` with `@function_tool` decorator |
| Group rules not applied | Verify `group_rules_enabled: true` in agent.yaml |
| Prompt not updating | Restart backend or use `force_reload=True` |

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](../../docs/developer/guides/CONFIG_DRIVEN_ARCHITECTURE.md) - Full architecture guide
- [_examples/README.md](./_examples/README.md) - Template documentation
- [groups.yaml.example](../groups.yaml.example) - Group configuration template
