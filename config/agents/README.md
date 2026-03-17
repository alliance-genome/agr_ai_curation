# Agents Directory

This repo directory is the source-development mirror of the shipped AGR agent
catalog. Public or organization-specific customization for a standard install
should happen through runtime packages under
`~/.agr_ai_curation/runtime/packages/` plus deployment overrides under
`~/.agr_ai_curation/runtime/config/`, not by editing this checkout in place.

See [Modular Packages and Upgrades](../../docs/deployment/modular-packages.md)
for the installed runtime layout. If you are maintaining the shipped packages
in this repository, keep `config/agents/supervisor/` aligned with
`packages/core/agents/supervisor/` and the specialist bundles aligned with
`packages/alliance/agents/`.

## Package-first authoring layout

Each installed package can export one or more agent bundles:

```text
~/.agr_ai_curation/runtime/packages/org_custom/
├── package.yaml
├── requirements/runtime.txt
└── agents/
    └── my_agent/
        ├── agent.yaml
        ├── prompt.yaml
        ├── schema.py
        └── group_rules/
            └── fb.yaml
```

The package manifest can use `agent_bundles` shorthand to export those files:

```yaml
package_id: org.custom
display_name: Org Custom Package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/org_custom
requirements_file: requirements/runtime.txt
agent_bundles:
  - name: my_agent
    has_schema: true
    group_rules: [fb]
```

## Quick Start: Add a Package-owned Agent

### Step 1: Create or choose a runtime package

Use a package directory under `~/.agr_ai_curation/runtime/packages/`. Keep the
package contents self-contained so the agent can move with the package.

### Step 2: Add the agent bundle

`agents/my_agent/agent.yaml`:

```yaml
agent_id: my_agent
name: "My Agent"
description: "Validates something"

supervisor_routing:
  description: "Use when [specific triggers]"

tools:
  - agr_curation_query

output_schema: MyAgentEnvelope

model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-4o}"
  temperature: 0.1
  reasoning: "medium"

group_rules_enabled: true
```

`agents/my_agent/prompt.yaml`:

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

`agents/my_agent/schema.py`:

```python
from pydantic import BaseModel, Field
from typing import List


class MyResult(BaseModel):
    id: str = Field(description="Unique identifier")
    name: str = Field(description="Display name")
    valid: bool = Field(description="Validation status")


class MyAgentEnvelope(BaseModel):
    results: List[MyResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
```

Optional group rule file at `agents/my_agent/group_rules/fb.yaml`:

```yaml
group_id: FB
content: |
  ## FlyBase-specific Rules
  - Use FB: prefix for identifiers.
  - Check for CG numbers.
```

### Step 3: Install and reload

Copy the completed package directory into
`~/.agr_ai_curation/runtime/packages/` and restart the backend:

```bash
docker compose --env-file ~/.agr_ai_curation/.env \
  -f docker-compose.production.yml restart backend
```

## Repo-local use in this checkout

Use the repo paths in this directory only when you are:

- maintaining the shipped core package from source,
- updating templates under `_examples/`, or
- testing loader/runtime changes from a repository checkout.

Do not treat `config/agents/` as the public customization path for standalone
installs.

## File reference

### agent.yaml fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Unique ID for the runtime agent |
| `name` | Yes | Human-readable display name |
| `description` | Yes | Brief description of agent purpose |
| `supervisor_routing.description` | Yes | Tells the supervisor when to route to this agent |
| `tools` | Yes | Tool IDs from the merged runtime tool registry |
| `output_schema` | Yes | Pydantic class name from `schema.py` |
| `model_config.model` | No | LLM model (default: `gpt-4o`) |
| `model_config.temperature` | No | Response randomness 0.0-1.0 (default: `0.1`) |
| `model_config.reasoning` | No | Thinking effort: `disabled` / `low` / `medium` / `high` |
| `group_rules_enabled` | No | Load `group_rules/*.yaml` (default: `false`) |

### prompt.yaml fields

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Must match `agent.yaml` |
| `content` | Yes | Base prompt content |

### schema.py requirements

- Define the envelope class referenced by `output_schema`.
- Use `Field(default_factory=list)` for envelope list fields.
- Add `Field(description=...)` for all schema fields.
- Keep schemas flat enough for reliable structured output.

### group_rules/*.yaml fields

| Field | Required | Description |
|-------|----------|-------------|
| `group_id` | Yes | Must match the filename and a key in `groups.yaml` |
| `content` | Yes | Rules injected into the prompt at runtime |

Migration note: older repo-based installs may still have `rules:` in
`group_rules/*.yaml`. Rename that key to `content:` before packaging or
migrating the agent bundle. The modular loader expects `content:` and skips the
file when that field is missing.

## Loading and override behavior

- Agent bundles are discovered from loaded runtime packages.
- Bundle names must be unique across packages. Duplicate bundle names are
  startup errors; agent bundles do not have an automatic override winner.
- Tools referenced by `agent.yaml` must exist in the merged runtime tool
  registry, usually via package `tools/bindings.yaml` exports.
- Provider, model, and tool-policy defaults can be overridden by runtime config
  files under `~/.agr_ai_curation/runtime/config/`, but agent bundle collisions
  must be resolved by renaming or removing the conflicting package content.

## Environment variables

Model configuration supports environment variable substitution:

```yaml
model_config:
  model: "${AGENT_GENE_MODEL:-gpt-4o}"
  temperature: ${AGENT_GENE_TEMP:-0.1}
```

Common pattern: `AGENT_{AGENT_ID}_MODEL`, `AGENT_{AGENT_ID}_TEMP`.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Agent not loading | Check the package manifest exports the bundle and the directory contains `agent.yaml` |
| Duplicate agent bundle | Two packages export the same agent bundle name; rename or remove one of them |
| Schema not found | Verify `output_schema` matches the class name exactly |
| Tool not available | Verify the tool ID exists in a loaded package `tools/bindings.yaml` export |
| Group rules not applied | Verify `group_rules_enabled: true`, the rule file uses `group_id` + `content`, and any legacy `rules:` key was renamed to `content:` |

## See also

- [backend/tools/README.md](../../backend/tools/README.md) - Package-first tool authoring
- [CONFIG_DRIVEN_ARCHITECTURE.md](../../docs/developer/guides/CONFIG_DRIVEN_ARCHITECTURE.md) - Repository architecture reference
- [_examples/README.md](./_examples/README.md) - Template documentation
- [groups.yaml.example](../groups.yaml.example) - Group configuration template
