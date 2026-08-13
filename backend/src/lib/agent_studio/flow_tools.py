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

import logging
from contextvars import ContextVar
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.lib.executable_flow_graph import project_executable_flow_graph
from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    substring_match,
)

from .catalog_service import AGENT_REGISTRY
from .diagnostic_tools import get_diagnostic_tools_registry
from .domain_envelope_tools import current_flow_domain_envelope_analysis
from .flow_agent_policy import (
    agent_allows_ordinary_flow_step,
    attachment_only_validator_reason,
)

logger = logging.getLogger(__name__)


# =============================================================================
# User Context Management (contextvars)
# =============================================================================

# Context variable for storing the current user ID during request processing
_current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)
_current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)

# Context variable for storing the current flow being edited in the UI
# This allows tools to access the flow state without it being embedded in the system prompt
_current_flow_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_flow_context", default=None)


def _truncate_preview(text: str, max_chars: int) -> str:
    """Return a preview string with ellipsis only when truncation occurred."""

    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def set_workflow_user_context(user_id: int, user_email: Optional[str] = None) -> None:
    """Set the current user context for flow tools.

    Called by the API layer before executing tools that need user context.

    Args:
        user_id: Database user ID from User model
        user_email: Optional user email for logging
    """
    _current_user_id.set(user_id)
    _current_user_email.set(user_email)
    logger.debug('Set workflow user context: user_id=%s, email=%s', user_id, user_email)


def clear_workflow_user_context() -> None:
    """Clear the current user context after request processing."""
    _current_user_id.set(None)
    _current_user_email.set(None)


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


# Keep this map aligned with flow-facing agent aliases/canonical IDs exported
# by the runtime packages so suggestions do not advertise missing agents.
_AGENT_ID_EQUIVALENTS: Dict[str, tuple[str, ...]] = {
    "gene": ("gene", "gene_validation"),
    "gene_validation": ("gene", "gene_validation"),
    "allele": ("allele", "allele_validation"),
    "allele_validation": ("allele", "allele_validation"),
    "disease": ("disease", "disease_validation"),
    "disease_validation": ("disease", "disease_validation"),
    "chemical": ("chemical", "chemical_validation"),
    "chemical_validation": ("chemical", "chemical_validation"),
    "gene_expression": ("gene_expression", "gene_expression_extraction"),
    "gene_expression_extraction": ("gene_expression", "gene_expression_extraction"),
    "gene_ontology": ("gene_ontology", "gene_ontology_lookup"),
    "gene_ontology_lookup": ("gene_ontology", "gene_ontology_lookup"),
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


def _agent_category_contains(agent_id: str, token: str) -> bool:
    entry = AGENT_REGISTRY.get(agent_id, {})
    category = str(entry.get("category") or "").strip().lower()
    subcategory = str(entry.get("subcategory") or "").strip().lower()
    normalized_token = token.strip().lower()
    return normalized_token in category or normalized_token in subcategory


def _is_extraction_agent_id(agent_id: str) -> bool:
    return _agent_category_contains(agent_id, "extract")
_DOCUMENT_CONTEXT_AGENT_IDS = (
    "gene",
    "gene_extractor",
    "allele",
    "allele_extractor",
    "disease",
    "disease_extractor",
    "chemical",
    "gene_expression",
    "phenotype_extractor",
)
_GENE_VALIDATION_AGENT_IDS = ("gene", "gene_validation")
_GENE_EXPRESSION_AGENT_IDS = ("gene_expression", "gene_expression_extraction")

_RAW_FLOW_TEMPLATES: List[Dict[str, Any]] = [
    {
        "name": "Gene Curation",
        "description": "Extract gene mentions from PDF and validate against database",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find gene symbols and identifiers"},
            {"agent_id": "gene", "step_goal": "Validate genes in Alliance database"},
            {"agent_id": "chat_output", "step_goal": "Display validated results", "source_step": 1},
        ],
    },
    {
        "name": "Gene Extraction",
        "description": "Extract experimentally supported gene assertions from papers",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find gene mentions and context"},
            {"agent_id": "gene_extractor", "step_goal": "Extract evidence-backed gene assertions"},
            {"agent_id": "chat_output", "step_goal": "Display extraction results", "source_step": 2},
        ],
    },
    {
        "name": "Disease Annotation",
        "description": "Extract disease mentions and map to ontology terms",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find disease mentions"},
            {"agent_id": "disease", "step_goal": "Map to Disease Ontology terms"},
            {"agent_id": "chat_output", "step_goal": "Display annotation results", "source_step": 1},
        ],
    },
    {
        "name": "Disease Extraction",
        "description": "Extract experimentally supported disease assertions from papers",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find disease mentions and evidence context"},
            {"agent_id": "disease_extractor", "step_goal": "Extract evidence-backed disease assertions"},
            {"agent_id": "chat_output", "step_goal": "Display extraction results", "source_step": 2},
        ],
    },
    {
        "name": "Chemical Entity Extraction",
        "description": "Extract chemical compounds and link to ChEBI",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Extract chemical names"},
            {"agent_id": "chemical", "step_goal": "Map to ChEBI identifiers"},
        ],
    },
    {
        "name": "Gene Expression Analysis",
        "description": "Extract gene expression data from methods sections",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find experimental methods"},
            {"agent_id": "gene_expression", "step_goal": "Extract expression patterns"},
            {"agent_id": "gene", "step_goal": "Validate gene identifiers"},
            {"agent_id": "chat_output", "step_goal": "Display expression data", "source_step": 2},
        ],
    },
    {
        "name": "Phenotype Extraction",
        "description": "Extract experimentally supported phenotype assertions from papers",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find phenotype-related result sections"},
            {"agent_id": "phenotype_extractor", "step_goal": "Extract phenotype assertions with evidence"},
            {"agent_id": "chat_output", "step_goal": "Display phenotype extraction results", "source_step": 2},
        ],
    },
    {
        "name": "Allele/Variant Extraction",
        "description": "Extract experimentally supported allele and variant assertions from papers",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find allele/variant mentions and context"},
            {"agent_id": "allele_extractor", "step_goal": "Extract evidence-backed allele/variant assertions"},
            {"agent_id": "chat_output", "step_goal": "Display extraction results", "source_step": 2},
        ],
    },
    {
        "name": "Allele Annotation",
        "description": "Extract allele/variant mentions and link to database",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find allele/variant mentions"},
            {"agent_id": "allele", "step_goal": "Validate alleles in Alliance database"},
            {"agent_id": "chat_output", "step_goal": "Display allele results", "source_step": 1},
        ],
    },
    {
        "name": "GO Annotation Pipeline",
        "description": "Extract and validate Gene Ontology annotations",
        "steps": [
            {"agent_id": "pdf_extraction", "step_goal": "Find GO term mentions and gene functions"},
            {"agent_id": "gene", "step_goal": "Validate gene identifiers"},
            {"agent_id": "gene_ontology", "step_goal": "Validate GO terms"},
            {"agent_id": "chat_output", "step_goal": "Display GO annotations", "source_step": 1},
        ],
    },
]


def _equivalent_agent_ids(agent_id: str) -> tuple[str, ...]:
    """Return equivalent aliases/canonical IDs for a flow-facing agent ID."""
    return _AGENT_ID_EQUIVALENTS.get(agent_id, (agent_id,))


_OUTPUT_AGENT_IDS = {
    agent_id
    for preferred_agent_id in _OUTPUT_AGENT_PREFERENCES
    for agent_id in _equivalent_agent_ids(preferred_agent_id)
}


def _resolve_available_agent_id(
    agent_id: str,
    available_agent_ids: set[str],
) -> Optional[str]:
    """Resolve a preferred flow agent ID to an installed equivalent."""
    for candidate in _equivalent_agent_ids(agent_id):
        if candidate in available_agent_ids:
            return candidate
    return None


def _installed_agent_choices(
    preferred_agent_ids: tuple[str, ...],
    available_agent_ids: set[str],
) -> List[str]:
    """Return installed agent IDs in preferred display order."""
    installed: List[str] = []
    for agent_id in preferred_agent_ids:
        resolved = _resolve_available_agent_id(agent_id, available_agent_ids)
        if resolved and resolved not in installed:
            installed.append(resolved)
    return installed


def _seen_any_equivalent(seen_agents: set[str], preferred_agent_ids: tuple[str, ...]) -> bool:
    """Whether any seen agent matches one of the preferred IDs or its aliases."""
    for agent_id in preferred_agent_ids:
        if any(candidate in seen_agents for candidate in _equivalent_agent_ids(agent_id)):
            return True
    return False


def _is_output_agent_id(agent_id: str) -> bool:
    """Whether an agent ID belongs to the output-agent family."""
    return agent_id in _OUTPUT_AGENT_IDS


def _build_output_suggestion(
    seen_agents: set[str],
    available_agent_ids: set[str],
) -> Optional[str]:
    """Build a final-step suggestion that only mentions installed agents."""
    installed_output_agents = _installed_agent_choices(
        _OUTPUT_AGENT_PREFERENCES,
        available_agent_ids,
    )
    if not installed_output_agents or _seen_any_equivalent(seen_agents, _OUTPUT_AGENT_PREFERENCES):
        return None

    primary_output = installed_output_agents[0]
    additional_outputs = installed_output_agents[1:]

    if primary_output in _equivalent_agent_ids("chat_output"):
        if additional_outputs:
            formatted_outputs = ", ".join(additional_outputs)
            return (
                f"Consider attaching '{primary_output}' to an Extraction step to display results, "
                f"or attach installed file formatters ({formatted_outputs}) for downloadable files"
            )
        return f"Consider attaching '{primary_output}' to an Extraction step to display results"

    if len(installed_output_agents) == 1:
        return f"Consider attaching installed output agent '{primary_output}' to an Extraction step"

    formatted_outputs = ", ".join(installed_output_agents)
    return f"Consider attaching one of these installed output agents to an Extraction step: {formatted_outputs}"


def _filter_flow_templates(available_agent_ids: set[str]) -> List[Dict[str, Any]]:
    """Filter template steps to installed agents without advertising missing specialists."""
    templates: List[Dict[str, Any]] = []

    for template in _RAW_FLOW_TEMPLATES:
        filtered_steps: List[Dict[str, Any]] = []
        missing_required_step = False

        for step in template["steps"]:
            resolved_agent_id = _resolve_available_agent_id(
                step["agent_id"],
                available_agent_ids,
            )
            if resolved_agent_id is None:
                if _is_output_agent_id(step["agent_id"]):
                    continue
                missing_required_step = True
                break

            filtered_steps.append({**step, "agent_id": resolved_agent_id})

        if missing_required_step or not filtered_steps:
            continue

        templates.append(
            {
                "name": template["name"],
                "description": template["description"],
                "steps": filtered_steps,
            }
        )

    return templates


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
                and source_step for each output formatter

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

        # Validate description (required for task_input instructions)
        if not description or not description.strip():
            return {
                "success": False,
                "error": "Flow description is required (used as task instructions)",
                "help": "Provide a description of what this flow should accomplish"
            }

        # Validate steps
        if not steps:
            return {
                "success": False,
                "error": "Flow must have at least one step",
                "help": "Provide at least one step with an agent_id"
            }

        if len(steps) > 30:
            return {
                "success": False,
                "error": f"Flow has {len(steps)} steps; maximum is 30",
                "help": "Reduce the number of steps"
            }

        # Validate agent IDs in steps
        invalid_agents = []
        for i, step in enumerate(steps):
            agent_id = step.get("agent_id")
            if not agent_id:
                return {
                    "success": False,
                    "error": f"Step {i+1}: missing agent_id",
                    "help": f"Available agents: {', '.join(FLOW_AGENT_IDS)}"
                }
            if agent_id not in FLOW_AGENT_IDS:
                invalid_agents.append(f"Step {i+1}: '{agent_id}'")

        if invalid_agents:
            return {
                "success": False,
                "error": f"Unknown agent_id(s): {', '.join(invalid_agents)}",
                "help": f"Valid agent IDs: {', '.join(FLOW_AGENT_IDS)}"
            }

        for i, step in enumerate(steps):
            agent_id = str(step["agent_id"])
            if not _is_output_agent_id(agent_id):
                continue
            source_step = step.get("source_step")
            if isinstance(source_step, bool) or not isinstance(source_step, int):
                return {
                    "success": False,
                    "error": f"Step {i+1}: output formatter requires integer source_step",
                    "help": "Set source_step to the earlier extraction step whose result this formatter owns",
                }
            if source_step < 1 or source_step > i:
                return {
                    "success": False,
                    "error": f"Step {i+1}: source_step must reference an earlier step",
                    "help": "Output formatters branch from a prior extraction step; they are not control-flow steps",
                }
            source_agent_id = str(steps[source_step - 1].get("agent_id") or "")
            if not _is_extraction_agent_id(source_agent_id):
                return {
                    "success": False,
                    "error": (
                        f"Step {i+1}: source_step {source_step} ('{source_agent_id}') "
                        "is not an extraction agent"
                    ),
                    "help": "Bind each output formatter directly to exactly one extraction step",
                }

        # Convert simplified steps to full FlowDefinition format
        # Start with a task_input node (required by schema validation)
        task_input_node = {
            "id": "task_input_0",
            "type": "task_input",
            "position": {"x": 100, "y": 50},
            "data": {
                "agent_id": "task_input",
                "agent_display_name": "Initial Instructions",
                "agent_description": "Define the task for this flow",
                "task_instructions": description,  # Use flow description as instructions
                "custom_instructions": "",
                "output_key": "task_input"
            }
        }
        nodes = [task_input_node]

        # Add agent nodes for each step. Output formatters are terminal leaves
        # attached to one extractor; ordinary steps retain the control chain.
        edges = []
        last_control_node_id = task_input_node["id"]
        for i, step in enumerate(steps):
            node_id = f"step_{i+1}"
            agent_id = step["agent_id"]

            # Get display name from registry
            agent_info = AGENT_REGISTRY.get(agent_id, {})
            display_name = agent_info.get("name", agent_id.replace("_", " ").title())

            is_output = _is_output_agent_id(agent_id)
            nodes.append({
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
                    "output_key": f"step_{i+1}_output",
                    **(
                        {
                            "output_filename_template": step.get(
                                "output_filename_template"
                            )
                        }
                        if is_output and step.get("output_filename_template")
                        else {}
                    ),
                }
            })

            if is_output:
                edges.append({
                    "id": f"output_edge_{i+1}",
                    "source": f"step_{step['source_step']}",
                    "target": node_id,
                    "role": "output_attachment",
                })
            else:
                edges.append({
                    "id": f"edge_{i+1}",
                    "source": last_control_node_id,
                    "target": node_id,
                    "role": "control_flow",
                })
                last_control_node_id = node_id

        flow_definition = {
            "version": "1.1",
            "nodes": nodes,
            "edges": edges,
            "entry_node_id": "task_input_0"  # task_input must be entry node
        }

        # Validate via Pydantic schema (same validation as API endpoint)
        from src.lib.flows.validation_attachments import (
            apply_flow_validation_attachment_defaults,
        )
        from src.schemas.flows import FlowDefinition
        from pydantic import ValidationError

        try:
            validated_flow_def = FlowDefinition(**flow_definition)
            flow_definition = apply_flow_validation_attachment_defaults(
                validated_flow_def,
            ).model_dump()
        except ValidationError as e:
            # Extract user-friendly error message
            errors = e.errors()
            if errors:
                error_msg = errors[0].get("msg", "Flow validation failed")
            else:
                error_msg = "Flow validation failed"
            return {"success": False, "error": error_msg}
        except ValueError as e:
            return {"success": False, "error": str(e)}

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
        errors = []
        warnings = []
        suggestions = []
        available_agent_ids = set(FLOW_AGENT_IDS)

        # Validate step count
        if not steps:
            errors.append("Flow must have at least one step")
        elif len(steps) > 30:
            errors.append(f"Flow has {len(steps)} steps; maximum is 30")

        # Validate each step
        seen_agents = set()
        for i, step in enumerate(steps):
            agent_id = step.get("agent_id")
            step_num = i + 1

            # Validate agent_id exists
            if not agent_id:
                errors.append(f"Step {step_num}: missing agent_id")
            elif agent_id not in FLOW_AGENT_IDS:
                errors.append(f"Step {step_num}: unknown agent_id '{agent_id}'")
            else:
                # Track for duplicate detection
                if agent_id in seen_agents:
                    warnings.append(
                        f"Step {step_num}: agent '{agent_id}' used multiple times "
                        "(allowed but unusual)"
                    )
                seen_agents.add(agent_id)

                if _is_output_agent_id(agent_id):
                    source_step = step.get("source_step")
                    if isinstance(source_step, bool) or not isinstance(source_step, int):
                        errors.append(
                            f"Step {step_num}: output formatter requires integer source_step"
                        )
                    elif source_step < 1 or source_step >= step_num:
                        errors.append(
                            f"Step {step_num}: source_step must reference an earlier step"
                        )
                    else:
                        source_agent_id = str(
                            steps[source_step - 1].get("agent_id") or ""
                        )
                        if not _is_extraction_agent_id(source_agent_id):
                            errors.append(
                                f"Step {step_num}: source_step {source_step} "
                                f"('{source_agent_id}') is not an extraction agent"
                            )

            # Validate custom_instructions length
            custom_instructions = step.get("custom_instructions")
            if custom_instructions and len(custom_instructions) > 2000:
                errors.append(
                    f"Step {step_num}: custom_instructions exceeds 2000 characters"
                )

            # Validate step_goal length
            step_goal = step.get("step_goal")
            if step_goal and len(step_goal) > 500:
                errors.append(
                    f"Step {step_num}: step_goal exceeds 500 characters"
                )

        # Validate name if provided
        if name is not None:
            if not name.strip():
                errors.append("Flow name cannot be empty")
            elif len(name) > 255:
                errors.append("Flow name exceeds 255 characters")

        # Generate suggestions based on agent patterns
        pdf_agent_id = _resolve_available_agent_id("pdf_extraction", available_agent_ids)
        if (
            pdf_agent_id
            and not _seen_any_equivalent(seen_agents, ("pdf_extraction",))
            and _seen_any_equivalent(seen_agents, _DOCUMENT_CONTEXT_AGENT_IDS)
        ):
            suggestions.append(
                f"Consider adding '{pdf_agent_id}' step first to extract entities from documents"
            )

        output_suggestion = _build_output_suggestion(seen_agents, available_agent_ids)
        if output_suggestion and len(seen_agents) >= 2:
            suggestions.append(output_suggestion)

        gene_expression_agent_id = _resolve_available_agent_id("gene_expression", available_agent_ids)
        gene_validation_agent_id = _resolve_available_agent_id("gene", available_agent_ids)
        if (
            gene_expression_agent_id
            and gene_validation_agent_id
            and _seen_any_equivalent(seen_agents, _GENE_EXPRESSION_AGENT_IDS)
            and not _seen_any_equivalent(seen_agents, _GENE_VALIDATION_AGENT_IDS)
        ):
            suggestions.append(
                f"Consider adding '{gene_validation_agent_id}' step after "
                f"'{gene_expression_agent_id}' to validate gene identifiers"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "step_count": len(steps),
            "unique_agents": list(seen_agents)
        }

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
        normalized_category = str(category).strip() if category else None

        all_agents = [
            {
                "agent_id": agent_id,
                "display_name": AGENT_REGISTRY.get(agent_id, {}).get("name", agent_id),
                "description": AGENT_REGISTRY.get(agent_id, {}).get("description", ""),
                "category": AGENT_REGISTRY.get(agent_id, {}).get("category", "Unknown"),
                "requires_document": AGENT_REGISTRY.get(agent_id, {}).get("requires_document", False),
            }
            for agent_id in FLOW_AGENT_IDS
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

        available_agent_ids = {agent["agent_id"] for agent in matched_agents}
        templates = _filter_flow_templates(available_agent_ids)

        total_count = len(matched_agents)
        bounded_limit = normalize_page_limit(limit)
        offset = parse_offset_cursor(cursor)
        page, truncated, next_cursor = offset_page(
            matched_agents,
            limit=bounded_limit,
            cursor=offset,
        )

        searched = bool(str(query or "").strip() or normalized_category)
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
                f"Found {len(templates)} compatible templates and {total_count} matching agents "
                f"(showing {len(page)}). "
                "Use validate_flow to check a custom workflow, or create_flow to save one."
            )

        return {
            "templates": templates,
            "available_agents": page,
            "total_count": total_count,
            "returned_count": len(page),
            "truncated": truncated,
            "next_cursor": next_cursor,
            "limit": bounded_limit,
            "query": str(query or "").strip() or None,
            "category": normalized_category,
            "message": message,
        }

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
        for agent_id, config in AGENT_REGISTRY.items():
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
        bounded_limit = normalize_page_limit(limit)
        offset = parse_offset_cursor(cursor)
        page, truncated, next_cursor = offset_page(
            matched,
            limit=bounded_limit,
            cursor=offset,
        )

        # Group only the returned page so callers see exactly what this page holds.
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

        searched = bool(str(query or "").strip() or normalized_category)
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
                "Attach each Output agent to exactly one earlier Extraction step; "
                "it is an output branch, not a control-path step."
            )
        else:
            message = (
                f"Found {total_agents} matching agents (showing {len(page)}). "
                "No output agents are on this page."
            )

        return {
            "categories": categories,
            "output_agents": output_agents,
            "extraction_agents": extraction_agents,
            "validation_agents": validation_agents,
            "total_agents": total_agents,
            "returned_count": len(page),
            "total_count": total_agents,
            "truncated": truncated,
            "next_cursor": next_cursor,
            "limit": bounded_limit,
            "query": str(query or "").strip() or None,
            "category": normalized_category,
            "message": message,
        }

    return handler


def _get_current_flow_handler():
    """Create handler for the get_current_flow tool.

    Returns the current flow being edited in the UI in execution order.
    The execution order is determined by traversing edges from the entry node,
    NOT by the order nodes were placed on the canvas.
    """
    def handler() -> Dict[str, Any]:
        """Get the current flow being edited in the UI.

        Returns the flow definition with nodes listed in EXECUTION ORDER
        (following edges from entry node), not canvas placement order.

        Returns:
            Dict with flow details in execution order, or error if no flow context
        """
        flow_context = get_current_flow_context()

        if not flow_context:
            return {
                "success": False,
                "error": "No flow is currently being edited",
                "help": "The user must be on the Flows tab with a flow open to use this tool"
            }

        flow_name = flow_context.get("flow_name", "Untitled Flow")
        nodes = flow_context.get("nodes", [])
        edges = flow_context.get("edges", [])
        if not nodes:
            domain_envelope_analysis = current_flow_domain_envelope_analysis(
                flow_context=flow_context,
                agent_registry=AGENT_REGISTRY,
            )
            return {
                "success": True,
                "flow_name": flow_name,
                "step_count": 0,
                "message": "Flow is empty - no steps have been added yet",
                "steps": [],
                "domain_envelope_analysis": domain_envelope_analysis,
                "execution_order_markdown": f"# {flow_name}\n\nThis flow has no steps yet."
            }

        # Project the same control topology used by save, batch, and runtime.
        node_by_id = {n.get("id"): n for n in nodes}
        projection = project_executable_flow_graph(flow_context, raise_on_invalid=False)
        output_attachment_by_node_id = {
            attachment.output_node_id: attachment
            for attachment in projection.output_attachments
        }
        execution_order = [
            node_by_id[node_id]
            for node_id in projection.ordered_executable_node_ids
            if node_id in node_by_id
        ]
        disconnected_ids = {
            node_id
            for issue in projection.issues
            if issue.code == "disconnected"
            for node_id in issue.node_ids
        }
        disconnected = [
            node_by_id[node_id]
            for node_id in projection.control_node_ids
            if node_id in disconnected_ids and node_id in node_by_id
        ]

        # Build the response
        steps = []
        validation_warnings = [
            {
                "type": "CRITICAL",
                "node_id": issue.node_ids[0] if issue.node_ids else None,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in projection.issues
        ]
        markdown_lines = [
            f"# {flow_name}",
            "",
            f"**{len(execution_order)} executable steps in execution order:**",
            "",
        ]
        task_input_node = next(
            (
                node
                for node in nodes
                if node.get("type") == "task_input"
                or node.get("data", node).get("agent_id") == "task_input"
            ),
            None,
        )
        if task_input_node is not None:
            task_data = task_input_node.get("data", task_input_node)
            task_instructions = task_data.get("task_instructions")
            if task_instructions and task_instructions.strip():
                markdown_lines.extend(
                    [
                        "**Task Input:** " + _truncate_preview(task_instructions, 300),
                        "",
                    ]
                )
            else:
                validation_warnings.append({
                    "type": "CRITICAL",
                    "node_id": task_input_node.get("id"),
                    "message": "task_input node has EMPTY task_instructions (this is required content)",
                })
                markdown_lines.extend(["**Task Input:** ⚠️ EMPTY (required)", ""])

        def _attachment_only_warning_for_node(node: Dict[str, Any]) -> Optional[str]:
            node_data = node.get("data", node)
            agent_id = node_data.get("agent_id", "unknown")
            entry = AGENT_REGISTRY.get(agent_id)
            if not isinstance(entry, dict):
                return None
            if agent_allows_ordinary_flow_step(agent_id, entry):
                return None
            agent_name = str(
                entry.get("name")
                or node_data.get("agent_display_name")
                or agent_id
            )
            return attachment_only_validator_reason(agent_name)

        for i, node in enumerate(execution_order, 1):
            node_data = node.get("data", node)  # Handle both nested and flat structures
            node_type = node.get("type", "agent")
            agent_id = node_data.get("agent_id", "unknown")
            display_name = node_data.get("agent_display_name", agent_id)
            custom_instructions = node_data.get("custom_instructions")
            task_instructions = node_data.get("task_instructions")
            output_filename_template = node_data.get("output_filename_template")
            include_evidence = node_data.get("include_evidence")
            projection_plan = node_data.get("projection_plan")
            output_key = node_data.get("output_key", f"step_{i}_output")
            validation_attachments = node_data.get("validation_attachments") or []
            output_attachment = output_attachment_by_node_id.get(str(node.get("id") or ""))

            # Check if this is a task_input node
            is_task_input = node_type == "task_input" or agent_id == "task_input"
            flow_step_policy_warning = (
                None if is_task_input else _attachment_only_warning_for_node(node)
            )

            step_info = {
                "step": i,
                "node_id": node.get("id"),
                "node_type": node_type,
                "agent_id": agent_id,
                "agent_display_name": display_name,
                "output_key": output_key
            }
            # For task_input nodes, ALWAYS include task_instructions (even if empty)
            # This allows Claude to detect empty task_instructions as a verification error
            if is_task_input:
                step_info["task_instructions"] = task_instructions or ""  # Empty string if None/empty
                is_empty = not task_instructions or not task_instructions.strip()
                step_info["task_instructions_is_empty"] = is_empty
                if is_empty:
                    validation_warnings.append({
                        "type": "CRITICAL",
                        "node_id": node.get("id"),
                        "message": "task_input node has EMPTY task_instructions (this is required content)"
                    })
            if custom_instructions:
                step_info["custom_instructions"] = custom_instructions
            if output_filename_template:
                step_info["output_filename_template"] = output_filename_template
            if include_evidence is not None:
                step_info["include_evidence"] = bool(include_evidence)
            if isinstance(projection_plan, dict):
                step_info["projection_plan"] = projection_plan
            if validation_attachments:
                step_info["validation_attachments"] = validation_attachments
            if output_attachment is not None:
                source_node = node_by_id.get(output_attachment.source_node_id, {})
                source_data = source_node.get("data", source_node)
                step_info["output_attachment"] = {
                    **output_attachment.to_dict(),
                    "source_agent_id": source_data.get("agent_id"),
                    "source_agent_display_name": source_data.get(
                        "agent_display_name",
                        output_attachment.source_node_id,
                    ),
                }
            if flow_step_policy_warning:
                step_info["flow_step_policy_warning"] = flow_step_policy_warning
                validation_warnings.append({
                    "type": "CRITICAL",
                    "node_id": node.get("id"),
                    "message": flow_step_policy_warning,
                })

            steps.append(step_info)

            # Build markdown representation
            markdown_lines.append(f"## Step {i}: {display_name}")
            markdown_lines.append(f"- **Type:** `{node_type}`")
            markdown_lines.append(f"- **Agent:** `{agent_id}`")
            if is_task_input:
                markdown_lines.append("- **Input:** Flow entry point")
                if task_instructions and task_instructions.strip():
                    truncated = _truncate_preview(task_instructions, 300)
                    markdown_lines.append(f"- **Task Instructions:** {truncated}")
                else:
                    # Explicitly flag empty task_instructions as a warning
                    markdown_lines.append("- **Task Instructions:** ⚠️ EMPTY (this is required content)")
            else:
                markdown_lines.append("- **Input:** Flow task and loaded document context")
                if flow_step_policy_warning:
                    markdown_lines.append(
                        f"- **Flow Placement:** CRITICAL - {flow_step_policy_warning}"
                    )
                if custom_instructions:
                    markdown_lines.append(
                        f"- **Custom Instructions:** {_truncate_preview(custom_instructions, 200)}"
                    )
                if output_filename_template:
                    markdown_lines.append(
                        "- **Output Filename Template:** "
                        f"{_truncate_preview(output_filename_template, 100)}"
                    )
                if output_attachment is not None:
                    source_node = node_by_id.get(output_attachment.source_node_id, {})
                    source_data = source_node.get("data", source_node)
                    markdown_lines.append(
                        "- **Formatter Binding:** Only projects the result owned by "
                        f"{source_data.get('agent_display_name', output_attachment.source_node_id)} "
                        f"(`{output_attachment.source_node_id}`)"
                    )
                if validation_attachments:
                    active_enabled = [
                        attachment
                        for attachment in validation_attachments
                        if attachment.get("state") == "active" and attachment.get("enabled")
                    ]
                    opted_out = [
                        attachment
                        for attachment in validation_attachments
                        if attachment.get("state") == "active"
                        and not attachment.get("enabled")
                    ]
                    under_development_metadata = [
                        attachment
                        for attachment in validation_attachments
                        if attachment.get("state") == "under_development"
                    ]
                    markdown_lines.append(
                        "- **Validation Attachments:** "
                        f"{len(active_enabled)} active scheduled"
                        + (f", {len(opted_out)} opted out" if opted_out else "")
                        + (
                            f", {len(under_development_metadata)} under-development metadata"
                            if under_development_metadata
                            else ""
                        )
                    )
            markdown_lines.append(f"- **Output Key:** `{output_key}`")
            markdown_lines.append("")

        topology_issues = list(projection.issues)
        if topology_issues:
            markdown_lines.append("---")
            markdown_lines.append("🚫 **Invalid executable flow topology:**")
            for issue in topology_issues:
                markdown_lines.append(f"- `[{issue.code}]` {issue.message}")
            markdown_lines.append("")

        # Add disconnected nodes warning if any
        if disconnected:
            markdown_lines.append("---")
            markdown_lines.append(f"⚠️ **Warning:** {len(disconnected)} disconnected node(s) not in execution path:")
            for node in disconnected:
                node_data = node.get("data", node)
                markdown_lines.append(f"- {node_data.get('agent_display_name', node_data.get('agent_id', 'unknown'))}")
                flow_step_policy_warning = _attachment_only_warning_for_node(node)
                if flow_step_policy_warning:
                    markdown_lines.append(f"  - CRITICAL: {flow_step_policy_warning}")
            markdown_lines.append("")

        # Add edge information
        if edges:
            markdown_lines.append("---")
            markdown_lines.append("**Connections:**")
            for edge in edges:
                source_node = node_by_id.get(edge.get("source"), {})
                target_node = node_by_id.get(edge.get("target"), {})
                source_name = source_node.get("data", source_node).get("agent_display_name", edge.get("source"))
                target_name = target_node.get("data", target_node).get("agent_display_name", edge.get("target"))
                edge_role = edge.get("role") or "control_flow"
                markdown_lines.append(
                    f"- {source_name} → {target_name} (`{edge_role}`)"
                )

        if projection.output_attachments:
            markdown_lines.append("")
            markdown_lines.append("**Formatter output branches:**")
            for attachment in projection.output_attachments:
                source_node = node_by_id.get(attachment.source_node_id, {})
                source_data = source_node.get("data", source_node)
                output_node = node_by_id.get(attachment.output_node_id, {})
                output_data = output_node.get("data", output_node)
                markdown_lines.append(
                    "- "
                    f"{output_data.get('agent_display_name', attachment.output_node_id)} "
                    "creates its own artifact from only "
                    f"{source_data.get('agent_display_name', attachment.source_node_id)}."
                )
            markdown_lines.append(
                "Multiple formatter branches create multiple independent chat/file "
                "outputs; control-flow steps may continue after a branch."
            )

        domain_envelope_analysis = current_flow_domain_envelope_analysis(
            flow_context=flow_context,
            agent_registry=AGENT_REGISTRY,
        )
        if domain_envelope_analysis["envelope_node_count"]:
            markdown_lines.append("---")
            markdown_lines.append("**Domain Envelope Metadata:**")
            for envelope_node in domain_envelope_analysis["nodes"]:
                scheduled_count = len(
                    envelope_node.get("validation_schedule", {}).get("scheduled_validators", [])
                )
                opt_out_count = len(
                    envelope_node.get("validation_schedule", {}).get("opt_outs", [])
                )
                inactive_count = len(
                    envelope_node.get("validation_schedule", {}).get("inactive_metadata", [])
                )
                replacement_count = len(
                    envelope_node.get("validation_schedule", {}).get("replacement_validators", [])
                )
                supplemental_count = len(
                    envelope_node.get("validation_schedule", {}).get("supplemental_validators", [])
                )
                markdown_lines.append(
                    "- "
                    f"{envelope_node.get('agent_display_name') or envelope_node.get('agent_id')} "
                    f"produces `{envelope_node.get('domain_pack_id')}` envelope objects "
                    f"({scheduled_count} scheduled validators"
                    + (f", {opt_out_count} policy opt-outs" if opt_out_count else "")
                    + (f", {replacement_count} replacement validators" if replacement_count else "")
                    + (f", {supplemental_count} supplemental validators" if supplemental_count else "")
                    + (f", {inactive_count} under-development metadata" if inactive_count else "")
                    + ")"
                )
            markdown_lines.append("")

        # Count critical issues for easy detection
        critical_count = sum(1 for w in validation_warnings if w.get("type") == "CRITICAL")

        return {
            "success": True,
            "flow_name": flow_name,
            "step_count": len(execution_order),
            "disconnected_count": len(disconnected),
            "validation_warnings": validation_warnings,
            "has_critical_issues": critical_count > 0,
            "critical_issue_count": critical_count,
            "domain_envelope_analysis": domain_envelope_analysis,
            "executable_graph": projection.to_dict(),
            "steps": steps,
            "edges": [
                {
                    "id": e.get("id"),
                    "source": e.get("source"),
                    "target": e.get("target"),
                    "role": e.get("role") or "control_flow",
                    "satisfies_binding_id": e.get("satisfies_binding_id"),
                    "replaces_attachment_id": e.get("replaces_attachment_id"),
                }
                for e in edges
            ],
            "execution_order_markdown": "\n".join(markdown_lines),
            "message": f"Flow '{flow_name}' has {len(execution_order)} steps in execution order" +
                      (f" ({len(disconnected)} disconnected)" if disconnected else "") +
                      (f" ⚠️ {critical_count} CRITICAL issue(s) found" if critical_count > 0 else "")
        }

    return handler


# =============================================================================
# Tool Registration
# =============================================================================

def register_flow_tools() -> None:
    """Register all flow tools with the DiagnosticToolRegistry.

    Called on module import to make flow tools available to Opus.
    """
    registry = get_diagnostic_tools_registry()

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
include source_step, the 1-based index of exactly one earlier Extraction agent.
Multiple formatters may point to the same extractor, and the ordinary extraction
steps may continue after a formatter branch.

Returns the created flow's ID for reference.

NOTE: This tool saves the flow to the database. Use validate_flow first
to check for issues without saving.""",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255,
                    "description": "Flow name (must be unique per user)"
                },
                "description": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "What this flow does - describe the overall goal"
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 30,
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "enum": FLOW_AGENT_IDS,
                                "description": "Agent to use for this step"
                            },
                            "step_goal": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Goal description for this step (optional)"
                            },
                            "custom_instructions": {
                                "type": "string",
                                "maxLength": 2000,
                                "description": "Custom instructions appended to agent prompt (optional)"
                            },
                            "source_step": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 29,
                                "description": "Required only for Output agents: 1-based earlier Extraction step whose result this formatter receives"
                            },
                            "output_filename_template": {
                                "type": "string",
                                "maxLength": 255,
                                "description": "Optional for file Output agents: runtime-resolved filename template using built-ins such as {{input_filename_stem}} and {{timestamp}}"
                            }
                        },
                        "required": ["agent_id"]
                    },
                    "description": "Ordered list of flow steps"
                }
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
                    "description": "Flow name to validate (optional)"
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string"},
                            "step_goal": {"type": "string"},
                            "custom_instructions": {"type": "string"}
                        },
                        "required": ["agent_id"]
                    },
                    "description": "Steps to validate"
                }
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

Use this tool to show the user example workflows they can use as
starting points. Returns template flows for common curation tasks
and a searchable, paged list of available agents with their descriptions.

Pass query to search agents by id, name, or description, category to keep
one kind (Extraction, Validation, Output), and limit/cursor to page through
large agent catalogs.

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
        description="""Get the current flow being edited in the Flow Builder UI.

ALWAYS call this tool when you need to analyze, verify, or discuss the user's
current flow. This tool returns the flow definition in EXECUTION ORDER
(following edges from the entry node), not canvas placement order.

Returns:
- flow_name: Name of the flow
- step_count: Number of steps in execution order
- steps: Array of steps with agent_id, display_name, custom_instructions, etc.
- edges: Connections between steps
- execution_order_markdown: Human-readable markdown representation
- disconnected_count: Number of nodes not connected to the main flow (warnings)

Use this tool BEFORE attempting to validate or provide feedback on a flow.
The tool ensures you see the flow exactly as it will execute.""",
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

    # -------------------------------------------------------------------------
    # get_available_agents - Return agent metadata for verification
    # -------------------------------------------------------------------------
    registry.register(
        name="get_available_agents",
        description="""Get available agents organized by category with descriptions.

Use this tool to understand agent types and purposes when verifying or analyzing flows.
Returns agents grouped by category (Extraction, Validation, Output) and identifies
which agents are designed for specific purposes:

- output_agents: Agents attached as output branches to one Extraction step
- extraction_agents: Agents that extract structured data from documents
- validation_agents: Agents that validate or look up structured entities

Pass query to search agents by id, name, or description, category to keep one
kind, and limit/cursor to page through large agent catalogs. Results report
total_count and next_cursor so you can fetch the rest.

ALWAYS call this tool along with get_current_flow() when verifying a flow,
so you can check if the flow ends with an appropriate output agent.""",
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
            },
        },
        handler=_get_available_agents_handler(),
        category="flows",
        tags=["flow", "agents", "catalog", "verification"]
    )
    logger.debug("Registered: get_available_agents")

    logger.info('Registered 5 flow tools (category: flows)')


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
