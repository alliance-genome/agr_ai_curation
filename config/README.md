# Configuration Directory

This directory contains the deployment override files that seed the modular AGR
AI Curation runtime. In a standard standalone install, these files are
materialized under `~/.agr_ai_curation/runtime/config/`, while package-owned
agents and tool bindings load from `~/.agr_ai_curation/runtime/packages/`.

Public customization path:

- Edit runtime config under `~/.agr_ai_curation/runtime/config/`.
- Add custom agents and tools through a package under
  `~/.agr_ai_curation/runtime/packages/<your-package>/`.
- Do not rely on repo-local `config/agents/` or `backend/src/...` edits as the
  customization path for an installed deployment.

Repository-development note:

- The repo-local `config/` tree still carries installer-seeded defaults plus
  source mirrors used when maintaining shipped packages from this checkout.
- `config/agents/supervisor/` mirrors the shipped `agr.core` (Alliance Core)
  supervisor bundle in `packages/core/agents/supervisor/`.
- The remaining shipped specialist bundles under `config/agents/` mirror the
  `agr.alliance` (Alliance Defaults) catalog in `packages/alliance/agents/`.
- `config/models.yaml`, `config/providers.yaml`, and
  `config/tool_policy_defaults.yaml` remain aligned with `packages/core/config/`.

## Directory Structure

```
config/
├── README.md                    # This file
├── groups.yaml                  # Group/organization mappings (from .example)
├── connections.yaml             # External service connections (from .example)
├── providers.yaml               # LLM runtime provider definitions
├── models.yaml                  # LLM model catalog overrides
├── tool_policy_defaults.yaml    # Tool policy default overrides
├── maintenance_message.txt      # Optional maintenance banner content
├── groups.yaml.example          # Template for groups configuration
├── connections.yaml.example     # Template for connections configuration
└── agents/                      # Repo-local mirror of shipped agr.core + agr.alliance agent bundles
    ├── README.md               # Agent configuration guide
    ├── _examples/              # Template agents (not loaded)
    │   └── basic_agent/        # Example agent structure
    └── [your_agent]/           # Source-development agent bundles
```

## Quick Start

### Standalone install

The installer seeds the runtime config for you. After
`scripts/install/install.sh` completes, edit the installed files directly:

1. Review `~/.agr_ai_curation/runtime/config/groups.yaml`.
2. Review `~/.agr_ai_curation/runtime/config/connections.yaml`.
3. If needed, override packaged provider/model/tool policy defaults in:
   - `~/.agr_ai_curation/runtime/config/providers.yaml`
   - `~/.agr_ai_curation/runtime/config/models.yaml`
   - `~/.agr_ai_curation/runtime/config/tool_policy_defaults.yaml`
4. If you add custom packages with conflicting tool bindings, create
   `~/.agr_ai_curation/runtime/config/overrides.yaml`.

### Source development

For repo-local development of the shipped defaults:

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

4. **Edit repo-local agent bundles** in `config/agents/` only when you are
   developing the shipped supervisor or specialist packages from source.

## Configuration Files

### groups.yaml

Maps external identity-provider groups/roles to internal group IDs used by the system.

```yaml
identity_provider:
  type: "oidc"
  group_claim: "groups"

groups:
  FB:
    name: "FlyBase"
    description: "Drosophila melanogaster curation team"
    species: "Drosophila melanogaster"
    taxon: "NCBITaxon:7227"
    provider_groups:
      - "flybase-curators"
      - "flybase-admins"
```

Required fields:
- `identity_provider.type` (e.g., `oidc`, `cognito`)
- `identity_provider.group_claim` (JWT claim containing groups)
- `groups.<GROUP_ID>.provider_groups` (list of external group names)

Note:
- Legacy `cognito_groups` is no longer supported in `groups.yaml`.
- `AUTH_PROVIDER` (environment variable) selects the active auth backend.
- `identity_provider.*` in `groups.yaml` controls group-claim extraction metadata and should align with your token claims.

The internal group ID key (for example, `FB`) is used to match group-specific
rules in agent bundles, whether they live in a runtime package
(`runtime/packages/<package>/agents/<agent>/group_rules/[group_id].yaml`) or in
the repo-local `config/agents/` source tree.

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

### providers.yaml

Defines deployment override entries for LLM runtime providers used by agent execution.

```yaml
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    base_url_env: OPENAI_BASE_URL
    api_mode: responses
    default_for_runner: true
    supports:
      parallel_tool_calls: true

  org_custom:
    driver: litellm
    api_key_env: ORG_CUSTOM_API_KEY
    base_url_env: ORG_CUSTOM_BASE_URL
    litellm_prefix: acme
    drop_params: true
    supports:
      parallel_tool_calls: true
```

Notes:
- Installed packages may export provider defaults first; this file is merged afterward and wins on key collisions.
- Override entries replace the full provider definition for the same provider key.
- Exactly one provider must set `default_for_runner: true`.
- `driver: litellm` providers must include `litellm_prefix`.
- API key values are never stored in YAML, only env var names.

### models.yaml

Defines deployment override entries for the model catalog and maps each model to a provider key in `providers.yaml`.

```yaml
models:
  - model_id: gpt-5.4
    name: GPT-5.4
    provider: openai
    default: false
    curator_visible: true

  - model_id: acme/model-x
    name: Model X
    provider: org_custom
    curator_visible: true
```

Notes:
- Installed packages may export model defaults first; this file is merged afterward and wins on `model_id` collisions.
- Override entries replace the full model definition for the same `model_id`.
- Unknown provider references are startup validation errors.
- `curator_visible: false` keeps runtime compatibility models hidden from Agent Workshop.

### tool_policy_defaults.yaml

Defines deployment override entries for default tool visibility and execution policies.

```yaml
tool_policies:
  search_document:
    display_name: Search Document
    description: Semantic search over the active document.
    category: Document
    curator_visible: true
    allow_attach: true
    allow_execute: true
    config: {}
```

Notes:
- Installed packages may export tool policy defaults first; this file is merged afterward and wins on `tool_key` collisions.
- Override entries replace the full tool policy definition for the same `tool_key`.

### maintenance_message.txt

Optional plain-text banner content for maintenance mode.

Notes:
- The frontend banner and maintenance page read the first non-comment line from this file.
- Leave the file empty (or keep only comments) to disable the maintenance message.
- `scripts/maintenance_mode.sh` checks this file before enabling the maintenance page.

### agents/

This repo-local directory mirrors the shipped `agr.core` supervisor bundle plus
the shipped `agr.alliance` specialist bundles for source development. Public
standalone installs load agents from `runtime/packages/*/agents`, not from
`runtime/config/agents`.

Each agent bundle is a self-contained folder with:

- `agent.yaml` - Agent metadata, tools, and model configuration
- `prompt.yaml` - The agent's system prompt
- `schema.py` - Pydantic output schema
- `group_rules/` - Optional group-specific behavior rules

See `config/agents/README.md` for the package-owned agent bundle contract.

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

1. `connections.yaml` is loaded to establish service connections.
2. `groups.yaml` is loaded for authentication mapping.
3. Package-backed provider/model/tool policy defaults load from
   `runtime/packages/*`.
4. The runtime override files in `runtime/config/` are merged on top.
5. Agent bundles are discovered from `runtime/packages/*/agents`.
6. Group rules are associated with agents based on `group_id` matching.

## Override Behavior

- Providers, models, and tool policies merge in deterministic package order; the
  runtime files in `runtime/config/` win last on key collisions.
- Agent bundle names must be unique across loaded packages; duplicate names are
  startup errors.
- Tool binding collisions require an explicit selection in
  `runtime/config/overrides.yaml`.

See [docs/deployment/modular-packages.md](../docs/deployment/modular-packages.md)
for the full runtime layout, package model, and upgrade workflow.

## Validation

The system validates configuration at startup:

- **Schema validation**: YAML structure matches expected format
- **Reference validation**: Tools referenced by agents exist
- **Health checks**: External connections are reachable
- **Group validation**: Group rules reference valid group IDs

Errors are logged with specific file and line information for easy debugging.

## Org Onboarding: Add a Custom LLM Provider (No Code Changes)

1. Add provider config in `~/.agr_ai_curation/runtime/config/providers.yaml`.
2. Add model entries in `~/.agr_ai_curation/runtime/config/models.yaml` that reference that provider key.
3. Set required secrets/env vars (for example `ORG_CUSTOM_API_KEY`).
4. Restart backend.
5. Verify diagnostics:
   - `GET /api/admin/health/llm-providers`
   - Confirm no `errors` and provider readiness is `ready`.

If strict startup validation is too aggressive during local bring-up, set:

```bash
LLM_PROVIDER_STRICT_MODE=false
```

This downgrades missing API key checks to warnings (structural config errors still fail).
