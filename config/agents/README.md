# Agents Directory

This directory contains agent definitions that are loaded at runtime.

## Structure

Each agent is a self-contained folder:

```
agent_name/
├── agent.yaml        # Agent definition + supervisor routing
├── schema.py         # Pydantic output schema
├── prompt.yaml       # Base prompt
└── group_rules/      # Organization-specific rules (optional)
    ├── fb.yaml
    └── wb.yaml
```

## Adding a New Agent

1. Create a folder with your agent's ID (e.g., `my_agent/`)
2. Add `agent.yaml` - defines the agent, its tools, and routing
3. Add `prompt.yaml` - the base prompt for the agent
4. Add `schema.py` - Pydantic model for structured output
5. Optionally add `group_rules/*.yaml` for organization-specific behavior

See `_examples/` for template files.

## Special Directories

- `supervisor/` - Core supervisor agent (ships with base installation)
- `_examples/` - Template agents for reference (not loaded at runtime)

## Loading Behavior

- Folders starting with `_` (underscore) are **not loaded**
- All other folders are discovered and loaded at startup
- YAML files are loaded into the database (YAML is source of truth)
