#!/usr/bin/env python3
"""
Agent scaffolding CLI tool.

Generates agent files and registry entries from command-line arguments.

Usage:
    ./scripts/create_agent.py gene_expression \
        --name "Gene Expression Agent" \
        --category "Extraction" \
        --tools "search_document,read_section" \
        --icon "📊" \
        --requires-document \
        --description "Extracts gene expression data from papers"
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Fallback categories (used if backend import fails)
_FALLBACK_CATEGORIES = ["Routing", "Extraction", "Validation", "Output", "Input"]

# Cache for dynamic values
_registry_cache = {
    "categories": None,
    "tools": None,
}


def _ensure_backend_path() -> None:
    """Add backend to path if not already present."""
    backend_path = str(Path(__file__).parent.parent / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def get_valid_categories() -> List[str]:
    """Get valid categories dynamically from AGENT_REGISTRY.

    Returns cached result on subsequent calls. Falls back to hardcoded
    list if backend import fails.
    """
    if _registry_cache["categories"] is not None:
        return _registry_cache["categories"]

    try:
        _ensure_backend_path()
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        # Extract unique categories from registry
        categories = sorted(set(
            config.get("category", "Uncategorized")
            for config in AGENT_REGISTRY.values()
            if config.get("category")
        ))
        _registry_cache["categories"] = categories
        return categories
    except ImportError:
        return _FALLBACK_CATEGORIES


def get_known_tools() -> set:
    """Get known tools dynamically from TOOL_REGISTRY.

    Returns cached result on subsequent calls. Falls back to empty set
    if backend import fails (will trigger warning for all tools).
    """
    if _registry_cache["tools"] is not None:
        return _registry_cache["tools"]

    try:
        _ensure_backend_path()
        from src.lib.agent_studio.catalog_service import get_all_tools

        tools = set(get_all_tools().keys())
        _registry_cache["tools"] = tools
        return tools
    except ImportError:
        # Return empty set - all tools will show as warnings but --force allows
        return set()


@dataclass
class NewAgentInput:
    """Input configuration for creating a new agent via CLI.

    Note: This is intentionally simpler than registry_types.AgentRegistryEntry.
    AgentRegistryEntry in the backend includes factory, batching, frontend metadata, etc.
    This class captures just the CLI inputs needed to generate agent scaffolding.
    """
    agent_id: str
    name: str
    description: str
    category: str
    tools: List[str]
    icon: str = "❓"
    subcategory: Optional[str] = None
    requires_document: bool = False


def validate_agent_id(agent_id: str) -> None:
    """Validate agent ID format."""
    if not re.match(r'^[a-z][a-z0-9_]*$', agent_id):
        raise ValueError(
            f"Invalid agent_id '{agent_id}'. Must be lowercase letters, "
            "numbers, underscores, and start with a letter."
        )


def validate_category(category: str) -> None:
    """Validate category is valid."""
    valid_categories = get_valid_categories()
    if category not in valid_categories:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {valid_categories}"
        )


def validate_icon(icon: str) -> None:
    """Validate icon is a single emoji."""
    # Simple check: should be 1-2 characters (emoji can be 2 chars)
    if len(icon) < 1 or len(icon) > 4:
        raise ValueError(f"Icon must be a single emoji, got: {icon}")


def check_agent_exists(agent_id: str) -> bool:
    """Check if agent ID already exists in registry."""
    try:
        _ensure_backend_path()
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
        return agent_id in AGENT_REGISTRY
    except ImportError:
        return False


def check_tools_exist(tool_names: List[str]) -> List[str]:
    """Check if tools exist in TOOL_REGISTRY. Returns list of missing tools.

    Uses dynamic lookup from catalog_service.get_all_tools().
    Falls back to warning mode if backend import fails.
    """
    known_tools = get_known_tools()

    # If we couldn't load tools (import error), return empty - allow all
    if not known_tools:
        return []

    return [tool for tool in tool_names if tool not in known_tools]


def generate_agent_skeleton(config: NewAgentInput) -> str:
    """Generate Python code for agent factory following project patterns."""

    # Build tool imports
    tool_imports = []
    tool_vars = []
    for tool in config.tools:
        # Common tools have known import locations
        if tool in ("search_document", "read_section", "read_subsection"):
            # These are created dynamically, not imported directly
            continue
        elif tool == "agr_curation_query":
            tool_imports.append("from ..tools.agr_curation import agr_curation_query")
            tool_vars.append("agr_curation_query")
        else:
            # Generic import - user may need to adjust
            tool_imports.append(f"# TODO: Import {tool} from appropriate module")
            tool_vars.append(tool)

    tool_imports_str = "\n".join(tool_imports) if tool_imports else "# No tool imports needed"
    tools_list = ", ".join(tool_vars) if tool_vars else ""

    # Factory parameters
    if config.requires_document:
        factory_params = """document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    active_groups: Optional[List[str]] = None,"""
    else:
        factory_params = "active_groups: Optional[List[str]] = None,"

    return f'''"""
{config.name}.

{config.description}
"""
import logging
from typing import Optional, List

from agents import Agent

from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

from ..config import (
    build_model_settings,
    get_agent_config,
    get_model_for_agent,
    log_agent_config,
)
{tool_imports_str}

logger = logging.getLogger(__name__)


def create_{config.agent_id}_agent(
    {factory_params}
) -> Agent:
    """
    Create {config.name}.

    {config.description}

    Args:
        active_groups: Optional list of group IDs for group-specific rules.

    Returns:
        An Agent instance configured for {config.agent_id} tasks
    """
    # Get config from registry + environment
    agent_config = get_agent_config("{config.agent_id}")
    log_agent_config("{config.name}", agent_config)

    # Get prompts from cache (zero DB queries at runtime)
    base_prompt = get_prompt("{config.agent_id}", "system")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject group-specific rules if provided
    if active_groups:
        try:
            from config.group_rules import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="{config.agent_id}",
                prompts_out=prompts_used,
            )
            logger.info(f"{config.name} configured with group rules: {{active_groups}}")
        except ImportError as e:
            logger.warning(f"Could not import mod_config, skipping: {{e}}")
        except Exception as e:
            logger.error(f"Failed to inject group rules: {{e}}")

    # Get model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(agent_config.model)

    # Build model settings
    model_settings = build_model_settings(
        model=agent_config.model,
        temperature=agent_config.temperature,
        reasoning_effort=agent_config.reasoning,
        tool_choice=agent_config.tool_choice,
        parallel_tool_calls=True,
    )

    logger.info(
        f"[OpenAI Agents] Creating {config.name}, "
        f"model={{agent_config.model}}, prompt_v={{base_prompt.version}}"
    )

    # Log agent configuration to Langfuse
    from ..langfuse_client import log_agent_config as log_to_langfuse
    log_to_langfuse(
        agent_name="{config.name}",
        instructions=instructions,
        model=agent_config.model,
        tools={config.tools},
        model_settings={{
            "temperature": agent_config.temperature,
            "reasoning": agent_config.reasoning,
            "prompt_version": base_prompt.version,
            "active_groups": active_groups,
        }},
    )

    # Create the agent
    # NOTE: Add output_type parameter if this agent returns structured data:
    #   output_type=YourResultModel  # Pydantic model for structured output
    agent = Agent(
        name="{config.name}",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=[{tools_list}],
    )

    # Register prompts for execution logging
    set_pending_prompts(agent.name, prompts_used)

    return agent
'''


def generate_registry_entry(config: NewAgentInput) -> dict:
    """Generate AGENT_REGISTRY entry dict matching project schema."""
    entry = {
        "name": config.name,
        "description": config.description,
        "category": config.category,
        "has_mod_rules": False,  # Set to True manually if needed
        "tools": config.tools,
        "factory": f"create_{config.agent_id}_agent",
        "requires_document": config.requires_document,
        "required_params": ["document_id", "user_id"] if config.requires_document else [],
        "batch_capabilities": ["pdf_extraction"] if config.requires_document else [],
        "supervisor": {
            "enabled": True,
            "tool_name": f"ask_{config.agent_id}_specialist",
            "tool_description": config.description,
        },
        "frontend": {
            "icon": config.icon,
            "show_in_palette": True,
        },
    }

    if config.subcategory:
        entry["subcategory"] = config.subcategory

    return entry


def generate_default_prompt(config: NewAgentInput) -> str:
    """Generate default system prompt content for the agent."""
    return f"""You are the {config.name}.

{config.description}

## Instructions

Use your available tools to complete the user's request. Be thorough and accurate.

## Available Tools

{chr(10).join(f"- {tool}" for tool in config.tools)}

When you have completed the task, provide a clear summary of what you found or accomplished.
"""


def get_agents_dir() -> Path:
    """Get the agents directory path."""
    return Path(__file__).parent.parent / "backend" / "src" / "lib" / "openai_agents" / "agents"


def print_preview(config: NewAgentInput, registry_entry: dict) -> None:
    """Print a verbose preview of what will be created."""
    agents_dir = get_agents_dir()
    catalog_path = get_catalog_service_path()

    print("\n" + "=" * 70)
    print("                    AGENT CREATION PREVIEW")
    print("=" * 70)

    print(f"""
This tool will create a new agent with the following configuration:

  Agent ID:        {config.agent_id}
  Display Name:    {config.name}
  Category:        {config.category}{f" > {config.subcategory}" if config.subcategory else ""}
  Icon:            {config.icon}
  Description:     {config.description}

  Tools:           {", ".join(config.tools) if config.tools else "(none)"}
  Requires Doc:    {"Yes" if config.requires_document else "No"}
""")

    print("-" * 70)
    print("FILES THAT WILL BE CREATED/MODIFIED:")
    print("-" * 70)

    agent_file = agents_dir / f"{config.agent_id}_agent.py"
    init_file = agents_dir / "__init__.py"

    print(f"""
  1. CREATE: {agent_file}
     - Agent factory function: create_{config.agent_id}_agent()
     - Uses database-backed prompts via get_prompt()
     - Supports group-specific rules injection
     - Logs to Langfuse for tracing

  2. MODIFY: {init_file}
     - Add import for create_{config.agent_id}_agent
     - Add to __all__ exports

  3. MODIFY: {catalog_path}
     - Add import for factory function
     - Insert entry into AGENT_REGISTRY with:
       • Category: {config.category}
       • Tools: {config.tools}
       • Supervisor tool: ask_{config.agent_id}_specialist
       • Frontend visibility: show_in_palette=True
""")

    print("-" * 70)
    print("AFTER CREATION, YOU WILL NEED TO:")
    print("-" * 70)
    print(f"""
  1. Add a system prompt to the database for '{config.agent_id}'
     (via Agent Studio UI or database script)

  2. Implement the agent's logic if needed (the generated code
     provides a working template)

  3. Restart the backend to pick up changes
""")
    print("=" * 70)


def confirm_proceed(args) -> bool:
    """Ask user to confirm before proceeding. Returns True if confirmed."""
    if args.yes:
        return True

    print("\nDo you want to proceed with creating this agent?")
    print("  [y] Yes, create the agent")
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


def get_catalog_service_path() -> Path:
    """Get the catalog_service.py path."""
    return Path(__file__).parent.parent / "backend" / "src" / "lib" / "agent_studio" / "catalog_service.py"


def write_agent_file(config: NewAgentInput, code: str) -> Optional[Path]:
    """Write the agent file to the agents directory.

    Returns the path of the written file, or None on error.
    """
    agents_dir = get_agents_dir()
    agent_file = agents_dir / f"{config.agent_id}_agent.py"

    try:
        agent_file.write_text(code)
        print(f"Created: {agent_file}")
        return agent_file
    except OSError as e:
        print(f"Error writing agent file {agent_file}: {e}", file=sys.stderr)
        return None


def update_agents_init(config: NewAgentInput) -> None:
    """Update agents/__init__.py to export the new factory."""
    agents_dir = get_agents_dir()
    init_file = agents_dir / "__init__.py"

    if not init_file.exists():
        print(f"Warning: {init_file} not found, skipping __init__.py update")
        return

    content = init_file.read_text()
    factory_name = f"create_{config.agent_id}_agent"

    # Add import line
    import_line = f"from .{config.agent_id}_agent import {factory_name}"

    if import_line in content:
        print(f"Import already exists in __init__.py")
        return

    # Find a good place to insert (after existing agent imports)
    lines = content.split("\n")
    import_insert_idx = len(lines)  # Default to end

    for i, line in enumerate(lines):
        if line.startswith("from .") and "_agent import create_" in line:
            import_insert_idx = i + 1  # Insert after last agent import

    lines.insert(import_insert_idx, import_line)

    # Also update __all__ if it exists
    all_updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("__all__"):
            # Find the closing bracket
            for j in range(i, min(i + 50, len(lines))):
                if "]" in lines[j]:
                    # Insert before the closing bracket
                    indent = "    "  # Standard indent
                    new_entry = f'{indent}"{factory_name}",'
                    # Check if it's already there
                    if factory_name not in content:
                        # Insert before the ]
                        if lines[j].strip() == "]":
                            lines.insert(j, new_entry)
                        else:
                            # ] is on same line as last entry
                            lines[j] = lines[j].replace("]", f"\n{new_entry}\n]")
                        all_updated = True
                    break
            break

    try:
        init_file.write_text("\n".join(lines))
        print(f"Updated: {init_file}")
        if all_updated:
            print(f"  - Added {factory_name} to __all__")
    except OSError as e:
        print(f"Error writing {init_file}: {e}", file=sys.stderr)


def insert_registry_entry(config: NewAgentInput, entry: dict) -> None:
    """Insert the agent entry into AGENT_REGISTRY in catalog_service.py."""
    catalog_path = get_catalog_service_path()

    if not catalog_path.exists():
        print(f"Warning: {catalog_path} not found, skipping registry update")
        return

    content = catalog_path.read_text()

    # Check if agent already exists
    if f'"{config.agent_id}"' in content or f"'{config.agent_id}'" in content:
        print(f"Warning: {config.agent_id} may already exist in AGENT_REGISTRY")
        return

    # Find the import location and add factory import
    factory_import = f"from src.lib.openai_agents.agents.{config.agent_id}_agent import create_{config.agent_id}_agent"

    # Find where other agent imports are
    import_marker = "from src.lib.openai_agents.agents."
    lines = content.split("\n")

    import_insert_idx = None
    for i, line in enumerate(lines):
        if import_marker in line:
            import_insert_idx = i + 1

    if import_insert_idx and factory_import not in content:
        lines.insert(import_insert_idx, factory_import)
        print(f"Added import for create_{config.agent_id}_agent")

    content = "\n".join(lines)

    # Build entry string with proper Python formatting
    entry_for_registry = entry.copy()
    entry_for_registry["factory"] = f"create_{config.agent_id}_agent"  # Will be replaced with actual ref

    entry_lines = []
    entry_lines.append(f'    "{config.agent_id}": {{')
    entry_lines.append(f'        "name": "{entry["name"]}",')
    entry_lines.append(f'        "description": "{entry["description"]}",')
    entry_lines.append(f'        "category": "{entry["category"]}",')
    if entry.get("subcategory"):
        entry_lines.append(f'        "subcategory": "{entry["subcategory"]}",')
    entry_lines.append(f'        "has_mod_rules": {str(entry.get("has_mod_rules", False))},')
    entry_lines.append(f'        "tools": {json.dumps(entry["tools"])},')
    entry_lines.append(f'        "factory": create_{config.agent_id}_agent,')
    entry_lines.append(f'        "requires_document": {str(entry["requires_document"])},')
    entry_lines.append(f'        "required_params": {json.dumps(entry.get("required_params", []))},')
    entry_lines.append(f'        "batch_capabilities": {json.dumps(entry.get("batch_capabilities", []))},')
    entry_lines.append(f'        "supervisor": {{')
    entry_lines.append(f'            "enabled": True,')
    entry_lines.append(f'            "tool_name": "{entry["supervisor"]["tool_name"]}",')
    entry_lines.append(f'            "tool_description": "{entry["supervisor"]["tool_description"]}",')
    entry_lines.append(f'        }},')
    entry_lines.append(f'        "frontend": {{')
    entry_lines.append(f'            "icon": "{entry["frontend"]["icon"]}",')
    entry_lines.append(f'            "show_in_palette": True,')
    entry_lines.append(f'        }},')
    entry_lines.append(f'    }},')

    entry_str = "\n".join(entry_lines)

    # Find a good place to insert in AGENT_REGISTRY
    if "AGENT_REGISTRY" not in content:
        print(f"Warning: AGENT_REGISTRY not found in {catalog_path}")
        print(f"Please manually add this entry:\n{entry_str}")
        return

    # Try multiple patterns for finding the insertion point (in order of preference)
    # Pattern 1: Standard format with trailing comma and newline before closing brace
    patterns = [
        r'(\n    },\n)(}\s*\n)',       # },\n}\n  (standard)
        r'(\n    },\n)(}\s*$)',        # },\n}$   (end of file)
        r'(\n    }\n)(}\s*\n)',        # }\n}\n   (no trailing comma)
        r'(},\s*\n)(}\s*\n)',          # More flexible whitespace
    ]

    inserted = False
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            insert_pos = match.start(2)
            content = content[:insert_pos] + "\n" + entry_str + "\n" + content[insert_pos:]
            inserted = True
            break

    if not inserted:
        print(f"Warning: Could not find insertion point in AGENT_REGISTRY")
        print(f"Tried patterns but none matched. Please manually add this entry:\n{entry_str}")
        return

    try:
        catalog_path.write_text(content)
        print(f"Updated: {catalog_path} (added {config.agent_id} to AGENT_REGISTRY)")
    except OSError as e:
        print(f"Error writing {catalog_path}: {e}", file=sys.stderr)
        print(f"Please manually add this entry to AGENT_REGISTRY:\n{entry_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a new agent with boilerplate code"
    )
    parser.add_argument("agent_id", help="Agent ID (e.g., gene_expression)")
    parser.add_argument("--name", required=True, help="Human-readable name")
    parser.add_argument("--description", required=True, help="Agent description")
    parser.add_argument(
        "--category", required=True,
        help=f"Agent category (valid: {', '.join(get_valid_categories())})"
    )
    parser.add_argument("--subcategory", help="Optional subcategory")
    parser.add_argument("--tools", required=True, help="Comma-separated tool names")
    parser.add_argument("--icon", default="❓", help="Emoji icon")
    parser.add_argument("--requires-document", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Preview generated code without creating files")
    parser.add_argument("--force", action="store_true", help="Force creation even with warnings")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--create-prompt", action="store_true", help="Show command to create database prompt")

    args = parser.parse_args()

    # Validate inputs
    try:
        validate_agent_id(args.agent_id)
        validate_category(args.category)
        validate_icon(args.icon)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check for duplicate agent
    if check_agent_exists(args.agent_id):
        print(f"Error: Agent '{args.agent_id}' already exists in AGENT_REGISTRY", file=sys.stderr)
        return 1

    # Check tools exist
    tools = [t.strip() for t in args.tools.split(",")]
    missing_tools = check_tools_exist(tools)
    if missing_tools and not args.dry_run:
        print(f"Warning: Tools not found in known tools: {missing_tools}", file=sys.stderr)
        if not args.force:
            print("Use --force to create anyway", file=sys.stderr)
            return 1

    config = NewAgentInput(
        agent_id=args.agent_id,
        name=args.name,
        description=args.description,
        category=args.category,
        subcategory=args.subcategory,
        tools=[t.strip() for t in args.tools.split(",")],
        icon=args.icon,
        requires_document=args.requires_document,
    )

    # Generate content
    agent_code = generate_agent_skeleton(config)
    registry_entry = generate_registry_entry(config)

    # Generate default prompt content
    prompt_content = generate_default_prompt(config)

    # Dry run: just show generated code and exit
    if args.dry_run:
        print("=" * 70)
        print("                      DRY RUN - NO FILES MODIFIED")
        print("=" * 70)
        print(f"\nWould create: backend/src/lib/openai_agents/agents/{args.agent_id}_agent.py")
        print("-" * 70)
        print(agent_code)
        print("\n" + "=" * 70)
        print("REGISTRY ENTRY")
        print("=" * 70)
        print(f"Would add to AGENT_REGISTRY['{args.agent_id}']:")
        print(json.dumps(registry_entry, indent=4, default=str))
        print("\n" + "=" * 70)
        print("DEFAULT SYSTEM PROMPT")
        print("=" * 70)
        print(prompt_content)
        if args.create_prompt:
            print("\n(--create-prompt specified: would show database prompt command)")
        print("\n" + "=" * 70)
        print("To create this agent, run without --dry-run")
        print("=" * 70)
        return 0

    # Show preview and ask for confirmation
    print_preview(config, registry_entry)

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
            print("GENERATED AGENT CODE")
            print("=" * 70)
            print(agent_code)
            print("\n" + "=" * 70)
            print("REGISTRY ENTRY")
            print("=" * 70)
            print(json.dumps(registry_entry, indent=4, default=str))
            print()

    # Proceed with creation
    print("\nCreating agent...")

    # Write the agent file
    result = write_agent_file(config, agent_code)
    if result is None:
        return 1

    # Update agents/__init__.py
    update_agents_init(config)

    # Insert into AGENT_REGISTRY
    insert_registry_entry(config, registry_entry)

    # Optionally show database prompt command
    if args.create_prompt:
        print("\n" + "=" * 70)
        print("DATABASE PROMPT COMMAND")
        print("=" * 70)
        print("To create the initial prompt in the database, run:")
        print(f"  docker compose exec backend python -c \"")
        print(f"from src.models.sql.database import SessionLocal")
        print(f"from src.lib.prompts.service import PromptService")
        print(f"db = SessionLocal()")
        print(f"svc = PromptService(db)")
        print(f"svc.create_version('{args.agent_id}', '''")
        print(prompt_content.replace("'", "\\'"))
        print(f"''', activate=True, created_by='create_agent.py')")
        print(f"db.commit()\"")
        print("\nOr add the prompt via the Agent Studio UI.")

    print("\n" + "=" * 70)
    print(f"  Agent '{args.agent_id}' created successfully!")
    print("=" * 70)
    print(f"""
Next steps:
  1. Review generated code:
     backend/src/lib/openai_agents/agents/{args.agent_id}_agent.py

  2. Add system prompt to database (via Agent Studio UI or script above)

  3. Restart backend to pick up changes:
     docker compose restart backend
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
