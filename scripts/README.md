# Scripts Directory

Utility scripts for development, validation, and operations.

## Directory Structure

```
scripts/
├── create_agent.py              # Agent scaffolding CLI (see Agents Development Guide)
├── validate_registry.py         # Validate AGENT_REGISTRY consistency
├── validate_current_agents.py   # Validate all agents can be instantiated
├── extract_identifier_prefixes.py  # Extract ID prefixes from Alliance API
├── refresh_prefixes_on_start.sh # Auto-refresh prefixes on container start
├── maintenance_mode.sh          # Toggle maintenance mode banner
│
├── testing/
│   └── run-tests.sh             # Docker Compose test runner
│
└── utilities/
    ├── check_services.sh               # Health check all Docker services
    ├── cleanup_orphaned_pdf_records.py # Clean PostgreSQL records missing from Weaviate
    ├── find_unused_files.py            # Static import analysis (AST-based)
    ├── validate_unused_files.py        # Multi-tool unused file detection
    └── generate_coverage.sh            # Generate coverage data for validation
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

## Infrastructure Scripts

### refresh_prefixes_on_start.sh

Called automatically on backend container startup. Fetches current identifier prefixes from the Alliance API and caches them for the prefix validation tools.

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

Extracts valid identifier prefixes from the Alliance of Genome Resources API. Used to populate the prefix validation cache.

```bash
docker compose exec backend python scripts/extract_identifier_prefixes.py
```

## Testing

### testing/run-tests.sh

Docker Compose test runner following the Unified Docker Compose Standard.

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
