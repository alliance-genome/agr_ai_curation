# Modular Packages and Upgrades

Last updated: 2026-03-14

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
├── runtime/
│   ├── config/
│   │   ├── connections.yaml
│   │   ├── groups.yaml
│   │   ├── maintenance_message.txt
│   │   ├── models.yaml
│   │   ├── providers.yaml
│   │   ├── tool_policy_defaults.yaml
│   │   └── overrides.yaml            # optional
│   ├── packages/
│   │   ├── core/
│   │   │   ├── package.yaml
│   │   │   ├── agents/
│   │   │   ├── config/
│   │   │   ├── requirements/
│   │   │   └── python/
│   │   ├── alliance/
│   │   │   ├── package.yaml
│   │   │   ├── agents/
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
- `runtime/packages/core/` is the shipped AI Core package: the minimum
  supervisor/startup contract for a healthy standalone install.
- `runtime/packages/alliance/` is the shipped AGR Alliance package: the
  specialist catalog plus default shipped tool bindings.
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

4. The installer creates `~/.agr_ai_curation/.env`, seeds
   `runtime/config/`, seeds `runtime/packages/core/` and
   `runtime/packages/alliance/`, creates the runtime/data directories, and
   starts the standalone stack.

## Package model

Each runtime package is a directory under `runtime/packages/` with a
`package.yaml` manifest. The shipped `core` directory contains the minimal AI
Core contract, and the shipped `alliance` directory contains the full Alliance
specialist/tool catalog. Custom organization packages live alongside them.

Packages can contribute:

- agent bundles
- tool bindings
- provider defaults
- model defaults
- tool policy defaults

The `core` package ships the default provider/model/tool policy files plus the
supervisor bundle. The `alliance` package ships the default specialist agent
catalog and shipped tool bindings. Keep custom behavior in a separate package
so upgrades can replace the shipped packages safely.

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
6. Restart the standalone stack:

   ```bash
   docker compose --env-file ~/.agr_ai_curation/.env \
     -f docker-compose.production.yml up -d
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
3. Move any local edits out of `runtime/packages/core/` before upgrading.
   Standard upgrades replace the shipped `core` package. If you need long-lived
   custom behavior, keep it in a separate package instead.
4. Re-run the installer from Stage 2 so the bundled `core` package and runtime
   config files are refreshed:

   ```bash
   scripts/install/install.sh --from-stage 2 --image-tag vX.Y.Z
   ```

5. Stage 2 is interactive today: it backs up the existing `.env`, recreates it
   from `scripts/install/lib/templates/env.standalone`, and prompts again for
   provider/API keys. If your deployment uses OIDC, Stage 3 also re-prompts
   for issuer/client/secret values. Reconcile any local changes you keep in
   `.env` or `runtime/config/` from your backup after the refresh, and treat
   this as a manual checkpoint when automating upgrades.
6. Let Stage 6 restart and verify the stack.

Notes:

- `--from-stage 6` is a restart/verification shortcut only. It does not refresh
  `runtime/packages/core/` or the runtime config files, so it is not a full
  package upgrade.
- There is no dedicated non-interactive Stage 2 flag today; if you automate
  upgrades, plan around the manual `.env` reconciliation step instead of
  assuming an unattended refresh.
- Extra packages that are not `core` are left in place, but you should still
  keep them backed up or version-controlled.

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
- copies `packages/core` and any already-package-backed content into
  `~/.agr_ai_curation/runtime/packages/`
- copies mutable data into `~/.agr_ai_curation/data/`
- patches `~/.agr_ai_curation/.env` with the standalone host-directory paths

Manual review is required when the helper finds custom repo-local agents,
modified shipped `core` files, repo-local tool sources, or extra non-package
directories. In that case it preserves a scaffold under
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
