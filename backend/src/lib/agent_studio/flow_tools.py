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

from .catalog_service import AGENT_REGISTRY
from .diagnostic_tools import get_diagnostic_tools_registry

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

    Excludes 'supervisor' (flows have their own supervisor) and
    'task_input' (not an agent, handled specially as flow input).
    Returns sorted list for consistent ordering.
    """
    return sorted([
        agent_id for agent_id in AGENT_REGISTRY.keys()
        if agent_id not in ("supervisor", "task_input")
    ])


# Cached list for schema validation
FLOW_AGENT_IDS = _get_flow_agent_ids()


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
            steps: List of step configs with agent_id, step_goal, custom_instructions

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
                "input_source": "user_query",
                "output_key": "task_input"
            }
        }
        nodes = [task_input_node]

        # Add agent nodes for each step
        for i, step in enumerate(steps):
            node_id = f"step_{i+1}"
            agent_id = step["agent_id"]

            # Get display name from registry
            agent_info = AGENT_REGISTRY.get(agent_id, {})
            display_name = agent_info.get("name", agent_id.replace("_", " ").title())

            nodes.append({
                "id": node_id,
                "type": "agent",
                "position": {"x": 100, "y": 200 + (i * 150)},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": display_name,
                    "step_goal": step.get("step_goal"),
                    "custom_instructions": step.get("custom_instructions"),
                    "input_source": "previous_output",
                    "output_key": f"step_{i+1}_output"
                }
            })

        # Create edges: task_input -> step_1 -> step_2 -> ...
        edges = []
        for i in range(len(nodes) - 1):
            edges.append({
                "id": f"edge_{i+1}",
                "source": nodes[i]["id"],
                "target": nodes[i+1]["id"]
            })

        flow_definition = {
            "version": "1.0",
            "nodes": nodes,
            "edges": edges,
            "entry_node_id": "task_input_0"  # task_input must be entry node
        }

        # Validate via Pydantic schema (same validation as API endpoint)
        from src.schemas.flows import FlowDefinition
        from pydantic import ValidationError

        try:
            validated_flow_def = FlowDefinition(**flow_definition)
            flow_definition = validated_flow_def.model_dump()
        except ValidationError as e:
            # Extract user-friendly error message
            errors = e.errors()
            if errors:
                error_msg = errors[0].get("msg", "Flow validation failed")
            else:
                error_msg = "Flow validation failed"
            return {"success": False, "error": error_msg}

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
        if "pdf" not in seen_agents and any(
            a in seen_agents for a in ["gene", "allele", "disease", "gene_expression"]
        ):
            suggestions.append(
                "Consider adding 'pdf' step first to extract entities from documents"
            )

        if "chat_output" not in seen_agents and len(seen_agents) >= 2:
            suggestions.append(
                "Consider adding 'chat_output' as final step to display results, or use file formatters (csv_formatter, tsv_formatter, json_formatter) for downloadable files"
            )

        if "gene_expression" in seen_agents and "gene" not in seen_agents:
            suggestions.append(
                "Consider adding 'gene' step after 'gene_expression' to validate gene identifiers"
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
    def handler() -> Dict[str, Any]:
        """Get flow templates and available agents.

        Returns:
            Dict with templates list, available_agents, and help message
        """
        templates = [
            {
                "name": "Gene Curation",
                "description": "Extract gene mentions from PDF and validate against database",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Find gene symbols and identifiers"},
                    {"agent_id": "gene", "step_goal": "Validate genes in Alliance database"},
                    {"agent_id": "chat_output", "step_goal": "Display validated results"}
                ]
            },
            {
                "name": "Disease Annotation",
                "description": "Extract disease mentions and map to ontology terms",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Find disease mentions"},
                    {"agent_id": "disease", "step_goal": "Map to Disease Ontology terms"},
                    {"agent_id": "chat_output", "step_goal": "Display annotation results"}
                ]
            },
            {
                "name": "Chemical Entity Extraction",
                "description": "Extract chemical compounds and link to ChEBI",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Extract chemical names"},
                    {"agent_id": "chemical", "step_goal": "Map to ChEBI identifiers"}
                ]
            },
            {
                "name": "Gene Expression Analysis",
                "description": "Extract gene expression data from methods sections",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Find experimental methods"},
                    {"agent_id": "gene_expression", "step_goal": "Extract expression patterns"},
                    {"agent_id": "gene", "step_goal": "Validate gene identifiers"},
                    {"agent_id": "chat_output", "step_goal": "Display expression data"}
                ]
            },
            {
                "name": "Allele Annotation",
                "description": "Extract allele/variant mentions and link to database",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Find allele/variant mentions"},
                    {"agent_id": "allele", "step_goal": "Validate alleles in Alliance database"},
                    {"agent_id": "chat_output", "step_goal": "Display allele results"}
                ]
            },
            {
                "name": "GO Annotation Pipeline",
                "description": "Extract and validate Gene Ontology annotations",
                "steps": [
                    {"agent_id": "pdf", "step_goal": "Find GO term mentions and gene functions"},
                    {"agent_id": "gene", "step_goal": "Validate gene identifiers"},
                    {"agent_id": "gene_ontology", "step_goal": "Validate GO terms"},
                    {"agent_id": "chat_output", "step_goal": "Display GO annotations"}
                ]
            }
        ]

        # Get available agents with descriptions
        available_agents = []
        for agent_id in FLOW_AGENT_IDS:
            agent_info = AGENT_REGISTRY.get(agent_id, {})
            available_agents.append({
                "agent_id": agent_id,
                "display_name": agent_info.get("name", agent_id),
                "description": agent_info.get("description", ""),
                "category": agent_info.get("category", "Unknown"),
                "requires_document": agent_info.get("requires_document", False)
            })

        return {
            "templates": templates,
            "available_agents": available_agents,
            "message": (
                f"Found {len(templates)} templates and {len(available_agents)} available agents. "
                "Use validate_flow to check a custom workflow, or create_flow to save one."
            )
        }

    return handler


def _get_available_agents_handler():
    """Create handler for the get_available_agents tool.

    Returns all available agents organized by category with metadata.
    This helps Claude understand agent types and purposes for flow verification.
    """
    def handler() -> Dict[str, Any]:
        """Get all available agents organized by category.

        Returns:
            Dict with categories, output_agents, extraction_agents, validation_agents
        """
        # Build categories dict from AGENT_REGISTRY
        categories: Dict[str, List[Dict[str, str]]] = {}
        output_agents: List[str] = []
        extraction_agents: List[str] = []
        validation_agents: List[str] = []

        for agent_id, config in AGENT_REGISTRY.items():
            # Skip supervisor (internal routing) and task_input (flow input node)
            if agent_id in ("supervisor", "task_input"):
                continue

            category = config.get("category", "Unknown")
            agent_info = {
                "agent_id": agent_id,
                "name": config.get("name", agent_id),
                "description": config.get("description", ""),
                "requires_document": config.get("requires_document", False),
            }

            # Add to category list
            if category not in categories:
                categories[category] = []
            categories[category].append(agent_info)

            # Categorize by purpose for verification
            if category == "Output":
                output_agents.append(agent_id)
            elif category == "Extraction":
                extraction_agents.append(agent_id)
            elif category == "Validation":
                validation_agents.append(agent_id)

        return {
            "categories": categories,
            "output_agents": output_agents,
            "extraction_agents": extraction_agents,
            "validation_agents": validation_agents,
            "total_agents": sum(len(agents) for agents in categories.values()),
            "message": (
                f"Found {sum(len(agents) for agents in categories.values())} agents across "
                f"{len(categories)} categories. Output agents ({len(output_agents)}): "
                f"{', '.join(output_agents)}. These are designed to be final steps in a flow."
            )
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
        entry_node_id = flow_context.get("entry_node_id")

        if not nodes:
            return {
                "success": True,
                "flow_name": flow_name,
                "step_count": 0,
                "message": "Flow is empty - no steps have been added yet",
                "steps": [],
                "execution_order_markdown": f"# {flow_name}\n\nThis flow has no steps yet."
            }

        # Build lookup structures
        node_by_id = {n.get("id"): n for n in nodes}
        edges_from = {}  # source_id -> list of target_ids
        edges_to = {}    # target_id -> list of source_ids

        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source and target:
                edges_from.setdefault(source, []).append(target)
                edges_to.setdefault(target, []).append(source)

        # Find entry node: either explicitly set, or the node with no incoming edges
        if entry_node_id and entry_node_id in node_by_id:
            start_node_id = entry_node_id
        else:
            # Find node with no incoming edges
            nodes_with_incoming = set(edges_to.keys())
            potential_starts = [n.get("id") for n in nodes if n.get("id") not in nodes_with_incoming]
            start_node_id = potential_starts[0] if potential_starts else (nodes[0].get("id") if nodes else None)

        # Traverse the flow in execution order (BFS from entry node)
        execution_order = []
        visited = set()
        queue = [start_node_id] if start_node_id else []

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            node = node_by_id.get(current_id)
            if node:
                execution_order.append(node)
                # Add next nodes (targets of outgoing edges)
                for next_id in edges_from.get(current_id, []):
                    if next_id not in visited:
                        queue.append(next_id)

        # Include any disconnected nodes at the end (with warning)
        disconnected = []
        for node in nodes:
            if node.get("id") not in visited:
                disconnected.append(node)

        # Build the response
        steps = []
        validation_warnings = []  # Collect issues for easy detection
        markdown_lines = [f"# {flow_name}", "", f"**{len(execution_order)} steps in execution order:**", ""]

        for i, node in enumerate(execution_order, 1):
            node_data = node.get("data", node)  # Handle both nested and flat structures
            node_type = node.get("type", "agent")
            agent_id = node_data.get("agent_id", "unknown")
            display_name = node_data.get("agent_display_name", agent_id)
            custom_instructions = node_data.get("custom_instructions")
            task_instructions = node_data.get("task_instructions")
            input_source = node_data.get("input_source", "previous_output")
            custom_input = node_data.get("custom_input")
            output_key = node_data.get("output_key", f"step_{i}_output")

            # Check if this is a task_input node
            is_task_input = node_type == "task_input" or agent_id == "task_input"

            step_info = {
                "step": i,
                "node_id": node.get("id"),
                "node_type": node_type,
                "agent_id": agent_id,
                "agent_display_name": display_name,
                "input_source": input_source,
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
            if custom_input:
                step_info["custom_input"] = custom_input

            steps.append(step_info)

            # Build markdown representation
            markdown_lines.append(f"## Step {i}: {display_name}")
            markdown_lines.append(f"- **Type:** `{node_type}`")
            markdown_lines.append(f"- **Agent:** `{agent_id}`")
            if is_task_input:
                markdown_lines.append(f"- **Input:** Flow entry point")
                if task_instructions and task_instructions.strip():
                    truncated = task_instructions[:300] + ('...' if len(task_instructions) > 300 else '')
                    markdown_lines.append(f"- **Task Instructions:** {truncated}")
                else:
                    # Explicitly flag empty task_instructions as a warning
                    markdown_lines.append(f"- **Task Instructions:** ⚠️ EMPTY (this is required content)")
            else:
                markdown_lines.append(f"- **Input:** {input_source.replace('_', ' ')}")
                if custom_input:
                    markdown_lines.append(f"- **Custom Input:** {custom_input[:100]}...")
                if custom_instructions:
                    markdown_lines.append(f"- **Custom Instructions:** {custom_instructions[:200]}{'...' if len(custom_instructions) > 200 else ''}")
            markdown_lines.append(f"- **Output Key:** `{output_key}`")
            markdown_lines.append("")

        # Check for parallel/branching flows (not yet supported)
        # Count outgoing edges per node
        outgoing_edge_counts: dict[str, int] = {}
        for edge in edges:
            source_id = edge.get("source")
            if source_id:
                outgoing_edge_counts[source_id] = outgoing_edge_counts.get(source_id, 0) + 1

        parallel_nodes = []
        for node in nodes:
            node_id = node.get("id")
            if outgoing_edge_counts.get(node_id, 0) > 1:
                node_data = node.get("data", node)
                node_name = node_data.get("agent_display_name", node_data.get("agent_id", "unknown"))
                parallel_nodes.append({"node_id": node_id, "name": node_name, "outgoing_count": outgoing_edge_counts[node_id]})
                validation_warnings.append({
                    "type": "CRITICAL",
                    "node_id": node_id,
                    "message": f"PARALLEL FLOW NOT YET SUPPORTED: '{node_name}' has {outgoing_edge_counts[node_id]} outgoing connections. Only sequential flows are currently supported (each node can connect to at most one next node). Parallel flows will be available in a future update."
                })

        # Add parallel flow warning to markdown if any
        if parallel_nodes:
            markdown_lines.append("---")
            markdown_lines.append(f"🚫 **CRITICAL: Parallel flows not yet supported** ({len(parallel_nodes)} node(s) with multiple outputs):")
            for pn in parallel_nodes:
                markdown_lines.append(f"- {pn['name']} has {pn['outgoing_count']} outgoing connections")
            markdown_lines.append("*Parallel/branching flows will be supported in a future update.*")
            markdown_lines.append("")

        # Add disconnected nodes warning if any
        if disconnected:
            markdown_lines.append("---")
            markdown_lines.append(f"⚠️ **Warning:** {len(disconnected)} disconnected node(s) not in execution path:")
            for node in disconnected:
                node_data = node.get("data", node)
                markdown_lines.append(f"- {node_data.get('agent_display_name', node_data.get('agent_id', 'unknown'))}")
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
                markdown_lines.append(f"- {source_name} → {target_name}")

        # Add warnings for disconnected nodes
        for node in disconnected:
            node_data = node.get("data", node)
            validation_warnings.append({
                "type": "WARNING",
                "node_id": node.get("id"),
                "message": f"Node '{node_data.get('agent_display_name', node_data.get('agent_id', 'unknown'))}' is disconnected and won't execute"
            })

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
            "steps": steps,
            "edges": [{"source": e.get("source"), "target": e.get("target")} for e in edges],
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
                                "maxLength": 500,
                                "description": "Goal description for this step (optional)"
                            },
                            "custom_instructions": {
                                "type": "string",
                                "maxLength": 2000,
                                "description": "Custom instructions appended to agent prompt (optional)"
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
and a list of all available agents with their descriptions.

Use this as a starting point when helping users design flows.""",
        input_schema={
            "type": "object",
            "properties": {},
            "description": "No parameters required"
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
        description="""Get all available agents organized by category with descriptions.

Use this tool to understand agent types and purposes when verifying or analyzing flows.
Returns agents grouped by category (Extraction, Validation, Output) and identifies
which agents are designed for specific purposes:

- output_agents: Agents meant to be the final step (chat_output, csv_formatter, tsv_formatter, json_formatter)
- extraction_agents: Agents that extract data from documents (pdf, gene_expression)
- validation_agents: Agents that validate/lookup data (gene, allele, disease, etc.)

ALWAYS call this tool along with get_current_flow() when verifying a flow,
so you can check if the flow ends with an appropriate output agent.""",
        input_schema={
            "type": "object",
            "properties": {},
            "description": "No parameters required"
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
