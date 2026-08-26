# Modular Packages and Upgrades

Last updated: 2026-08-15

## Scope

This guide is the public contract for standalone installs that run from the
modular runtime under `~/.agr_ai_curation/`.

- Use this guide when you are installing the published runtime, adding your own
  package, or upgrading an existing standalone deployment.
- If you are developing AGR AI Curation itself from a repository checkout, the
  repo-local `config/` and `backend/` paths are still useful, but they are not
  the public customization path for installed deployments.

## Installed runtime layout

The standalone installer seeds an installed runtime under
`~/.agr_ai_curation/`:

```text
~/.agr_ai_curation/
├── .env
├── .install_package_profile.env
├── runtime/
│   ├── config/
│   │   ├── connections.yaml
│   │   ├── groups.yaml
│   │   ├── maintenance_message.txt
│   │   ├── models.yaml
│   │   ├── providers.yaml
│   │   ├── tool_policy_defaults.yaml
│   │   └── overrides.yaml            # package export selections
│   ├── packages/
│   │   ├── core/
│   │   │   ├── package.yaml
│   │   │   ├── agents/
│   │   │   ├── config/
│   │   │   ├── requirements/
│   │   │   └── python/
│   │   ├── alliance/                   # optional unless profile includes agr.alliance
│   │   │   ├── package.yaml
│   │   │   ├── agents/
│   │   │   ├── config/
│   │   │   ├── python/
│   │   │   ├── requirements/
│   │   │   └── tools/bindings.yaml
│   │   └── <your-package>/
│   │       └── ...
│   └── state/
│       ├── identifier_prefixes/
│       └── package_runner/
│           └── <package_id>/
│               ├── environment.json
│               └── venv/
└── data/
    ├── file_outputs/
    ├── pdf_storage/
    └── weaviate/
```

Key ownership rules:

- `~/.agr_ai_curation/.env` stores secrets, image tags, and host mount paths.
- `runtime/config/` is the operator-owned override layer for deployment YAML.
- `runtime/packages/core/` is `agr.core` (Alliance Core): the minimum
  supervisor/startup contract for a healthy standalone install.
- `runtime/packages/alliance/` is `agr.alliance` (Alliance Defaults): the
  optional specialist catalog plus default shipped tool bindings.
- `runtime/packages/<your-package>/` is where custom organization packages
  belong.
- `runtime/state/` is writable runtime state. The package runner creates one
  isolated virtual environment per loaded package under
  `runtime/state/package_runner/<package_id>/venv`.
- `data/` holds mutable deployment data. The standalone compose stack mounts
  these host directories into the container runtime paths used by the app.

## Fresh install

For a standard standalone install:

1. Check out the repository, or unpack the published release bundle that
   contains `scripts/install/`.
2. Run the installer:

   ```bash
   scripts/install/install.sh
   ```

3. To pin a specific published release, pass an image tag:

   ```bash
   scripts/install/install.sh --image-tag vX.Y.Z
   ```

4. Stage 2 prompts `Package profile [1=core only, 2=core + alliance]` and
   defaults to `core only`.
5. The installer creates `~/.agr_ai_curation/.env`, writes the selected
   package profile to `~/.agr_ai_curation/.install_package_profile.env`, seeds
   `runtime/config/`, seeds `runtime/packages/core/`, optionally seeds
   `runtime/packages/alliance/`, creates the runtime/data directories, and
   starts the standalone stack.

## Install profiles

Two shipped package profiles are supported:

- `core only` (default) installs `agr.core` (Alliance Core) only.
- `core + alliance` installs both `agr.core` (Alliance Core) and
  `agr.alliance` (Alliance Defaults).

`core only` is expected to start healthy. In that profile:

- the main chat still runs through the core supervisor in core-only mode, but
  without the domain specialist/tool catalog,
- Agent Studio derives its core catalog from `agr.core`: `task_input`,
  `supervisor`, `curation_prep`, and `curation_handoff`, and
- `curation_handoff` remains available as the generic terminal flow step while
  domain extraction and validation agents require another package.

Use `core + alliance` when you want the richer shipped AGR/Alliance defaults,
including the specialist agent catalog and tool bindings.

You can add `agr.alliance` later by re-running Stage 2:

```bash
scripts/install/install.sh --from-stage 2 --package-profile core-plus-alliance
```

That updates `~/.agr_ai_curation/.install_package_profile.env` to include both
`agr.core` and `agr.alliance`.

## Package model

Each runtime package is a directory under `runtime/packages/` with a
`package.yaml` manifest. The shipped `core` directory is package ID
`agr.core` with display name `Alliance Core`, and the shipped `alliance`
directory is package ID `agr.alliance` with display name `Alliance Defaults`.
Custom organization packages live alongside them.

Packages can contribute:

- agent bundles
- tool bindings
- external document-source provider registrations
- provider defaults
- model defaults
- tool policy defaults
- Agent Studio flow recipes, agent equivalences, and composition suggestions
- one Agent Studio system prompt per package profile

`agr.core` ships the default provider/model/tool policy files, the supervisor
and generic curation handoff bundles, and a neutral Agent Studio prompt.
`agr.alliance` ships the default specialist agent catalog along with shipped
tool bindings, flow recipes, and the Alliance curator Agent Studio prompt. Keep
custom behavior in a separate package so upgrades can replace the shipped
packages safely.

The standalone installer's bundled profile contract also expects
`config/runtime_overrides.yaml` in each shipped profile package. The core
template is neutral; the Alliance template selects the Alliance Agent Studio
prompt. This file is an installer profile template, not a manifest export for
ordinary third-party packages.

### Minimal custom package layout

```text
~/.agr_ai_curation/runtime/packages/org-custom/
├── package.yaml
├── requirements/runtime.txt
├── agents/
│   └── literature_helper/
│       ├── agent.yaml
│       ├── prompt.yaml
│       └── schema.py
├── python/src/org_custom/
│   └── tools/
│       └── literature.py
└── tools/bindings.yaml
```

Example `package.yaml`:

```yaml
package_id: org.custom
display_name: Org Custom Package
version: 0.1.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/org_custom
requirements_file: requirements/runtime.txt
exports:
  - kind: tool_binding
    name: default
    path: tools/bindings.yaml
    description: Org-specific tool bindings
agent_bundles:
  - name: literature_helper
    has_schema: true
```

The `agent_bundles` shorthand expands into the required agent, prompt, schema,
and group-rule exports automatically.

### External document-source providers

A package can register an external document source with a Python module export:

```yaml
exports:
  - kind: document_source_provider
    name: example_literature
    path: python/src/org_custom/document_sources.py
    description: Example literature service integration
```

The module must define `get_document_source_provider_registrations()` and
return a list or tuple of `DocumentSourceProviderRegistration` values. Each
registration supplies a unique lowercase provider ID, a lazy provider factory,
optional lazy development-token resolver, non-secret presentation metadata,
and boolean capability metadata. Factories and token resolvers are not called
while packages are enumerated. A configured provider is selected with
`DOCUMENT_SOURCE_PROVIDER=<provider_id>`.

Import `DocumentSourceProviderRegistration` and
`DocumentSourceProviderPresentation` from `src.lib.packages`; the synthetic
package under `backend/tests/unit/lib/packages/fixtures/org_custom_runtime/`
shows a minimal third-provider implementation.

Provider IDs are unique across all loaded packages. Missing modules, malformed
registrations, and collisions fail startup with the package ID, manifest/module
path, export name, and provider ID where available. `local_pdf` is reserved for
the built-in local upload flow and is not an external provider registration.
The shipped `agr.alliance` package owns the `abc_literature` registration; a
core-only install starts without it and continues to use `local_pdf`.

### Identifier-prefix providers

A package that derives valid identifier prefixes from its curation source can
declare a Python provider:

```yaml
exports:
  - kind: identifier_prefixes
    name: curation_database
    path: python/src/org_custom/identifier_prefixes.py
    description: Organization-specific identifier-prefix discovery
```

The module must define `get_identifier_prefixes(database_url)`. The callable
receives the resolved curation connection URL and returns an iterable of
non-empty strings. Core trims, deduplicates, and sorts contributions from every
installed provider before atomically publishing the shared runtime prefix
file. Provider loading errors fail startup. If provider execution or
contribution validation fails during refresh, the complete last known-good file
remains in place and no partial contribution is published.

A profile with no `identifier_prefixes` export does not resolve or connect to a
curation database for prefix discovery. It removes stale generated prefix state
so switching away from a package cannot retain that package's identifiers. The
shipped Alliance package owns its curation-schema queries through this export;
the generic core package contains no database-schema assumptions.

### Agent Studio flow recipes

A package can export `config/flow_recipes.yaml` with `kind: flow_recipes`.
The strict versioned contract contributes starter recipes plus optional agent
equivalence and suggestion metadata. Recipe steps are checked by the same
validation path as `validate_flow` and `create_flow`; malformed exports fail
startup with package and source-file context. Core-only installs expose no
domain recipes. See the developer configuration guide for the complete YAML
shape.

Recipes use the same canonical group-availability contract as agents:

```yaml
flow_recipes_api_version: 1.0.0
recipes:
  - name: RGD Curation
    description: Starter flow for RGD curators
    access:
      allowed_group_ids: [RGD]
    steps:
      - agent_id: gene_validation
```

`allowed_group_ids: []` (and an omitted `access` block) is unrestricted by
group. Values are case-sensitive IDs owned by `config/groups.yaml`; arbitrary
labels fail package loading. Custom-agent clones inherit a restricted source
as a floor and may select a non-empty subset, but cannot clear or broaden it.

### Agent Studio system prompt

Each healthy package profile must resolve exactly one `agent_studio_prompt`
export. The export is a UTF-8 Markdown template and must retain the
`{{USER_GREETING}}` and `{{PACKAGE_DIAGNOSTIC_TOOLS}}` placeholders when that
dynamic context is desired:

```yaml
exports:
  - kind: agent_studio_prompt
    name: system
    path: config/agent_studio_system_prompt.md
    description: Organization-specific Agent Studio guidance
```

With one active prompt export and no prompt selection, that prompt is selected
automatically. With multiple active exports, startup fails unless
`runtime/config/overrides.yaml` selects exactly one package/export. An explicit
selection is always authoritative and fails if it does not name an active
candidate. The error includes the package ID, manifest, export name, and
resolved file path for each candidate. There is no silent substitution,
filesystem fallback, or package-order winner.

## Merge and override behavior

Runtime loading is deterministic, but not every content type resolves conflicts
the same way.

### Providers, models, and tool policies

- Package defaults load from `runtime/packages/*` in sorted `package_id` order.
- If two packages define the same provider key, `model_id`, or tool policy key,
  the later package replaces the earlier definition.
- The runtime override files in `runtime/config/` load last and replace any
  colliding package defaults completely.

Use `runtime/config/providers.yaml`, `runtime/config/models.yaml`, and
`runtime/config/tool_policy_defaults.yaml` for deployment-local overrides. Use a
custom package when you want a reusable bundle that can move across installs.

### Agents

- Agent bundle names must be unique across all loaded packages.
- If two packages export the same agent bundle name, startup fails with a
  duplicate-agent error.
- There is no automatic winner for agent collisions. Rename or consolidate the
  bundle instead of expecting an override.

### Tools

- Tool bindings must resolve to one winning binding per `tool_id`.
- If multiple packages export the same `tool_id`, startup fails unless you
  select a winning tool-binding export in `runtime/config/overrides.yaml`.
- `disabled_packages` in `overrides.yaml` excludes a package from runtime
  loading without deleting it from disk.

Example `overrides.yaml`:

```yaml
overrides_api_version: 1.0.0
disabled_packages:
  - experimental.package
selections:
  - export_kind: tool_binding
    name: default
    package_id: org.custom
    reason: Prefer org.custom for conflicting shared tools.
```

Important: tool-binding selections do not target individual `tool_id` entries.
Each selection names the exported binding bundle (`export_kind` + export
`name`) and the winning `package_id`. Most packages use `name: default` for
their `tools/bindings.yaml` export.

If you need only some conflicting tools from a package to win, split them into
separate tool-binding exports instead of keeping every tool in one `default`
export.

### Agent Studio prompt

- A profile with one `agent_studio_prompt` export uses it directly.
- A profile with multiple prompt exports requires an explicit selection.
- An explicit selection must resolve even if the profile has only one candidate.
- Package-owned templates define the shipped profiles:
  `packages/core/config/runtime_overrides.yaml` is neutral, while
  `packages/alliance/config/runtime_overrides.yaml` selects
  `agr.alliance:system`. The installer copies the template for the selected
  profile into `runtime/config/overrides.yaml`. The source checkout's
  `config/overrides.yaml` mirrors the Alliance template because the supported
  source-development and direct Compose profiles mount both shipped packages;
  it is not unconditionally seeded into standalone installs.

```yaml
overrides_api_version: 1.0.0
selections:
  - export_kind: agent_studio_prompt
    name: system
    package_id: agr.alliance
    reason: Use the Alliance curator prompt for this package profile.
```

## Install a custom tool package

1. Create a new package directory under
   `~/.agr_ai_curation/runtime/packages/`.
2. Add a valid `package.yaml` and `requirements/runtime.txt`.
3. Put your tool implementation under the package's Python source root.
4. Declare the tool in `tools/bindings.yaml`.

   Example:

   ```yaml
   package_id: org.custom
   bindings_api_version: 1.0.0
   tools:
     - tool_id: literature_lookup
       binding_kind: static
       callable: org_custom.tools.literature:literature_lookup
       required_context: []
       description: Query the org-specific literature service
   ```

5. If an agent should use the tool, add an agent bundle in the same package and
   reference the tool ID from that agent's `tools:` list.
6. Rerun the guarded production start-and-verify stage:

   ```bash
   scripts/install/install.sh --from-stage 6
   ```

7. Verify package loading in backend logs and, if you use the admin health
   endpoints, check `GET /api/admin/health/packages`.

The package runner installs `requirements/runtime.txt` into an isolated virtual
environment the first time a package-backed tool executes.

## Upgrade a standard standalone install

Use this path when you already have a modular install under
`~/.agr_ai_curation/`.

1. Pull the new release checkout or unpack the new release bundle.
2. Back up:
   - `~/.agr_ai_curation/.env`
   - `~/.agr_ai_curation/runtime/config/`
   - any custom directories under `~/.agr_ai_curation/runtime/packages/`
3. Move any local edits out of the shipped package directories before
   upgrading. Standard upgrades replace `agr.core`, and they replace
   `agr.alliance` too when the selected profile includes it. If you need
   long-lived custom behavior, keep it in a separate package instead.
4. Re-run the installer from Stage 2 so the bundled packages and runtime
   config files are refreshed:

   ```bash
   scripts/install/install.sh --from-stage 2 --image-tag vX.Y.Z
   ```

5. Stage 2 is interactive today: it backs up the existing `.env`, recreates it
   from `scripts/install/lib/templates/env.standalone`, and prompts again for
   the package profile and provider/API keys. If your deployment uses OIDC,
   Stage 3 also re-prompts for issuer/client/secret values. Reconcile any
   local changes you keep in `.env` or `runtime/config/` from your backup after
   the refresh, and treat this as a manual checkpoint when automating upgrades.
6. Let Stage 6 restart and verify the stack.

Notes:

- `--from-stage 6` is a restart/verification shortcut only. It does not refresh
  shipped package contents or the runtime config files, so it is not a full
  package upgrade.
- There is no dedicated non-interactive Stage 2 flag today; if you automate
  upgrades, plan around the manual `.env` reconciliation step instead of
  assuming an unattended refresh.
- Extra packages beyond the selected shipped profile are left in place, but you
  should still keep them backed up or version-controlled.

## Migrate an existing repo-based install

Use `scripts/install/migrate_repo_install.sh` when your deployment still runs
from a repo checkout, repo-local `config/agents`, or repo-local tool code.

Preview first:

```bash
scripts/install/migrate_repo_install.sh --dry-run
```

Apply the migration:

```bash
scripts/install/migrate_repo_install.sh --apply
```

The helper:

- copies repo-local deployment config into `~/.agr_ai_curation/runtime/config/`
- copies the shipped `packages/core` and `packages/alliance` directories plus
  any already-package-backed content into `~/.agr_ai_curation/runtime/packages/`
- copies mutable data into `~/.agr_ai_curation/data/`
- patches `~/.agr_ai_curation/.env` with the standalone host-directory paths

If the source checkout has a customized `config/overrides.yaml`, the helper
stops before writing the target and requires manual reconciliation. (The exact
shipped Alliance template is accepted.) Keep a running deployment's checkout
unchanged. Instead, stop that deployment or copy the checkout, move the
customized file aside there without deleting it, run the migration, and then
merge its operator-owned entries into the installed profile template. The
helper never replaces a customized overrides file with a shipped template.

Manual review is required when the helper finds custom repo-local agents,
modified shipped `core` or `alliance` files, repo-local tool sources, or extra
non-package directories. In that case it preserves a scaffold under
`~/.agr_ai_curation/migration/legacy_local/` and exits with
`MIGRATION_STATUS=manual_review_required`.

Review the preserved scaffold, convert the pieces you still need into a real
runtime package, and only then switch the deployment to
`docker-compose.production.yml`.

## Related docs

- [Independent deployment](independent-deployment.md)
- [Configuration directory](../../config/README.md)
- [Agent bundle authoring](../../config/agents/README.md)
- [Tool package authoring](../../backend/tools/README.md)
