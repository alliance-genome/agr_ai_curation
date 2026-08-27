# Agents Directory

This repo directory contains explicit source-development overrides. The source
Compose file mounts it at `/runtime/config/agents`; merely having a repository
checkout no longer makes these bundles discoverable. Canonical shipped generic
agents live under `packages/core/agents/`, and Alliance specialists live under
`packages/alliance/agents/`.

Public or organization-specific customization for a standard install should
happen through runtime packages under `~/.agr_ai_curation/runtime/packages/`
plus deployment overrides under `~/.agr_ai_curation/runtime/config/`, not by
editing this checkout in place.

See [Modular Packages and Upgrades](../../docs/deployment/modular-packages.md)
for the installed runtime layout. If you are maintaining shipped packages in
this repository, edit the canonical bundle under its owning package and declare
it in that package's `agent_bundles`. Use `config/agents/supervisor/` only for
the source checkout's intentional supervisor override.

When you add or materially change supervisor-routable specialists, review
`config/agents/supervisor/prompt.yaml` so the org-level handoff examples and
expectations stay in sync with the current specialist catalog.

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

A package may contribute only `group_rule` exports to an agent owned by one of
its explicit package dependencies. The contributor must not duplicate the
agent bundle:

```yaml
dependencies:
  - package_id: org.core
    version_range: ">=1.0.0,<2.0.0"
exports:
  - kind: group_rule
    name: supervisor.FB
    path: group_rules/supervisor/fb.yaml
```

The runtime fails closed when the target is missing or ambiguous, the target
owner is not a declared dependency, or two packages export the same
agent/group pair. Contributed foreign-agent rules must remain outside every
agent-bundle root declared by the contributor. Prompt provenance remains
attached to the contributing package and file.

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

group_tool_policy:
  rules:
    - tool_id: narrow_group_context_helper
      allowed_group_ids: [RGD]
      field_paths:
        - annotation.subject

output_schema: MyAgentEnvelope

model_config:
  model: "${AGENT_MY_AGENT_MODEL:-gpt-5.6-sol}"
  temperature: 0.1
  reasoning: "medium"

group_rules_enabled: true
```

`group_tool_policy` is package-owned capability metadata, distinct from agent
availability under `access.allowed_group_ids`. Each rule exposes its tool only
when authenticated active groups intersect `allowed_group_ids`. If the ruled
tool is absent from `tools`, it is added for an allowed group; if it is present,
the rule restricts that base tool. Every rule requires one or more
`field_paths`; shared runtime never infers tool access from extracted content.

When a package replaces a public agent ID, declare the canonical unified key
with `system_agent_key` and list the removed IDs under `retired_agent_ids` in
that agent's `agent.yaml`. The runtime rejects those package-owned retired IDs;
persisted references must be moved forward by an explicit migration.

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
`~/.agr_ai_curation/runtime/packages/` and rerun the guarded production
start-and-verify stage:

```bash
scripts/install/install.sh --from-stage 6
```

## Repo-local use in this checkout

Use the repo paths in this directory only when you are:

- maintaining an intentional source-development runtime override,
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
| `model_config.model` | No | Registered LLM model (current default: `gpt-5.6-terra`) |
| `model_config.temperature` | No | Response randomness 0.0-1.0 (default: `0.1`) |
| `model_config.reasoning` | No | Thinking effort: `disabled` / `low` / `medium` / `high` / `xhigh` |
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

- Agent bundles are discovered from loaded runtime packages first, then from
  `/runtime/config/agents` when that explicit override directory exists.
- Bundle names must be unique across packages. When an explicit runtime-config
  bundle has the same folder name as a package bundle, the runtime-config copy
  overrides it deterministically.
- Tools referenced by `agent.yaml` must exist in the merged runtime tool
  registry, usually via package `tools/bindings.yaml` exports.
- Provider, model, and tool-policy defaults can be overridden by runtime config
  files under `~/.agr_ai_curation/runtime/config/`, but agent bundle collisions
  must be resolved by renaming or removing the conflicting package content.

## Environment variables

Model configuration supports environment variable substitution:

```yaml
# Database validation/lookup agent
model_config:
  model: "${AGENT_GENE_MODEL:-gpt-5.6-terra}"
  temperature: ${AGENT_GENE_TEMP:-0.1}
```

Document extractors use Sol instead, for example
`AGENT_GENE_EXTRACTOR_MODEL` defaults to `gpt-5.6-sol`.

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
