# Developer Documentation

Documentation for the AI Curation Platform developers.

> Scope: These guides are primarily for repository contributors and package
> maintainers. Standard installs should customize agents, tools, and defaults
> through `~/.agr_ai_curation/runtime/packages/` and
> `~/.agr_ai_curation/runtime/config/`, not by editing repo `config/` or
> `backend/src/` paths directly. For the public runtime contract, see
> [docs/deployment/modular-packages.md](../deployment/modular-packages.md).

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `api/` | API reference documentation |
| `guides/` | Developer guides and how-tos |
| `traces/` | Langfuse trace analysis tools |

## Quick Navigation

### Getting Started

Start here for new developers:

1. **[CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md)** -- Repository architecture and package-aware runtime loading
2. **[ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md)** -- How to add an agent bundle, with separate notes for runtime packages vs source checkouts
3. **[ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md)** -- How to add a tool, with separate notes for package-owned exports vs runtime internals
4. **[AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md)** -- Comprehensive agent/runtime architecture reference
5. **[UPLOAD_RUNTIME_CONTRACT.md](guides/UPLOAD_RUNTIME_CONTRACT.md)** -- Upload runtime contract (status/cancellation/rollback/idempotency; implementation in ALL-23)

### Developer Guides

| Guide | Description |
|-------|-------------|
| [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) | Full architecture guide for repo contributors -- package loading, database runtime, loaders, deployment |
| [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) | Add agent bundles for runtime packages or source-checkout shipped-package maintenance |
| [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) | Add package-owned tools or maintain Alliance Defaults tool catalogs/runtime tool plumbing |
| [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) | Comprehensive reference: unified agents table, dynamic supervisor, tool bindings, prompt management |
| [DOCKER_CLI_REMOVAL_VERIFICATION.md](guides/DOCKER_CLI_REMOVAL_VERIFICATION.md) | 2026-03-24 backend audit for Docker CLI/socket hardening and remaining Docker references |
| [PDF_HIGHLIGHT_VERIFICATION.md](guides/PDF_HIGHLIGHT_VERIFICATION.md) | Verification checklist and diagnostics for PDF chunk highlighting bugs |
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

Choose the path that matches your goal:

- Standard install or org customization: create a runtime package under `~/.agr_ai_curation/runtime/packages/<package>/agents/<agent>/` and install that package.
- Repository package maintenance: use the repo template under `config/agents/_examples/basic_agent`, then keep the shipped supervisor copy in `packages/core/agents/` or the shipped specialist copy in `packages/alliance/agents/` aligned before shipping.
- UI-only customization: create custom agents in Agent Studio with no file changes.

See [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) and
[config/agents/README.md](../../config/agents/README.md) for details.

### Add a New Tool

Choose the path that matches your goal:

- Standard install or org customization: add Python code plus `tools/bindings.yaml` to a runtime package under `~/.agr_ai_curation/runtime/packages/<package>/`.
- Repository package maintenance: update the shipped tool sources in `packages/alliance/python/src/...` and `packages/alliance/tools/bindings.yaml`.
- Runtime-internal development: only edit `backend/src/...` when the runtime itself needs new loader, resolver, or execution behavior.

See [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) and
[backend/tools/README.md](../../backend/tools/README.md) for details.

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

1. **Package-backed YAML defines initial state** -- Agent metadata, prompts, and group rules come from bundles under `~/.agr_ai_curation/runtime/packages/*/agents/` in standalone installs. In a source checkout, `config/agents/` is the repo mirror for the shipped `agr.core` supervisor plus the shipped `agr.alliance` specialist bundles.
2. **Database is runtime authority** -- The unified `agents` table stores all agent records (system + custom)
3. **Dynamic discovery** -- The supervisor queries the database for enabled agents and builds streaming tools at runtime
4. **Declarative tool bindings** -- Package `tools/bindings.yaml` exports are normalized into `TOOL_BINDINGS` with explicit context requirements
5. **No hardcoded agent files** -- All 16 original Python agent files have been replaced by generic construction from database rows

```text
~/.agr_ai_curation/runtime/packages/org-custom/
  package.yaml
  agents/
    my_agent/
      agent.yaml        # Agent definition
      prompt.yaml       # Instructions
      schema.py         # Output schema
      group_rules/      # Org-specific behavior
  tools/
    bindings.yaml       # Tool exports for the merged runtime registry

          |
          | Runtime loaders + seed/update flow populate:
          v

agents table            # Unified database (runtime authority)
  (system agents from package-backed YAML + custom agents from UI)

          |
          | get_agent_by_id() reads from DB
          v

Runtime Agent           # OpenAI Agents SDK Agent instance
  (tools resolved via TOOL_BINDINGS)
  (group rules injected from prompt cache)
  (output schema resolved from schema discovery)
```

For shipped-package maintenance in this repository, keep
`packages/core/agents/supervisor/` generic as the shipped baseline, use
`config/agents/supervisor/` as the repo-local or deployment-specific override,
and keep the remaining repo mirror bundles aligned with
`packages/alliance/agents/`. When the specialist catalog changes in a way that
affects routing or handoff style, review the config supervisor override too.

See [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) and [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) for the complete reference.
