#!/usr/bin/env python3
"""
Tool scaffolding CLI.

Generates @function_tool decorated functions from command-line arguments.

Usage:
    ./scripts/create_tool.py my_api_tool \
        --name "My API Tool" \
        --description "Queries the My API service" \
        --return-type "MyApiResult" \
        --params "query:str,limit:int=10"
"""
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class ToolParam:
    """A tool parameter with type and optional default."""
    name: str
    param_type: str
    default: Optional[str] = None
    description: str = ""

    def to_signature(self) -> str:
        """Generate function signature for this parameter."""
        if self.default is not None:
            return f"{self.name}: {self.param_type} = {self.default}"
        return f"{self.name}: {self.param_type}"

    def to_docstring(self) -> str:
        """Generate docstring entry for this parameter."""
        desc = self.description or f"The {self.name} parameter"
        return f"        {self.name}: {desc}"


@dataclass
class NewToolInput:
    """Input configuration for creating a new tool."""
    tool_id: str
    name: str
    description: str
    return_type: str
    params: List[ToolParam] = field(default_factory=list)
    category: str = "General"
    is_async: bool = True


# Common Python types for validation
COMMON_TYPES = {
    "str", "int", "float", "bool", "list", "dict", "tuple", "set",
    "List", "Dict", "Tuple", "Set", "Optional", "Any", "Union",
    "List[str]", "List[int]", "Dict[str, Any]", "Optional[str]", "Optional[int]",
}


def get_tools_dir() -> Path:
    """Get the tools directory path."""
    return Path(__file__).parent.parent / "backend" / "src" / "lib" / "openai_agents" / "tools"


def validate_tool_id(tool_id: str) -> None:
    """Validate tool ID format (snake_case)."""
    if not re.match(r'^[a-z][a-z0-9_]*$', tool_id):
        raise ValueError(
            f"Invalid tool_id '{tool_id}'. Must be lowercase letters, "
            "numbers, underscores, and start with a letter."
        )


def check_tool_exists(tool_id: str) -> bool:
    """Check if tool file already exists."""
    tools_dir = get_tools_dir()
    tool_file = tools_dir / f"{tool_id}.py"
    return tool_file.exists()


def validate_param_type(param_type: str) -> List[str]:
    """Validate parameter type. Returns list of warnings (empty if valid)."""
    warnings = []

    # Strip whitespace
    clean_type = param_type.strip()

    # Check for common typos
    if clean_type.lower() in {"string", "integer", "boolean", "strin", "intr"}:
        warnings.append(f"Type '{param_type}' looks like a typo. Did you mean 'str', 'int', or 'bool'?")

    # Check if it's a known common type (simple check)
    base_type = clean_type.split("[")[0]  # Get base type from generics
    if base_type not in COMMON_TYPES and not base_type[0].isupper():
        warnings.append(f"Type '{param_type}' is not a common Python type. Verify it's correct.")

    return warnings


def _split_respecting_brackets(s: str, delimiter: str = ",") -> List[str]:
    """Split string by delimiter, respecting brackets.

    Handles commas inside brackets like Dict[str, Any].
    """
    result = []
    current = []
    bracket_depth = 0

    for char in s:
        if char == "[":
            bracket_depth += 1
            current.append(char)
        elif char == "]":
            bracket_depth -= 1
            current.append(char)
        elif char == delimiter and bracket_depth == 0:
            result.append("".join(current))
            current = []
        else:
            current.append(char)

    # Don't forget the last segment
    if current:
        result.append("".join(current))

    return result


def parse_params(params_str: str) -> List[ToolParam]:
    """Parse parameter string into ToolParam list.

    Format: "name:type,name:type=default,..."
    Examples:
        "query:str,limit:int=10"
        "gene_id:str,include_orthologs:bool=False"
        "items:List[str],mapping:Dict[str, Any]"
    """
    if not params_str or not params_str.strip():
        return []

    params = []
    # Use bracket-aware splitting to handle types like Dict[str, Any]
    for param_def in _split_respecting_brackets(params_str, ","):
        param_def = param_def.strip()
        if not param_def:
            continue

        # Check for default value
        default = None
        if "=" in param_def:
            param_def, default = param_def.rsplit("=", 1)
            default = default.strip()

        # Parse name:type
        if ":" not in param_def:
            raise ValueError(f"Invalid parameter format '{param_def}'. Use 'name:type'")

        name, param_type = param_def.split(":", 1)
        name = name.strip()
        param_type = param_type.strip()

        if not name or not param_type:
            raise ValueError(f"Invalid parameter '{param_def}'")

        params.append(ToolParam(
            name=name,
            param_type=param_type,
            default=default,
        ))

    return params


def generate_result_model(config: NewToolInput) -> str:
    """Generate Pydantic result model code."""
    return f'''class {config.return_type}(BaseModel):
    """Result from {config.name}."""
    status: str
    data: Any = None
    message: Optional[str] = None
'''


def generate_tool_function(config: NewToolInput) -> str:
    """Generate the @function_tool decorated function."""
    # Build parameter signature
    param_sigs = [p.to_signature() for p in config.params]
    params_str = ",\n    ".join(param_sigs) if param_sigs else ""

    # Build docstring params
    docstring_params = "\n".join(p.to_docstring() for p in config.params)
    if docstring_params:
        docstring_params = f"\n\n    Args:\n{docstring_params}"

    # Async or sync
    async_prefix = "async " if config.is_async else ""
    await_prefix = "await " if config.is_async else ""

    return f'''@function_tool
{async_prefix}def {config.tool_id}(
    {params_str}
) -> {config.return_type}:
    """
    {config.description}{docstring_params}

    Returns:
        {config.return_type} with status and data
    """
    # NOTE: For Langfuse tracing integration, wrap async operations with:
    #   from ..langfuse_client import langfuse_context
    #   with langfuse_context.observe(name="{config.tool_id}") as span:
    #       span.update(input={{"params": ...}})
    #       result = await your_operation()
    #       span.update(output=result)

    logger.info(f"[{config.name}] Called with params: {{{', '.join(f'{p.name}={{{p.name}}}' for p in config.params)}}}")

    try:
        # TODO: Implement tool logic here
        result_data = {{"placeholder": "Implement your tool logic"}}

        return {config.return_type}(
            status="success",
            data=result_data,
        )

    except ValueError as e:
        # Handle validation/input errors
        logger.warning(f"[{config.name}] Validation error: {{e}}")
        return {config.return_type}(
            status="error",
            message=str(e),
        )
    except Exception as e:
        # Catch-all for unexpected errors - consider using more specific exceptions
        # in production code to avoid masking bugs
        logger.error(f"[{config.name}] Unexpected error: {{e}}", exc_info=True)
        return {config.return_type}(
            status="error",
            message=f"Internal error: {{type(e).__name__}}",
        )
'''


def generate_tool_file(config: NewToolInput) -> str:
    """Generate complete tool module code."""
    result_model = generate_result_model(config)
    tool_function = generate_tool_function(config)

    return f'''"""
{config.name} tool for OpenAI Agents SDK.

{config.description}
"""
import logging
from typing import Optional, List, Any

from pydantic import BaseModel
from agents import function_tool

logger = logging.getLogger(__name__)


{result_model}

{tool_function}
'''


def write_tool_file(config: NewToolInput, code: str) -> Optional[Path]:
    """Write the tool file to the tools directory.

    Returns the path of the written file, or None on error.
    """
    tools_dir = get_tools_dir()
    tool_file = tools_dir / f"{config.tool_id}.py"

    try:
        tool_file.write_text(code)
        print(f"Created: {tool_file}")
        return tool_file
    except OSError as e:
        print(f"Error writing tool file {tool_file}: {e}", file=sys.stderr)
        return None


def update_tools_init(config: NewToolInput) -> None:
    """Update tools/__init__.py to export the new tool."""
    tools_dir = get_tools_dir()
    init_file = tools_dir / "__init__.py"

    if not init_file.exists():
        print(f"Warning: {init_file} not found, skipping __init__.py update")
        return

    content = init_file.read_text()

    # Add import line
    import_line = f"from .{config.tool_id} import {config.tool_id}"

    if import_line in content:
        print(f"Import already exists in tools/__init__.py")
        return

    # Find a good place to insert (after existing tool imports)
    lines = content.split("\n")
    import_insert_idx = len(lines)  # Default to end

    for i, line in enumerate(lines):
        if line.startswith("from .") and "import" in line:
            import_insert_idx = i + 1  # Insert after last import

    lines.insert(import_insert_idx, import_line)

    # Also update __all__ if it exists
    all_updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("__all__"):
            # Find the closing bracket
            for j in range(i, min(i + 50, len(lines))):
                if "]" in lines[j]:
                    # Insert before the closing bracket
                    indent = "    "
                    new_entry = f'{indent}"{config.tool_id}",'
                    if config.tool_id not in content:
                        if lines[j].strip() == "]":
                            lines.insert(j, new_entry)
                        else:
                            lines[j] = lines[j].replace("]", f"\n{new_entry}\n]")
                        all_updated = True
                    break
            break

    try:
        init_file.write_text("\n".join(lines))
        print(f"Updated: {init_file}")
        if all_updated:
            print(f"  - Added {config.tool_id} to __all__")
    except OSError as e:
        print(f"Error writing {init_file}: {e}", file=sys.stderr)


def generate_tool_override_entry(config: NewToolInput) -> str:
    """Generate TOOL_OVERRIDES entry for catalog_service.py."""
    return f'''    "{config.tool_id}": {{
        "category": "{config.category}",
        "description": "{config.description}",
    }},'''


def print_preview(config: NewToolInput) -> None:
    """Print a verbose preview of what will be created."""
    tools_dir = get_tools_dir()

    print("\n" + "=" * 70)
    print("                     TOOL CREATION PREVIEW")
    print("=" * 70)

    # Format parameters nicely
    if config.params:
        params_display = "\n".join(
            f"     - {p.name}: {p.param_type}" + (f" = {p.default}" if p.default else "")
            for p in config.params
        )
    else:
        params_display = "     (none)"

    print(f"""
This tool will create a new @function_tool with the following configuration:

  Tool ID:         {config.tool_id}
  Display Name:    {config.name}
  Category:        {config.category}
  Return Type:     {config.return_type}
  Async:           {"Yes" if config.is_async else "No (sync)"}
  Description:     {config.description}

  Parameters:
{params_display}
""")

    print("-" * 70)
    print("FILES THAT WILL BE CREATED/MODIFIED:")
    print("-" * 70)

    tool_file = tools_dir / f"{config.tool_id}.py"
    init_file = tools_dir / "__init__.py"
    exists = tool_file.exists()

    print(f"""
  1. {"OVERWRITE" if exists else "CREATE"}: {tool_file}
     - Pydantic result model: {config.return_type}
     - @function_tool decorated {"async " if config.is_async else ""}function
     - Includes Langfuse tracing integration notes
     - Structured error handling (ValueError + generic Exception)

  2. MODIFY: {init_file}
     - Add import for {config.tool_id}
     - Add to __all__ exports
""")

    print("-" * 70)
    print("AFTER CREATION, YOU WILL NEED TO:")
    print("-" * 70)
    print(f"""
  1. Implement the tool logic in the generated file
     (look for the TODO comment)

  2. Optionally add to TOOL_OVERRIDES in catalog_service.py
     for rich metadata display

  3. Import and use in agent factories
""")
    print("=" * 70)


def confirm_proceed(args) -> bool:
    """Ask user to confirm before proceeding. Returns True if confirmed."""
    if args.yes:
        return True

    print("\nDo you want to proceed with creating this tool?")
    print("  [y] Yes, create the tool")
    print("  [n] No, cancel")
    print("  [p] Preview the generated code first")

    while True:
        try:
            response = input("\nYour choice [y/n/p]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return False

        if response in ("y", "yes"):
            return True
        elif response in ("n", "no", ""):
            print("Cancelled.")
            return False
        elif response in ("p", "preview"):
            return None  # Signal to show preview
        else:
            print("Please enter 'y', 'n', or 'p'")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a new @function_tool with boilerplate code"
    )
    parser.add_argument("tool_id", help="Tool ID in snake_case (e.g., my_api_tool)")
    parser.add_argument("--name", required=True, help="Human-readable name")
    parser.add_argument("--description", required=True, help="Tool description")
    parser.add_argument(
        "--return-type", default="ToolResult",
        help="Pydantic model name for return type (default: ToolResult)"
    )
    parser.add_argument(
        "--params", default="",
        help="Comma-separated params: 'name:type,name:type=default'"
    )
    parser.add_argument(
        "--category", default="General",
        help="Tool category for TOOL_OVERRIDES"
    )
    parser.add_argument("--sync", action="store_true", help="Generate sync function (default: async)")
    parser.add_argument("--dry-run", action="store_true", help="Preview generated code without creating files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing tool file and ignore warnings")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    # Validate tool_id
    try:
        validate_tool_id(args.tool_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check for existing tool file
    if check_tool_exists(args.tool_id) and not args.force and not args.dry_run:
        print(f"Error: Tool '{args.tool_id}' already exists at {get_tools_dir() / f'{args.tool_id}.py'}", file=sys.stderr)
        print("Use --force to overwrite", file=sys.stderr)
        return 1

    # Parse parameters
    try:
        params = parse_params(args.params)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Validate parameter types and collect warnings
    type_warnings = []
    for param in params:
        warnings = validate_param_type(param.param_type)
        type_warnings.extend(warnings)

    if type_warnings and not args.force:
        print("Parameter type warnings:", file=sys.stderr)
        for warning in type_warnings:
            print(f"  - {warning}", file=sys.stderr)
        print("Use --force to create anyway", file=sys.stderr)
        return 1

    config = NewToolInput(
        tool_id=args.tool_id,
        name=args.name,
        description=args.description,
        return_type=args.return_type,
        params=params,
        category=args.category,
        is_async=not args.sync,
    )

    # Generate code
    tool_code = generate_tool_file(config)
    override_entry = generate_tool_override_entry(config)

    # Dry run: just show generated code and exit
    if args.dry_run:
        print("=" * 70)
        print("                      DRY RUN - NO FILES MODIFIED")
        print("=" * 70)
        if check_tool_exists(args.tool_id):
            print(f"\nWould OVERWRITE: backend/src/lib/openai_agents/tools/{args.tool_id}.py")
        else:
            print(f"\nWould create: backend/src/lib/openai_agents/tools/{args.tool_id}.py")
        print("-" * 70)
        print(tool_code)
        print("\n" + "=" * 70)
        print("TOOL_OVERRIDES ENTRY")
        print("=" * 70)
        print(f"Add to TOOL_OVERRIDES in catalog_service.py:")
        print(override_entry)
        print("\n" + "=" * 70)
        print("To create this tool, run without --dry-run")
        print("=" * 70)
        return 0

    # Show preview and ask for confirmation
    print_preview(config)

    # Confirmation loop
    while True:
        result = confirm_proceed(args)
        if result is True:
            break  # Proceed with creation
        elif result is False:
            return 0  # User cancelled
        else:
            # User wants to see the code preview
            print("\n" + "=" * 70)
            print("GENERATED TOOL CODE")
            print("=" * 70)
            print(tool_code)
            print("\n" + "=" * 70)
            print("TOOL_OVERRIDES ENTRY")
            print("=" * 70)
            print(override_entry)
            print()

    # Proceed with creation
    print("\nCreating tool...")

    # Write the tool file
    result = write_tool_file(config, tool_code)
    if result is None:
        return 1

    # Update tools/__init__.py
    update_tools_init(config)

    print("\n" + "=" * 70)
    print(f"  Tool '{args.tool_id}' created successfully!")
    print("=" * 70)
    print(f"""
Next steps:
  1. Implement tool logic:
     backend/src/lib/openai_agents/tools/{args.tool_id}.py
     (look for the TODO comment)

  2. Optionally add to TOOL_OVERRIDES in catalog_service.py:
{override_entry}

  3. Import and use in agent factories
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
