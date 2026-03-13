# Independent Deployment

Last updated: 2026-03-13

## Scope

This guide covers standalone deployment of `agr_ai_curation` outside Alliance production infrastructure.

## Published images

The standalone stack now expects `trace_review_backend` to run from a published image instead of a local source build.

- Canonical trace review image repository: `public.ecr.aws/v4p5b7m9/agr-ai-curation-trace-review-backend`
- Standalone env defaults pin the repository with `TRACE_REVIEW_BACKEND_IMAGE` and `TRACE_REVIEW_BACKEND_IMAGE_TAG`
- The GitHub release workflow publishes `trace_review_backend` from `trace_review/backend/Dockerfile.prod` and tags each release as both `v<version>` and `latest`

## Trace review diagnostics service

`trace_review_backend` is part of the supported standalone diagnostics story and starts in the main Compose stack by default.

- The main backend reaches it through `TRACE_REVIEW_URL=http://trace_review_backend:8001`
- The service uses standard Docker bridge networking in standalone deployment
- Default standalone Langfuse connectivity is `http://langfuse:3000`, not `network_mode: host`
- `network_mode: host` remains a local-development convenience in `trace_review/docker-compose.yml`; it is not the supported standalone production model

## Required trace review environment

The authoritative standalone template is `scripts/install/lib/templates/env.standalone`.

Key values:

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
