# Configuration Directory

This directory contains all configuration files for the AI Curation system. The configuration follows a YAML-as-source-of-truth pattern where YAML files define the system behavior, and the database serves as a runtime cache.

## Directory Structure

```
config/
├── README.md                    # This file
├── groups.yaml                  # Group/organization mappings (from .example)
├── connections.yaml             # External service connections (from .example)
├── groups.yaml.example          # Template for groups configuration
├── connections.yaml.example     # Template for connections configuration
└── agents/                      # Agent definitions
    ├── README.md               # Agent configuration guide
    ├── _examples/              # Template agents (not loaded)
    │   └── basic_agent/        # Example agent structure
    └── [your_agent]/           # Your custom agents
```

## Quick Start

1. **Copy example files:**
   ```bash
   cp config/groups.yaml.example config/groups.yaml
   cp config/connections.yaml.example config/connections.yaml
   ```

2. **Configure your groups** in `groups.yaml`:
   - Map your identity provider groups to internal group IDs
   - These group IDs are used in agent group rules

3. **Configure your connections** in `connections.yaml`:
   - Set up database connections
   - Configure external API endpoints
   - Define health check parameters

4. **Create or copy agents** to `config/agents/`:
   - Copy from `_examples/` as a starting point
   - Or copy from `alliance_agents/` for Alliance-specific agents

## Configuration Files

### groups.yaml

Maps authentication provider groups (e.g., AWS Cognito groups) to internal group IDs used by the system.

```yaml
groups:
  ExternalGroupName:
    group_id: INTERNAL_ID
    display_name: "Human Readable Name"
    description: "Optional description"
```

The `group_id` value is used to match group-specific rules in agent configurations (`config/agents/[agent]/group_rules/[group_id].yaml`).

### connections.yaml

Defines connections to external services with health check configuration.

```yaml
databases:
  primary:
    type: postgresql
    host: "${DB_HOST:-localhost}"
    # ... connection settings
    health_check:
      enabled: true
      query: "SELECT 1"
```

Supports environment variable substitution using `${VAR}` or `${VAR:-default}` syntax.

### agents/

Contains agent definitions. Each agent is a self-contained folder with:

- `agent.yaml` - Agent metadata, tools, and model configuration
- `prompt.yaml` - The agent's system prompt
- `schema.py` - Pydantic output schema
- `group_rules/` - Optional group-specific behavior rules

See `config/agents/README.md` for detailed documentation.

## Environment Variables

Configuration files support environment variable substitution:

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Use value of VAR, error if not set |
| `${VAR:-default}` | Use value of VAR, or "default" if not set |

**Never commit actual secrets.** Use environment variables for:
- Database passwords
- API keys
- Authentication tokens

## Loading Order

At system startup:

1. `connections.yaml` is loaded to establish service connections
2. `groups.yaml` is loaded for authentication mapping
3. Agent folders in `config/agents/` are discovered and loaded
4. Group rules are associated with agents based on `group_id` matching

## For Alliance Genome Deployments

Alliance-specific configuration is maintained separately:

- `alliance_agents/` - Alliance agent definitions
- `alliance_config/` - Alliance group and connection configs
- `alliance_tools/` - Alliance-specific tools

During deployment, these are copied to the appropriate locations:
- `alliance_agents/*` → `config/agents/`
- `alliance_config/*` → `config/`
- `alliance_tools/*` → `backend/tools/custom/`

## Validation

The system validates configuration at startup:

- **Schema validation**: YAML structure matches expected format
- **Reference validation**: Tools referenced by agents exist
- **Health checks**: External connections are reachable
- **Group validation**: Group rules reference valid group IDs

Errors are logged with specific file and line information for easy debugging.
