# Independent Deployment

Last updated: 2026-03-13

## Scope

This guide covers standalone deployment of `agr_ai_curation` outside Alliance production infrastructure.

## Published images

The standalone stack now expects `backend`, `frontend`, and `trace_review_backend` to run from published images instead of local source builds.

- Canonical backend image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-backend`
- Canonical frontend image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend`
- Canonical trace review image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend`
- The authoritative standalone env template currently pins `BACKEND_IMAGE_TAG` and `FRONTEND_IMAGE_TAG` to `smoke-20260310-final`, which was the latest verified public tag on March 13, 2026
- Standalone env defaults pin the repository with `TRACE_REVIEW_BACKEND_IMAGE` and `TRACE_REVIEW_BACKEND_IMAGE_TAG`
- The GitHub release workflow publishes `trace_review_backend` from `trace_review/backend/Dockerfile.prod` and tags each release as both `v<version>` and `latest`

## Compose file

Use `docker-compose.production.yml` for standalone deployments.

- `docker-compose.production.yml` is the published-image path with modular runtime/data mounts
- `docker-compose.prod.yml` remains the GELF logging override for the source-build stack and is unchanged by this deployment path
- Validation commands:
  - `docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml config`
  - `docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml up -d`

## Installed runtime layout

The standalone installer now seeds the extracted bundle into an installed runtime/data layout under `~/.agr_ai_curation/`.

- Runtime config: `~/.agr_ai_curation/runtime/config`
- Runtime packages: `~/.agr_ai_curation/runtime/packages`
- Shipped core package: `~/.agr_ai_curation/runtime/packages/core`
- Runtime state: `~/.agr_ai_curation/runtime/state`
- Data directories: `~/.agr_ai_curation/data/pdf_storage`, `~/.agr_ai_curation/data/file_outputs`, `~/.agr_ai_curation/data/weaviate`

`scripts/install/install.sh --image-tag <tag>` can be used to pin the published backend/frontend/trace-review images to a specific release tag during installation.

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
- `BACKEND_IMAGE_TAG=smoke-20260310-final`
- `FRONTEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-frontend`
- `FRONTEND_IMAGE_TAG=smoke-20260310-final`
- `TRACE_REVIEW_BACKEND_IMAGE=public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend`
- `TRACE_REVIEW_BACKEND_IMAGE_TAG=latest`
- `TRACE_REVIEW_URL=http://trace_review_backend:8001`
- `TRACE_REVIEW_LANGFUSE_HOST=http://langfuse:3000`
- `TRACE_REVIEW_LANGFUSE_LOCAL_HOST=http://langfuse:3000`

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
