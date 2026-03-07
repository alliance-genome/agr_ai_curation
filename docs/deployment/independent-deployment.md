# Independent Deployment

Last updated: 2026-03-07

## Scope

This guide covers standalone deployment of `agr_ai_curation` outside Alliance production infrastructure.

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
