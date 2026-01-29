# Tools Directory

This directory contains tool implementations that agents can use.

## Directory Structure

```
tools/
├── core/              # Generic utilities (ships with base)
├── custom/            # Organization-specific tools (add yours here)
├── _examples/         # Template tools (not loaded)
└── alliance_tools/    # Alliance-specific (copied during deployment)
```

## Loading Behavior

At startup, the system:
1. Scans `core/` and `custom/` directories
2. Imports all Python files
3. Registers functions decorated with `@function_tool`
4. Agents reference tools by name in their `agent.yaml`

**Note:** Directories starting with `_` are not loaded.

## Adding a Custom Tool

1. Create a Python file in `custom/` (e.g., `my_api_tool.py`)
2. Implement your tool function with the `@function_tool` decorator
3. Restart the application
4. Reference the tool by name in your agent's `agent.yaml`

See `_examples/` for template implementations.

## Tool Implementation Pattern

```python
from agents import function_tool

@function_tool(
    name_override="my_tool_name",
    description_override="What this tool does"
)
async def my_tool(param1: str, param2: int = 10) -> dict:
    """
    Tool docstring with Args and Returns sections.
    """
    # Implementation here
    # Handle your own data transformation
    # Return clean, structured data
    return {"result": "..."}
```

## Key Principle

Tools handle their own data transformation and return clean, structured data. Agents should not need to post-process tool outputs.
