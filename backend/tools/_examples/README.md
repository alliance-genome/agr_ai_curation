# Example Tools (Templates)

This directory contains tool templates for reference. These are **not loaded** at runtime (underscore prefix).

## Usage

To create a new tool from these templates:

1. Copy a template file to `../custom/your_tool.py`
2. Rename the function and update all placeholders
3. Implement your logic
4. Restart the application

## Templates

| Template | Description |
|----------|-------------|
| `example_rest_tool.py` | REST API integration template |
| `example_db_tool.py` | Database query template |

## Tool Anatomy

Every tool needs:

1. **Module docstring** - Explains what the tool does
2. **@function_tool decorator** - Registers the tool with name and description
3. **Async function** - The implementation (use async for I/O)
4. **Type hints** - For parameters and return value
5. **Docstring** - With Args and Returns sections

## Common Patterns

### Authentication
```python
# Load from environment
api_key = os.getenv("MY_API_KEY")
```

### Error Handling
```python
try:
    response = await client.get(url)
    response.raise_for_status()
except httpx.HTTPError as e:
    return {"error": str(e), "results": []}
```

### Data Transformation
```python
# Transform API response to clean structure
results = [
    {"id": item["id"], "name": item["display_name"]}
    for item in raw_response["data"]
]
```
