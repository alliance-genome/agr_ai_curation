"""Resolve explicit installed builder choices at save time, never at execution."""

from src.lib.agent_access import is_resource_access_allowed, require_allowed_group_ids_narrowing
from src.lib.config.agent_loader import AgentDefinition, get_agent_definition_for_package
from src.lib.group_tool_policy import parse_group_tool_policy
from src.schemas.agent_execution_revision import AgentOutputContract, DomainExtractionRef, initial_output_contract


def require_no_output_without_builder_tools(output: AgentOutputContract, tool_ids: list[str]) -> None:
    """Ordinary response mode cannot secretly invoke builder finalization."""
    if output.output_state != "none":
        return
    from src.lib.openai_agents.streaming_tools import builder_finalization_tool_names

    if set(tool_ids).intersection(builder_finalization_tool_names()):
        raise ValueError(
            "No structured output is incompatible with builder finalization tools. "
            "Choose the matching extraction format or start from an agent without builder tools, "
            "then save a new revision. Existing revisions remain unchanged."
        )


def initial_agent_output_contract(agent) -> AgentOutputContract:
    """Capture a new baseline from declared curation and actual builder tools.

    Never call this to reinterpret an already saved explicit output contract.
    A missing response schema by itself is not evidence of extraction.
    """
    from src.lib.agent_studio.catalog_service import _inherited_curation_definition_for_db_agent

    if agent.output_schema_key:
        return initial_output_contract(agent.output_schema_key)
    definition = _inherited_curation_definition_for_db_agent(agent)
    if definition is None and getattr(agent, "visibility", None) == "system":
        from src.lib.config.agent_loader import get_agent_definition
        from src.lib.openai_agents.streaming_tools import builder_finalization_tool_names

        if set(agent.tool_ids or []).intersection(builder_finalization_tool_names()):
            definition = get_agent_definition(agent.agent_key)
    if definition is None:
        return AgentOutputContract(output_state="none")
    if definition.curation.domain_pack_id == "generic":
        return AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic")
    if not definition.package_id or not definition.curation.domain_pack_id:
        raise ValueError("Cannot baseline an extraction agent without its installed package/domain identity")
    return AgentOutputContract(
        output_state="structured_extraction", output_mode="domain",
        domain_extraction_ref=DomainExtractionRef(
            package_id=definition.package_id, agent_id=definition.agent_id,
            domain_pack_id=definition.curation.domain_pack_id,
        ),
    )


def domain_extraction_ref_for_agent(
    agent_key: str, *, active_group_ids: list[str],
) -> DomainExtractionRef | None:
    """Project an authorized installed builder into both human and AI catalogs."""
    from src.lib.config.agent_loader import get_agent_definition

    definition = get_agent_definition(agent_key)
    if (
        definition is None or not definition.package_id
        or not definition.curation.domain_pack_id
        or definition.output_schema is not None
        or not is_resource_access_allowed(
            visibility_allowed=True, allowed_group_ids=definition.access.allowed_group_ids,
            active_group_ids=active_group_ids, resource_kind="packaged extraction capability",
        )
    ):
        return None
    ref = DomainExtractionRef(
        package_id=definition.package_id, agent_id=definition.agent_id,
        domain_pack_id=definition.curation.domain_pack_id,
    )
    try:
        resolve_domain_extraction_definition(ref)
    except ValueError:
        return None
    return ref


def resolve_domain_extraction_definition(ref: DomainExtractionRef) -> AgentDefinition:
    from src.lib.flows.validation_attachments import domain_pack_validation_registries
    from src.lib.openai_agents.streaming_tools import builder_finalization_tool_names

    definition = get_agent_definition_for_package(ref.package_id, ref.agent_id)
    if definition is None:
        raise ValueError("The selected packaged extraction capability is unavailable")
    curation = definition.curation
    if (
        definition.output_schema is not None
        or not curation.launchable
        or not curation.adapter_key
        or curation.domain_pack_id != ref.domain_pack_id
        or ref.domain_pack_id == "generic"
        or ref.domain_pack_id not in domain_pack_validation_registries()
        or not set(definition.tools).intersection(builder_finalization_tool_names())
    ):
        raise ValueError("The selected capability is not the declared packaged builder format")
    return definition


def validate_domain_extraction_selection(
    definition: AgentDefinition,
    *,
    tool_ids: list[str],
    allowed_group_ids: list[str],
    group_tool_policy: dict,
    active_group_ids: list[str] | None,
) -> None:
    """Changing format does not grant tools or erase package access policy."""
    from src.lib.openai_agents.streaming_tools import builder_finalization_tool_names

    finalizers = builder_finalization_tool_names()
    expected = set(definition.tools).intersection(finalizers)
    if not expected or set(tool_ids).intersection(finalizers) != expected:
        raise ValueError("Selected tools must use the packaged format's matching builder finalizer")
    require_allowed_group_ids_narrowing(
        definition.access.allowed_group_ids, allowed_group_ids,
        source_name="selected packaged extraction capability",
    )
    if active_group_ids is not None and not is_resource_access_allowed(
        visibility_allowed=True,
        allowed_group_ids=definition.access.allowed_group_ids,
        active_group_ids=active_group_ids,
        resource_kind="packaged extraction capability",
    ):
        raise ValueError("The selected packaged extraction capability is not available to this user")
    saved_rules = parse_group_tool_policy(group_tool_policy).rules
    for rule in definition.group_tool_policy.rules:
        if rule not in saved_rules:
            raise ValueError("Selected format requires its package-owned group tool policy")
