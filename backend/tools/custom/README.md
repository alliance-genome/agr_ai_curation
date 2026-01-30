# Custom Tools

Add your organization-specific tools here.

## Adding a Tool

1. Create a Python file (e.g., `my_tool.py`)
2. Implement with the `@function_tool` decorator
3. Restart the application
4. Reference by name in your agent's `agent.yaml`

## Example

```python
"""
My custom API tool.

Connects to our internal service for specialized lookups.
"""

from agents import function_tool

@function_tool(
    name_override="my_internal_api",
    description_override="Query our internal service for data"
)
async def my_internal_api(query: str, limit: int = 10) -> dict:
    """
    Query the internal API.

    Args:
        query: Search query
        limit: Maximum results

    Returns:
        Dict with results and metadata
    """
    # Your implementation here
    # Handle authentication, API calls, data transformation
    return {"results": [...], "total": 0}
```

## Best Practices

- Tools should handle their own data transformation
- Return clean, structured data (no raw API responses)
- Include good error handling
- Document parameters and return values
- Use async for I/O operations

## See Also

- `../_examples/` for complete template implementations
- `../core/` for reference implementations (read-only)
