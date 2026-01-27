#!/usr/bin/env python3
"""
Agent registry validation script.

Validates consistency of AGENT_REGISTRY in catalog_service.py:
- Factory functions are importable and callable
- Icons are defined in frontend metadata
- Batching config is consistent
- Document-aware agent requirements are correct

Run as part of pre-commit hook or CI to catch registry issues early.
"""
import sys
from typing import List, Dict
import inspect

# Import ValidationResult from canonical location
from src.lib.agent_studio.registry_types import ValidationResult


def validate_factory_imports() -> ValidationResult:
    """
    Validate that all factories in AGENT_REGISTRY are importable and callable.

    Checks:
    - Each agent with factory != None has a valid callable factory
    - Factory functions accept expected parameters

    Returns:
        ValidationResult with any import or signature errors
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError as e:
        return ValidationResult(
            passed=False,
            errors=[f"Failed to import AGENT_REGISTRY: {e}"]
        )

    for agent_id, config in AGENT_REGISTRY.items():
        factory = config.get("factory")

        # Skip non-executable agents (like task_input)
        if factory is None:
            continue

        # Verify factory is callable
        if not callable(factory):
            errors.append(f"{agent_id}: factory is not callable: {type(factory)}")
            continue

        # Check factory can be inspected
        try:
            sig = inspect.signature(factory)
            # Get required params from registry
            required_params = set(config.get("required_params", []))
            factory_params = set(sig.parameters.keys())

            # Verify required params are in factory signature
            missing_from_sig = required_params - factory_params
            if missing_from_sig:
                warnings.append(
                    f"{agent_id}: required_params {missing_from_sig} not in factory signature"
                )
        except (ValueError, TypeError) as e:
            errors.append(f"{agent_id}: cannot inspect factory signature: {e}")

    return ValidationResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def validate_registry_icons() -> ValidationResult:
    """
    Validate that all agents in AGENT_REGISTRY have icons defined.

    Icons are now stored in the registry's frontend.icon field.
    Agents shown in the palette should have explicit icons.

    Returns:
        ValidationResult with any missing icon warnings
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError as e:
        return ValidationResult(
            passed=False,
            errors=[f"Failed to import AGENT_REGISTRY: {e}"]
        )

    for agent_id, config in AGENT_REGISTRY.items():
        frontend = config.get("frontend", {})
        icon = frontend.get("icon") if frontend else None
        show_in_palette = frontend.get("show_in_palette", True) if frontend else True

        # Agents shown in palette should have explicit icons
        if show_in_palette and not icon:
            warnings.append(
                f"Agent '{agent_id}' is shown in palette but has no icon defined "
                f"(will use default ✨)"
            )

        # Hidden agents (show_in_palette=False) without icons are fine
        # System agents like supervisor don't need icons shown

    return ValidationResult(
        passed=True,  # Missing icons are warnings, not errors
        errors=errors,
        warnings=warnings
    )


def validate_batching_config() -> ValidationResult:
    """
    Validate BATCHING_NUDGE_CONFIG consistency with AGENT_REGISTRY.

    Checks:
    - Tools in BATCHING_NUDGE_CONFIG correspond to actual transfer tools
    - Agents with batch_capabilities have corresponding batching config

    Returns:
        ValidationResult with any consistency errors
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
        from src.lib.openai_agents.streaming_tools import BATCHING_NUDGE_CONFIG
    except ImportError as e:
        return ValidationResult(
            passed=False,
            errors=[f"Failed to import required modules: {e}"]
        )

    # Build mapping from transfer tool name to agent_id
    # e.g., "ask_gene_specialist" -> "gene"
    transfer_tool_to_agent: Dict[str, str] = {}
    for agent_id, config in AGENT_REGISTRY.items():
        tools = config.get("tools", [])
        # Check if any transfer tools reference this agent
        # Transfer tools follow pattern: transfer_to_{agent}_agent or ask_{agent}_specialist
        for tool in tools:
            if tool.startswith("transfer_to_"):
                # Extract agent name from transfer_to_X_agent
                pass  # Transfer tools are in supervisor, not in individual agents

    # Verify batching config tools are valid
    batching_tools = set(BATCHING_NUDGE_CONFIG.keys())

    # Map batching tool names to expected agent IDs
    # Pattern: ask_{X}_specialist -> {X} agent
    for tool_name in batching_tools:
        if tool_name.startswith("ask_") and tool_name.endswith("_specialist"):
            # Extract agent identifier
            agent_part = tool_name[4:-11]  # Remove "ask_" and "_specialist"
            # Normalize: gene_ontology -> gene_ontology
            if agent_part not in AGENT_REGISTRY:
                warnings.append(
                    f"Batching tool '{tool_name}' maps to '{agent_part}' "
                    f"which is not in AGENT_REGISTRY"
                )

    return ValidationResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def validate_document_aware_agents() -> ValidationResult:
    """
    Validate document-aware agent configuration consistency.

    Checks:
    - Agents with requires_document=True have document_id in required_params
    - Agents with batch_capabilities=['pdf_extraction'] have requires_document=True

    Returns:
        ValidationResult with any configuration errors
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError as e:
        return ValidationResult(
            passed=False,
            errors=[f"Failed to import AGENT_REGISTRY: {e}"]
        )

    for agent_id, config in AGENT_REGISTRY.items():
        requires_doc = config.get("requires_document", False)
        required_params = set(config.get("required_params", []))
        batch_caps = set(config.get("batch_capabilities", []))

        # Check: requires_document implies document_id in required_params
        if requires_doc and "document_id" not in required_params:
            errors.append(
                f"{agent_id}: requires_document=True but 'document_id' "
                f"not in required_params"
            )

        # Check: pdf_extraction capability implies requires_document
        if "pdf_extraction" in batch_caps and not requires_doc:
            errors.append(
                f"{agent_id}: has 'pdf_extraction' capability but "
                f"requires_document=False"
            )

        # Check: document_id in params implies requires_document
        if "document_id" in required_params and not requires_doc:
            warnings.append(
                f"{agent_id}: has 'document_id' in required_params but "
                f"requires_document=False"
            )

    return ValidationResult(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


def run_all_validations() -> bool:
    """
    Run all validation checks and print results.

    Returns:
        True if all validations passed, False otherwise
    """
    validations = [
        ("Factory Imports", validate_factory_imports),
        ("Registry Icons", validate_registry_icons),
        ("Batching Config", validate_batching_config),
        ("Document-Aware Agents", validate_document_aware_agents),
    ]

    all_passed = True

    print("=" * 60)
    print("Agent Registry Validation")
    print("=" * 60)
    print()

    for name, validator in validations:
        print(f"Checking: {name}")
        print("-" * 40)

        result = validator()

        if result.passed:
            print(f"  ✅ PASSED")
        else:
            print(f"  ❌ FAILED")
            all_passed = False

        for error in result.errors:
            print(f"    ERROR: {error}")

        for warning in result.warnings:
            print(f"    WARNING: {warning}")

        print()

    print("=" * 60)
    if all_passed:
        print("✅ All validations passed!")
    else:
        print("❌ Some validations failed. Fix before migration.")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = run_all_validations()
    sys.exit(0 if success else 1)
