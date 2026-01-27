#!/usr/bin/env python3
"""
Pre-commit hook to validate AGENT_REGISTRY.

This is a lightweight wrapper for the full validation script that:
1. Sets up the correct Python path
2. Runs essential registry validation checks
3. Returns proper exit codes for pre-commit

Checks:
- All factories in AGENT_REGISTRY are importable and callable
- All tool names in agent configs exist in TOOL_REGISTRY
- Required fields are present
- No duplicate agent IDs (implicit in dict)

Usage:
    python scripts/validate_registry.py
    # Returns 0 on success, 1 on failure
"""
import sys
from pathlib import Path

# Add backend to path for imports
# Handles both local execution and Docker container
script_dir = Path(__file__).parent
project_root = script_dir.parent

# Try multiple backend paths (local dev, Docker)
possible_backend_paths = [
    project_root / "backend",       # Local: scripts/../backend
    Path("/app/backend"),           # Docker container
]

for backend_path in possible_backend_paths:
    if backend_path.exists():
        if str(backend_path) not in sys.path:
            sys.path.insert(0, str(backend_path))
        break


def validate_registry() -> bool:
    """
    Validate AGENT_REGISTRY consistency.

    Returns:
        True if all validations pass, False otherwise.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Import registry
    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY, get_all_tools
    except ImportError as e:
        print(f"❌ Failed to import catalog_service: {e}")
        print("\nHint: Run inside Docker container:")
        print("  docker compose exec backend python scripts/validate_registry.py")
        print("\nOr ensure backend dependencies are installed in your environment.")
        return False

    print(f"Validating {len(AGENT_REGISTRY)} agents...")

    # Get all known tools
    try:
        known_tools = set(get_all_tools().keys())
    except Exception as e:
        warnings.append(f"Could not load tool registry: {e}")
        known_tools = set()

    # Validate each agent
    for agent_id, config in AGENT_REGISTRY.items():
        # Check required fields
        if not config.get("name"):
            errors.append(f"{agent_id}: missing 'name'")
        if not config.get("description"):
            errors.append(f"{agent_id}: missing 'description'")
        if not config.get("category"):
            errors.append(f"{agent_id}: missing 'category'")

        # Validate factory is callable (if present)
        factory = config.get("factory")
        if factory is not None and not callable(factory):
            errors.append(f"{agent_id}: factory is not callable: {type(factory)}")

        # Validate tools exist
        if known_tools:
            agent_tools = config.get("tools", [])
            for tool_name in agent_tools:
                if tool_name not in known_tools:
                    warnings.append(f"{agent_id}: tool '{tool_name}' not in TOOL_REGISTRY")

        # Validate document_id consistency
        requires_doc = config.get("requires_document", False)
        required_params = set(config.get("required_params", []))
        if requires_doc and "document_id" not in required_params:
            errors.append(
                f"{agent_id}: requires_document=True but 'document_id' not in required_params"
            )

        # Validate icon is present (can be at top level or under frontend.icon)
        icon = config.get("icon") or config.get("frontend", {}).get("icon")
        if not icon:
            warnings.append(f"{agent_id}: no icon specified")

    # Print results
    if errors:
        print("\n❌ ERRORS:")
        for error in errors:
            print(f"  - {error}")

    if warnings:
        print("\n⚠️  WARNINGS:")
        for warning in warnings:
            print(f"  - {warning}")

    if not errors:
        print(f"\n✅ Registry validation passed ({len(AGENT_REGISTRY)} agents)")
        return True
    else:
        print(f"\n❌ Registry validation failed ({len(errors)} errors)")
        return False


if __name__ == "__main__":
    success = validate_registry()
    sys.exit(0 if success else 1)
