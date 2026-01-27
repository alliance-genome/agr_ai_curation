"""
Tool introspection utility for extracting metadata from @function_tool decorated functions.

Extracts:
- Function name -> tool_id
- Docstring -> description
- Type hints -> parameters with types
- Pydantic model -> parameter descriptions (if available)

Handles both:
- Raw Python functions
- FunctionTool objects from @function_tool decorator
"""
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union, get_type_hints


@dataclass
class ToolMetadata:
    """Extracted metadata from a tool function."""
    name: str
    description: str
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    return_type: Optional[str] = None
    category: Optional[str] = None
    source_file: Optional[str] = None


def introspect_tool(func: Union[Callable, Any]) -> ToolMetadata:
    """
    Extract metadata from a @function_tool decorated function or FunctionTool object.

    Args:
        func: The decorated function, wrapper, or FunctionTool object

    Returns:
        ToolMetadata with extracted information
    """
    # Check if this is a FunctionTool object (from @function_tool decorator)
    if hasattr(func, 'params_json_schema') and hasattr(func, 'description'):
        return _introspect_function_tool(func)

    # Fall back to standard function introspection
    return _introspect_raw_function(func)


def _introspect_function_tool(tool: Any) -> ToolMetadata:
    """
    Extract metadata from a FunctionTool object.

    FunctionTool objects have:
    - name: str
    - description: str
    - params_json_schema: dict (JSON Schema for parameters)
    """
    name = getattr(tool, 'name', 'unknown')
    description = getattr(tool, 'description', '') or ''

    # Extract parameters from JSON schema
    parameters = {}
    schema = getattr(tool, 'params_json_schema', {}) or {}
    properties = schema.get('properties', {})
    required = set(schema.get('required', []))

    for param_name, param_schema in properties.items():
        param_info = {
            "type": param_schema.get('type', 'any'),
            "required": param_name in required,
        }

        if 'default' in param_schema:
            param_info["default"] = param_schema['default']

        if 'description' in param_schema:
            param_info["description"] = param_schema['description']

        parameters[param_name] = param_info

    return ToolMetadata(
        name=name,
        description=description,
        parameters=parameters,
        return_type=None,  # Not available in FunctionTool
        source_file=None,  # Not easily accessible
    )


def _introspect_raw_function(func: Callable) -> ToolMetadata:
    """
    Extract metadata from a raw Python function.

    Uses inspect and type hints to extract information.
    """
    # Handle wrapped functions
    actual_func = getattr(func, '__wrapped__', func)

    # Get function name
    name = getattr(func, '__name__', None)
    if hasattr(func, 'name'):
        name = func.name
    if not name:
        name = actual_func.__name__

    # Get docstring
    description = inspect.getdoc(actual_func) or ""
    if description:
        description = description.split('\n\n')[0].split('\n')[0]

    # Get type hints safely
    hints = _safe_get_type_hints(actual_func)

    # Get parameters from signature
    parameters = {}
    try:
        sig = inspect.signature(actual_func)

        for param_name, param in sig.parameters.items():
            if param_name in ['self', 'cls']:
                continue

            param_info = {
                "type": _python_type_to_json_type(hints.get(param_name)),
                "required": param.default is inspect.Parameter.empty,
            }

            if param.default is not inspect.Parameter.empty:
                param_info["default"] = param.default

            parameters[param_name] = param_info

    except (ValueError, TypeError):
        pass

    # Get return type
    return_type = None
    if 'return' in hints:
        return_type = _python_type_to_json_type(hints['return'])

    # Get source file
    source_file = None
    try:
        source_file = inspect.getfile(actual_func)
    except (TypeError, OSError):
        pass

    return ToolMetadata(
        name=name,
        description=description,
        parameters=parameters,
        return_type=return_type,
        source_file=source_file,
    )


def _safe_get_type_hints(func: Callable) -> Dict[str, Any]:
    """
    Safely get type hints from a function.

    get_type_hints() can fail on decorated functions when type names
    aren't in the function's global namespace. Fall back to __annotations__.
    """
    try:
        return get_type_hints(func)
    except (NameError, TypeError, AttributeError):
        annotations = getattr(func, '__annotations__', {})
        result = {}
        for name, hint in annotations.items():
            if isinstance(hint, str):
                type_map = {'str': str, 'int': int, 'float': float, 'bool': bool}
                result[name] = type_map.get(hint, hint)
            else:
                result[name] = hint
        return result


def _python_type_to_json_type(py_type: Any) -> str:
    """Convert Python type hint to JSON schema type."""
    if py_type is None:
        return "any"

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    origin = getattr(py_type, '__origin__', None)
    if origin is not None:
        if origin is list:
            return "array"
        if origin is dict:
            return "object"

    return type_map.get(py_type, "string")
