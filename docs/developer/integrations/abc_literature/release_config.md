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

### Machine readers

AI Curation services that call ABC without a real curator login authenticate
only with a machine reader: `ABC_LITERATURE_AUTH_MODE=cognito_client_credentials`
with a dedicated read-only Cognito app client that holds the
`abc-literature/read` scope. The rule is:

- one dedicated reader client per environment (for example the DEV benchmark
  resolver's "AI Curation Benchmark Resolver Dev ABC Reader"), with its own
  client secret;
- never shared between environments, and never reused by another service;
- never a user account. ABC deliberately rejects user-session access tokens and
  accepts user ID tokens only from its allow-listed app clients, so a
  username/password sign-in cannot authenticate a service to ABC.

ABC grants a client-credentials token access to every file. AI Curation is
therefore responsible for restricting what each request may import: see
[Groups And Access](#groups-and-access).

Final artifact downloads intended to represent the logged-in curator should use
the request-local curator bearer token. The request context captures the
validated browser `auth_token` cookie only for normal Cognito users; API-key
requests cannot donate an arbitrary browser cookie.

Login-free development has no curator login, so development imports use the
environment's machine reader instead: set
`ABC_LITERATURE_AUTH_MODE=cognito_client_credentials` with that environment's
reader client. The backend obtains and caches the reader bearer
(`DOCUMENT_SOURCE_DEV_READER_REFRESH_SKEW_SECONDS`) and forwards it to ABC
Literature in place of a curator token. The bearer carries no curator identity:
provider groups come from the development user's own claims, which are set by
`DEV_USER_GROUPS`, and document ownership remains attached to `dev-user-123`.
This path is active only when effective dev mode, external import, and a
non-local provider are all enabled. Any other ABC auth mode makes development
imports fail with a sanitized "authentication unavailable" error; nothing falls
back to unauthenticated access.

## Groups And Access

Curator access is based on identity-provider group claims mapped through
`config/groups.yaml` / deployment overrides. The relevant claims are
`cognito:groups` and `groups`.

Source/main PDF metadata is the entitlement source for ABC-backed imports. It
follows ABC's `download_file` rule: a source PDF is open to every caller when
its reference is open access; to any caller with at least one authorized group
when any of its `referencefile_mods` rows has a null MOD (even if other rows
name MODs); otherwise only to callers in the MODs its rows name. A file with no
usable rows, or whose reference record shows a license with an unset
open-access flag, is denied. Converted Markdown rows may have null MOD
metadata and must not grant access by themselves. See
`api_contract_verification.md` for the full table.

This cutover does not add a separate "all MOD" or full-access provider-group
allowlist. A machine reader sees every ABC file, so AI Curation applies
source-PDF-derived access from the requesting user's groups (for login-free
development, `DEV_USER_GROUPS`) before it downloads or ingests a source PDF:
identifier imports, checksum imports, and benchmark conversions select only a
source PDF the user's groups authorize under the rule above, and a user it does
not authorize gets an access-denied result with nothing downloaded.

The default login-free identity (`DEV_USER_GROUPS` empty) carries only the
`developers` claim, which maps to no configured group, so like an ABC caller
with no MOD access it can import open-access papers only. Set
`DEV_USER_GROUPS` (for example `DEV_USER_GROUPS=WB`) to a group ID from
`config/groups.yaml` to import no-MOD papers and that group's restricted
papers.

Derived artifacts follow the bearer that reads them. Every request records its
bearer kind explicitly: a logged-in curator's own token is `curator`; the
development reader and the benchmark conversion's application credential are
`service`.

- `curator`: ABC enforces the curator's access on every `download_file`, so
  converted Markdown and figure-metadata sidecars may be selected at reference
  level as before.
- `service`: ABC lets a machine reader download every file, so AI Curation uses
  a converted Markdown or figure-metadata artifact only when ABC binds it to the
  selected, authorized source PDF (nested under that PDF, so
  `parent_artifact_id` is its ID). Reference-level artifacts are never used and
  provider conversion is never requested or awaited. Without bound converted
  Markdown, AI Curation parses the authorized source PDF itself with PDFX: an
  identifier import downloads and parses that PDF, a checksum upload parses the
  uploaded PDF, and a benchmark conversion parses the selected PDF. Conversion
  polling and figure-sidecar discovery apply the same binding.

## Timeouts And Batches

Document-source operational limits are env-configurable in `.env.example`:

- `DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS`
- `DOCUMENT_SOURCE_DEV_READER_REFRESH_SKEW_SECONDS`
- `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS`
- `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS`
- `DOCUMENT_SOURCE_DEAD_WAIT_POLLS`
- `DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS`
- `DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS`
- `DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS`
- `DOCUMENT_SOURCE_MAX_POLLS_PER_WAIT`
- `DOCUMENT_SUPPLEMENT_ADD_RESERVE_SECONDS`
- `DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS`
- `DOCUMENT_SOURCE_IMPORT_BATCH_LIMIT`
- ABC Literature live-smoke timeout/evidence knobs
- READY upload and identifier-import smoke timeout/evidence knobs

**`DOCUMENT_SOURCE_MAX_POLLS_PER_WAIT` (default 400) is our own polling cap,
not a protection for ABC.** It bounds the listing reads one wait makes (plus the
main wait's two conversion requests); start-up refuses wait settings over it.
ABC recycles each API worker after about 1,000 requests from all of its clients,
and an on-demand conversion runs inside the worker that accepted it, so no
per-wait number can keep a conversion from being ended by a recycle; downloads
and listings are not counted.

**Operators: `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS` depends on an ABC rule.**
Before the deadline of a main paper's own on-demand request (the wait is timed
from that request, also for later papers of a batch), AI Curation asks ABC once
more why the main text has not appeared, and never after it. ABC answers a
failed conversion with that failure, without converting again, for one hour
after it failed. Keep the deadline at 3,600 seconds or less: above it, that late request can make ABC start a new
conversion job (main text and pending supplements) for a paper that failed
early. Nothing in AI Curation refuses a longer deadline.

**Start-up check (KANBAN-2191 / ALL-1725):** start-up refuses
`DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS` at or above
`DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS` minus `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS`,
naming the settings: the main paper's end request must be answerable before the
deadline. With the pinned deadline of 600 and a poll of 30, any request timeout
below 570 passes; the default 10 passes at every deadline the poll check allows.
The wait's timeout message ("Provider conversion exceeded {n} seconds") gives the
whole seconds actually waited since the paper's own request.

**Release step (unchanged from KANBAN-1976 P7w):** production's prod-state
override and dev's `.env` files still pin `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS=2`
and `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS=600`. Remove both pins (or set
30 / 3600) in `.env` and every private override before pulling or starting the
release image, render the config with each environment's exact Compose
invocation, and check the start-up line "Document-source wait settings: ..."
shows 30 and 3600. A pinned poll of 2 makes start-up refuse; a pinned deadline of
600 silently keeps the old 10-minute wait.

General provider limits are passed through both Compose files. The development
reader renewal skew is intentionally passed through development Compose only;
production Compose continues to force `DEV_MODE=false`.

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
- Renewable dev mode obtains the cached validated token pair and performs an
  authenticated lookup for a valid absent MD5 with the access token. Cognito
  failures and provider authorization failures make health and readiness
  unhealthy.
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
`~/.agr_ai_curation/.env`. Evidence is written under
`file_outputs/temp/` with secrets redacted.

The raw ABC Literature live smoke remains available for lower-level endpoint
contract checks:

```bash
python3 scripts/testing/abc_literature_live_smoke.py --aws-profile your-aws-profile
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
creation, service-token content imports, or a new local PDFX
fallback for known ABC papers waiting on provider conversion.
