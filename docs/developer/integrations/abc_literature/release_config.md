# ABC Literature Release Configuration

Status: ALL-367 closeout note for the first ABC Literature import cutover.

This note records how to configure, health-check, disable, and smoke-test the
ABC Literature document-source integration without committing secrets.

## Runtime Switches

The default runtime remains the ordinary local PDF upload/extraction path:

```bash
DOCUMENT_SOURCE_PROVIDER=local_pdf
DOCUMENT_SOURCE_IMPORT_ENABLED=false
```

Enable the ABC Literature import path only on stacks that have the Add
Literature UI/API, request-local curator-token forwarding, group mapping, and
ABC Literature REST config in place:

```bash
DOCUMENT_SOURCE_PROVIDER=abc_literature
DOCUMENT_SOURCE_IMPORT_ENABLED=true
ABC_LITERATURE_API_BASE_URL=https://stage-literature-rest.alliancegenome.org
```

Use the production REST base URL only after release approval:

```bash
ABC_LITERATURE_API_BASE_URL=https://literature-rest.alliancegenome.org
```

`DOCUMENT_SOURCE_IMPORT_ENABLED=false` is the supported disable path. It does not
delete imported documents, change existing document provenance, or enable any
Literature mutation path. It leaves ordinary local PDF upload available through
the existing `local_pdf` flow.

No additional rollback machinery is required for this cutover. Reverting to
local PDF behavior means disabling ABC Literature import and selecting
`DOCUMENT_SOURCE_PROVIDER=local_pdf` for new imports.

## Auth Configuration

ABC Literature endpoint metadata/listing calls support these backend auth modes:

```bash
ABC_LITERATURE_AUTH_MODE=none
ABC_LITERATURE_AUTH_MODE=static_bearer
ABC_LITERATURE_AUTH_MODE=cognito_client_credentials
```

Required secrets by mode:

- `none`: no service credential. Request-local curator bearer tokens are still
  forwarded for authenticated user import/download calls.
- `static_bearer`: requires `ABC_LITERATURE_BEARER_TOKEN`.
- `cognito_client_credentials`: requires
  `ABC_LITERATURE_COGNITO_TOKEN_URL`,
  `ABC_LITERATURE_COGNITO_CLIENT_ID`,
  `ABC_LITERATURE_COGNITO_CLIENT_SECRET`, and
  `ABC_LITERATURE_COGNITO_SCOPE`.

Keep all token/client-secret values in uncommitted deployment env files or
secret stores. Do not put them in Git, Linear, Jira, smoke evidence, logs, or
Weaviate metadata.

Final artifact downloads intended to represent the logged-in curator should use
the request-local curator bearer token. The request context captures the
validated browser `auth_token` cookie only for normal Cognito users; API-key and
dev-mode requests do not forward arbitrary browser cookies as curator tokens.

## Groups And Access

Curator access is based on identity-provider group claims mapped through
`config/groups.yaml` / deployment overrides. The relevant claims are
`cognito:groups` and `groups`.

Source/main PDF metadata is the entitlement source for ABC-backed imports.
Converted Markdown rows may have null MOD metadata and must not grant access by
themselves.

This cutover does not add a separate "all MOD" or full-access provider-group
allowlist. Service credentials may be used for health/listing only when
configured, but AI Curation must still apply source-PDF-derived access before
showing, downloading, caching, ingesting, or serving restricted content.

## Timeouts And Batches

Document-source operational limits are env-configurable in `.env.example`:

- `DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS`
- `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS`
- `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS`
- `DOCUMENT_SOURCE_IMPORT_BATCH_LIMIT`
- ABC Literature live-smoke timeout/evidence knobs
- READY upload and identifier-import smoke timeout/evidence knobs

These values are passed through both development and production Compose files.

## Health And Readiness

`GET /weaviate/health` and `GET /weaviate/readiness` include document-source
provider status. The top-level `/health` route is lightweight liveness and does
not replace these provider/readiness checks.

Expected disabled/local behavior:

- `DOCUMENT_SOURCE_IMPORT_ENABLED=false`: document-source health is OK and
  reports disabled; local PDF upload remains active.
- `DOCUMENT_SOURCE_PROVIDER=local_pdf`: document-source health is OK and uses
  the local upload flow.

Expected enabled external-provider behavior:

- Missing required ABC config makes document-source health/readiness fail with
  sanitized provider-misconfigured details.
- ABC provider unavailability makes health/readiness fail with sanitized
  provider-unavailable details.
- Health checks use safe read/list/search endpoints only. They must not call
  `conversion_request`, `file_upload`, `reference/add`, or restricted
  `download_file` with a service credential.

## Release Smoke

Use the Docker READY upload smoke before enabling ABC Literature import for a
release stack:

```bash
scripts/testing/abc_literature_ready_upload_smoke_docker.sh
```

The smoke expects a configured running backend and a persistent test curator
credential supplied through an uncommitted env file such as
`/home/ctabone/.agr_ai_curation/.env`. Evidence is written under
`file_outputs/temp/` with secrets redacted.

The raw ABC Literature live smoke remains available for lower-level endpoint
contract checks:

```bash
python3 scripts/testing/abc_literature_live_smoke.py --aws-profile ctabone
```

## Disable Procedure

To disable ABC Literature for new imports:

```bash
DOCUMENT_SOURCE_IMPORT_ENABLED=false
DOCUMENT_SOURCE_PROVIDER=local_pdf
```

Then redeploy/restart the backend using the normal environment-specific
deployment process and verify:

- `/weaviate/health` reports document-source OK with `enabled=false` or
  `provider=local_pdf`.
- ordinary PDF upload still works.
- Add Literature identifier/provider import paths no longer use ABC Literature
  as the selected document-source provider.

Do not use disablement to introduce direct Literature uploads, reference
creation, TEI overwrite, service-token content imports, or a new local PDFX
fallback for known ABC papers waiting on provider conversion.
