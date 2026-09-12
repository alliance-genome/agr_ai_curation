"""Save-time selection of the installed builders for custom output profiles."""

from src.schemas.agent_execution_revision import AgentOutputContract


def declared_builder_tool_ids(mode: str | None = None) -> set[str]:
    """Installed package lifecycle membership, independent of attach permission."""
    from src.lib.agent_studio.catalog_service import _load_package_tool_registry

    return {
        binding.tool_id for binding in _load_package_tool_registry().bindings
        if binding.metadata.get("builder_output_mode") in ({mode} if mode else {"domain", "generic"})
    }


def profile_builder_tool_ids(tool_ids: list[str]) -> list[str]:
    """Replace only declared extraction builders; preserve other capabilities.

    This is an explicit save-time output-format transition, never a runtime
    fallback for an immutable revision. Package metadata owns builder membership.
    """
    from src.lib.agent_studio.catalog_service import _load_package_tool_registry

    registry = _load_package_tool_registry()
    bindings = registry.bindings_by_tool_id
    generic_tools = [
        binding.tool_id for binding in registry.bindings
        if binding.metadata.get("builder_output_mode") == "generic"
    ]
    if not {"stage_generic_object", "finalize_generic_extraction"}.issubset(generic_tools):
        raise ValueError("Custom output requires installed generic staging and finalization tools")
    retained = []
    for tool_id in tool_ids:
        binding = bindings.get(tool_id)
        if binding is not None and binding.metadata.get("builder_output_mode") in {"domain", "generic"}:
            continue
        if binding is not None and binding.metadata.get("builder_finalization"):
            raise ValueError("An extraction finalizer lacks output-mode metadata; review its package before changing output")
        retained.append(tool_id)
    return list(dict.fromkeys([*retained, *generic_tools]))


def validate_profile_builder_tools(output: AgentOutputContract, tool_ids: list[str]) -> None:
    """Reject incompatible new snapshots without modifying historical receipts."""
    if output.output_mode != "profile_bound_generic":
        return
    expected = profile_builder_tool_ids(tool_ids)
    required = {"stage_generic_object", "finalize_generic_extraction"}
    if not required.issubset(tool_ids) or not set(tool_ids).issubset(expected):
        raise ValueError(
            "Custom output requires its matching generic builders. Reopen the agent "
            "in Workshop and save a new revision to repair its system-managed tools; "
            "existing flow pins are not changed."
        )
