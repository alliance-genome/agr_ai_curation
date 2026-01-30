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

1. **[CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md)** - Overview of the YAML-based configuration system
2. **[ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md)** - Step-by-step guide to create a new agent
3. **[ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md)** - How to add tools that agents can use

### Developer Guides

| Guide | Description |
|-------|-------------|
| [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) | Full architecture guide - YAML source of truth, loaders, deployment |
| [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) | Create agents with agent.yaml, prompt.yaml, schema.py |
| [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) | Add Python tools for database/API access |
| [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) | Legacy guide - comprehensive but pre-dates config-driven architecture |

### API Reference

- **[API_USAGE.md](api/API_USAGE.md)** - Complete HTTP reference with streaming, auth, and workflows

### Trace Analysis

- **[TRACE_REVIEW_API.md](traces/TRACE_REVIEW_API.md)** - Trace review service API documentation
- **[TRACE_REVIEW_SPECIFICATION.md](traces/TRACE_REVIEW_SPECIFICATION.md)** - Trace review system specification

## Configuration Reference

For configuration files, see:

| File | Description |
|------|-------------|
| [config/README.md](../../config/README.md) | Configuration directory overview |
| [config/agents/README.md](../../config/agents/README.md) | Agent configuration reference |
| [config/groups.yaml.example](../../config/groups.yaml.example) | Group/Cognito mapping template |
| [config/connections.yaml.example](../../config/connections.yaml.example) | External connections template |

## Common Tasks

### Add a New Agent

```bash
# Copy template
cp -r config/agents/_examples/basic_agent config/agents/my_agent

# Edit files
# - config/agents/my_agent/agent.yaml
# - config/agents/my_agent/prompt.yaml
# - config/agents/my_agent/schema.py

# Restart backend
docker compose restart backend
```

See [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) for details.

### Add a New Tool

```bash
# Create tool file
# backend/tools/custom/my_tool.py

# Export in __init__.py
# backend/tools/custom/__init__.py
# backend/tools/__init__.py

# Reference in agent.yaml
# tools:
#   - my_tool
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

The system uses a **config-driven architecture** where:

1. **YAML is source of truth** - All configuration in YAML files
2. **Database is runtime cache** - YAML loaded at startup
3. **Self-contained agents** - Each agent is a folder with all its config
4. **Thread-safe loaders** - Configuration loaded once, cached

```
config/
├── groups.yaml           # Group/Cognito mapping
├── connections.yaml      # External service connections
└── agents/               # Agent definitions
    ├── supervisor/       # Core routing agent
    ├── gene/             # Gene validation agent
    └── [your_agent]/     # Your custom agents
        ├── agent.yaml    # Agent definition
        ├── prompt.yaml   # Instructions
        ├── schema.py     # Output format
        └── group_rules/  # Org-specific behavior
```

See [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) for the complete guide.
