# Scripts Directory

Utility scripts for development, validation, and operations.

## Directory Structure

```
scripts/
├── install/
│   └── lib/
│       ├── common.sh           # Shared installer helpers (colors/prompts/validation)
│       └── templates/
│           ├── env.standalone  # Authoritative standalone .env template
│           └── groups.standalone.yaml # Standalone groups mapping template
├── release/
│   └── prepare_publish_artifacts.sh # Build reproducible split package/env assets for publish-images.yml
├── create_agent.py              # Agent scaffolding CLI (see Agents Development Guide)
├── validate_registry.py         # Validate AGENT_REGISTRY consistency
├── validate_current_agents.py   # Validate all agents can be instantiated
├── tool_idea_triage.py          # Developer triage CLI for Agent Workshop tool requests
├── extract_identifier_prefixes.py  # Extract ID prefixes from Alliance API
├── refresh_prefixes_on_start.sh # Best-effort identifier prefix refresh (startup + manual)
├── maintenance_mode.sh          # Toggle maintenance mode banner
│
├── testing/
│   └── docker-test-compose.sh # Rootless-by-default wrapper for docker-compose.test.yml
│   └── run-tests.sh             # Docker Compose test runner
│   └── llm_provider_smoke_local.sh  # Local LLM provider smoke checks (health/contracts)
│   └── rerank_provider_smoke_local.sh  # Local rerank provider smoke across bedrock/local/none
│   └── file_output_storage_preflight.sh # Deployment-stage probe for export temp/output writeability
│   └── dev_release_smoke.py     # Deep dev-release smoke: upload, chat, custom flow, batch, optional rerank smoke, cleanup
│   └── abc_literature_live_smoke.py # ABC Literature stage smoke with ephemeral Cognito users and evidence JSON
│
└── utilities/
    ├── check_services.sh               # Health check all Docker services
    ├── cleanup_orphaned_pdf_records.py # Clean PostgreSQL records missing from Weaviate
    ├── pdfjs_find_probe.mjs            # Inspect raw PDF text, real PDF.js find internals, and whitespace-boundary drift
    ├── pdfjs_quote_benchmark.mjs       # Sample realistic quote-like passages from chunks and benchmark them against PDF.js
    ├── pdfjs_native_verifier_benchmark.py # Benchmark the frontend's native-highlight verifier against the 100-quote corpus
    └── pdf_text_matcher_bakeoff.py     # Compare Python fuzzy/local-alignment libraries against the same quote benchmark
```

### PDF Quote Matching Diagnostics

These utilities are useful when comparing backend quote text against the live PDF.js search corpus:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
PDF_PATH="${REPO_ROOT}/sample_fly_publication.pdf"

# Build a realistic quote benchmark from live chunk data
node scripts/utilities/pdfjs_quote_benchmark.mjs \
  --pdf "${PDF_PATH}" \
  --backend-url http://127.0.0.1:8000 \
  --document-id 64fa682e-a074-446c-821e-c4a605d102f0 \
  --sample-size 100 \
  --max-quotes-per-chunk 8 \
  --output /tmp/pdf-quote-benchmark-100.json

# Compare Python fuzzy/local-alignment libraries against the same benchmark
/tmp/pdf-match-bench-venv/bin/python scripts/utilities/pdf_text_matcher_bakeoff.py \
  --benchmark-report /tmp/pdf-quote-benchmark-100.json \
  --pdf "${PDF_PATH}" \
  --page-corpus /tmp/pdf-page-corpus.json \
  --output /tmp/pdf-text-matcher-bakeoff-100.json

# Measure the frontend's native PDF.js verifier thresholds against the same corpus
/tmp/pdf-match-bench-venv/bin/python scripts/utilities/pdfjs_native_verifier_benchmark.py \
  --benchmark-report /tmp/pdf-quote-benchmark-100-refreshed.json \
  --pdf "${PDF_PATH}" \
  --page-corpus /tmp/pdf-page-corpus.json \
  --output /tmp/pdfjs-native-verifier-benchmark-100.json
```

## Agent Development Tools

### create_agent.py

CLI scaffolding tool for creating new agents. Generates agent factory code and registry entries with validation against existing registries.

**Full documentation:** See `docs/developer/guides/AGENTS_DEVELOPMENT_GUIDE.md` - "Quick Start: CLI Scaffolding Tool" section.

```bash
# Preview what will be generated (no files modified)
docker compose exec backend python scripts/create_agent.py my_new_agent \
    --name "My New Agent" \
    --description "What this agent does" \
    --category Validation \
    --tools agr_curation_query \
    --icon "🔍" \
    --dry-run

# Create agent with interactive confirmation
docker compose exec backend python scripts/create_agent.py my_new_agent \
    --name "My New Agent" \
    --description "What this agent does" \
    --category Validation \
    --tools agr_curation_query \
    --icon "🔍"

# Skip confirmation prompt (for scripting)
docker compose exec backend python scripts/create_agent.py my_new_agent \
    --name "My New Agent" \
    --description "What this agent does" \
    --category Validation \
    --tools agr_curation_query \
    --icon "🔍" \
    --yes

# Show help with available options
docker compose exec backend python scripts/create_agent.py --help
```

**Flags:**
| Flag | Description |
|------|-------------|
| `--dry-run` | Preview generated code without creating files |
| `--yes, -y` | Skip interactive confirmation prompt |
| `--force` | Force creation even with tool validation warnings |
| `--create-prompt` | Show command to create database prompt |
| `--requires-document` | Agent requires document context |

**Features:**
- **Interactive confirmation** - Shows verbose preview and asks before creating files
- Validates agent_id format (snake_case)
- Validates category against existing AGENT_REGISTRY
- Validates tools against TOOL_REGISTRY
- Generates agent factory code following project patterns (database prompts, MOD rules support)
- Writes agent file to `backend/src/lib/openai_agents/agents/`
- Updates `agents/__init__.py` with export and `__all__`
- Inserts entry into AGENT_REGISTRY in catalog_service.py

### create_tool.py

CLI scaffolding tool for creating new `@function_tool` decorated functions.

```bash
# Preview what will be generated (no files modified)
python scripts/create_tool.py my_api_tool \
    --name "My API Tool" \
    --description "Queries the My API service" \
    --return-type "MyApiResult" \
    --params "query:str,limit:int=10" \
    --category "API" \
    --dry-run

# Create tool with interactive confirmation
python scripts/create_tool.py my_api_tool \
    --name "My API Tool" \
    --description "Queries the My API service" \
    --return-type "MyApiResult" \
    --params "query:str,limit:int=10"

# Skip confirmation prompt (for scripting)
python scripts/create_tool.py my_api_tool \
    --name "My API Tool" \
    --description "Queries the My API service" \
    --return-type "MyApiResult" \
    --params "query:str,limit:int=10" \
    --yes

# Show help
python scripts/create_tool.py --help
```

**Flags:**
| Flag | Description |
|------|-------------|
| `--dry-run` | Preview generated code without creating files |
| `--yes, -y` | Skip interactive confirmation prompt |
| `--force` | Overwrite existing tool and ignore type warnings |
| `--sync` | Generate synchronous function (default: async) |
| `--category` | Tool category for TOOL_OVERRIDES metadata |

**Features:**
- **Interactive confirmation** - Shows verbose preview and asks before creating files
- Validates tool_id format (snake_case)
- Validates parameter types (catches common typos like "strin" → "str")
- Checks for existing tool files (prevents accidental overwrites)
- Parses parameter definitions with types and defaults
- Generates Pydantic result model
- Generates `@function_tool` decorated async function
- Includes Langfuse tracing integration notes
- Writes tool file to `backend/src/lib/openai_agents/tools/`
- Updates `tools/__init__.py` with export and `__all__`
- Shows TOOL_OVERRIDES entry for catalog_service.py

### validate_registry.py

Validates AGENT_REGISTRY consistency - checks that all registered agents have valid factory functions, tools exist, and required fields are present.

```bash
docker compose exec backend python scripts/validate_registry.py
```

### validate_current_agents.py

Instantiates all registered agents to verify they can be created without errors. Useful after making changes to agent factories or dependencies.

```bash
docker compose exec backend python scripts/validate_current_agents.py
```

### tool_idea_triage.py

Developer triage queue for Agent Workshop `tool_idea_requests`.

Preferred execution path (Docker backend container):

```bash
# Show open queue (submitted/reviewed/in_progress)
docker compose exec backend python /app/scripts/tool_idea_triage.py queue --limit 25

# List only completed requests
docker compose exec backend python /app/scripts/tool_idea_triage.py list --status completed

# Update a request status + notes
docker compose exec backend python /app/scripts/tool_idea_triage.py update <request_uuid> \
  --status reviewed \
  --notes "Confirmed scope; estimating implementation."

# Mark request completed and link resulting tool key
docker compose exec backend python /app/scripts/tool_idea_triage.py update <request_uuid> \
  --status completed \
  --resulting-tool-key go_relationship_enrichment
```

Alternative execution path (host machine):
- Requires the backend Python dependencies installed in your local venv.

```bash
python scripts/tool_idea_triage.py queue --limit 25
```

## Infrastructure Scripts

### refresh_prefixes_on_start.sh

Best-effort helper to refresh identifier prefixes for CURIE validation.
This runs on backend container startup and can also be invoked manually.

```bash
# Manual run via dedicated compose profile
make prefix-refresh
```

### maintenance_mode.sh

Toggles maintenance mode which displays a banner in the UI warning users that the system is under maintenance.

```bash
# Enable maintenance mode
./scripts/maintenance_mode.sh enable

# Disable maintenance mode
./scripts/maintenance_mode.sh disable

# Check current status
./scripts/maintenance_mode.sh status
```

### extract_identifier_prefixes.py

Extracts valid identifier prefixes from curation-database SQL queries. Used to populate the prefix validation cache.

```bash
docker compose exec backend python scripts/extract_identifier_prefixes.py
```

Default output:
- `/runtime/state/identifier_prefixes/identifier_prefixes.json`

## Testing

### testing/run-tests.sh

Docker Compose test runner following the Unified Docker Compose Standard.
It now always tears down the isolated test stack on exit for non-`prepare`
commands, even when the test command itself fails.

```bash
# Run all tests
./scripts/testing/run-tests.sh all

# Run specific test type
./scripts/testing/run-tests.sh unit
./scripts/testing/run-tests.sh integration
./scripts/testing/run-tests.sh contract

# Build test image
./scripts/testing/run-tests.sh build
```

**Note:** For comprehensive testing documentation, see `TESTING_TODO.md` in the project root.

### testing/llm_provider_smoke_local.sh

Runs the local LLM provider smoke preflight checks and writes evidence JSON.

Checks:
- `/health`
- `/api/admin/health/llm-providers`
- `/api/agent-studio/models`
- derived structural check that provider-health `errors` is empty

```bash
# Run directly (defaults to http://localhost:8000)
./scripts/testing/llm_provider_smoke_local.sh

# Run against a custom backend URL
./scripts/testing/llm_provider_smoke_local.sh http://localhost:18000

# Or via Make target (sources ~/.agr_ai_curation/.env and ensures backend is up)
make smoke-llm-local
```

Outputs:
- `file_outputs/temp/llm_provider_smoke_local_<timestamp>.json`

### testing/rerank_provider_smoke_local.sh

Runs the local rerank provider smoke and writes evidence JSON.

Checks:
- `bedrock_cohere` backend startup without the local reranker service
- `local_transformers` backend startup with the `local-reranker` Compose profile
- `none` backend startup with reranking disabled
- `/api/admin/health/connections` contract for when the reranker service is
  actually required
- for `local_transformers`, the backend's effective `RERANKER_URL` matches the
  configured target resolved from exported env, then the local backend `.env`,
  defaulting to `http://reranker-transformers:8080`
- a real `rerank_chunks(...)` probe inside the backend container to prove that
  `bedrock_cohere` and `local_transformers` reorder results while `none`
  preserves retrieval order

```bash
# Run directly (defaults to http://localhost:8000)
./scripts/testing/rerank_provider_smoke_local.sh

# Run against a custom backend URL
./scripts/testing/rerank_provider_smoke_local.sh http://localhost:18000
```

Outputs:
- `file_outputs/temp/rerank_provider_smoke_local_<timestamp>.json`

### testing/trace_review_preflight.sh

Runs report-only TraceReview diagnostics before a trace review starts. It does
not start, stop, restart, SSH into, or mutate services.

What it checks:
- local TraceReview backend `/health` identity and `/health/preflight` availability
- selected Langfuse source (`remote` or `local`), credential presence, and health
- port/listener hints for the common `8001` TraceReview vs. review-proxy confusion
- production-readiness hints: VPN route to remote Langfuse, optional SSH TCP
  reachability, and non-secret environment presence

```bash
./scripts/testing/trace_review_preflight.sh --source remote

# If TraceReview is running on an issue-local port:
./scripts/testing/trace_review_preflight.sh \
  --backend-url http://127.0.0.1:8901 \
  --source local
```

Optional flags:
- `--backend-url <url>` to target a non-default TraceReview backend URL
- `--source remote|local` to match the TraceReview source selection
- `--ssh-host <host>` and `--ssh-port <port>` to TCP-probe production SSH reachability

Useful environment:
- `TRACE_REVIEW_PREFLIGHT_TIMEOUT_SECONDS=2` to shorten network probes
- `TRACE_REVIEW_PREFLIGHT_REQUIRE_PRODUCTION=true` to make production-readiness
  warnings hard failures
- `TRACE_REVIEW_BACKEND_HOST_PORT=8901` to select the TraceReview backend port
  when `--backend-url` is omitted
- `TRACE_REVIEW_PRODUCTION_SSH_HOST`, `TRACE_REVIEW_PRODUCTION_SSH_PORT`, and
  `TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE` for production SSH readiness checks

### testing/file_output_storage_preflight.sh

Runs a deployment-safe export-storage probe against the live backend container.
This is meant for release cutovers and hotfix verification when generated CSV/TSV/JSON
downloads must be proven writable before traffic is restored.

What it checks:
- direct write access to `outputs`, `temp/processing`, and `temp/failed`
- a real `FileOutputStorageService.save_output()` CSV round-trip
- JSON evidence written outside the app mount, under `/tmp` by default

```bash
./scripts/testing/file_output_storage_preflight.sh
```

Optional flags:
- `--service <name>` to target a different compose service name
- `EXPORT_STORAGE_PREFLIGHT_OUT_DIR=/tmp/custom-dir` to override the evidence directory

Evidence:
- `/tmp/agr_ai_curation_export_storage_preflight/file_output_storage_preflight_<timestamp>.json`

### testing/dev_release_smoke.py

Runs the deep deployed-backend smoke for dev release validation:

- checks the installed `openai-agents` package against the backend lockfile pin
- verifies backend health
- checks/wakes the PDF extraction worker
- uploads a real sample PDF through the backend API
- waits for processing completion
- verifies document download metadata
- loads the document into chat context
- asks one real OpenAI-backed question
- creates a temporary custom agent
- creates and executes a real flow over the SSE endpoint
- uploads a second document
- creates and validates a batch-compatible flow
- runs a real two-document batch and downloads the ZIP results
- cleans up temporary documents, flows, and custom agents
- writes evidence JSON

Typical usage on the dev host:

```bash
cd ~/agr_ai_curation
python3 scripts/testing/dev_release_smoke.py --base-url http://localhost:8000
```

Notes:

- The script auto-loads `TESTING_API_KEY` from `.env` when available.
- Default PDFs come from `backend/tests/fixtures/`.
- Use `--skip-chat`, `--skip-flow`, or `--skip-batch` to isolate one stage while debugging.
- Add `--include-rerank-provider-smoke` when you also want the local
  Bedrock-vs-local-vs-none rerank smoke. That stage remains opt-in because it
  restarts the local Compose backend.
- Evidence output:
  - `/tmp/agr_ai_curation_dev_release_smoke/dev_release_smoke_<timestamp>.json`
- Any PR that changes the `openai-agents` pin must pass the full smoke and add a
  PR body evidence line like:
  `SDK-Smoke-Evidence: dev_release_smoke PASS <evidence-link-or-path>`

Full local-stack coverage example:

```bash
python3 scripts/testing/dev_release_smoke.py \
  --base-url http://localhost:8000 \
  --include-rerank-provider-smoke
```

### testing/abc_literature_live_smoke.py

Runs the durable ABC Literature stage smoke for release evidence:

- creates one temporary authorized Cognito user and one temporary unauthorized
  control user through boto3 using the selected AWS profile
- gives the authorized user the configured Literature/MOD groups
- obtains request-local bearer tokens through Cognito admin auth
- runs `backend/tests/live_integration/test_abc_literature_live_smoke.py`
  against the real Literature stage API
- verifies `by_md5`, PMID/reference lookup, `show_all`, authorized
  `download_file`, and unauthorized `download_file` -> `403`
- deletes the temporary Cognito users in `finally`
- writes non-secret evidence JSON under `file_outputs/temp/`

Typical usage on the dev host:

```bash
python3 scripts/testing/abc_literature_live_smoke.py --aws-profile your-aws-profile
```

Useful environment/CLI overrides:

- `ABC_LITERATURE_SMOKE_AWS_PROFILE` / `--aws-profile`
- `ABC_LITERATURE_SMOKE_USER_POOL_ID` / `--user-pool-id`
- `ABC_LITERATURE_SMOKE_CLIENT_ID` / `--client-id`
- `ABC_LITERATURE_SMOKE_CLIENT_SECRET` / `--client-secret`, only if the
  Cognito app client requires one and the runner cannot discover it through
  `describe-user-pool-client`
- `ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS` / `--authorized-groups`
- `ABC_LITERATURE_SMOKE_EVIDENCE_DIR` / `--evidence-dir`
- `ABC_LITERATURE_SMOKE_PYTEST_TIMEOUT_SECONDS` / `--pytest-timeout-seconds`
- `ABC_LITERATURE_SMOKE_AWS_API_TIMEOUT_SECONDS` / `--aws-api-timeout-seconds`
- `ABC_LITERATURE_SMOKE_EVIDENCE_TAIL_LIMIT` / `--evidence-tail-limit`

Evidence output:

- `file_outputs/temp/abc_literature_live_smoke_<timestamp>.json`

Do not use `--keep-users` for release evidence. It is a debugging escape hatch
only; runs with retained users are marked `debug_keep_users` and exit nonzero.
The normal smoke deletes the temporary Cognito users and does not write tokens,
passwords, or Cognito client secrets into the evidence file.

### testing/abc_literature_ready_upload_smoke.py

Runs the durable end-to-end AI Curation upload smoke for an ABC Literature
READY fixture:

- authenticates as an existing test Cognito curator from local `.env` values
  using either username/password or an already-issued IdToken
- verifies the target backend is using real Cognito auth and an external
  document-source provider, not the local PDF fallback
- downloads the known ABC Literature source PDF fixture into a temporary
  directory and verifies its MD5 before upload
- posts the PDF to `/weaviate/documents/upload` using an `auth_token` cookie so
  the backend can forward the request-local curator token to ABC
- waits for provider Markdown ingestion to complete
- verifies source provenance, PDF-backed download-info, chunks, source Markdown
  download, and original-PDF download availability
- deletes the uploaded document in `finally`
- writes globally redacted, non-secret evidence JSON under `file_outputs/temp/`

Typical usage on a backend configured with `AUTH_PROVIDER=cognito`,
`ABC_LITERATURE_IMPORT_ENABLED=true`, and
`DOCUMENT_SOURCE_PROVIDER=abc_literature`:

```bash
python3 scripts/testing/abc_literature_ready_upload_smoke.py \
  --aws-profile your-aws-profile \
  --backend-base-url http://localhost:8000
```

Docker-first usage against a running Compose backend:

```bash
./scripts/testing/abc_literature_ready_upload_smoke_docker.sh
```

The Docker wrapper runs the smoke from the Compose `backend` image, builds a
temporary smoke-only env file plus a temporary AWS config containing only the
selected profile chain, mounts both read-only, and defaults the backend URL to
`http://backend:8000` so the runner talks to the backend service over the
Compose network. It also mounts `scripts/` into the one-off runner container so
production-style backend images that do not bake test scripts can still execute
the smoke, mounts `file_outputs/` so evidence persists on the host, and
overrides the one-off entrypoint to `python` so production startup/bootstrap
hooks are not run by the smoke container. Curator password, IdToken, and
Cognito client secrets are read from the mounted smoke env file rather than
passed as `docker compose run -e NAME=value` arguments. If
`ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN` is set, the wrapper skips
AWS credential setup because the runner does not need Cognito admin auth for
that mode.

Useful runner configuration follows. The environment-variable forms work for
both direct Python and Docker-wrapper runs. The CLI forms are options for the
direct Python runner; the Docker wrapper resolves its host-side AWS/token
preflight from environment and env-file settings before forwarding other
runner arguments. In particular, the Docker wrapper rejects
`--curator-id-token`: put
`ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN` in the uncommitted smoke
env file (or use `ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN` for that
wrapper) so token mode can skip AWS setup safely.

Environment/direct-Python CLI overrides:

- `ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL` / `--backend-base-url`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_LITERATURE_BASE_URL` /
  `--literature-base-url`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE` / `--env-file`, local
  uncommitted env file loaded for smoke defaults, default
  `${HOME}/.agr_ai_curation/.env`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_PROFILE` / `--aws-profile`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_DIR` for the Docker wrapper's source
  AWS config directory, default `${HOME}/.aws`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_COMPOSE_FILE` for the Docker wrapper's
  Compose file, default `docker-compose.yml`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_SERVICE` for the Docker wrapper's
  running backend service preflight, default `backend`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_SERVICE` for the Docker wrapper's
  runner image service, default `backend`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_BACKEND_BASE_URL` for the Docker
  wrapper's in-network backend URL, default `http://backend:8000`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_USER` for the one-off runner user,
  default the host UID:GID running the wrapper, so persisted evidence files stay
  writable by the checkout owner
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_USER_POOL_ID` / `--user-pool-id`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_ID` / `--client-id`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CLIENT_SECRET` / `--client-secret`, only if
  the Cognito app client requires one and the runner cannot discover it through
  `describe-user-pool-client`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_AUTHORIZED_GROUPS` / `--authorized-groups`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME` /
  `--curator-username`, existing test curator username
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD` /
  `--curator-password`, existing test curator password from local `.env`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN` /
  `--curator-id-token`, optional short-lived token for manual runs instead of
  username/password; the CLI form is direct-Python only
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_DIR` / `--evidence-dir`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_HTTP_TIMEOUT_SECONDS` /
  `--http-timeout-seconds`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_UPLOAD_TIMEOUT_SECONDS` /
  `--upload-timeout-seconds`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_PROCESSING_TIMEOUT_SECONDS` /
  `--processing-timeout-seconds`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_POLL_INTERVAL_SECONDS` /
  `--poll-interval-seconds`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_API_TIMEOUT_SECONDS` /
  `--aws-api-timeout-seconds`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_EVIDENCE_TAIL_LIMIT` /
  `--evidence-tail-limit`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_KNOWN_MD5` / `--known-md5`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_PMID` / `--pmid`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_REFERENCE` / `--reference`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_REFERENCEFILE_ID` /
  `--source-referencefile-id`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_CONVERTED_REFERENCEFILE_ID` /
  `--converted-referencefile-id`
- `ABC_LITERATURE_READY_UPLOAD_SMOKE_SOURCE_PDF_FILENAME` /
  `--source-pdf-filename`

Evidence output:

- `file_outputs/temp/abc_literature_ready_upload_smoke_<timestamp>.json`

Do not use `--keep-document` for release evidence. It is a debugging escape
hatch; successful runs with the uploaded document retained exit nonzero with a
`debug_keep_document` status. Normal runs delete the uploaded document and do
not write tokens, passwords, Cognito client secrets, or PDF contents into the
evidence file.

### testing/abc_literature_identifier_import_smoke.py

Runs the durable AI Curation identifier-import smoke for the same ABC
Literature READY fixture, but enters through the backend identifier endpoint
instead of uploading the PDF:

- authenticates as the existing test Cognito curator using the same local
  `.env` values as the READY upload smoke
- independently downloads the expected ABC source PDF and converted Markdown
  fixture for byte/hash comparison
- posts `{"identifiers": "<identifier>"}` to
  `/weaviate/documents/import/source-identifiers`
- verifies the import response is PDF-backed with `viewer_mode=local_pdf`, the
  expected source PDF artifact ID, and the expected converted Markdown artifact
  ID
- waits for processing completion, then reuses the READY smoke checks for
  provenance, PDF-backed download-info, chunks, source Markdown download,
  original-PDF download, cleanup, and secret-redacted evidence

Typical usage:

```bash
python3 scripts/testing/abc_literature_identifier_import_smoke.py \
  --aws-profile your-aws-profile \
  --backend-base-url http://localhost:8000
```

Docker-first usage against a running Compose backend:

```bash
./scripts/testing/abc_literature_identifier_import_smoke_docker.sh
```

The identifier Docker wrapper delegates to the READY upload wrapper's secure
temporary-env/AWS-profile machinery and runs
`abc_literature_identifier_import_smoke.py` inside the Compose backend image.
It accepts the same overrides as `abc_literature_ready_upload_smoke.py`, plus:

- `ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER` / `--identifier`, default
  `PMID:<ABC_LITERATURE_READY_UPLOAD_SMOKE_PMID>`

Evidence output:

- `file_outputs/temp/abc_literature_identifier_import_smoke_<timestamp>.json`

### utilities/check_services.sh

Health check script that verifies all Docker services are running and responding.

```bash
./scripts/utilities/check_services.sh
```

### utilities/cleanup_orphaned_pdf_records.py

Finds and removes PostgreSQL `PDFDocument` records that don't have corresponding entries in Weaviate. These "orphan" records can prevent users from re-uploading files.

```bash
# Dry-run (default) - shows what would be deleted
docker compose exec backend python scripts/utilities/cleanup_orphaned_pdf_records.py

# Actually delete orphaned records
docker compose exec backend python scripts/utilities/cleanup_orphaned_pdf_records.py --no-dry-run
```

**Note:** The application has automatic cleanup that runs on document list operations (`cleanup_phantom_documents()`), so this script is typically only needed for emergency admin access or bulk cleanup of legacy data.

## Running Scripts

Most Python scripts should be run inside the Docker container:

```bash
# Run a Python script in the backend container
docker compose exec backend python scripts/<script_name>.py

# Run a shell script from host
./scripts/<script_name>.sh
```

## Adding New Scripts

When creating new scripts:

1. **Agent/registry tools** - Place in `scripts/` root
2. **Testing scripts** - Place in `scripts/testing/`
3. **Utility/maintenance** - Place in `scripts/utilities/`
4. Make shell scripts executable: `chmod +x script_name.sh`
5. Add shebang: `#!/bin/bash` or `#!/usr/bin/env python3`
6. Update this README with usage documentation

**Prefer pytest tests** over standalone test scripts. Place tests in `backend/tests/` following existing patterns.
