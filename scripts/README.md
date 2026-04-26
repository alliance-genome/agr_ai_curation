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
│
└── utilities/
    ├── check_services.sh               # Health check all Docker services
    ├── cleanup_orphaned_pdf_records.py # Clean PostgreSQL records missing from Weaviate
    ├── find_unused_files.py            # Static import analysis (AST-based)
    ├── pdfjs_find_probe.mjs            # Inspect raw PDF text, real PDF.js find internals, and whitespace-boundary drift
    ├── pdfjs_quote_benchmark.mjs       # Sample realistic quote-like passages from chunks and benchmark them against PDF.js
    ├── pdfjs_native_verifier_benchmark.py # Benchmark the frontend's native-highlight verifier against the 100-quote corpus
    ├── pdf_text_matcher_bakeoff.py     # Compare Python fuzzy/local-alignment libraries against the same quote benchmark
    ├── symphony_ensure_git_safety_tools.sh # Ensure Gitleaks + TruffleHog are installed in the Symphony VM user environment
    ├── symphony_git_safety_tool_versions.sh # Shared pinned versions/checksums for VM git safety scanners
    ├── symphony_install_vm_shell_shortcuts.sh # Install/update the managed ~/.bash_aliases block for Symphony VM Codex shortcuts
    ├── symphony_materialize_linear_auth.sh # Materialize low-risk Linear helper files inside the Symphony VM user home
    ├── symphony_print_incus_vm_cloud_init.sh # Print tracked cloud-init for rebuilding the Symphony Incus VM
    ├── symphony_rebuild_incus_vm.sh    # Rebuild the Symphony Incus VM shell from the tracked cloud-init source
    ├── symphony_ruff_tool_version.sh   # Shared pinned version/checksums for the VM-baked ruff install
    ├── symphony_sync_codex_auth_to_vm.sh # Sync host ~/.codex/auth.json into the Symphony Incus VM when it changes
    ├── symphony_vm_shell_shortcuts.sh  # Sourceable Codex helper functions (`co`, `comain`, `cor`) for Symphony VM shells
    ├── validate_unused_files.py        # Multi-tool unused file detection
    └── generate_coverage.sh            # Generate coverage data for validation
```

### PDF Quote Matching Diagnostics

These utilities are useful when comparing backend quote text against the live PDF.js search corpus:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
PDF_PATH="${REPO_ROOT}/sample_fly_publication.pdf"

# Build a realistic quote benchmark from live chunk data
node scripts/utilities/pdfjs_quote_benchmark.mjs \
  --pdf "${PDF_PATH}" \
  --backend-url http://10.222.162.167:8900 \
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

### utilities/symphony_local_db_tunnel_start.sh

Fire-and-forget DB tunnel lifecycle for Symphony Human Review Prep.
Starts the SSM tunnel and `socat` forwarder in the background, writes
`scripts/local_db_tunnel_env.sh`, and records state so it can be checked or stopped later.

```bash
# Start background tunnel for the current workspace
./scripts/utilities/symphony_local_db_tunnel_start.sh

# Check status
./scripts/utilities/symphony_local_db_tunnel_status.sh

# Stop and clean up
./scripts/utilities/symphony_local_db_tunnel_stop.sh
```

### utilities/symphony_curation_db_psql.sh

Thin launcher for real `psql` against the read-only AGR curation database from
a Symphony workspace. It starts or reuses the Symphony DB tunnel, sources the
workspace-local `scripts/local_db_tunnel_env.sh`, and runs `psql` with the
read-only credentials from `ai-curation/db/curation-readonly`.

```bash
# Probe the connection
./scripts/utilities/symphony_curation_db_psql.sh -- \
  -c "select current_database(), current_user;"

# Use normal psql flags for focused investigation
./scripts/utilities/symphony_curation_db_psql.sh -- \
  -c "select table_schema, table_name from information_schema.tables where table_schema not in ('pg_catalog','information_schema') order by 1,2 limit 50;"

# Check the tunnel without running psql
./scripts/utilities/symphony_curation_db_psql.sh --status
```

The helper does not parse or rewrite SQL. Keep curator-feedback investigations
focused, prefer `SELECT`/schema-inspection queries with `LIMIT`, and never print
or paste the generated tunnel env file because it contains credentials.

### utilities/symphony_human_review_prep.sh

One-command Human Review Prep for a Symphony workspace. It derives issue-specific
ports, starts the local curation DB tunnel, prepares a workspace-local Docker
config, stages dependency startup with retry/diagnostics before bringing up the
app services, rebuilds backend and frontend by default so the review stack
reflects the workspace branch, force-recreates the backend so fresh tunnel env
reaches the container, and prints review URLs plus health summaries.

```bash
# Prepare the current workspace for local review
./scripts/utilities/symphony_human_review_prep.sh

# Prepare a specific workspace with explicit review host
./scripts/utilities/symphony_human_review_prep.sh \
  --workspace-dir ~/.symphony/workspaces/agr_ai_curation/ALL-49 \
  --review-host 192.168.86.44
```

### utilities/symphony_main_sandbox.sh

Launches or cleans a dedicated latest-`main` sandbox checkout for manual work
inside the Symphony VM. It creates a fresh git worktree, runs the same review
prep wrapper used by Human Review Prep, automatically picks a free frontend /
backend port pair from the existing Symphony review-proxy ranges unless you
override them, and can tear the sandbox down when you are done.

```bash
# Launch or refresh the sandbox from origin/main
./scripts/utilities/symphony_main_sandbox.sh prepare

# Tear down the sandbox stack and remove the worktree
./scripts/utilities/symphony_main_sandbox.sh cleanup
```

### utilities/symphony_install_vm_shell_shortcuts.sh

Installs or refreshes the managed `~/.bash_aliases` block that sources the
tracked Symphony VM Codex shortcuts. This is the durable way to keep `co`,
`comain`, and `cor` working in rebuilt VM shells.

```bash
# Install/update the managed shell shortcut block
./scripts/utilities/symphony_install_vm_shell_shortcuts.sh
```

### utilities/symphony_materialize_linear_auth.sh

Materializes the low-risk Linear helper files under `~/.linear/` inside the
Symphony VM user environment. This is useful when shell helpers need the
traditional `~/.linear/api_key.txt` path even though Symphony itself already
loaded `LINEAR_API_KEY` from the vault at startup.

`.symphony/run.sh` now calls this automatically on startup, but you can also
run it manually to repair a live VM without restarting Symphony.

```bash
# Recreate ~/.linear/api_key.txt and project_slug.txt from the current env/vault
./scripts/utilities/symphony_materialize_linear_auth.sh
```

### utilities/symphony_ensure_git_safety_tools.sh

Ensures `gitleaks` and `trufflehog` are available in the Symphony VM user
environment. The script installs missing tools into `~/.local/bin`, which keeps
the current repo `pre-commit` hook's secret scanning active for the source
checkout, the main sandbox worktree, and issue workspaces that inherit the same
hooks. The installer uses pinned release versions and checksum verification; the
shared pins live in `utilities/symphony_git_safety_tool_versions.sh`.

```bash
# Verify the tools are already available
./scripts/utilities/symphony_ensure_git_safety_tools.sh --check

# Install missing tools into ~/.local/bin
./scripts/utilities/symphony_ensure_git_safety_tools.sh
```

### utilities/symphony_print_incus_vm_cloud_init.sh

Prints the tracked `cloud-init.user-data` payload for a fresh `symphony-main`
VM build. The generated payload creates the VM user and installs pinned
`gitleaks`, `trufflehog`, and `ruff` into `/usr/local/bin`.

```bash
./scripts/utilities/symphony_print_incus_vm_cloud_init.sh \
  --ssh-key-file ~/.ssh/id_ed25519.pub
```

### utilities/symphony_rebuild_incus_vm.sh

Rebuilds the base `symphony-main` Incus VM shell from the tracked cloud-init
source. This is the supported way to make fresh VM builds include the git
safety scanners by default rather than waiting for repo bootstrap.

```bash
# Preview only
./scripts/utilities/symphony_rebuild_incus_vm.sh \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --dry-run

# Rebuild the VM shell
./scripts/utilities/symphony_rebuild_incus_vm.sh \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --replace
```

### local_db_tunnel.sh

Interactive/manual version of the curation DB tunnel. This keeps the tunnel alive in the
foreground until you stop it, which is useful for ad hoc debugging outside Symphony.

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

Full local-stack coverage example:

```bash
python3 scripts/testing/dev_release_smoke.py \
  --base-url http://localhost:8000 \
  --include-rerank-provider-smoke
```

## Code Analysis Utilities

### utilities/find_unused_files.py

Static import analysis using Python AST. Traces imports starting from `backend/main.py` to find files that are never imported.

```bash
docker compose exec backend python scripts/utilities/find_unused_files.py
```

**Output:** List of potentially unused files with summary statistics.

**Caveat:** May flag dynamically imported files (e.g., via `importlib`) as unused.

### utilities/validate_unused_files.py

Multi-tool validation that layers 4 analysis techniques:

1. **Static Import Tracing** - AST-based (same as find_unused_files.py)
2. **Runtime Coverage Analysis** - Shows which files actually execute
3. **Test Collection** - Finds orphaned test files
4. **Config File Usage** - Grep-based search for references

```bash
# First, generate coverage data
./scripts/utilities/generate_coverage.sh

# Then run validation
docker compose exec backend python scripts/utilities/validate_unused_files.py
```

**Confidence Levels:**
- **HIGH** - Not imported AND 0% coverage (safe to remove)
- **MEDIUM** - Not imported but HAS coverage (may be dynamic imports)
- **LOW COVERAGE** - Imported but mostly dead code (<20% coverage)

### utilities/generate_coverage.sh

Generates pytest coverage data for use with validate_unused_files.py.

```bash
./scripts/utilities/generate_coverage.sh
```

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
