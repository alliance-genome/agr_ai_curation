# Adding a New Tool

Step-by-step guide to adding a new tool that agents can use to interact with external systems.

> **Time**: 15-30 minutes
> **Prerequisites**: Python knowledge, understanding of the data source you're connecting

---

## Overview

Tools are Python functions decorated with `@function_tool` that agents call to interact with databases, APIs, and files. They live in `backend/src/lib/openai_agents/tools/`.

For an agent to use a tool, the tool must have a **tool binding** registered in `TOOL_BINDINGS` (in `catalog_service.py`). This binding declares how to resolve the tool at runtime and what execution context it needs.

```
backend/src/lib/openai_agents/tools/
  agr_curation.py        # AGR database access
  weaviate_search.py     # Document search
  rest_api.py            # REST API wrappers
  sql_query.py           # SQL query tool
  file_output_tools.py   # CSV/TSV/JSON output
  __init__.py            # Exports
```

---

## Step 1: Create the Tool File

Create `backend/src/lib/openai_agents/tools/my_tool.py`:

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

## Step 2: Register a Tool Binding

Add a resolver function and binding entry in `backend/src/lib/agent_studio/catalog_service.py`.

### For static tools (no runtime context needed):

```python
def _resolve_my_custom_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.my_tool import my_custom_tool
    return my_custom_tool

# Add to TOOL_BINDINGS dict:
TOOL_BINDINGS["my_custom_tool"] = {
    "binding": "static",
    "required_context": [],
    "resolver": _resolve_my_custom_tool,
}
```

### For context-dependent tools (need document_id, database_url, etc.):

```python
def _resolve_my_context_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.my_tool import create_my_tool
    return create_my_tool(
        database_url=context.database_url,
    )

TOOL_BINDINGS["my_context_tool"] = {
    "binding": "context_factory",
    "required_context": ["database_url"],
    "resolver": _resolve_my_context_tool,
}
```

The `required_context` list tells the system what execution context fields must be present. If any are missing at runtime, a clear error is raised.

---

## Step 3: Reference in Agent Configuration

Add the tool to your agent's `tool_ids` list. This can be done in:

**YAML (system agents)** -- `config/agents/my_agent/agent.yaml`:

```yaml
tools:
  - my_custom_tool
  - agr_curation_query  # Other existing tools
```

**Database (custom agents)** -- Update the `tool_ids` JSONB array:

```sql
UPDATE agents SET tool_ids = '["my_custom_tool", "agr_curation_query"]'
WHERE agent_key = 'my_agent';
```

---

## Step 4: Add Tool Documentation (Optional)

Add an entry to `TOOL_REGISTRY` in `catalog_service.py` for Agent Studio's Tool Inspector:

```python
TOOL_REGISTRY["my_custom_tool"] = {
    "name": "My Custom Tool",
    "description": "Search for [something] in [data source].",
    "category": "Database",
    "source_file": "backend/src/lib/openai_agents/tools/my_tool.py",
    "documentation": {
        "summary": "Searches [data source] for [entities].",
        "parameters": [
            {
                "name": "query",
                "type": "string",
                "required": True,
                "description": "The search query.",
            },
            {
                "name": "limit",
                "type": "integer",
                "required": False,
                "description": "Maximum results (default: 10).",
            },
        ],
    },
    "methods": None,
    "agent_methods": None,
}
```

---

## Step 5: Restart and Verify

```bash
# Restart backend to pick up new tool
docker compose restart backend

# Verify tool binding is resolved (check logs for errors)
docker compose logs backend | grep "my_custom_tool"
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
    ],
    "total": 1,
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

**Never raise exceptions** -- return error information in the response:

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
    from sqlalchemy import text
    from src.models.sql.database import SessionLocal

    try:
        with SessionLocal() as db:
            results = db.execute(
                text("SELECT * FROM :table WHERE name LIKE :query LIMIT :limit"),
                {"table": table, "query": f"%{query}%", "limit": limit}
            ).fetchall()

            return {
                "status": "success",
                "results": [dict(row._mapping) for row in results],
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

### Context-Factory Tool

For tools that need runtime context (document ID, database URL), use a factory pattern:

```python
def create_my_scoped_tool(database_url: str):
    """Create a tool bound to a specific database connection."""

    @function_tool(name_override="my_scoped_query")
    def my_scoped_query(query: str) -> dict:
        """Query a scoped database."""
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url)
        try:
            with engine.connect() as conn:
                result = conn.execute(text(query))
                return {"status": "success", "data": [dict(r._mapping) for r in result]}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    return my_scoped_query
```

Then register the binding with `required_context`:

```python
def _resolve_my_scoped_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.my_tool import create_my_scoped_tool
    return create_my_scoped_tool(context.database_url)

TOOL_BINDINGS["my_scoped_query"] = {
    "binding": "context_factory",
    "required_context": ["database_url"],
    "resolver": _resolve_my_scoped_tool,
}
```

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

## Testing Your Tool

### Unit Test

Create `backend/tests/unit/tools/test_my_tool.py`:

```python
import pytest
from unittest.mock import patch


def test_my_tool_success():
    from src.lib.openai_agents.tools.my_tool import my_custom_tool
    # For function_tool decorated functions, call the underlying function
    result = my_custom_tool(query="test")
    assert result["status"] == "success"
    assert "results" in result


def test_my_tool_empty_query():
    from src.lib.openai_agents.tools.my_tool import my_custom_tool
    result = my_custom_tool(query="")
    assert result["status"] == "success"
    assert result["results"] == []
```

### Manual Test in Agent

1. Add the tool to an agent's `tool_ids` in the database
2. Start the backend: `docker compose up -d backend`
3. Test via chat: "Search for [something] using the new tool"
4. Check logs: `docker compose logs backend | grep my_custom_tool`

---

## Checklist

- [ ] Tool function created in `backend/src/lib/openai_agents/tools/`
- [ ] `@function_tool` decorator applied
- [ ] Clear docstring for LLM tool selection
- [ ] Error handling returns dict (not raises)
- [ ] Resolver function added in `catalog_service.py`
- [ ] `TOOL_BINDINGS` entry added with correct `required_context`
- [ ] Tool added to agent's `tools` list in `agent.yaml` or `tool_ids` in DB
- [ ] `TOOL_REGISTRY` entry added (optional, for UI documentation)
- [ ] Backend restarted and tool verified in logs
- [ ] Unit test written

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Unknown tool binding" | Add entry to `TOOL_BINDINGS` in `catalog_service.py` |
| Tool not called by LLM | Improve docstring with clear "when to use" guidance |
| "requires execution context" error | Missing runtime context (document_id, database_url, etc.) -- check `required_context` in binding |
| Wrong parameters passed | Check type hints match expectations |
| Timeout errors | Add timeout parameter, increase limits |

---

## See Also

- [CONFIG_DRIVEN_ARCHITECTURE.md](./CONFIG_DRIVEN_ARCHITECTURE.md) -- Full architecture guide
- [ADDING_NEW_AGENT.md](./ADDING_NEW_AGENT.md) -- Creating agents that use tools
- [AGENTS_DEVELOPMENT_GUIDE.md](./AGENTS_DEVELOPMENT_GUIDE.md) -- Comprehensive development reference
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) -- Tool decorator reference
