"""Canonical projection and validation of executable flow topology.

Only ``control_flow`` edges participate in the main execution topology.
Validator attachments are sidecars of their source step. Output attachments are
terminal leaves scheduled after the ordinary control path. Neither
attachment role creates a control-flow branch, join, entry, or exit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from src.lib.flow_edge_roles import (
    CONTROL_FLOW_EDGE_ROLE,
    OUTPUT_ATTACHMENT_EDGE_ROLE,
    VALIDATION_ATTACHMENT_EDGE_ROLE,
)


@dataclass(frozen=True)
class ExecutableFlowIssue:
    """One stable, serializable topology validation issue."""

    code: str
    message: str
    node_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationSidecar:
    """A non-control validator attachment associated with one control step."""

    edge_id: str
    source_node_id: str
    validator_node_id: str
    binding_id: str
    replaces_attachment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutputAttachment:
    """A terminal formatter bound to one control-step extraction result."""

    edge_id: str
    source_node_id: str
    output_node_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutableFlowGraph:
    """Canonical executable projection shared by all backend consumers."""

    control_node_ids: tuple[str, ...]
    ordered_control_node_ids: tuple[str, ...]
    ordered_executable_node_ids: tuple[str, ...]
    entry_node_ids: tuple[str, ...]
    exit_node_ids: tuple[str, ...]
    terminal_node_ids: tuple[str, ...]
    output_attachments: tuple[OutputAttachment, ...]
    validation_sidecars: tuple[ValidationSidecar, ...]
    issues: tuple[ExecutableFlowIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def sidecars_for(self, source_node_id: str) -> tuple[ValidationSidecar, ...]:
        return tuple(
            sidecar
            for sidecar in self.validation_sidecars
            if sidecar.source_node_id == source_node_id
        )

    def outputs_for(self, source_node_id: str) -> tuple[OutputAttachment, ...]:
        return tuple(
            attachment
            for attachment in self.output_attachments
            if attachment.source_node_id == source_node_id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "control_node_ids": list(self.control_node_ids),
            "ordered_control_node_ids": list(self.ordered_control_node_ids),
            "ordered_executable_node_ids": list(self.ordered_executable_node_ids),
            "entry_node_ids": list(self.entry_node_ids),
            "exit_node_ids": list(self.exit_node_ids),
            "terminal_node_ids": list(self.terminal_node_ids),
            "output_attachments": [
                attachment.to_dict() for attachment in self.output_attachments
            ],
            "validation_sidecars": [sidecar.to_dict() for sidecar in self.validation_sidecars],
            "issues": [issue.to_dict() for issue in self.issues],
        }


class ExecutableFlowTopologyError(ValueError):
    """Raised when a flow cannot be projected as one sequential control path."""

    def __init__(self, issues: Sequence[ExecutableFlowIssue]) -> None:
        self.issues = tuple(issues)
        details = "; ".join(f"[{issue.code}] {issue.message}" for issue in self.issues)
        super().__init__(f"Invalid executable flow topology: {details}")


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _items(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _validation_sidecar_binding_id(
    edge: Mapping[str, Any],
    node_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    explicit_binding_id = str(edge.get("satisfies_binding_id") or "").strip()
    if explicit_binding_id:
        return explicit_binding_id

    replaced_attachment_id = str(edge.get("replaces_attachment_id") or "").strip()
    source_node = node_by_id.get(str(edge.get("source") or ""), {})
    source_data = _mapping(source_node.get("data"))
    for attachment_value in _items(source_data.get("validation_attachments")):
        attachment = _mapping(attachment_value)
        if str(attachment.get("attachment_id") or "") != replaced_attachment_id:
            continue
        resolved_binding_id = str(
            attachment.get("validator_binding_id") or ""
        ).strip()
        if resolved_binding_id:
            return resolved_binding_id
    return replaced_attachment_id


def project_executable_flow_graph(
    flow_definition: Any,
    *,
    raise_on_invalid: bool = True,
) -> ExecutableFlowGraph:
    """Build the sole executable topology projection for a flow definition."""

    flow = _mapping(flow_definition)
    nodes = [_mapping(node) for node in _items(flow.get("nodes"))]
    edges = [_mapping(edge) for edge in _items(flow.get("edges"))]
    node_ids = tuple(
        str(node.get("id")) for node in nodes if str(node.get("id") or "").strip()
    )
    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id")}
    flow_version = str(flow.get("version") or "1.0")

    control_edges = [
        edge
        for edge in edges
        if str(edge.get("role") or CONTROL_FLOW_EDGE_ROLE) == CONTROL_FLOW_EDGE_ROLE
    ]
    validation_attachment_edges = [
        edge
        for edge in edges
        if str(edge.get("role") or CONTROL_FLOW_EDGE_ROLE)
        == VALIDATION_ATTACHMENT_EDGE_ROLE
    ]
    output_attachment_edges = [
        edge
        for edge in edges
        if str(edge.get("role") or CONTROL_FLOW_EDGE_ROLE)
        == OUTPUT_ATTACHMENT_EDGE_ROLE
    ]
    validation_target_ids = {
        str(edge.get("target"))
        for edge in validation_attachment_edges
        if edge.get("target")
    }
    declared_output_node_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("type") == "output"
    }
    output_target_ids = {
        str(edge.get("target"))
        for edge in output_attachment_edges
        if edge.get("target")
    }
    cross_role_target_ids = validation_target_ids & output_target_ids
    detached_output_node_ids = (
        declared_output_node_ids if flow_version == "1.1" else output_target_ids
    )
    attachment_target_ids = validation_target_ids | detached_output_node_ids
    control_node_ids = tuple(
        node_id for node_id in node_ids if node_id not in attachment_target_ids
    )
    control_node_set = set(control_node_ids)

    outgoing: dict[str, list[Mapping[str, Any]]] = {
        node_id: [] for node_id in control_node_ids
    }
    incoming: dict[str, list[Mapping[str, Any]]] = {
        node_id: [] for node_id in control_node_ids
    }
    issues: list[ExecutableFlowIssue] = []

    for node_id in sorted(cross_role_target_ids):
        issues.append(
            ExecutableFlowIssue(
                code="attachment_target_role_conflict",
                message=(
                    f"Node '{node_id}' cannot be both an output attachment and a "
                    "validation attachment target"
                ),
                node_ids=(node_id,),
                edge_ids=tuple(
                    str(edge.get("id") or "")
                    for edge in (*output_attachment_edges, *validation_attachment_edges)
                    if str(edge.get("target") or "") == node_id
                ),
            )
        )

    for edge in control_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_id = str(edge.get("id") or "")
        if source not in control_node_set or target not in control_node_set:
            validation_ids = tuple(
                node_id for node_id in (source, target) if node_id in validation_target_ids
            )
            output_ids = tuple(
                node_id for node_id in (source, target) if node_id in detached_output_node_ids
            )
            if output_ids:
                code = "output_in_control_flow"
                message = "Output nodes cannot participate in control_flow edges"
                attached_ids = output_ids
            else:
                code = "sidecar_in_control_flow"
                message = "Validation sidecar nodes cannot participate in control_flow edges"
                attached_ids = validation_ids
            issues.append(
                ExecutableFlowIssue(
                    code=code,
                    message=message,
                    node_ids=attached_ids,
                    edge_ids=(edge_id,) if edge_id else (),
                )
            )
            continue
        outgoing[source].append(edge)
        incoming[target].append(edge)

    for node_id in control_node_ids:
        if len(outgoing[node_id]) > 1:
            issues.append(
                ExecutableFlowIssue(
                    code="branch",
                    message=(
                        f"Control node '{node_id}' has {len(outgoing[node_id])} outgoing "
                        "control_flow edges; sequential flows require at most one"
                    ),
                    node_ids=(node_id,),
                    edge_ids=tuple(str(edge.get("id") or "") for edge in outgoing[node_id]),
                )
            )
        if len(incoming[node_id]) > 1:
            issues.append(
                ExecutableFlowIssue(
                    code="join",
                    message=(
                        f"Control node '{node_id}' has {len(incoming[node_id])} incoming "
                        "control_flow edges; sequential flows require at most one"
                    ),
                    node_ids=(node_id,),
                    edge_ids=tuple(str(edge.get("id") or "") for edge in incoming[node_id]),
                )
            )

    entry_node_ids = tuple(
        node_id for node_id in control_node_ids if not incoming[node_id]
    )
    exit_node_ids = tuple(
        node_id for node_id in control_node_ids if not outgoing[node_id]
    )
    if len(entry_node_ids) != 1:
        issues.append(
            ExecutableFlowIssue(
                code="ambiguous_entry",
                message=(
                    "Sequential control flow requires exactly one entry node; "
                    f"found {len(entry_node_ids)}"
                ),
                node_ids=entry_node_ids,
            )
        )
    declared_entry = str(flow.get("entry_node_id") or "")
    if len(entry_node_ids) == 1 and declared_entry != entry_node_ids[0]:
        issues.append(
            ExecutableFlowIssue(
                code="entry_mismatch",
                message=(
                    f"entry_node_id '{declared_entry}' does not match the control-flow "
                    f"entry '{entry_node_ids[0]}'"
                ),
                node_ids=tuple(
                    node_id for node_id in (declared_entry, entry_node_ids[0]) if node_id
                ),
            )
        )
    task_input_ids = tuple(
        node_id
        for node_id in control_node_ids
        if node_by_id[node_id].get("type") == "task_input"
        or _mapping(node_by_id[node_id].get("data")).get("agent_id") == "task_input"
    )
    if len(task_input_ids) == 1 and entry_node_ids != task_input_ids:
        issues.append(
            ExecutableFlowIssue(
                code="task_input_not_entry",
                message=(
                    f"Task Input node '{task_input_ids[0]}' must be the control-flow entry"
                ),
                node_ids=task_input_ids,
            )
        )
    if len(exit_node_ids) != 1:
        issues.append(
            ExecutableFlowIssue(
                code="ambiguous_terminal",
                message=(
                    "Sequential control flow requires exactly one terminal node; "
                    f"found {len(exit_node_ids)}"
                ),
                node_ids=exit_node_ids,
            )
        )

    ordered_control: list[str] = []
    seen: set[str] = set()
    cursor = entry_node_ids[0] if len(entry_node_ids) == 1 else declared_entry
    while cursor in control_node_set and cursor not in seen:
        ordered_control.append(cursor)
        seen.add(cursor)
        next_edges = outgoing[cursor]
        if len(next_edges) != 1:
            break
        cursor = str(next_edges[0].get("target") or "")
    # Detect cycles outside the declared path too, so a cycle plus a valid path is
    # not reported merely as an orphan.
    globally_seen: set[str] = set()
    for start in control_node_ids:
        if start in globally_seen:
            continue
        local_positions: dict[str, int] = {}
        path: list[str] = []
        current = start
        while current in control_node_set and current not in globally_seen:
            if current in local_positions:
                cycle_nodes = tuple(path[local_positions[current]:])
                if not any(issue.code == "cycle" and issue.node_ids == cycle_nodes for issue in issues):
                    issues.append(
                        ExecutableFlowIssue(
                            code="cycle",
                            message=(
                                "Control flow contains a cycle through nodes "
                                + ", ".join(f"'{node_id}'" for node_id in cycle_nodes)
                            ),
                            node_ids=cycle_nodes,
                        )
                    )
                break
            local_positions[current] = len(path)
            path.append(current)
            next_edges = outgoing[current]
            if len(next_edges) != 1:
                break
            current = str(next_edges[0].get("target") or "")
        globally_seen.update(path)

    disconnected = tuple(node_id for node_id in control_node_ids if node_id not in seen)
    if disconnected:
        issues.append(
            ExecutableFlowIssue(
                code="disconnected",
                message=(
                    "Executable control nodes are disconnected from the entry path: "
                    + ", ".join(f"'{node_id}'" for node_id in disconnected)
                ),
                node_ids=disconnected,
            )
        )

    output_attachments: list[OutputAttachment] = []
    output_edge_ids_by_target: dict[str, str] = {}
    if flow_version != "1.1" and output_attachment_edges:
        issues.append(
            ExecutableFlowIssue(
                code="output_attachment_requires_v1_1",
                message="output_attachment edges require flow schema version '1.1'",
                edge_ids=tuple(
                    str(edge.get("id") or "") for edge in output_attachment_edges
                ),
            )
        )

    for edge in output_attachment_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_id = str(edge.get("id") or "")
        source_node = node_by_id.get(source, {})
        target_node = node_by_id.get(target, {})
        if source not in control_node_set:
            issues.append(
                ExecutableFlowIssue(
                    code="invalid_output_source",
                    message=(
                        f"Output attachment '{edge_id}' must originate from a "
                        "control-flow extraction node"
                    ),
                    node_ids=tuple(node_id for node_id in (source, target) if node_id),
                    edge_ids=(edge_id,) if edge_id else (),
                )
            )
        if target_node.get("type") != "output":
            issues.append(
                ExecutableFlowIssue(
                    code="invalid_output_target",
                    message=(
                        f"Output attachment '{edge_id}' must target a node with type 'output'"
                    ),
                    node_ids=(target,) if target else (),
                    edge_ids=(edge_id,) if edge_id else (),
                )
            )
        if source_node.get("type") == "output":
            issues.append(
                ExecutableFlowIssue(
                    code="output_from_output",
                    message="Output nodes cannot own other output attachments",
                    node_ids=tuple(node_id for node_id in (source, target) if node_id),
                    edge_ids=(edge_id,) if edge_id else (),
                )
            )
        prior_edge_id = output_edge_ids_by_target.get(target)
        if prior_edge_id:
            issues.append(
                ExecutableFlowIssue(
                    code="multiple_output_sources",
                    message=(
                        f"Output node '{target}' must be attached to exactly one source"
                    ),
                    node_ids=(target,),
                    edge_ids=(prior_edge_id, edge_id),
                )
            )
        elif target:
            output_edge_ids_by_target[target] = edge_id
        output_attachments.append(
            OutputAttachment(
                edge_id=edge_id,
                source_node_id=source,
                output_node_id=target,
            )
        )

    if flow_version == "1.1":
        missing_output_bindings = tuple(
            node_id
            for node_id in node_ids
            if node_id in declared_output_node_ids
            and node_id not in output_edge_ids_by_target
        )
        for node_id in missing_output_bindings:
            issues.append(
                ExecutableFlowIssue(
                    code="missing_output_binding",
                    message=(
                        f"Output node '{node_id}' must be attached to exactly one "
                        "control-flow extraction node"
                    ),
                    node_ids=(node_id,),
                )
            )

    validation_sidecars: list[ValidationSidecar] = []
    seen_bindings: dict[tuple[str, str], str] = {}
    for edge in validation_attachment_edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_id = str(edge.get("id") or "")
        binding_id = _validation_sidecar_binding_id(edge, node_by_id)
        duplicate_key = (source, binding_id)
        if binding_id and duplicate_key in seen_bindings:
            issues.append(
                ExecutableFlowIssue(
                    code="duplicate_validation_binding",
                    message=(
                        f"Control node '{source}' has multiple validation sidecars for "
                        f"binding '{binding_id}'"
                    ),
                    node_ids=(source, target),
                    edge_ids=(seen_bindings[duplicate_key], edge_id),
                )
            )
        elif binding_id:
            seen_bindings[duplicate_key] = edge_id
        validation_sidecars.append(
            ValidationSidecar(
                edge_id=edge_id,
                source_node_id=source,
                validator_node_id=target,
                binding_id=binding_id,
                replaces_attachment_id=(
                    str(edge.get("replaces_attachment_id"))
                    if edge.get("replaces_attachment_id")
                    else None
                ),
            )
        )

    executable_ids_list = [
        node_id
        for node_id in ordered_control
        if (
            node_by_id[node_id].get("type", "agent") != "task_input"
            and _mapping(node_by_id[node_id].get("data")).get("agent_id")
            not in ("task_input", "supervisor")
        )
    ]
    executable_ids_list.extend(
        attachment.output_node_id for attachment in output_attachments
    )
    executable_ids = tuple(executable_ids_list)
    terminal_node_ids = tuple(
        dict.fromkeys(
            (
                *exit_node_ids,
                *(attachment.output_node_id for attachment in output_attachments),
            )
        )
    )
    graph = ExecutableFlowGraph(
        control_node_ids=control_node_ids,
        ordered_control_node_ids=tuple(ordered_control),
        ordered_executable_node_ids=executable_ids,
        entry_node_ids=entry_node_ids,
        exit_node_ids=exit_node_ids,
        terminal_node_ids=terminal_node_ids,
        output_attachments=tuple(output_attachments),
        validation_sidecars=tuple(validation_sidecars),
        issues=tuple(issues),
    )
    if raise_on_invalid and not graph.valid:
        raise ExecutableFlowTopologyError(graph.issues)
    return graph


__all__ = [
    "ExecutableFlowGraph",
    "ExecutableFlowIssue",
    "ExecutableFlowTopologyError",
    "OutputAttachment",
    "ValidationSidecar",
    "project_executable_flow_graph",
]
