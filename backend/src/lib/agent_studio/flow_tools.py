"""
Flow Tools for Opus AI to create and manage curation flows.

Section 7 of the Curation Flows implementation.
Provides three tools for Opus to help users create curation flows:

1. create_flow - Create a flow from simplified step input
2. validate_flow - Validate agent IDs and flow structure
3. get_flow_templates - Return common flow patterns and available agents

Tools are registered with the DiagnosticToolRegistry and appear in
Opus's available tools via _get_all_opus_tools() in agent_studio.py.

User Context:
    The create_flow tool requires user context (user_id) to save flows.
    This is provided via contextvars set by the API layer before tool execution.
    See set_workflow_user_context() and get_current_user_id().
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from uuid import uuid4

from src.lib.executable_flow_graph import project_executable_flow_graph
from src.lib.flow_edge_roles import (
    SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS,
    agent_can_source_output_attachment,
)
from src.lib.packages.flow_recipes import (
    FlowRecipeCatalog,
    FlowRecipeLoadError,
    load_flow_recipe_catalog,
)
from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    substring_match,
)
from src.lib.openai_agents.config import (
    get_agent_studio_flow_catalog_chunk_max_chars,
    get_agent_studio_flow_catalog_result_max_chars,
    get_agent_studio_flow_custom_instructions_max_chars,
    get_agent_studio_flow_description_max_chars,
    get_agent_studio_flow_max_steps,
    get_agent_studio_flow_name_max_chars,
    get_agent_studio_flow_output_filename_template_max_chars,
    get_agent_studio_flow_inspection_chunk_max_chars,
    get_agent_studio_flow_inspection_page_limit,
    get_agent_studio_flow_step_goal_max_chars,
    get_agent_studio_flow_template_default_items,
    get_agent_studio_flow_template_max_items,
    get_agent_studio_provider_tool_result_inline_max_chars,
    get_tool_page_default_limit,
    get_tool_page_max_limit,
)
from src.lib.flows.validation_attachments import validation_schedule_from_node_data

from .catalog_service import AGENT_REGISTRY
from .diagnostic_tools import get_diagnostic_tools_registry
from .flow_agent_policy import (
    agent_allows_ordinary_flow_step,
    attachment_only_validator_reason,
)

logger = logging.getLogger(__name__)

_FLOW_TEMPLATE_DEFAULT_ITEMS = get_agent_studio_flow_template_default_items()
_FLOW_TEMPLATE_MAX_ITEMS = get_agent_studio_flow_template_max_items()
_FLOW_CATALOG_RESULT_MAX_CHARS = min(
    get_agent_studio_flow_catalog_result_max_chars(),
    get_agent_studio_provider_tool_result_inline_max_chars(),
)
_FLOW_CATALOG_CHUNK_MAX_CHARS = get_agent_studio_flow_catalog_chunk_max_chars()


def _flow_catalog_json(value: Any) -> str:
    """Return stable UTF-8 canonical JSON used for exact record reconstruction."""
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _flow_catalog_provider_chars(value: Any) -> int:
    """Measure the exact JSON representation used for provider continuation."""
    return len(json.dumps(value, default=str))

if TYPE_CHECKING:
    from src.schemas.flows import FlowDefinition


# =============================================================================
# User Context Management (contextvars)
# =============================================================================

# Context variable for storing the current user ID during request processing
_current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)
_current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)
_current_active_group_ids: ContextVar[tuple[str, ...]] = ContextVar(
    "current_active_group_ids",
    default=(),
)

# Context variable for storing the current flow being edited in the UI
# This allows tools to access the flow state without it being embedded in the system prompt
_current_flow_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_flow_context", default=None)


def set_workflow_user_context(
    user_id: int,
    user_email: Optional[str] = None,
    active_group_ids: Optional[List[str]] = None,
) -> None:
    """Set the current user context for flow tools.

    Called by the API layer before executing tools that need user context.

    Args:
        user_id: Database user ID from User model
        user_email: Optional user email for logging
    """
    _current_user_id.set(user_id)
    _current_user_email.set(user_email)
    _current_active_group_ids.set(tuple(active_group_ids or []))
    logger.debug('Set workflow user context: user_id=%s, email=%s', user_id, user_email)


def clear_workflow_user_context() -> None:
    """Clear the current user context after request processing."""
    _current_user_id.set(None)
    _current_user_email.set(None)
    _current_active_group_ids.set(())


def get_current_user_id() -> Optional[int]:
    """Get the current user ID from context.

    Returns:
        User ID if set, None otherwise.
        Returns None when called outside of an authenticated request context.
    """
    return _current_user_id.get()


def get_current_user_email() -> Optional[str]:
    """Get the current user email from context."""
    return _current_user_email.get()


def get_current_active_group_ids() -> List[str]:
    """Return the authenticated canonical group snapshot for flow tools."""

    return list(_current_active_group_ids.get())


def set_current_flow_context(flow_context: Optional[Dict[str, Any]]) -> None:
    """Set the current flow context for tool access.

    Called by the API layer when the user is on the Flows tab.
    Stores the flow definition being edited so tools can access it.

    Args:
        flow_context: Dict with flow_name, nodes, edges, entry_node_id
    """
    _current_flow_context.set(flow_context)
    if flow_context:
        logger.debug('Set flow context: %s', flow_context.get('flow_name', 'Unnamed'))


def get_current_flow_context() -> Optional[Dict[str, Any]]:
    """Get the current flow context from context variable.

    Returns:
        Flow context dict if set, None otherwise.
    """
    return _current_flow_context.get()


def clear_current_flow_context() -> None:
    """Clear the current flow context after request processing."""
    _current_flow_context.set(None)


# =============================================================================
# Flow Agent IDs (derived from AGENT_REGISTRY)
# =============================================================================

def _get_flow_agent_ids() -> List[str]:
    """Get list of agent IDs available for use in flows.

    Excludes internal agents and attachment-only validators. Validation agents
    use the same YAML ``supervisor_routing.enabled`` source of truth as chat:
    direct-callable validators can be ordinary flow steps; disabled validators
    are only reachable through validation attachments on extraction steps.
    Returns sorted list for consistent ordering.
    """
    return sorted([
        agent_id
        for agent_id, entry in AGENT_REGISTRY.items()
        if agent_allows_ordinary_flow_step(agent_id, entry)
    ])


# Cached list for schema validation
FLOW_AGENT_IDS = _get_flow_agent_ids()


class _SimplifiedFlowValidationError(ValueError):
    """Aggregate validation failures from the shared simplified-flow path."""

    def __init__(self, errors: List[str]) -> None:
        super().__init__(errors[0] if errors else "Flow validation failed")
        self.errors = errors


def _simplified_flow_recovery_help(errors: List[str]) -> str:
    """Return recovery guidance aligned with the first validation failure."""

    first_error = errors[0] if errors else ""
    if " exceeds " in first_error and first_error.endswith(" characters"):
        return "Shorten the named field to the configured maximum"
    if "output_filename_template" in first_error:
        return (
            "Use only the supported filename variables: "
            "{{input_filename}}, {{input_filename_stem}}, {{trace_id}}, "
            "and {{timestamp}}"
        )
    if "agent_id" in first_error:
        return f"Valid agent IDs: {', '.join(FLOW_AGENT_IDS)}"
    if "source_steps" in first_error:
        return (
            "Bind formatter source_steps to one or more earlier Extraction or "
            "typed Validation steps, in the desired output order"
        )
    if "must be an object" in first_error:
        return "Provide each flow step as an object with an agent_id"
    if "steps" in first_error.lower() or "step" in first_error.lower():
        return "Provide a non-empty steps array within the configured step limit"
    return "Correct the reported flow validation error and try again"


def _simplified_flow_steps_schema() -> Dict[str, Any]:
    """Build the complete shared provider schema for simplified flow steps."""

    max_steps = get_agent_studio_flow_max_steps()
    max_source_step = max(1, max_steps - 1)
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": max_steps,
        "items": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "enum": FLOW_AGENT_IDS,
                    "description": "Agent to use for this step",
                },
                "step_goal": {
                    "type": "string",
                    "maxLength": get_agent_studio_flow_step_goal_max_chars(),
                    "description": "Goal description for this step (optional)",
                },
                "custom_instructions": {
                    "type": "string",
                    "maxLength": (
                        get_agent_studio_flow_custom_instructions_max_chars()
                    ),
                    "description": (
                        "Custom instructions appended to agent prompt (optional)"
                    ),
                },
                "source_steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": max_source_step,
                    "uniqueItems": True,
                    "items": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_source_step,
                    },
                    "description": (
                        "Required only for Output agents: ordered 1-based earlier "
                        "Extraction or typed Validation steps whose results this "
                        "formatter receives"
                    ),
                },
                "output_filename_template": {
                    "type": "string",
                    "maxLength": (
                        get_agent_studio_flow_output_filename_template_max_chars()
                    ),
                    "description": (
                        "Optional for file Output agents: runtime-resolved filename "
                        "template using built-ins such as {{input_filename_stem}} "
                        "and {{timestamp}}"
                    ),
                },
            },
            "required": ["agent_id"],
        },
        "description": "Ordered list of flow steps",
    }


# Formatter classification and display preference are generic runtime mechanics.
_CORE_AGENT_ID_EQUIVALENTS: Dict[str, tuple[str, ...]] = {
    "chat_output": ("chat_output", "chat_output_formatter"),
    "chat_output_formatter": ("chat_output", "chat_output_formatter"),
    "csv_formatter": ("csv_formatter",),
    "tsv_formatter": ("tsv_formatter",),
    "json_formatter": ("json_formatter",),
}

_OUTPUT_AGENT_PREFERENCES = (
    "chat_output",
    "csv_formatter",
    "tsv_formatter",
    "json_formatter",
)


def _agent_id_equivalences(catalog: FlowRecipeCatalog) -> Dict[str, tuple[str, ...]]:
    equivalents = dict(_CORE_AGENT_ID_EQUIVALENTS)
    for contribution in catalog.contributions:
        for group in contribution.manifest.equivalence_groups:
            group_ids = tuple(group.agent_ids)
            for agent_id in group_ids:
                equivalents[agent_id] = group_ids
    return equivalents


def _equivalent_agent_ids(
    agent_id: str,
    equivalences: Dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Return equivalent aliases/canonical IDs for a flow-facing agent ID."""
    return equivalences.get(agent_id, (agent_id,))


def _resolve_available_agent_id(
    agent_id: str,
    available_agent_ids: set[str],
    equivalences: Dict[str, tuple[str, ...]],
) -> Optional[str]:
    """Resolve a preferred flow agent ID to an installed equivalent."""
    for candidate in _equivalent_agent_ids(agent_id, equivalences):
        if candidate in available_agent_ids:
            return candidate
    return None


def _installed_agent_choices(
    preferred_agent_ids: tuple[str, ...],
    available_agent_ids: set[str],
    equivalences: Dict[str, tuple[str, ...]],
) -> List[str]:
    """Return installed agent IDs in preferred display order."""
    installed: List[str] = []
    for agent_id in preferred_agent_ids:
        resolved = _resolve_available_agent_id(
            agent_id,
            available_agent_ids,
            equivalences,
        )
        if resolved and resolved not in installed:
            installed.append(resolved)
    return installed


def _seen_any_equivalent(
    seen_agents: set[str],
    preferred_agent_ids: tuple[str, ...] | list[str],
    equivalences: Dict[str, tuple[str, ...]],
) -> bool:
    """Whether any seen agent matches one of the preferred IDs or its aliases."""
    for agent_id in preferred_agent_ids:
        if any(
            candidate in seen_agents
            for candidate in _equivalent_agent_ids(agent_id, equivalences)
        ):
            return True
    return False


def _is_output_agent_id(agent_id: str) -> bool:
    """Whether an agent ID belongs to the output-agent family."""
    return agent_id in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS


def _validated_output_source_steps(
    steps: List[Dict[str, Any]],
    output_index: int,
    agent_registry: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[tuple[int, ...], Optional[str]]:
    """Validate one formatter's canonical ordered source-step selection."""

    if agent_registry is None:
        agent_registry = AGENT_REGISTRY
    step_num = output_index + 1
    source_steps = steps[output_index].get("source_steps")
    # Removed singular source_step fallback — v1.1 output attachments use
    # canonical ordered sources.
    if not isinstance(source_steps, list) or not source_steps:
        return (), (
            f"Step {step_num}: output formatter requires non-empty source_steps"
        )

    validated: list[int] = []
    seen: set[int] = set()
    for source_step in source_steps:
        if isinstance(source_step, bool) or not isinstance(source_step, int):
            return (), (
                f"Step {step_num}: source_steps must contain only integer step numbers"
            )
        if source_step < 1 or source_step >= step_num:
            return (), (
                f"Step {step_num}: source_steps must reference earlier steps"
            )
        if source_step in seen:
            return (), (
                f"Step {step_num}: source_steps must not contain duplicates"
            )

        source_step_config = steps[source_step - 1]
        if not isinstance(source_step_config, dict):
            return (), (
                f"Step {step_num}: source_steps entry {source_step} must "
                "reference a step object"
            )
        source_agent_id = str(source_step_config.get("agent_id") or "")
        if not agent_can_source_output_attachment(
            agent_registry.get(source_agent_id),
        ):
            return (), (
                f"Step {step_num}: source_steps entry {source_step} "
                f"('{source_agent_id}') is not an extraction agent or a typed "
                "validation agent"
            )
        seen.add(source_step)
        validated.append(source_step)

    return tuple(validated), None


def _simplified_flow_metadata_errors(
    *,
    name: Optional[str],
    description: Optional[str] = None,
    require_description: bool = False,
) -> List[str]:
    """Validate tool-level metadata against the configured admission limits."""

    errors: List[str] = []
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            errors.append("Flow name cannot be empty")
        elif len(name) > get_agent_studio_flow_name_max_chars():
            errors.append(
                "Flow name exceeds "
                f"{get_agent_studio_flow_name_max_chars()} characters"
            )

    if require_description:
        if not isinstance(description, str) or not description.strip():
            errors.append(
                "Flow description is required (used as task instructions)"
            )
        elif len(description) > get_agent_studio_flow_description_max_chars():
            errors.append(
                "Flow description exceeds "
                f"{get_agent_studio_flow_description_max_chars()} characters"
            )
    return errors


def _build_simplified_flow_definition(
    *,
    steps: Any,
    task_instructions: str,
    flow_agent_ids: List[str] | None = None,
    agent_registry: Dict[str, Dict[str, Any]] | None = None,
) -> "FlowDefinition":
    """Build and canonically validate a simplified Agent Studio flow.

    This function is deliberately side-effect free so validation and creation
    use the same pre-persistence contract.
    """

    from pydantic import ValidationError

    from src.lib.flows.validation_attachments import (
        apply_flow_validation_attachment_defaults,
    )
    from src.schemas.flows import FlowDefinition

    if flow_agent_ids is None:
        flow_agent_ids = FLOW_AGENT_IDS
    if agent_registry is None:
        agent_registry = AGENT_REGISTRY
    errors: List[str] = []
    if not isinstance(steps, list):
        raise _SimplifiedFlowValidationError(["Flow steps must be an array"])
    if not steps:
        raise _SimplifiedFlowValidationError(
            ["Flow must have at least one step"]
        )

    max_steps = get_agent_studio_flow_max_steps()
    if len(steps) > max_steps:
        raise _SimplifiedFlowValidationError(
            [f"Flow has {len(steps)} steps; maximum is {max_steps}"]
        )

    output_source_steps: dict[int, tuple[int, ...]] = {}
    custom_limit = get_agent_studio_flow_custom_instructions_max_chars()
    step_goal_limit = get_agent_studio_flow_step_goal_max_chars()
    template_limit = get_agent_studio_flow_output_filename_template_max_chars()
    for i, step in enumerate(steps):
        step_num = i + 1
        if not isinstance(step, dict):
            errors.append(f"Step {step_num}: must be an object")
            continue

        agent_id = step.get("agent_id")
        custom_instructions = step.get("custom_instructions")
        if (
            isinstance(custom_instructions, str)
            and len(custom_instructions) > custom_limit
        ):
            errors.append(
                f"Step {step_num}: custom_instructions exceeds "
                f"{custom_limit} characters"
            )

        step_goal = step.get("step_goal")
        if isinstance(step_goal, str) and len(step_goal) > step_goal_limit:
            errors.append(
                f"Step {step_num}: step_goal exceeds "
                f"{step_goal_limit} characters"
            )

        output_filename_template = step.get("output_filename_template")
        if (
            isinstance(output_filename_template, str)
            and len(output_filename_template) > template_limit
        ):
            errors.append(
                f"Step {step_num}: output_filename_template exceeds "
                f"{template_limit} characters"
            )

        if not agent_id:
            errors.append(f"Step {step_num}: missing agent_id")
            continue
        if not isinstance(agent_id, str) or agent_id not in flow_agent_ids:
            errors.append(f"Step {step_num}: unknown agent_id '{agent_id}'")
            continue

        if _is_output_agent_id(agent_id):
            source_steps, source_error = _validated_output_source_steps(
                steps,
                i,
                agent_registry,
            )
            if source_error is not None:
                errors.append(source_error)
            else:
                output_source_steps[i] = source_steps

    if errors:
        raise _SimplifiedFlowValidationError(errors)

    task_input_node = {
        "id": "task_input_0",
        "type": "task_input",
        "position": {"x": 100, "y": 50},
        "data": {
            "agent_id": "task_input",
            "agent_display_name": "Initial Instructions",
            "agent_description": "Define the task for this flow",
            "task_instructions": task_instructions,
            "custom_instructions": "",
            "output_key": "task_input",
        },
    }
    nodes = [task_input_node]
    edges = []
    last_control_node_id = task_input_node["id"]

    for i, step in enumerate(steps):
        node_id = f"step_{i + 1}"
        agent_id = step["agent_id"]
        agent_info = agent_registry.get(agent_id, {})
        display_name = agent_info.get(
            "name",
            agent_id.replace("_", " ").title(),
        )
        is_output = _is_output_agent_id(agent_id)
        nodes.append(
            {
                "id": node_id,
                "type": "output" if is_output else "agent",
                "position": {
                    "x": 420 if is_output else 100,
                    "y": 200 + (i * 150),
                },
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": display_name,
                    "step_goal": step.get("step_goal"),
                    "custom_instructions": step.get("custom_instructions"),
                    "output_key": f"step_{i + 1}_output",
                    **(
                        {
                            "output_filename_template": step.get(
                                "output_filename_template"
                            )
                        }
                        if is_output and step.get("output_filename_template")
                        else {}
                    ),
                },
            }
        )

        if is_output:
            edges.extend(
                {
                    "id": f"output_edge_{i + 1}_{source_position}",
                    "source": f"step_{source_step}",
                    "target": node_id,
                    "role": "output_attachment",
                }
                for source_position, source_step in enumerate(
                    output_source_steps[i],
                    1,
                )
            )
        else:
            edges.append(
                {
                    "id": f"edge_{i + 1}",
                    "source": last_control_node_id,
                    "target": node_id,
                    "role": "control_flow",
                }
            )
            last_control_node_id = node_id

    try:
        flow_definition = FlowDefinition.model_validate(
            {
                "version": "1.1",
                "task_instructions_default_only": False,
                "nodes": nodes,
                "edges": edges,
                "entry_node_id": "task_input_0",
            }
        )
        return apply_flow_validation_attachment_defaults(flow_definition)
    except ValidationError as exc:
        validation_errors = [
            str(error.get("msg") or "Flow validation failed")
            for error in exc.errors()
        ]
        raise _SimplifiedFlowValidationError(validation_errors) from exc
    except ValueError as exc:
        raise _SimplifiedFlowValidationError([str(exc)]) from exc


def _accessible_flow_agent_ids() -> set[str]:
    """Return database-backed flow agents available to the current request."""

    from src.lib.agent_studio.catalog_service import list_available_agents

    user_id = get_current_user_id()
    if user_id is None:
        return set()
    return {
        str(agent["agent_id"])
        for agent in list_available_agents(
            db_user_id=user_id,
            authenticated_groups=get_current_active_group_ids(),
        )
        if str(agent.get("agent_id") or "") in FLOW_AGENT_IDS
    }


def _build_output_suggestion(
    seen_agents: set[str],
    available_agent_ids: set[str],
    equivalences: Dict[str, tuple[str, ...]],
) -> Optional[str]:
    """Build a final-step suggestion that only mentions installed agents."""
    installed_output_agents = _installed_agent_choices(
        _OUTPUT_AGENT_PREFERENCES,
        available_agent_ids,
        equivalences,
    )
    if not installed_output_agents or _seen_any_equivalent(
        seen_agents,
        _OUTPUT_AGENT_PREFERENCES,
        equivalences,
    ):
        return None

    primary_output = installed_output_agents[0]
    additional_outputs = installed_output_agents[1:]
    source_guidance = (
        "via ordered source_steps to one or more earlier Extraction or typed "
        "Validation steps"
    )

    if primary_output in _equivalent_agent_ids("chat_output", equivalences):
        if additional_outputs:
            formatted_outputs = ", ".join(additional_outputs)
            return (
                f"Consider attaching '{primary_output}' {source_guidance} to display results, "
                f"or attach installed file formatters ({formatted_outputs}) for downloadable files"
            )
        return f"Consider attaching '{primary_output}' {source_guidance} to display results"

    if len(installed_output_agents) == 1:
        return (
            f"Consider attaching installed output agent '{primary_output}' "
            f"{source_guidance}"
        )

    formatted_outputs = ", ".join(installed_output_agents)
    return (
        "Consider attaching one of these installed output agents "
        f"{source_guidance}: {formatted_outputs}"
    )


def _filter_flow_templates(
    available_agent_ids: set[str],
    catalog: FlowRecipeCatalog | None = None,
    active_group_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Filter template steps to installed agents without advertising missing specialists."""
    catalog = catalog or load_flow_recipe_catalog()
    equivalences = _agent_id_equivalences(catalog)
    templates: List[Dict[str, Any]] = []

    for contribution in catalog.contributions:
        for recipe in contribution.manifest.recipes:
            from src.lib.agent_access import is_resource_access_allowed

            template = recipe.model_dump(exclude_none=True)
            contract_agent_ids = sorted(
                {str(step["agent_id"]) for step in template["steps"]}
            )
            # This install-independent pass checks recipe structure and limits.
            # The resolved pass below enforces real extraction/typed-validator
            # source eligibility against the installed agent registry.
            contract_agent_registry = {
                agent_id: {
                    "category": (
                        "Output" if _is_output_agent_id(agent_id) else "Extraction"
                    )
                }
                for agent_id in contract_agent_ids
            }
            metadata_errors = _simplified_flow_metadata_errors(
                name=template["name"],
                description=template["description"],
                require_description=True,
            )
            try:
                if metadata_errors:
                    raise _SimplifiedFlowValidationError(metadata_errors)
                _build_simplified_flow_definition(
                    steps=template["steps"],
                    task_instructions=template["description"],
                    flow_agent_ids=contract_agent_ids,
                    agent_registry=contract_agent_registry,
                )
            except _SimplifiedFlowValidationError as exc:
                raise FlowRecipeLoadError(
                    f"Invalid flow recipe '{template['name']}' from package "
                    f"'{contribution.package_id}' at {contribution.source_path}: "
                    f"{'; '.join(exc.errors)}"
                ) from exc

            resolved_steps: list[tuple[int, Dict[str, Any]]] = []
            missing_required_step = False

            for original_step_number, step in enumerate(template["steps"], 1):
                resolved_agent_id = _resolve_available_agent_id(
                    step["agent_id"],
                    available_agent_ids,
                    equivalences,
                )
                if resolved_agent_id is None:
                    if _is_output_agent_id(step["agent_id"]):
                        continue
                    missing_required_step = True
                    break

                resolved_steps.append(
                    (
                        original_step_number,
                        {**step, "agent_id": resolved_agent_id},
                    )
                )

            if missing_required_step or not resolved_steps:
                continue

            step_number_map = {
                original_step_number: filtered_step_number
                for filtered_step_number, (original_step_number, _) in enumerate(
                    resolved_steps,
                    1,
                )
            }
            filtered_steps: List[Dict[str, Any]] = []
            for _, step in resolved_steps:
                remapped_step = dict(step)
                if "source_steps" in remapped_step:
                    remapped_step["source_steps"] = [
                        step_number_map[source_step]
                        for source_step in remapped_step["source_steps"]
                    ]
                filtered_steps.append(remapped_step)

            incompatible_output_binding = False
            for output_index, step in enumerate(filtered_steps):
                if not _is_output_agent_id(str(step["agent_id"])):
                    continue
                _, source_error = _validated_output_source_steps(
                    filtered_steps,
                    output_index,
                )
                if source_error is not None:
                    incompatible_output_binding = True
                    break
            if incompatible_output_binding:
                continue

            try:
                _build_simplified_flow_definition(
                    steps=filtered_steps,
                    task_instructions=template["description"],
                )
            except _SimplifiedFlowValidationError as exc:
                raise FlowRecipeLoadError(
                    f"Invalid flow recipe '{template['name']}' from package "
                    f"'{contribution.package_id}' at {contribution.source_path}: "
                    f"{'; '.join(exc.errors)}"
                ) from exc

            if not is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=recipe.access.allowed_group_ids,
                active_group_ids=list(active_group_ids or []),
                resource_kind="flow_recipe",
            ):
                continue

            templates.append(
                {
                    "name": template["name"],
                    "description": template["description"],
                    "allowed_group_ids": template["access"]["allowed_group_ids"],
                    "steps": filtered_steps,
                }
            )

    return templates


def validate_installed_flow_recipe_catalog(catalog: FlowRecipeCatalog) -> int:
    """Validate package recipes at startup and return the compatible count."""

    return len(_filter_flow_templates(set(FLOW_AGENT_IDS), catalog))


def _build_package_suggestions(
    seen_agents: set[str],
    available_agent_ids: set[str],
    catalog: FlowRecipeCatalog,
    equivalences: Dict[str, tuple[str, ...]],
    placement: Literal["first", "after"],
) -> List[str]:
    """Evaluate declarative package suggestion rules against installed agents."""

    suggestions: List[str] = []
    for rule in catalog.suggestions:
        if rule.placement != placement:
            continue
        trigger_preference = next(
            (
                preferred
                for preferred in rule.when_present
                if _seen_any_equivalent(
                    seen_agents,
                    (preferred,),
                    equivalences,
                )
            ),
            None,
        )
        if trigger_preference is None or _seen_any_equivalent(
            seen_agents,
            rule.when_absent,
            equivalences,
        ):
            continue

        trigger_agent_id = _resolve_available_agent_id(
            trigger_preference,
            available_agent_ids,
            equivalences,
        )
        if trigger_agent_id is None:
            continue

        suggested_agent_id = _resolve_available_agent_id(
            rule.suggested_agent_id,
            available_agent_ids,
            equivalences,
        )
        if suggested_agent_id is None:
            continue

        suggestions.append(
            rule.message.format(
                suggested_agent_id=suggested_agent_id,
                trigger_agent_id=trigger_agent_id,
            )
        )
    return suggestions


# =============================================================================
# Tool Handlers
# =============================================================================

def _create_flow_handler():
    """Create handler for the create_flow tool.

    Converts simplified step input to FlowDefinition format and saves to database.
    Requires user context to be set via set_workflow_user_context().
    """
    def handler(
        name: str,
        description: str,
        steps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a new curation flow.

        Args:
            name: Flow name (must be unique per user)
            description: What this flow does (REQUIRED - used as task instructions
                for the flow's Initial Instructions node)
            steps: List of step configs with agent_id, step_goal, custom_instructions,
                and source_steps for each output formatter

        Returns:
            Dict with success status, flow_id (if created), and message

        Note:
            The description parameter is mandatory and cannot be empty. It becomes
            the task_instructions for the auto-generated task_input node, which
            tells the flow supervisor what task to perform.
        """
        # Import here to avoid circular dependencies
        from src.models.sql import get_db, CurationFlow

        # Get user context
        user_id = get_current_user_id()
        if not user_id:
            return {
                "success": False,
                "error": "User not authenticated. Cannot save flow without user context.",
                "help": "This tool requires authentication. Ensure you're logged in."
            }

        metadata_errors = _simplified_flow_metadata_errors(
            name=name,
            description=description,
            require_description=True,
        )
        if metadata_errors:
            recovery_help = "Provide a valid flow name and description"
            if (
                " exceeds " in metadata_errors[0]
                and metadata_errors[0].endswith(" characters")
            ):
                recovery_help = _simplified_flow_recovery_help(metadata_errors)
            return {
                "success": False,
                "error": metadata_errors[0],
                "help": recovery_help,
            }

        try:
            validated_flow_def = _build_simplified_flow_definition(
                steps=steps,
                task_instructions=description,
            )
        except _SimplifiedFlowValidationError as exc:
            return {
                "success": False,
                "error": exc.errors[0],
                "help": _simplified_flow_recovery_help(exc.errors),
            }

        accessible_agent_ids = _accessible_flow_agent_ids()
        if any(
            isinstance(step, dict)
            and str(step.get("agent_id") or "").strip() in FLOW_AGENT_IDS
            and str(step.get("agent_id") or "").strip()
            not in accessible_agent_ids
            for step in steps
        ):
            return {
                "success": False,
                "error": "Flow references unavailable agents",
                "help": "Re-select agents from get_available_agents before saving.",
            }

        flow_definition = validated_flow_def.model_dump()

        # Save to database
        try:
            db = next(get_db())
            try:
                flow = CurationFlow(
                    id=uuid4(),
                    user_id=user_id,
                    name=name,
                    description=description,
                    flow_definition=flow_definition
                )
                db.add(flow)
                db.commit()
                db.refresh(flow)

                logger.info("Created flow '%s' (id=%s) for user %s", name, flow.id, user_id)

                return {
                    "success": True,
                    "flow_id": str(flow.id),
                    "message": f"Flow '{name}' created with {len(steps)} steps",
                    "steps_summary": [
                        {"step": i+1, "agent": step["agent_id"]}
                        for i, step in enumerate(steps)
                    ]
                }
            finally:
                db.close()

        except Exception as e:
            logger.error('Failed to create flow: %s', e, exc_info=True)
            # Check for unique constraint violation
            error_str = str(e).lower()
            if "uq_user_flow_name_active" in error_str or "unique constraint" in error_str:
                return {
                    "success": False,
                    "error": f"A flow named '{name}' already exists. Choose a different name.",
                    "help": "Flow names must be unique per user"
                }
            # Sanitize error message - don't expose internal DB details
            return {
                "success": False,
                "error": "Failed to save flow due to a database error",
                "help": "Please try again or contact support if this persists"
            }

    return handler


def _validate_flow_handler():
    """Create handler for the validate_flow tool.

    Validates flow structure without saving. No user context required.
    """
    def handler(
        steps: List[Dict[str, Any]],
        name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate a flow definition.

        Args:
            steps: List of step configs to validate
            name: Optional flow name to validate

        Returns:
            Dict with valid (bool), errors, warnings, and suggestions
        """
        errors = _simplified_flow_metadata_errors(name=name)
        warnings: List[str] = []
        suggestions: List[str] = []
        available_agent_ids = _accessible_flow_agent_ids()
        recipe_catalog = load_flow_recipe_catalog()
        equivalences = _agent_id_equivalences(recipe_catalog)

        try:
            _build_simplified_flow_definition(
                steps=steps,
                task_instructions="Agent Studio validation preflight",
            )
        except _SimplifiedFlowValidationError as exc:
            errors.extend(exc.errors)

        validated_steps = steps if isinstance(steps, list) else []
        if any(
            isinstance(step, dict)
            and str(step.get("agent_id") or "").strip() in FLOW_AGENT_IDS
            and str(step.get("agent_id") or "").strip()
            not in available_agent_ids
            for step in validated_steps
        ):
            errors.append("Flow references unavailable agents")
        seen_agents: set[str] = set()
        for i, step in enumerate(validated_steps):
            if not isinstance(step, dict):
                continue
            agent_id = step.get("agent_id")
            if not isinstance(agent_id, str) or agent_id not in available_agent_ids:
                continue
            if agent_id in seen_agents:
                warnings.append(
                    f"Step {i + 1}: agent '{agent_id}' used multiple times "
                    "(allowed but unusual)"
                )
            seen_agents.add(agent_id)

        suggestions.extend(
            _build_package_suggestions(
                seen_agents,
                available_agent_ids,
                recipe_catalog,
                equivalences,
                "first",
            )
        )

        output_suggestion = _build_output_suggestion(
            seen_agents,
            available_agent_ids,
            equivalences,
        )
        if output_suggestion and len(seen_agents) >= 2:
            suggestions.append(output_suggestion)
        suggestions.extend(
            _build_package_suggestions(
                seen_agents,
                available_agent_ids,
                recipe_catalog,
                equivalences,
                "after",
            )
        )

        result = {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "step_count": len(validated_steps),
            "unique_agents": list(seen_agents),
        }
        if errors:
            result["help"] = _simplified_flow_recovery_help(errors)
        return result

    return handler


def _get_flow_templates_handler():
    """Create handler for the get_flow_templates tool.

    Returns common flow patterns and available agents.
    """
    def handler(
        query: Optional[str] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        template_query: Optional[str] = None,
        template_limit: Optional[int] = None,
        template_cursor: Optional[str] = None,
        section: Literal["both", "templates", "agents"] = "both",
        pending_template_cursor: Optional[str] = None,
        pending_agent_cursor: Optional[str] = None,
        detail_kind: Optional[Literal["template", "agent"]] = None,
        detail_index: Optional[int] = None,
        detail_cursor: Optional[int] = None,
        detail_max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get flow templates and available agents.

        Args:
            query: Optional words to match against an agent's id, display name,
                or description (case-insensitive). Blank returns every agent.
            category: Optional exact category to keep, such as Extraction,
                Validation, or Output.
            limit: How many agents to return in this page.
            cursor: Page marker returned as next_cursor by a previous call.

        Returns:
            Dict with templates list, a bounded available_agents page, and the
            standard total_count/returned_count/truncated/next_cursor keys.
        """
        if section not in {"both", "templates", "agents"}:
            raise ValueError("section must be 'both', 'templates', or 'agents'")
        if detail_kind not in {None, "template", "agent"}:
            raise ValueError("detail_kind must be 'template' or 'agent'")
        normalized_category = str(category).strip() if category else None

        accessible_agent_ids = _accessible_flow_agent_ids()
        all_agents = [
            {
                "agent_id": agent_id,
                "display_name": AGENT_REGISTRY.get(agent_id, {}).get("name", agent_id),
                "description": AGENT_REGISTRY.get(agent_id, {}).get("description", ""),
                "category": AGENT_REGISTRY.get(agent_id, {}).get("category", "Unknown"),
                "requires_document": AGENT_REGISTRY.get(agent_id, {}).get("requires_document", False),
            }
            for agent_id in FLOW_AGENT_IDS
            if agent_id in accessible_agent_ids
        ]

        matched_agents = [
            agent
            for agent in all_agents
            if (not normalized_category or agent["category"] == normalized_category)
            and substring_match(
                query,
                agent["agent_id"],
                agent["display_name"],
                agent["description"],
            )
        ]

        installed_agent_ids = {agent["agent_id"] for agent in all_agents}
        compatible_templates = _filter_flow_templates(
            installed_agent_ids,
            active_group_ids=get_current_active_group_ids(),
        )
        matched_templates = [template for template in compatible_templates if substring_match(
            template_query, template.get("name"), template.get("description"),
            *(step.get("agent_id") for step in template.get("steps", []) if isinstance(step, dict)),
        )]

        normalized_query = str(query or "").strip() or None
        normalized_template_query = str(template_query or "").strip() or None
        bounded_limit = normalize_page_limit(limit)
        bounded_template_limit = normalize_page_limit(
            template_limit,
            default=_FLOW_TEMPLATE_DEFAULT_ITEMS,
            maximum=_FLOW_TEMPLATE_MAX_ITEMS,
        )
        pending_template_offset = (
            parse_offset_cursor(pending_template_cursor)
            if pending_template_cursor is not None
            else None
        )
        pending_agent_offset = (
            parse_offset_cursor(pending_agent_cursor)
            if pending_agent_cursor is not None
            else None
        )

        def agent_filters() -> Dict[str, Any]:
            return {
                **({"query": normalized_query} if normalized_query else {}),
                **({"category": normalized_category} if normalized_category else {}),
                "limit": bounded_limit,
            }

        def template_filters() -> Dict[str, Any]:
            return {
                **(
                    {"template_query": normalized_template_query}
                    if normalized_template_query
                    else {}
                ),
                "template_limit": bounded_template_limit,
            }

        def pending_frontier_arguments(
            kind: Literal["template", "agent"],
        ) -> Dict[str, str]:
            if kind == "template" and pending_agent_offset is not None:
                return {"pending_agent_cursor": str(pending_agent_offset)}
            if kind == "agent" and pending_template_offset is not None:
                return {"pending_template_cursor": str(pending_template_offset)}
            return {}

        if detail_kind is not None:
            if detail_index is None or detail_index < 0:
                raise ValueError("detail_index must be a non-negative record selector")
            records = matched_templates if detail_kind == "template" else matched_agents
            if detail_index >= len(records):
                raise ValueError("detail_index is beyond the selected catalog")
            record_json = _flow_catalog_json(records[detail_index])
            record_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
            start = parse_offset_cursor(detail_cursor)
            if start > len(record_json):
                raise ValueError("detail_cursor is beyond the selected record")
            requested_chars = normalize_page_limit(
                detail_max_chars,
                default=_FLOW_CATALOG_CHUNK_MAX_CHARS,
                maximum=_FLOW_CATALOG_CHUNK_MAX_CHARS,
            )

            def build_detail(end: int) -> Dict[str, Any]:
                complete = end >= len(record_json)
                if not complete:
                    next_call = {
                        "tool": "get_flow_templates",
                        "arguments": {
                            **(
                                template_filters()
                                if detail_kind == "template"
                                else agent_filters()
                            ),
                            "section": (
                                "templates" if detail_kind == "template" else "agents"
                            ),
                            "detail_kind": detail_kind,
                            "detail_index": detail_index,
                            "detail_cursor": end,
                            "detail_max_chars": requested_chars,
                            **pending_frontier_arguments(detail_kind),
                        },
                    }
                elif detail_index + 1 < len(records):
                    next_arguments = {
                        **(
                            template_filters()
                            if detail_kind == "template"
                            else agent_filters()
                        ),
                        "section": (
                            "templates" if detail_kind == "template" else "agents"
                        ),
                        **pending_frontier_arguments(detail_kind),
                    }
                    next_arguments[
                        "template_cursor" if detail_kind == "template" else "cursor"
                    ] = str(detail_index + 1)
                    next_call = {
                        "tool": "get_flow_templates",
                        "arguments": next_arguments,
                    }
                elif detail_kind == "template" and pending_agent_offset is not None:
                    next_call = {
                        "tool": "get_flow_templates",
                        "arguments": {
                            **agent_filters(),
                            "section": "agents",
                            "cursor": str(pending_agent_offset),
                        },
                    }
                elif detail_kind == "agent" and pending_template_offset is not None:
                    next_call = {
                        "tool": "get_flow_templates",
                        "arguments": {
                            **template_filters(),
                            "section": "templates",
                            "template_cursor": str(pending_template_offset),
                        },
                    }
                else:
                    next_call = None
                return {
                    "success": True,
                    "detail_mode": "record",
                    "detail_kind": detail_kind,
                    "detail_index": detail_index,
                    "sha256": record_hash,
                    "total_chars": len(record_json),
                    "range": {"start": start, "end": end},
                    "content": record_json[start:end],
                    "complete": complete,
                    "next_cursor": None if complete else end,
                    "next_call": next_call,
                    "instruction": (
                        "Concatenate content in range order and verify sha256. "
                        "Follow next_call to continue this record or resume its page."
                    ),
                }

            low = start + 1
            high = min(len(record_json), start + requested_chars)
            fitting_end: Optional[int] = start if start == len(record_json) else None
            while low <= high:
                end = (low + high) // 2
                if _flow_catalog_provider_chars(build_detail(end)) <= _FLOW_CATALOG_RESULT_MAX_CHARS:
                    fitting_end, low = end, end + 1
                else:
                    high = end - 1
            if fitting_end is None:
                return {
                    "success": False,
                    "error": "metadata_too_large",
                    "message": (
                        "Flow catalog record identity exceeds "
                        "AGENT_STUDIO_FLOW_CATALOG_RESULT_MAX_CHARS before one exact "
                        "character can be returned."
                    ),
                    "detail_kind": detail_kind,
                    "detail_index": detail_index,
                    "sha256": record_hash,
                }
            return build_detail(fitting_end)

        total_count = len(matched_agents)
        offset = parse_offset_cursor(cursor)
        page, _, _ = offset_page(
            matched_agents,
            limit=bounded_limit,
            cursor=offset,
        )
        template_offset = parse_offset_cursor(template_cursor)
        template_page, _, _ = offset_page(
            matched_templates, limit=bounded_template_limit, cursor=template_offset)
        if section == "agents":
            template_page = []
        elif section == "templates":
            page = []

        searched = bool(normalized_query or normalized_category)
        if total_count == 0 and not searched:
            message = (
                "No flow-capable agents are currently installed. "
                "Add specialist packages to unlock flow templates."
            )
        elif total_count == 0:
            message = (
                "No flow-capable agents matched. "
                "Broaden the search or add specialist packages to unlock flow templates."
            )
        else:
            message = (
                f"Found {len(matched_templates)} compatible templates and {total_count} matching agents "
                f"(showing {len(template_page)} templates and {len(page)} agents). "
                "Use validate_flow to check a custom workflow, or create_flow to save one."
            )

        def continuation_call(
            *,
            kind: Literal["template", "agent"],
            next_offset: Optional[str],
            other_next_offset: Optional[str],
            record_was_attempted: bool,
            returned_count: int,
        ) -> Optional[Dict[str, Any]]:
            if next_offset is None:
                return None
            arguments = {
                **(template_filters() if kind == "template" else agent_filters()),
                "section": "templates" if kind == "template" else "agents",
            }
            if other_next_offset is not None:
                arguments.update(
                    agent_filters() if kind == "template" else template_filters()
                )
                arguments[
                    "pending_agent_cursor"
                    if kind == "template"
                    else "pending_template_cursor"
                ] = other_next_offset
            if returned_count or not record_was_attempted:
                arguments["template_cursor" if kind == "template" else "cursor"] = next_offset
            else:
                arguments.update(
                    {
                        "detail_kind": kind,
                        "detail_index": int(next_offset),
                    }
                )
            return {"tool": "get_flow_templates", "arguments": arguments}

        def build_response() -> Dict[str, Any]:
            actual_template_next = (
                str(template_offset + len(template_page))
                if section != "agents"
                and template_offset + len(template_page) < len(matched_templates)
                else (
                    str(pending_template_offset)
                    if section == "agents" and pending_template_offset is not None
                    else None
                )
            )
            actual_agent_next = (
                str(offset + len(page))
                if section != "templates" and offset + len(page) < len(matched_agents)
                else (
                    str(pending_agent_offset)
                    if section == "templates" and pending_agent_offset is not None
                    else None
                )
            )
            template_call = continuation_call(
                kind="template",
                next_offset=actual_template_next,
                other_next_offset=actual_agent_next,
                record_was_attempted=section != "agents",
                returned_count=len(template_page),
            )
            agent_call = continuation_call(
                kind="agent",
                next_offset=actual_agent_next,
                other_next_offset=actual_template_next,
                record_was_attempted=section != "templates",
                returned_count=len(page),
            )
            return {
                "templates": template_page,
                "template_total_count": len(matched_templates),
                "template_returned_count": len(template_page),
                "templates_truncated": actual_template_next is not None,
                "template_next_cursor": actual_template_next,
                "template_query": normalized_template_query,
                "template_limit": bounded_template_limit,
                "template_next_call": template_call,
                "available_agents": page,
                "total_count": total_count,
                "returned_count": len(page),
                "truncated": actual_agent_next is not None,
                "next_cursor": actual_agent_next,
                "complete": (
                    actual_agent_next is None and actual_template_next is None
                ),
                "agent_next_call": agent_call,
                "next_call": agent_call or template_call,
                "limit": bounded_limit,
                "query": normalized_query,
                "category": normalized_category,
                "message": message,
            }

        response = build_response()
        while _flow_catalog_provider_chars(response) > _FLOW_CATALOG_RESULT_MAX_CHARS:
            if template_page:
                template_page.pop()
            elif page:
                page.pop()
            else:
                return {
                    "success": False,
                    "error": "metadata_too_large",
                    "message": (
                        "Flow catalog continuation metadata exceeds "
                        "AGENT_STUDIO_FLOW_CATALOG_RESULT_MAX_CHARS."
                    ),
                }
            response = build_response()
        return response

    return handler


def _get_available_agents_handler():
    """Create handler for the get_available_agents tool.

    Returns all available agents organized by category with metadata.
    This helps Claude understand agent types and purposes for flow verification.
    """
    def handler(
        query: Optional[str] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        detail_index: Optional[int] = None,
        detail_cursor: Optional[int] = None,
        detail_max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get available agents, searchable and grouped by category.

        Args:
            query: Optional words to match against an agent's id, name, or
                description (case-insensitive). Blank returns every agent.
            category: Optional exact category to keep, such as Extraction,
                Validation, or Output.
            limit: How many agents to return in this page.
            cursor: Page marker returned as next_cursor by a previous call.

        Returns:
            Dict with the matching agents grouped into categories plus the
            standard total_count/returned_count/truncated/next_cursor keys.
        """
        normalized_category = str(category).strip() if category else None

        matched: List[Dict[str, Any]] = []
        accessible_agent_ids = _accessible_flow_agent_ids()
        for agent_id, config in AGENT_REGISTRY.items():
            if agent_id not in accessible_agent_ids:
                continue
            if not agent_allows_ordinary_flow_step(agent_id, config):
                continue

            agent_category = config.get("category", "Unknown")
            if normalized_category and agent_category != normalized_category:
                continue

            name = config.get("name", agent_id)
            description = config.get("description", "")
            if not substring_match(query, agent_id, name, description):
                continue

            matched.append(
                {
                    "agent_id": agent_id,
                    "name": name,
                    "description": description,
                    "category": agent_category,
                    "requires_document": config.get("requires_document", False),
                }
            )

        total_agents = len(matched)
        page_maximum = get_tool_page_max_limit()
        page_default = min(get_tool_page_default_limit(), page_maximum)
        bounded_limit = normalize_page_limit(
            limit,
            default=page_default,
            maximum=page_maximum,
        )
        offset = parse_offset_cursor(cursor)

        normalized_query = str(query or "").strip() or None

        def filters() -> Dict[str, Any]:
            return {
                **({"query": normalized_query} if normalized_query else {}),
                **({"category": normalized_category} if normalized_category else {}),
                "limit": bounded_limit,
            }

        if detail_index is not None:
            if detail_index < 0 or detail_index >= len(matched):
                raise ValueError("detail_index is outside the selected agent catalog")
            record_json = _flow_catalog_json(matched[detail_index])
            record_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
            start = parse_offset_cursor(detail_cursor)
            if start > len(record_json):
                raise ValueError("detail_cursor is beyond the selected agent record")
            requested_chars = normalize_page_limit(
                detail_max_chars,
                default=_FLOW_CATALOG_CHUNK_MAX_CHARS,
                maximum=_FLOW_CATALOG_CHUNK_MAX_CHARS,
            )

            def build_detail(end: int) -> Dict[str, Any]:
                record_complete = end >= len(record_json)
                if not record_complete:
                    next_call = {
                        "tool": "get_available_agents",
                        "arguments": {
                            **filters(),
                            "detail_index": detail_index,
                            "detail_cursor": end,
                            "detail_max_chars": requested_chars,
                        },
                    }
                elif detail_index + 1 < len(matched):
                    next_call = {
                        "tool": "get_available_agents",
                        "arguments": {
                            **filters(),
                            "cursor": str(detail_index + 1),
                        },
                    }
                else:
                    next_call = None
                return {
                    "success": True,
                    "detail_mode": "agent_record",
                    "detail_index": detail_index,
                    "encoding": "canonical_json",
                    "sha256": record_hash,
                    "total_chars": len(record_json),
                    "range": {"start": start, "end": end},
                    "content": record_json[start:end],
                    "complete": record_complete,
                    "next_cursor": None if record_complete else end,
                    "next_call": next_call,
                    "instruction": (
                        "Concatenate content in range order and verify sha256. "
                        "Follow next_call to continue this record or resume the catalog."
                    ),
                }

            requested_end = min(len(record_json), start + requested_chars)
            fitting_end: Optional[int] = None
            if (
                _flow_catalog_provider_chars(build_detail(requested_end))
                <= _FLOW_CATALOG_RESULT_MAX_CHARS
            ):
                fitting_end = requested_end
            low = start + 1
            high = requested_end - 1
            if fitting_end is None:
                while low <= high:
                    candidate_end = (low + high) // 2
                    if (
                        _flow_catalog_provider_chars(build_detail(candidate_end))
                        <= _FLOW_CATALOG_RESULT_MAX_CHARS
                    ):
                        fitting_end = candidate_end
                        low = candidate_end + 1
                    else:
                        high = candidate_end - 1
            if fitting_end is None:
                return {
                    "success": False,
                    "error": "provider_limit_too_small",
                    "message": (
                        "The configured provider result envelope cannot hold agent "
                        "record metadata plus one exact character."
                    ),
                }
            return build_detail(fitting_end)

        page, truncated, next_cursor = offset_page(
            matched,
            limit=bounded_limit,
            cursor=offset,
        )

        searched = bool(str(query or "").strip() or normalized_category)

        def build_response() -> Dict[str, Any]:
            categories: Dict[str, List[Dict[str, Any]]] = {}
            output_agents: List[str] = []
            extraction_agents: List[str] = []
            validation_agents: List[str] = []
            for agent_info in page:
                agent_category = agent_info["category"]
                categories.setdefault(agent_category, []).append(
                    {
                        "agent_id": agent_info["agent_id"],
                        "name": agent_info["name"],
                        "description": agent_info["description"],
                        "requires_document": agent_info["requires_document"],
                    }
                )
                if agent_category == "Output":
                    output_agents.append(agent_info["agent_id"])
                elif agent_category == "Extraction":
                    extraction_agents.append(agent_info["agent_id"])
                elif agent_category == "Validation":
                    validation_agents.append(agent_info["agent_id"])

            actual_next_cursor = (
                str(offset + len(page)) if offset + len(page) < total_agents else None
            )
            if total_agents == 0 and not searched:
                message = (
                    "No flow-capable agents are currently installed. "
                    "Install additional agent packages to unlock flow verification helpers."
                )
            elif total_agents == 0:
                message = (
                    "No flow-capable agents matched. "
                    "Broaden the search or install additional agent packages."
                )
            elif output_agents:
                message = (
                    f"Found {total_agents} matching agents (showing {len(page)}). "
                    f"Output agents on this page ({len(output_agents)}): {', '.join(output_agents)}. "
                    "Attach each Output agent to one or more earlier Extraction or typed "
                    "Validation steps using ordered source_steps; "
                    "it is an output branch, not a control-path step."
                )
            else:
                message = (
                    f"Found {total_agents} matching agents (showing {len(page)}). "
                    "No output agents are on this page."
                )

            if actual_next_cursor is None:
                next_call = None
            elif page:
                next_call = {
                    "tool": "get_available_agents",
                    "arguments": {**filters(), "cursor": actual_next_cursor},
                }
            else:
                next_call = {
                    "tool": "get_available_agents",
                    "arguments": {**filters(), "detail_index": offset},
                }
            return {
                "categories": categories,
                "output_agents": output_agents,
                "extraction_agents": extraction_agents,
                "validation_agents": validation_agents,
                "total_agents": total_agents,
                "returned_count": len(page),
                "total_count": total_agents,
                "truncated": actual_next_cursor is not None,
                "next_cursor": actual_next_cursor,
                "complete": actual_next_cursor is None,
                "next_call": next_call,
                "limit": bounded_limit,
                "query": normalized_query,
                "category": normalized_category,
                "message": message,
            }

        response = build_response()
        while page and _flow_catalog_provider_chars(response) > _FLOW_CATALOG_RESULT_MAX_CHARS:
            page.pop()
            response = build_response()
        if _flow_catalog_provider_chars(response) > _FLOW_CATALOG_RESULT_MAX_CHARS:
            return {
                "success": False,
                "error": "provider_limit_too_small",
                "message": "The configured provider result envelope cannot hold agent catalog metadata.",
            }
        return response

    return handler


_FLOW_INSTRUCTION_FIELDS = frozenset(
    {"task_instructions", "custom_instructions", "step_goal"}
)
_FLOW_SCHEDULE_SECTIONS = (
    "selections",
    "scheduled_validators",
    "opt_outs",
    "replacement_validators",
    "supplemental_validators",
    "inactive_metadata",
)


def _inspection_limit(limit: Optional[int]) -> int:
    maximum = get_agent_studio_flow_inspection_page_limit()
    return normalize_page_limit(limit, default=maximum, maximum=maximum)


def _inspection_chunk_limit(limit: Optional[int]) -> int:
    maximum = get_agent_studio_flow_inspection_chunk_max_chars()
    return normalize_page_limit(limit, default=maximum, maximum=maximum)


def _flow_node_data(node: Mapping[str, Any]) -> Mapping[str, Any]:
    data = node.get("data")
    return data if isinstance(data, Mapping) else node


def _flow_node_type(node: Mapping[str, Any]) -> str:
    return str(node.get("type") or node.get("node_type") or "agent")


def _current_flow_state() -> tuple[
    Dict[str, Any],
    list[Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    Any,
] | None:
    flow_context = get_current_flow_context()
    if not flow_context:
        return None
    nodes = [node for node in flow_context.get("nodes", []) if isinstance(node, Mapping)]
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if node.get("id") not in (None, "")
    }
    projection = project_executable_flow_graph(flow_context, raise_on_invalid=False)
    return flow_context, nodes, node_by_id, projection


def _attachment_only_finding(node: Mapping[str, Any]) -> Dict[str, Any] | None:
    data = _flow_node_data(node)
    agent_id = str(data.get("agent_id") or "unknown")
    entry = AGENT_REGISTRY.get(agent_id)
    if not isinstance(entry, dict) or agent_allows_ordinary_flow_step(agent_id, entry):
        return None
    agent_name = str(entry.get("name") or data.get("agent_display_name") or agent_id)
    return {
        "severity": "CRITICAL",
        "code": "attachment_only_validator_step",
        "node_ids": [str(node.get("id"))],
        "message": attachment_only_validator_reason(agent_name),
    }


def _current_flow_findings(
    nodes: Sequence[Mapping[str, Any]],
    node_by_id: Mapping[str, Mapping[str, Any]],
    projection: Any,
) -> list[Dict[str, Any]]:
    findings: list[Dict[str, Any]] = [
        {
            "severity": "CRITICAL",
            "code": issue.code,
            "node_ids": list(issue.node_ids),
            "edge_ids": list(issue.edge_ids),
            "message": issue.message,
        }
        for issue in projection.issues
    ]

    task_nodes = [
        node
        for node in nodes
        if _flow_node_type(node) == "task_input"
        or _flow_node_data(node).get("agent_id") == "task_input"
    ]
    if not task_nodes:
        findings.append(
            {
                "severity": "CRITICAL",
                "code": "missing_task_input",
                "node_ids": [],
                "message": "Flow has no task_input node.",
            }
        )
    else:
        for task_node in task_nodes:
            instructions = _flow_node_data(task_node).get("task_instructions")
            if not isinstance(instructions, str) or not instructions.strip():
                findings.append(
                    {
                        "severity": "CRITICAL",
                        "code": "empty_task_input",
                        "node_ids": [str(task_node.get("id"))],
                        "message": "task_input node has empty task_instructions.",
                    }
                )

    output_keys: dict[str, list[str]] = {}
    for node in nodes:
        output_key = _flow_node_data(node).get("output_key")
        if isinstance(output_key, str) and output_key:
            output_keys.setdefault(output_key, []).append(str(node.get("id")))
    for output_key, node_ids in sorted(output_keys.items()):
        if len(node_ids) > 1:
            findings.append(
                {
                    "severity": "HIGH",
                    "code": "duplicate_output_key",
                    "node_ids": node_ids,
                    "output_key": output_key,
                    "duplicate_count": len(node_ids),
                    "message": f"output_key '{output_key}' is used by {len(node_ids)} nodes.",
                }
            )

    for node_id in projection.control_node_ids:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        data = _flow_node_data(node)
        if (
            _flow_node_type(node) in {"task_input", "output"}
            or data.get("agent_id") in {"task_input", "supervisor"}
        ):
            continue
        finding = _attachment_only_finding(node)
        if finding is not None:
            findings.append(finding)

    return findings


def _domain_pack_link(node: Mapping[str, Any]) -> str | None:
    agent_id = str(_flow_node_data(node).get("agent_id") or "")
    entry = AGENT_REGISTRY.get(agent_id)
    curation = entry.get("curation") if isinstance(entry, Mapping) else None
    domain_pack_id = curation.get("domain_pack_id") if isinstance(curation, Mapping) else None
    return str(domain_pack_id) if domain_pack_id else None


def _build_current_flow_manifest() -> Dict[str, Any]:
    state = _current_flow_state()
    if state is None:
        return {
            "success": False,
            "error": "No flow is currently being edited",
            "help": "The user must be on the Flows tab with a flow open to use this tool",
            "complete": True,
            "truncated": False,
            "next_call": None,
        }
    flow_context, nodes, node_by_id, projection = state
    findings = _current_flow_findings(nodes, node_by_id, projection)
    critical_findings = [item for item in findings if item["severity"] == "CRITICAL"]
    high_findings = [item for item in findings if item["severity"] == "HIGH"]
    output_node_ids = list(
        dict.fromkeys(
            [
                str(node.get("id"))
                for node in nodes
                if _flow_node_type(node) == "output"
            ]
            + [attachment.output_node_id for attachment in projection.output_attachments]
        )
    )
    validation_sidecar_node_ids = list(
        dict.fromkeys(sidecar.validator_node_id for sidecar in projection.validation_sidecars)
    )
    task_input_node_ids = [
        str(node.get("id"))
        for node in nodes
        if _flow_node_type(node) == "task_input"
        or _flow_node_data(node).get("agent_id") == "task_input"
    ]
    executable_agent_node_ids = [
        node_id
        for node_id in projection.ordered_control_node_ids
        if node_id not in task_input_node_ids
        and node_id not in output_node_ids
        and node_id not in validation_sidecar_node_ids
    ]
    disconnected_node_ids = list(
        dict.fromkeys(
            node_id
            for issue in projection.issues
            if issue.code == "disconnected"
            for node_id in issue.node_ids
        )
    )
    compact_nodes = []
    for node in nodes:
        data = _flow_node_data(node)
        compact = {
            "node_id": str(node.get("id")),
            "node_type": _flow_node_type(node),
            "agent_id": data.get("agent_id"),
        }
        domain_pack_id = _domain_pack_link(node)
        if domain_pack_id:
            compact["domain_pack_id"] = domain_pack_id
        compact_nodes.append(compact)

    return {
        "success": True,
        "contract": "current_flow_manifest_v1",
        "flow_name": flow_context.get("flow_name", "Untitled Flow"),
        "version": flow_context.get("version", "1.1"),
        "topology_valid": projection.valid,
        "has_critical_issues": bool(critical_findings),
        "critical_issue_count": len(critical_findings),
        "high_issue_count": len(high_findings),
        "findings": findings,
        "counts": {
            "all_nodes": len(nodes),
            "control_nodes": len(projection.control_node_ids),
            "ordered_control_nodes": len(projection.ordered_control_node_ids),
            "executable_agents": len(executable_agent_node_ids),
            "output_nodes": len(output_node_ids),
            "validation_sidecars": len(validation_sidecar_node_ids),
            "disconnected_nodes": len(disconnected_node_ids),
        },
        "ordered_control_node_ids": list(projection.ordered_control_node_ids),
        "executable_agent_node_ids": executable_agent_node_ids,
        "output_node_ids": output_node_ids,
        "validation_sidecar_node_ids": validation_sidecar_node_ids,
        "disconnected_node_ids": disconnected_node_ids,
        "task_input": {
            "node_ids": task_input_node_ids,
            "present": bool(task_input_node_ids),
            "empty": any(item["code"] == "empty_task_input" for item in findings),
        },
        "nodes": compact_nodes,
        "detail_calls": {
            "topology": {"tool": "get_current_flow_topology", "section": "issues"},
            "node": {"tool": "get_current_flow_node", "node_id": "<node_id>"},
            "instructions": {
                "tool": "get_current_flow_instructions",
                "node_id": "<node_id>",
                "field": "task_instructions|custom_instructions|step_goal",
            },
            "projection_plan": {
                "tool": "get_current_flow_projection_plan",
                "node_id": "<node_id>",
            },
            "warnings": {"tool": "get_current_flow_validation_warnings"},
            "validation_schedule": {
                "tool": "get_current_flow_validation_schedule",
                "node_id": "<node_id>",
                "section": "selections",
            },
        },
        "complete": True,
        "truncated": False,
        "next_call": None,
    }


def _get_current_flow_handler():
    """Create the minimal current-flow verification-manifest handler."""

    def handler() -> Dict[str, Any]:
        return _build_current_flow_manifest()

    return handler


def _flow_detail_error(error: str, *, help_text: str | None = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "success": False,
        "error": error,
        "complete": True,
        "truncated": False,
        "next_call": None,
    }
    if help_text:
        response["help"] = help_text
    return response


def _current_node(node_id: str) -> tuple[Mapping[str, Any], Any] | None:
    state = _current_flow_state()
    if state is None:
        return None
    node = state[2].get(str(node_id))
    if node is None:
        return None
    return node, state


def _paged_flow_response(
    *,
    tool: str,
    section: str,
    items: Sequence[Any],
    limit: Optional[int],
    cursor: Optional[str],
    next_arguments: Mapping[str, Any] | None = None,
    include_section_argument: bool = True,
) -> Dict[str, Any]:
    bounded_limit = _inspection_limit(limit)
    page, truncated, next_cursor = offset_page(
        list(items),
        limit=bounded_limit,
        cursor=parse_offset_cursor(cursor),
    )
    arguments = dict(next_arguments or {})
    arguments["limit"] = bounded_limit
    if include_section_argument:
        arguments["section"] = section
    next_call = None
    if next_cursor is not None:
        arguments["cursor"] = next_cursor
        next_call = {"tool": tool, "arguments": arguments}
    return {
        "success": True,
        "section": section,
        "items": page,
        "total_count": len(items),
        "returned_count": len(page),
        "cursor": str(parse_offset_cursor(cursor)),
        "limit": bounded_limit,
        "complete": not truncated,
        "truncated": truncated,
        "next_call": next_call,
    }


def _get_current_flow_topology_handler():
    def handler(
        section: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = _current_flow_state()
        if state is None:
            return _flow_detail_error("No flow is currently being edited")
        flow_context, _nodes, node_by_id, projection = state
        edges = [edge for edge in flow_context.get("edges", []) if isinstance(edge, Mapping)]
        sections: dict[str, list[Any]] = {
            "issues": [issue.to_dict() for issue in projection.issues],
            "control_path": [
                {
                    "position": position,
                    "node_id": node_id,
                    "agent_id": _flow_node_data(node_by_id.get(node_id, {})).get("agent_id"),
                }
                for position, node_id in enumerate(projection.ordered_control_node_ids)
            ],
            "control_edges": [
                {
                    "edge_id": edge.get("id"),
                    "source_node_id": edge.get("source"),
                    "target_node_id": edge.get("target"),
                }
                for edge in edges
                if (edge.get("role") or "control_flow") == "control_flow"
            ],
            "output_bindings": [
                {
                    "output_node_id": attachment.output_node_id,
                    "output_agent_id": _flow_node_data(
                        node_by_id.get(attachment.output_node_id, {})
                    ).get("agent_id"),
                    "sources": [
                        {
                            **source.to_dict(),
                            "source_agent_id": _flow_node_data(
                                node_by_id.get(source.source_node_id, {})
                            ).get("agent_id"),
                        }
                        for source in attachment.sources
                    ],
                }
                for attachment in projection.output_attachments
            ],
            "validation_sidecars": [
                sidecar.to_dict() for sidecar in projection.validation_sidecars
            ],
        }
        if section not in sections:
            return _flow_detail_error(
                f"Unknown topology section '{section}'",
                help_text=f"Use one of: {', '.join(sections)}",
            )
        response = _paged_flow_response(
            tool="get_current_flow_topology",
            section=section,
            items=sections[section],
            limit=limit,
            cursor=cursor,
        )
        response["topology_valid"] = projection.valid
        return response

    return handler


def _get_current_flow_node_handler():
    def handler(node_id: str) -> Dict[str, Any]:
        resolved = _current_node(node_id)
        if resolved is None:
            return _flow_detail_error(
                f"Current flow has no node_id '{node_id}'",
                help_text="Call get_current_flow for stable node IDs.",
            )
        node, _state = resolved
        data = _flow_node_data(node)
        excluded = {
            *_FLOW_INSTRUCTION_FIELDS,
            "projection_plan",
            "validation_attachments",
            "validation_groups",
        }
        scalar_configuration = {
            key: value
            for key, value in sorted(data.items())
            if key not in excluded
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        return {
            "success": True,
            "node_id": str(node_id),
            "node_type": _flow_node_type(node),
            "scalar_configuration": scalar_configuration,
            "detail_availability": {
                field: isinstance(data.get(field), str)
                for field in sorted(_FLOW_INSTRUCTION_FIELDS)
            }
            | {
                "projection_plan": isinstance(data.get("projection_plan"), Mapping),
                "validation_schedule": bool(
                    data.get("validation_attachments") or data.get("validation_groups")
                ),
            },
            "complete": True,
            "truncated": False,
            "next_call": None,
        }

    return handler


def _exact_chunk_response(
    *,
    tool: str,
    arguments: Mapping[str, Any],
    text: str,
    limit: Optional[int],
    cursor: Optional[str],
    response_metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    bounded_limit = _inspection_chunk_limit(limit)
    start = parse_offset_cursor(cursor)
    if start > len(text):
        raise ValueError("cursor is beyond the selected content")
    requested_end = min(len(text), start + bounded_limit)

    def render(end: int) -> Dict[str, Any]:
        truncated = end < len(text)
        return {
            **response_metadata,
            "content": text[start:end],
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "total_chars": len(text),
            "start_char": start,
            "end_char": end,
            "limit": bounded_limit,
            "complete": not truncated,
            "truncated": truncated,
            "next_call": (
                {
                    "tool": tool,
                    "arguments": {
                        **arguments,
                        "limit": bounded_limit,
                        "cursor": str(end),
                    },
                }
                if truncated
                else None
            ),
        }

    provider_limit = get_agent_studio_provider_tool_result_inline_max_chars()
    fitting_end: Optional[int] = None
    if _flow_catalog_provider_chars(render(requested_end)) <= provider_limit:
        fitting_end = requested_end
    low = start + 1
    high = requested_end - 1
    if fitting_end is None:
        while low <= high:
            candidate_end = (low + high) // 2
            if _flow_catalog_provider_chars(render(candidate_end)) <= provider_limit:
                fitting_end = candidate_end
                low = candidate_end + 1
            else:
                high = candidate_end - 1
    if fitting_end is None and requested_end == start:
        if _flow_catalog_provider_chars(render(start)) <= provider_limit:
            fitting_end = start
    if fitting_end is None:
        return {
            "success": False,
            "error": "provider_limit_too_small",
            "message": (
                "The configured provider result envelope cannot hold flow detail "
                "metadata plus one exact character."
            ),
        }
    return render(fitting_end)


def _get_current_flow_instructions_handler():
    def handler(
        node_id: str,
        field: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        if field not in _FLOW_INSTRUCTION_FIELDS:
            return _flow_detail_error(
                f"Unknown instruction field '{field}'",
                help_text="Use task_instructions, custom_instructions, or step_goal.",
            )
        resolved = _current_node(node_id)
        if resolved is None:
            return _flow_detail_error(f"Current flow has no node_id '{node_id}'")
        value = _flow_node_data(resolved[0]).get(field)
        if value is None:
            value = ""
        if not isinstance(value, str):
            return _flow_detail_error(f"Node field '{field}' is not text")
        return _exact_chunk_response(
            tool="get_current_flow_instructions",
            arguments={"node_id": str(node_id), "field": field},
            text=value,
            limit=limit,
            cursor=cursor,
            response_metadata={
                "success": True,
                "node_id": str(node_id),
                "field": field,
            },
        )

    return handler


def _decode_json_pointer_token(raw_token: str) -> tuple[bool, str]:
    token_parts: list[str] = []
    index = 0
    while index < len(raw_token):
        if raw_token[index] != "~":
            token_parts.append(raw_token[index])
            index += 1
            continue
        if index + 1 >= len(raw_token) or raw_token[index + 1] not in ("0", "1"):
            return False, ""
        token_parts.append("~" if raw_token[index + 1] == "0" else "/")
        index += 2
    return True, "".join(token_parts)


def _json_pointer_value(value: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, value
    if not pointer.startswith("/"):
        return False, None
    current = value
    for raw_token in pointer[1:].split("/"):
        valid_token, token = _decode_json_pointer_token(raw_token)
        if not valid_token:
            return False, None
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if token != "0" and not (
                token.startswith(tuple("123456789"))
                and all("0" <= character <= "9" for character in token[1:])
            ):
                return False, None
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _get_current_flow_projection_plan_handler():
    def handler(
        node_id: str,
        field: Optional[str] = None,
        section: str = "",
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = _current_node(node_id)
        if resolved is None:
            return _flow_detail_error(f"Current flow has no node_id '{node_id}'")
        plan = _flow_node_data(resolved[0]).get("projection_plan")
        if not isinstance(plan, Mapping):
            return _flow_detail_error(f"Node '{node_id}' has no projection_plan")
        fields = sorted(str(key) for key in plan)
        if field is None:
            summaries = [
                {"field": key, "value_type": type(plan[key]).__name__}
                for key in fields
            ]
            response = _paged_flow_response(
                tool="get_current_flow_projection_plan",
                section="fields",
                items=summaries,
                limit=limit,
                cursor=cursor,
                next_arguments={"node_id": str(node_id)},
            )
            response["node_id"] = str(node_id)
            return response
        if field not in plan:
            return _flow_detail_error(
                f"projection_plan has no field '{field}'",
                help_text=f"Available fields: {', '.join(fields)}",
            )
        found, selected = _json_pointer_value(plan[field], section)
        if not found:
            return _flow_detail_error(
                f"projection_plan field '{field}' has no JSON Pointer section '{section}'"
            )
        canonical_json = json.dumps(
            selected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return _exact_chunk_response(
            tool="get_current_flow_projection_plan",
            arguments={"node_id": str(node_id), "field": field, "section": section},
            text=canonical_json,
            limit=limit,
            cursor=cursor,
            response_metadata={
                "success": True,
                "node_id": str(node_id),
                "field": field,
                "section": section,
                "encoding": "canonical_json",
            },
        )

    return handler


def _get_current_flow_validation_warnings_handler():
    def handler(
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = _current_flow_state()
        if state is None:
            return _flow_detail_error("No flow is currently being edited")
        findings = _current_flow_findings(state[1], state[2], state[3])
        response = _paged_flow_response(
            tool="get_current_flow_validation_warnings",
            section="findings",
            items=findings,
            limit=limit,
            cursor=cursor,
            include_section_argument=False,
        )
        response["severity_counts"] = dict(Counter(item["severity"] for item in findings))
        return response

    return handler


def _get_current_flow_validation_schedule_handler():
    def handler(
        node_id: str,
        section: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        if section not in _FLOW_SCHEDULE_SECTIONS:
            return _flow_detail_error(
                f"Unknown validation schedule section '{section}'",
                help_text=f"Use one of: {', '.join(_FLOW_SCHEDULE_SECTIONS)}",
            )
        resolved = _current_node(node_id)
        if resolved is None:
            return _flow_detail_error(f"Current flow has no node_id '{node_id}'")
        data = _flow_node_data(resolved[0])
        schedule = validation_schedule_from_node_data(data)
        selection_keys = (
            "attachment_id",
            "domain_pack_id",
            "domain_pack_version",
            "validator_id",
            "validator_binding_id",
            "validation_kind",
            "tool_name",
            "tool_method",
            "validator_package_id",
            "validator_agent_id",
            "state",
            "scope",
            "object_type",
            "object_role",
            "field_path",
            "required",
            "blocking",
            "export_blocking",
            "default_enabled",
            "allow_opt_out",
            "enabled",
            "blocked_by",
        )
        selections = [
            {
                key: attachment[key]
                for key in selection_keys
                if key in attachment and attachment[key] not in (None, "")
            }
            for attachment in (data.get("validation_attachments") or [])
            if isinstance(attachment, Mapping)
        ]
        sections = {
            "selections": selections,
            "scheduled_validators": schedule["scheduled_validators"],
            "opt_outs": schedule["opt_outs"],
            "replacement_validators": schedule["replacement_validators"],
            "supplemental_validators": schedule["supplemental_validators"],
            "inactive_metadata": schedule["inactive_metadata"],
        }
        response = _paged_flow_response(
            tool="get_current_flow_validation_schedule",
            section=section,
            items=sections[section],
            limit=limit,
            cursor=cursor,
            next_arguments={"node_id": str(node_id)},
        )
        response["node_id"] = str(node_id)
        response["section_counts"] = {
            name: len(items) for name, items in sections.items()
        }
        return response

    return handler


# =============================================================================
# Tool Registration
# =============================================================================

def register_flow_tools() -> None:
    """Register all flow tools with the DiagnosticToolRegistry.

    Called on module import to make flow tools available to Opus.
    """
    registry = get_diagnostic_tools_registry()
    simplified_steps_schema = _simplified_flow_steps_schema()

    logger.info("Registering flow tools...")

    # -------------------------------------------------------------------------
    # create_flow - Generate flow from natural language
    # -------------------------------------------------------------------------
    registry.register(
        name="create_flow",
        description="""Create a new curation flow from a specification.

Use this tool when the user wants to create a workflow that chains multiple
agents together. Accepts a flow name, description, and list of steps.

Each step specifies which agent to use and optionally includes a goal
description and custom instructions to guide that agent's behavior.

Output formatters are branches, not sequential steps. Every Output agent must
include source_steps, an ordered list of one or more 1-based indexes of earlier
Extraction or typed Validation agents. Multiple formatters may point to the same
sources, and ordinary control-flow steps may continue after a formatter branch.

Returns the created flow's ID for reference.

NOTE: This tool saves the flow to the database. Use validate_flow first
to check for issues without saving.""",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": get_agent_studio_flow_name_max_chars(),
                    "description": "Flow name (must be unique per user)"
                },
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": get_agent_studio_flow_description_max_chars(),
                    "description": "What this flow does - describe the overall goal"
                },
                "steps": simplified_steps_schema,
            },
            "required": ["name", "description", "steps"]
        },
        handler=_create_flow_handler(),
        category="flows",
        tags=["flow", "creation", "workflow"]
    )
    logger.debug("Registered: create_flow")

    # -------------------------------------------------------------------------
    # validate_flow - Check flow for issues
    # -------------------------------------------------------------------------
    registry.register(
        name="validate_flow",
        description="""Validate a flow definition and provide recommendations.

Use this tool to check if a flow structure is valid BEFORE saving.
Validates agent IDs, step configuration, and provides suggestions
for improvement.

Returns validation results with any errors, warnings, and suggestions.

ALWAYS use this before create_flow to catch issues early.""",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": get_agent_studio_flow_name_max_chars(),
                    "description": "Flow name to validate (optional)"
                },
                "steps": simplified_steps_schema,
            },
            "required": ["steps"]
        },
        handler=_validate_flow_handler(),
        category="flows",
        tags=["flow", "validation", "workflow"]
    )
    logger.debug("Registered: validate_flow")

    # -------------------------------------------------------------------------
    # get_flow_templates - Return common flow patterns
    # -------------------------------------------------------------------------
    registry.register(
        name="get_flow_templates",
        description="""Get list of common flow patterns and examples.

Use this tool to show the user example workflows they can use as starting
points. Templates and agents have independent filters, totals, cursors, and
continuation calls so paging one collection does not repeat the other.

Pass query to search agents by id, name, or description, category to keep
one kind (Extraction, Validation, Output), and limit/cursor to page through
large agent catalogs. Use template_query and template_limit/template_cursor to
search and page compatible templates independently. When a single template or
agent is too large for an inline page, follow the returned detail call and its
hash-addressed character chunks, then follow its final next_call to resume paging.
Always copy returned next_call arguments exactly; pending cursors retain the other
collection's frontier while a section-specific page or detail chunk is retrieved.

Use this as a starting point when helping users design flows.""",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional words to match against an agent's id, name, or description (case-insensitive). Leave blank to list every agent.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional exact category to keep, such as Extraction, Validation, or Output.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many agents to return in this page (default: 20, max: 50).",
                    "minimum": 1,
                    "maximum": 50,
                },
                "cursor": {
                    "type": "string",
                    "description": "Page marker returned as next_cursor by a previous call. Omit to start from the first page.",
                },
                "template_query": {"type": "string", "description": "Optional words to match against template name, description, or step agent IDs."},
                "template_limit": {"type": "integer", "minimum": 1, "maximum": _FLOW_TEMPLATE_MAX_ITEMS,
                                   "default": _FLOW_TEMPLATE_DEFAULT_ITEMS,
                                   "description": "How many compatible templates to return independently."},
                "template_cursor": {"type": "string", "description": "Independent marker returned as template_next_cursor."},
                "section": {"type": "string", "enum": ["both", "templates", "agents"], "default": "both",
                            "description": "Return both first pages or only one collection; continuations select one collection."},
                "pending_template_cursor": {
                    "type": "string",
                    "description": "Template frontier retained by a returned agent continuation. Copy only from next_call.",
                },
                "pending_agent_cursor": {
                    "type": "string",
                    "description": "Agent frontier retained by a returned template continuation. Copy only from next_call.",
                },
                "detail_kind": {"type": "string", "enum": ["template", "agent"],
                                "description": "Record collection selector copied from an oversized-record continuation."},
                "detail_index": {"type": "integer", "minimum": 0,
                                 "description": "Stable filtered-record index copied from an oversized-record continuation."},
                "detail_cursor": {"type": "integer", "minimum": 0,
                                  "description": "Exact canonical-JSON character offset returned by a detail chunk."},
                "detail_max_chars": {"type": "integer", "minimum": 1,
                                     "maximum": _FLOW_CATALOG_CHUNK_MAX_CHARS,
                                     "default": _FLOW_CATALOG_CHUNK_MAX_CHARS,
                                     "description": "Requested exact record characters, capped by runtime configuration."},
            },
        },
        handler=_get_flow_templates_handler(),
        category="flows",
        tags=["flow", "templates", "examples"]
    )
    logger.debug("Registered: get_flow_templates")

    # -------------------------------------------------------------------------
    # get_current_flow - Fetch the flow currently being edited in UI
    # -------------------------------------------------------------------------
    registry.register(
        name="get_current_flow",
        description="""Get the minimal verification manifest for the current Flow Builder flow.

ALWAYS call this tool when you need to analyze, verify, or discuss the user's
current flow. The manifest reports canonical control-path, executable-agent,
Output attachment, and validation-sidecar identities separately. It includes
all CRITICAL/HIGH findings but intentionally omits large configuration values.

Returns:
- ordered_control_node_ids: Control path including task input and excluding attachments
- executable_agent_node_ids: Ordinary control-path agents, excluding task input
- output_node_ids and validation_sidecar_node_ids: Attachment identities
- findings and has_critical_issues: Authoritative first-call verification status
- detail_calls: Valid targeted calls for every omitted detail

Use this tool BEFORE attempting to validate or provide feedback on a flow.
Do not infer omitted details; use the returned bounded detail calls.""",
        input_schema={
            "type": "object",
            "properties": {},
            "description": "No parameters required - reads from current UI context"
        },
        handler=_get_current_flow_handler(),
        category="flows",
        tags=["flow", "inspection", "current"]
    )
    logger.debug("Registered: get_current_flow")

    page_max = get_agent_studio_flow_inspection_page_limit()
    chunk_max = get_agent_studio_flow_inspection_chunk_max_chars()
    page_properties = {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": page_max,
            "description": f"Maximum entries to return (default/max: {page_max}).",
        },
        "cursor": {
            "type": "string",
            "description": "next_call cursor from the previous response.",
        },
    }
    chunk_properties = {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": chunk_max,
            "description": f"Maximum exact characters to return (default/max: {chunk_max}).",
        },
        "cursor": {
            "type": "string",
            "description": "next_call character cursor from the previous response.",
        },
    }

    registry.register(
        name="get_current_flow_topology",
        description=(
            "Inspect one bounded canonical current-flow topology section. Use issues, "
            "control_path, control_edges, output_bindings, or validation_sidecars."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "issues",
                        "control_path",
                        "control_edges",
                        "output_bindings",
                        "validation_sidecars",
                    ],
                },
                **page_properties,
            },
            "required": ["section"],
        },
        handler=_get_current_flow_topology_handler(),
        category="flows",
        tags=["flow", "inspection", "topology"],
    )
    registry.register(
        name="get_current_flow_node",
        description="Get one current-flow node's bounded scalar configuration by stable node_id.",
        input_schema={
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
        handler=_get_current_flow_node_handler(),
        category="flows",
        tags=["flow", "inspection", "node"],
    )
    registry.register(
        name="get_current_flow_instructions",
        description=(
            "Retrieve exact bounded task_instructions, custom_instructions, or step_goal "
            "text for one stable node_id. Follow next_call to reconstruct long text."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "field": {
                    "type": "string",
                    "enum": sorted(_FLOW_INSTRUCTION_FIELDS),
                },
                **chunk_properties,
            },
            "required": ["node_id", "field"],
        },
        handler=_get_current_flow_instructions_handler(),
        category="flows",
        tags=["flow", "inspection", "instructions"],
    )
    registry.register(
        name="get_current_flow_projection_plan",
        description=(
            "List projection_plan fields or retrieve one explicit field/JSON-Pointer "
            "section as exact bounded canonical JSON. Follow next_call to reconstruct it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "field": {"type": "string"},
                "section": {
                    "type": "string",
                    "description": "JSON Pointer rooted at field; blank selects the whole field.",
                },
                **chunk_properties,
            },
            "required": ["node_id"],
        },
        handler=_get_current_flow_projection_plan_handler(),
        category="flows",
        tags=["flow", "inspection", "projection"],
    )
    registry.register(
        name="get_current_flow_validation_warnings",
        description="Page through exact current-flow CRITICAL and HIGH verification findings.",
        input_schema={"type": "object", "properties": page_properties},
        handler=_get_current_flow_validation_warnings_handler(),
        category="flows",
        tags=["flow", "inspection", "validation"],
    )
    registry.register(
        name="get_current_flow_validation_schedule",
        description=(
            "Inspect one node's bounded validator selections, scheduled defaults, opt-outs, "
            "replacements, supplemental validators, or inactive metadata."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "section": {
                    "type": "string",
                    "enum": list(_FLOW_SCHEDULE_SECTIONS),
                },
                **page_properties,
            },
            "required": ["node_id", "section"],
        },
        handler=_get_current_flow_validation_schedule_handler(),
        category="flows",
        tags=["flow", "inspection", "validation"],
    )
    logger.debug("Registered bounded current-flow detail tools")

    # -------------------------------------------------------------------------
    # get_available_agents - Return agent metadata for verification
    # -------------------------------------------------------------------------
    available_agent_page_max = get_tool_page_max_limit()
    available_agent_page_default = min(
        get_tool_page_default_limit(), available_agent_page_max
    )
    registry.register(
        name="get_available_agents",
        description="""Get available agents organized by category with descriptions.

Use this tool to understand agent types and purposes when verifying or analyzing flows.
Returns agents grouped by category (Extraction, Validation, Output) and identifies
which agents are designed for specific purposes:

- output_agents: Output-category agents present on this returned page only
- extraction_agents: Agents that extract structured data from documents
- validation_agents: Agents that validate or look up structured entities

Pass query to search agents by id, name, or description, category to keep one
kind, and limit/cursor to page through large agent catalogs. Results report
total_count and next_cursor so you can fetch the rest. Use category="Output"
for a focused Output lookup; every category list describes only the current page.

ALWAYS call this tool along with get_current_flow() when verifying a flow,
so you can verify every configured Output attachment against the complete focused
Output catalog without treating attachment branches as terminal control nodes.""",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional words to match against an agent's id, name, or description (case-insensitive). Leave blank to list every agent.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional exact category to keep, such as Extraction, Validation, or Output.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "How many agents to return in this page "
                        f"(default: {available_agent_page_default}, "
                        f"max: {available_agent_page_max})."
                    ),
                    "minimum": 1,
                    "maximum": available_agent_page_max,
                    "default": available_agent_page_default,
                },
                "cursor": {
                    "type": "string",
                    "description": "Page marker returned as next_cursor by a previous call. Omit to start from the first page.",
                },
                "detail_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Stable filtered-agent index copied from an oversized-record continuation.",
                },
                "detail_cursor": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Exact canonical-JSON character offset returned by a detail chunk.",
                },
                "detail_max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _FLOW_CATALOG_CHUNK_MAX_CHARS,
                    "default": _FLOW_CATALOG_CHUNK_MAX_CHARS,
                    "description": "Requested exact agent-record characters, capped by runtime configuration.",
                },
            },
        },
        handler=_get_available_agents_handler(),
        category="flows",
        tags=["flow", "agents", "catalog", "verification"]
    )
    logger.debug("Registered: get_available_agents")

    logger.info('Registered 11 flow tools (category: flows)')


# Export public API
__all__ = [
    "register_flow_tools",
    "set_workflow_user_context",
    "clear_workflow_user_context",
    "get_current_user_id",
    "get_current_user_email",
    "set_current_flow_context",
    "get_current_flow_context",
    "clear_current_flow_context",
    "FLOW_AGENT_IDS",
]
