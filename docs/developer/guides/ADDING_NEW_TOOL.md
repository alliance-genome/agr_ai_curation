# Adding a New Tool

Step-by-step guide to adding a new tool that agents can use to interact with external systems.

> **Time**: 15-30 minutes
> **Prerequisites**: Python knowledge, understanding of the data source you're connecting

---

## Overview

Tools are Python functions decorated with `@function_tool` that agents call to interact with databases, APIs, and files. They live in `backend/tools/`.

```
backend/tools/
├── core/              # Generic tools (ship with base product)
│   ├── weaviate_search.py
│   ├── file_output.py
│   └── ...
├── custom/            # Organization-specific tools
│   ├── agr_curation.py
│   ├── alliance_api.py
│   └── ...
└── __init__.py        # Exports all tools
```

---

## Step 1: Choose the Right Location

| Location | Use For |
|----------|---------|
| `backend/tools/core/` | Generic utilities (search, file I/O) that work anywhere |
| `backend/tools/custom/` | Organization-specific integrations (databases, APIs) |
| `backend/tools/_examples/` | Template implementations (not loaded, for reference) |
| `backend/tools/alliance_tools/` | Alliance-specific tools (copied during deployment) |

---

## Step 2: Create the Tool File

Start from an example template or create from scratch:

```bash
# Option 1: Copy from examples
cp backend/tools/_examples/example_rest_tool.py backend/tools/custom/my_tool.py

# Option 2: Create from scratch
touch backend/tools/custom/my_tool.py
```

Edit `backend/tools/custom/my_tool.py`:

```python
"""
My Custom Tool - [Brief description].

This tool provides [functionality] for agents.
"""

import logging
from typing import Optional, List
from agents import function_tool

logger = logging.getLogger(__name__)


@function_tool
def my_custom_tool(
    query: str,
    limit: int = 10,
    species: Optional[str] = None,
) -> dict:
    """
    Search for [something] in [data source].

    Use this tool when you need to [specific use case].

    Args:
        query: The search query (e.g., "gene symbol", "disease name")
        limit: Maximum number of results to return (default: 10)
        species: Optional species filter (e.g., "FB", "WB")

    Returns:
        Dictionary with:
        - status: "success" or "error"
        - results: List of matching items
        - total: Total count of matches
        - query_executed: The actual query that was run
    """
    try:
        # Your implementation here
        results = []

        # Example: Call an API or database
        # data = fetch_from_api(query, limit=limit)
        # results = [transform(item) for item in data]

        return {
            "status": "success",
            "results": results,
            "total": len(results),
            "query_executed": query,
        }

    except Exception as e:
        logger.error(f"my_custom_tool error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "results": [],
            "total": 0,
        }
```

---

## Step 3: Tool Auto-Discovery

Tools are **automatically discovered** at startup. The system:

1. Scans `core/` and `custom/` directories
2. Imports all Python files
3. Registers functions decorated with `@function_tool`

**No `__init__.py` exports needed.** Just create the file and restart.

> **Note:** Use `name_override` in the decorator to set the tool name that agents reference:
> ```python
> @function_tool(name_override="my_custom_tool")
> def _internal_function_name(...):
> ```

---

## Step 4: Restart the Application

```bash
# Restart backend to pick up new tool
docker compose restart backend

# Verify tool is loaded (check logs)
docker compose logs backend | grep "my_custom_tool"
```

---

## Step 5: Use in Agent Configuration

Reference the tool in your agent's `agent.yaml`:

```yaml
tools:
  - my_custom_tool
  - agr_curation_query  # Other existing tools
```

---

## Tool Guidelines

### Naming

- Use snake_case: `my_custom_tool`
- Be descriptive: `search_disease_ontology` not `do_search`
- Include action verb: `query_`, `search_`, `validate_`, `fetch_`

### Parameters

```python
@function_tool
def my_tool(
    # Required parameters first
    query: str,

    # Optional parameters with defaults
    limit: int = 10,
    include_synonyms: bool = True,
    species: Optional[str] = None,
) -> dict:
```

| Type | Use For |
|------|---------|
| `str` | Text input, queries, identifiers |
| `int` | Counts, limits, offsets |
| `bool` | Flags, options |
| `Optional[X]` | Truly optional parameters |
| `List[str]` | Multiple values |

### Docstrings

**Critical**: The docstring is what the LLM sees to decide when to call your tool.

```python
"""
Search for genes in the Alliance database.

Use this tool when:
- Looking up gene symbols or identifiers
- Validating that a gene exists
- Finding gene information by name

Do NOT use this tool for:
- Disease lookups (use disease_ontology_query instead)
- Chemical compounds (use chebi_query instead)

Args:
    query: Gene symbol or ID to search for (e.g., "dpp", "FBgn0000490")
    species: Optional species filter (e.g., "FB" for FlyBase)
    limit: Maximum results (default: 10, max: 100)

Returns:
    Dictionary with:
    - status: "success" or "error"
    - results: List of gene records with primary_id, symbol, species
    - total: Total matches found
"""
```

### Return Values

Always return a dictionary or Pydantic model:

```python
# Success case
return {
    "status": "success",
    "results": [
        {"id": "FB:FBgn0000490", "symbol": "dpp", "species": "FB"},
        {"id": "FB:FBgn0000001", "symbol": "eve", "species": "FB"},
    ],
    "total": 2,
    "query_executed": query,
}

# Error case - don't raise, return error dict
return {
    "status": "error",
    "error": "Database connection timeout",
    "results": [],
    "total": 0,
}

# Empty results - still success
return {
    "status": "success",
    "results": [],
    "total": 0,
    "message": "No matching genes found",
}
```

### Error Handling

**Never raise exceptions** - return error information in the response:

```python
try:
    data = fetch_data(query)
    return {"status": "success", "results": data}
except ConnectionError as e:
    logger.error(f"Connection error: {e}")
    return {"status": "error", "error": "Database unavailable", "results": []}
except ValueError as e:
    return {"status": "error", "error": f"Invalid query: {e}", "results": []}
except Exception as e:
    logger.exception("Unexpected error in my_tool")
    return {"status": "error", "error": str(e), "results": []}
```

---

## Common Tool Patterns

### Database Query Tool

```python
@function_tool
def query_my_database(
    query: str,
    table: str = "default_table",
    limit: int = 100,
) -> dict:
    """Query the database for matching records."""
    from backend.src.models.sql.database import SessionLocal

    try:
        with SessionLocal() as db:
            results = db.execute(
                text("SELECT * FROM :table WHERE name LIKE :query LIMIT :limit"),
                {"table": table, "query": f"%{query}%", "limit": limit}
            ).fetchall()

            return {
                "status": "success",
                "results": [dict(row) for row in results],
                "total": len(results),
            }
    except Exception as e:
        return {"status": "error", "error": str(e), "results": []}
```

### REST API Tool

```python
import httpx

@function_tool
def call_external_api(
    endpoint: str,
    params: Optional[dict] = None,
) -> dict:
    """Call an external REST API."""
    BASE_URL = os.environ.get("MY_API_URL", "https://api.example.com")

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{BASE_URL}/{endpoint}", params=params)
            response.raise_for_status()
            return {
                "status": "success",
                "data": response.json(),
            }
    except httpx.HTTPError as e:
        return {"status": "error", "error": f"API error: {e}"}
```

### File Output Tool

```python
import json
from pathlib import Path

@function_tool
def save_results_json(
    data: dict,
    filename: str,
) -> dict:
    """Save results to a JSON file."""
    output_dir = Path("/app/outputs")
    output_dir.mkdir(exist_ok=True)

    filepath = output_dir / f"{filename}.json"

    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        return {
            "status": "success",
            "filepath": str(filepath),
            "size_bytes": filepath.stat().st_size,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

### Validation Tool

```python
@function_tool
def validate_identifiers(
    identifiers: List[str],
    id_type: str = "gene",
) -> dict:
    """Validate a list of identifiers against the database."""
    valid = []
    invalid = []

    for id in identifiers:
        if check_exists(id, id_type):
            valid.append({"id": id, "valid": True})
        else:
            invalid.append({"id": id, "valid": False, "reason": "Not found"})

    return {
        "status": "success",
        "valid": valid,
        "invalid": invalid,
        "valid_count": len(valid),
        "invalid_count": len(invalid),
    }
```

---

## Testing Your Tool

### Unit Test

Create `backend/tests/unit/test_my_tool.py`:

```python
import pytest
from backend.tools.custom.my_tool import my_custom_tool


def test_my_tool_success():
    result = my_custom_tool(query="test")
    assert result["status"] == "success"
    assert "results" in result


def test_my_tool_empty_query():
    result = my_custom_tool(query="")
    assert result["status"] == "success"
    assert result["results"] == []


def test_my_tool_with_limit():
    result = my_custom_tool(query="test", limit=5)
    assert len(result["results"]) <= 5
```

### Integration Test

```python
@pytest.mark.integration
def test_my_tool_real_database():
    """Test against real database (requires connection)."""
    result = my_custom_tool(query="known_value")
    assert result["status"] == "success"
    assert len(result["results"]) > 0
```

### Manual Test in Agent

1. Create a test agent that uses your tool
2. Start the backend: `docker compose up -d backend`
3. Test via chat: "Search for [something] using the new tool"
4. Check logs: `docker compose logs backend | grep my_custom_tool`

---

## Async Tools

For I/O-bound operations, use async:

```python
import httpx

@function_tool
async def async_api_call(query: str) -> dict:
    """Async API call for better performance."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.example.com/search?q={query}")
        return {"status": "success", "data": response.json()}
```

---

## Environment Variables

Access configuration from environment:

```python
import os

@function_tool
def my_configured_tool(query: str) -> dict:
    """Tool with configurable settings."""
    api_key = os.environ.get("MY_API_KEY")
    base_url = os.environ.get("MY_API_URL", "https://api.example.com")

    if not api_key:
        return {"status": "error", "error": "MY_API_KEY not configured"}

    # Use api_key and base_url...
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Tool not found by agent | Check export in `__init__.py` files |
| "Invalid tool" error | Verify `@function_tool` decorator |
| LLM not calling tool | Improve docstring description |
| Wrong parameters passed | Check type hints match expectations |
| Timeout errors | Add timeout parameter, increase limits |

---

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](./CONFIG_DRIVEN_ARCHITECTURE.md) - Full architecture guide
- [ADDING_NEW_AGENT.md](./ADDING_NEW_AGENT.md) - Creating agents that use tools
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) - Tool decorator reference
