# Independent Deployment

Last updated: 2026-03-18

## Scope

This guide covers standalone deployment of `agr_ai_curation` outside Alliance production infrastructure.

Related guide:

- [Modular Packages and Upgrades](modular-packages.md) for the installed runtime
  layout, package authoring contract, collision behavior, standard upgrades,
  and repo-install migration.

## Published images

The standalone stack now expects `backend`, `frontend`, and `trace_review_backend` to run from published images instead of local source builds.

- Canonical backend image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-backend`
- Canonical frontend image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend`
- Canonical trace review image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend`
- `publish-images.yml` publishes backend/frontend/trace-review runtime images from `main` as `latest` plus `sha-<shortsha>` for dev and local-server validation
- Pushed `vX.Y.Z` tags publish versioned backend/frontend/trace-review runtime images tagged `vX.Y.Z`
- Tagged releases also attach `core-vX.Y.Z.tar.gz`, `alliance-vX.Y.Z.tar.gz`,
  `env.standalone-vX.Y.Z`, and `release-manifest-vX.Y.Z.json` so
  installer/release consumers can pin exact image refs instead of a floating
  `latest` lane

## Compose file

Use `docker-compose.production.yml` for standalone deployments.

- `docker-compose.production.yml` is the published-image path with modular runtime/data mounts
- `docker-compose.prod.yml` remains the GELF logging override for the source-build stack and is unchanged by this deployment path
- Validation commands:
  - `docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml config`
  - `docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml up -d`

## Installed runtime layout

The standalone installer seeds the modular runtime under
`~/.agr_ai_curation/`.

- Secrets + image tags: `~/.agr_ai_curation/.env`
- Selected package profile state: `~/.agr_ai_curation/.install_package_profile.env`
- Runtime config: `~/.agr_ai_curation/runtime/config`
- Repo config mirror for legacy compatibility: `AGR_REPO_CONFIG_HOST_DIR=/path/to/your/repo/config`
- Optional package/tool collision selections: `~/.agr_ai_curation/runtime/config/overrides.yaml`
- Runtime packages: `~/.agr_ai_curation/runtime/packages`
- Default shipped package directory: `~/.agr_ai_curation/runtime/packages/core`
- Optional shipped Alliance defaults directory: `~/.agr_ai_curation/runtime/packages/alliance`
- Runtime state: `~/.agr_ai_curation/runtime/state`
- Package-runner virtualenvs: `~/.agr_ai_curation/runtime/state/package_runner/<package_id>/venv`
- Host data directories: `~/.agr_ai_curation/data/pdf_storage`, `~/.agr_ai_curation/data/file_outputs`, `~/.agr_ai_curation/data/weaviate`

`scripts/install/install.sh --image-tag <tag>` can be used to pin the published backend/frontend/trace-review images to a specific release tag during installation.
Without an explicit override, Stage 2 resolves the checked-out repo to an exact published image tag when possible:

- exact `vX.Y.Z` when `HEAD` is a tagged release
- `sha-<shortsha>` for ordinary Git checkouts such as `main`
- checked-in template defaults when Git metadata is unavailable

For tagged releases, prefer the exact `vX.Y.Z` tag or image digests from the release manifest (or use the attached `env.standalone-vX.Y.Z` asset) instead of a floating `latest` lane.

## Package install profiles

Stage 2 of `scripts/install/install.sh` prompts:

```text
Package profile [1=core only, 2=core + alliance]
```

- `core only` is the default profile. It seeds only `agr.core` (Alliance Core),
  records `INSTALL_PACKAGE_IDS=agr.core` in
  `~/.agr_ai_curation/.install_package_profile.env`, and is expected to start
  healthy.
- `core + alliance` seeds both `agr.core` (Alliance Core) and `agr.alliance`
  (Alliance Defaults), restoring the richer shipped AGR/Alliance specialist and
  tool catalog.
- You can add `agr.alliance` later by re-running Stage 2:

  ```bash
  scripts/install/install.sh --from-stage 2 --package-profile core-plus-alliance
  ```

On a healthy `core only` install, the main chat still starts in core-only mode
but without the domain specialist/tool catalog, Agent Studio shows only
`task_input` plus `supervisor`, and flow helpers report that no flow-capable
agents are installed until `agr.alliance` is added.

## Upgrading a standard standalone install

When the existing deployment already runs from `~/.agr_ai_curation/`:

1. Pull the updated release checkout or unpack the updated release bundle.
2. Back up `~/.agr_ai_curation/.env`, `~/.agr_ai_curation/runtime/config/`,
   and any custom package directories under
   `~/.agr_ai_curation/runtime/packages/`.
3. Move any long-lived customizations out of the shipped package directories
   before upgrading. Stage 2 refreshes `agr.core`, refreshes `agr.alliance`
   when the selected profile includes it, and re-seeds the runtime config
   files.
4. Re-run the installer from Stage 2:

   ```bash
   scripts/install/install.sh --from-stage 2 --image-tag vX.Y.Z
   ```

5. Stage 2 is interactive today: it backs up `~/.agr_ai_curation/.env`,
   recreates it from `scripts/install/lib/templates/env.standalone`, and
   prompts again for the package profile and provider/API keys. If your
   deployment uses OIDC, Stage 3 also re-prompts for issuer/client/secret
   values. Reconcile any local `.env` or runtime-config changes from your
   backup after the refresh completes, and treat this as a manual checkpoint
   when automating upgrades.

Use `--from-stage 6` only for restart/verification work. It does not refresh
the packaged runtime content.

## Migrating an existing repo-based install

Use `scripts/install/migrate_repo_install.sh` before switching a repo-coupled deployment to `docker-compose.production.yml`.

Examples:

```bash
# Preview the migration without writing files
scripts/install/migrate_repo_install.sh --dry-run

# Copy repo-local config/data into ~/.agr_ai_curation and patch ~/.agr_ai_curation/.env
scripts/install/migrate_repo_install.sh --apply
```

What the helper does:

- Copies repo-local deployment config into `~/.agr_ai_curation/runtime/config`
- Copies the shipped `packages/core` and `packages/alliance` directories plus
  any additional package-backed content into
  `~/.agr_ai_curation/runtime/packages`
- Copies repo-local mutable data into `~/.agr_ai_curation/data/*`
- Patches `~/.agr_ai_curation/.env` with the standalone host-directory variables when a repo `.env` already exists

Custom local code handling:

- If repo-local custom agents, modified shipped `packages/core` or
  `packages/alliance` content, repo-local custom tool sources, or extra
  non-package code directories are detected, the helper preserves them under
  `~/.agr_ai_curation/migration/legacy_local`
- In that case `--apply` exits with `MIGRATION_STATUS=manual_review_required` and a non-zero status so the upgrade cannot look clean by accident
- Review the preserved scaffold and package/binding templates before mounting any legacy local code into `runtime/packages`

## Trace review diagnostics service

`trace_review_backend` is part of the supported standalone diagnostics story and starts in the main Compose stack by default.

- The main backend reaches it through `TRACE_REVIEW_URL=http://trace_review_backend:8001`
- The service uses standard Docker bridge networking in standalone deployment
- Default standalone Langfuse connectivity is `http://langfuse:3000`, not `network_mode: host`
- `network_mode: host` remains a local-development convenience in `trace_review/docker-compose.yml`; it is not the supported standalone production model

## Required trace review environment

The authoritative standalone template is `scripts/install/lib/templates/env.standalone`.

Key values:

- `BACKEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-backend`
- `BACKEND_IMAGE_TAG=latest`
- `FRONTEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend`
- `FRONTEND_IMAGE_TAG=latest`
- `TRACE_REVIEW_BACKEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend`
- `TRACE_REVIEW_BACKEND_IMAGE_TAG=latest`
- `TRACE_REVIEW_URL=http://trace_review_backend:8001`
- `TRACE_REVIEW_LANGFUSE_HOST=http://langfuse:3000`
- `TRACE_REVIEW_LANGFUSE_LOCAL_HOST=http://langfuse:3000`

The checked-in template is the manual baseline.
When you run the installer from a Git checkout, Stage 2 rewrites those image tags to the matching published release tag or `sha-<shortsha>` for that checkout unless you pass `--image-tag`.
Tagged releases publish a pinned `env.standalone-vX.Y.Z` companion asset so standalone installs can consume exact versioned image tags without editing the template by hand.

## Optional Alliance curation database integration

`curation_db` is optional in standalone deployment and is not part of the
generic third-party install path.

- If you do nothing, the standalone stack should still install and run normally
  with `curation_db` treated as `not_configured`
- The default standalone installer does not prompt for `CURATION_DB_*` values
- This integration is mainly for deployments that want direct SQL access to an
  AGR curation PostgreSQL database for extra lookup/tooling features

Treat it as a post-install add-on rather than a base requirement.

To add it later, set either:

- `CURATION_DB_URL=postgresql://...`
- or `CURATION_DB_CREDENTIALS_SOURCE=aws_secrets` plus the relevant
  `CURATION_DB_AWS_SECRET_ID`, `AWS_PROFILE`, and `AWS_REGION`

Then restart the backend:

```bash
docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml up -d backend
```

On a generic third-party install, `/health` should not be degraded just because
`curation_db` is unset.

## PDF extraction service

The PDF extraction service (`agr_pdf_extraction_service`) runs in its own
Docker Compose stack, separate from the main AI Curation stack.  Because
the backend runs inside a Docker container, service URLs that use
`localhost` will not work — `localhost` inside a container refers to the
container itself, not the host machine.

The installer (Stage 5) writes the correct URL automatically:

```
PDF_EXTRACTION_SERVICE_URL=http://host.docker.internal:<port>
```

`host.docker.internal` resolves to the host machine from inside any
container because the production Compose file includes:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

If you configured the PDF extraction URL manually or are upgrading from an
older install, verify that `~/.agr_ai_curation/.env` uses
`host.docker.internal` rather than `localhost`:

```bash
# Wrong — unreachable from inside the backend container
PDF_EXTRACTION_SERVICE_URL=http://localhost:5000

# Correct
PDF_EXTRACTION_SERVICE_URL=http://host.docker.internal:5000
```

After changing the URL, restart the backend:

```bash
docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml up -d backend
```

## Authentication (OIDC)

OIDC authentication is implemented and supported for independent deployments.
Use your provider's OIDC configuration values in environment variables and runtime config.

## Group Mapping Configuration

Group mapping uses `provider_groups` in `config/groups.yaml`.

Example:

```yaml
groups:
  MGI:
    name: MGI Curators
    provider_groups:
      - mgi-curators
      - mgi-admins
```

`cognito_groups` is legacy terminology and should not be used in new configuration examples.
