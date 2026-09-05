"""Canonical, side-effect-free validation for exact Agent Studio drafts.

The validator consumes complete authoring candidates plus request-scoped catalog
snapshots.  It never persists, repairs, or applies a proposal.  Save endpoints,
AI proposal compilation, and pre/post-apply checks can therefore use the same
finding vocabulary without making validation itself a source of writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.lib.agent_studio.flow_agent_policy import (
    agent_allows_ordinary_flow_step,
    attachment_only_validator_reason,
)
from src.lib.executable_flow_graph import ExecutableFlowTopologyError
from src.lib.flow_edge_roles import (
    OUTPUT_ATTACHMENT_EDGE_ROLE,
    SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS,
    agent_can_source_output_attachment,
)
from src.schemas.flows import (
    DEFAULT_FLOW_EDGE_ROLE,
    FlowDefinition,
    VALIDATION_ATTACHMENT_EDGE_ROLE,
)


FindingSeverity = Literal["error", "warning", "info"]
ValidationPhase = Literal["proposal", "pre_apply", "post_apply", "save"]
LOCKED_PROMPT_MARKERS = (
    "Platform Runtime Contract",
    "backend-owned instructions",
    "Generated runtime contract",
)


@dataclass(frozen=True)
class AuthoringValidationFinding:
    """One stable, curator-safe exact-draft validation finding."""

    code: str
    severity: FindingSeverity
    path: str
    message: str
    fix_hint: str | None = None
    node_id: str | None = None
    edge_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "severity": self.severity,
                "path": self.path,
                "node_id": self.node_id,
                "edge_id": self.edge_id,
                "message": self.message,
                "fix_hint": self.fix_hint,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class AuthoringValidationResult:
    """Validation outcome shared by every authoring lifecycle phase."""

    artifact_kind: Literal["flow", "custom_agent"]
    phase: ValidationPhase
    findings: tuple[AuthoringValidationFinding, ...] = ()
    candidate: Any | None = field(default=None, compare=False, repr=False)
    projection_fields_by_node: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def valid(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[AuthoringValidationFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity == "error"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_kind": self.artifact_kind,
            "phase": self.phase,
            "valid": self.valid,
            "findings": [finding.to_dict() for finding in self.findings],
            **({"projection_fields_by_node": self.projection_fields_by_node} if self.projection_fields_by_node else {}),
        }


class AuthoringValidationError(ValueError):
    """Raised by save adapters when canonical validation has blocking findings."""

    def __init__(self, result: AuthoringValidationResult) -> None:
        self.result = result
        first = result.errors[0] if result.errors else None
        super().__init__(first.message if first else "Authoring validation failed")


class AuthoringValidationEngineError(RuntimeError):
    """Sanitized unexpected validator-engine failure."""


def report_authoring_validation_engine_failure(
    *,
    artifact_kind: Literal["flow", "custom_agent"],
    phase: ValidationPhase,
) -> AuthoringValidationEngineError:
    """Report only bounded validator metadata and return a safe exception."""

    from src.lib.observability.runtime import report_runtime_exception

    sanitized = AuthoringValidationEngineError(
        f"Unexpected {artifact_kind} authoring validator engine failure"
    )
    report_runtime_exception(
        sanitized,
        component="agent_studio_authoring_validation",
        operation="validate_exact_draft",
        tags={
            "validator_kind": artifact_kind,
            "validation_code": "engine_failure",
            "validation_path": artifact_kind,
            "validation_phase": phase,
        },
        context={"finding_count": 0},
    )
    return sanitized


@dataclass(frozen=True)
class AuthoringValidationContext:
    """Authenticated request context available to deterministic validators."""

    db_user_id: int | None
    active_group_ids: tuple[str, ...] = ()
    expected_draft_fingerprint: str | None = None
    current_draft_fingerprint: str | None = None

    @classmethod
    def from_values(
        cls,
        *,
        db_user_id: int | None,
        active_group_ids: Sequence[str] | None = None,
        expected_draft_fingerprint: str | None = None,
        current_draft_fingerprint: str | None = None,
    ) -> "AuthoringValidationContext":
        return cls(
            db_user_id=db_user_id,
            active_group_ids=tuple(active_group_ids or ()),
            expected_draft_fingerprint=expected_draft_fingerprint,
            current_draft_fingerprint=current_draft_fingerprint,
        )


def _stale_draft_finding(
    context: AuthoringValidationContext,
    *,
    path: str,
) -> AuthoringValidationFinding | None:
    expected = context.expected_draft_fingerprint
    current = context.current_draft_fingerprint
    if expected is None or current is None or expected == current:
        return None
    return AuthoringValidationFinding(
        code="stale_draft_fingerprint",
        severity="error",
        path=path,
        message="The editable draft changed after this proposal was prepared.",
        fix_hint="Refresh the exact draft, recompile the proposal, and validate again.",
    )


FlowAgentResolver = Callable[[str, AuthoringValidationContext], Mapping[str, Any] | None]
FlowAttachmentDefaultApplier = Callable[[FlowDefinition], FlowDefinition]


def resolve_live_flow_agent(
    agent_id: str,
    context: AuthoringValidationContext,
) -> Mapping[str, Any] | None:
    """Resolve one flow reference from the authenticated live catalog."""

    from src.lib.agent_studio.catalog_service import get_active_visible_agent_metadata
    from src.lib.config.schema_discovery import resolve_output_schema

    metadata_kwargs: dict[str, Any] = {
        "authenticated_groups": list(context.active_group_ids),
    }
    if context.db_user_id is not None:
        metadata_kwargs["db_user_id"] = context.db_user_id
    try:
        metadata = get_active_visible_agent_metadata(agent_id, **metadata_kwargs)
    except ValueError:
        return None
    if not isinstance(metadata, Mapping):
        return None

    category = str(metadata.get("category") or "").strip().lower()
    subcategory = str(metadata.get("subcategory") or "").strip().lower()
    output_schema_key = str(
        metadata.get("output_schema_key") or metadata.get("output_schema") or ""
    ).strip()
    return {
        "name": metadata.get("display_name", agent_id),
        "category": metadata.get("category") or "",
        "subcategory": metadata.get("subcategory") or "",
        "output_schema_key": output_schema_key or None,
        "is_active": metadata.get("is_active", True),
        "visible": metadata.get("visible", True),
        "visibility": metadata.get("visibility"),
        "produces_flow_artifacts": (
            "extract" in category
            or "extract" in subcategory
            or bool(
                "validation" in category
                and output_schema_key
                and resolve_output_schema(output_schema_key) is not None
            )
        ),
        "supervisor": metadata.get("supervisor") or {},
        "curation": metadata.get("curation"),
    }


def _candidate_mapping(candidate: Any) -> Mapping[str, Any]:
    if isinstance(candidate, Mapping):
        return candidate
    if hasattr(candidate, "model_dump"):
        dumped = candidate.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _path_from_location(
    location: Iterable[Any],
    candidate: Any,
    *,
    prefix: str,
) -> tuple[str, str | None, str | None]:
    """Translate Pydantic indexes into stable node/edge identity paths."""

    raw = _candidate_mapping(candidate)
    pieces = list(location)
    node_id: str | None = None
    edge_id: str | None = None
    if len(pieces) >= 2 and pieces[0] in {"nodes", "edges"}:
        collection_name = str(pieces[0])
        index = pieces[1]
        collection = raw.get(collection_name)
        if isinstance(index, int) and isinstance(collection, Sequence):
            item = collection[index] if index < len(collection) else None
            item_id = item.get("id") if isinstance(item, Mapping) else None
            if isinstance(item_id, str) and item_id:
                pieces[1] = item_id
                if collection_name == "nodes":
                    node_id = item_id
                else:
                    edge_id = item_id
    suffix = ".".join(str(piece) for piece in pieces if str(piece))
    return (f"{prefix}.{suffix}" if suffix else prefix, node_id, edge_id)


def _pydantic_flow_findings(
    exc: ValidationError,
    candidate: Any,
) -> list[AuthoringValidationFinding]:
    findings: list[AuthoringValidationFinding] = []
    for error in exc.errors(include_url=False, include_input=False):
        underlying = (error.get("ctx") or {}).get("error")
        if isinstance(underlying, ExecutableFlowTopologyError):
            for issue in underlying.issues:
                node_id = issue.node_ids[0] if issue.node_ids else None
                edge_id = issue.edge_ids[0] if issue.edge_ids else None
                if edge_id:
                    path = f"flow_definition.edges.{edge_id}"
                elif node_id:
                    path = f"flow_definition.nodes.{node_id}"
                else:
                    path = "flow_definition"
                findings.append(
                    AuthoringValidationFinding(
                        code=issue.code,
                        severity="error",
                        path=path,
                        node_id=node_id,
                        edge_id=edge_id,
                        message=issue.message,
                        fix_hint="Correct the identified graph connection and validate again.",
                    )
                )
            continue
        path, node_id, edge_id = _path_from_location(
            error.get("loc", ()),
            candidate,
            prefix="flow_definition",
        )
        message = str(error.get("msg") or "The flow definition is invalid.")
        findings.append(
            AuthoringValidationFinding(
                code="invalid_flow_definition",
                severity="error",
                path=path,
                node_id=node_id,
                edge_id=edge_id,
                message=message,
                fix_hint="Correct the exact draft field identified by path and validate again.",
            )
        )
    return findings


def validate_flow_authoring_draft(
    candidate: Mapping[str, Any] | FlowDefinition,
    *,
    context: AuthoringValidationContext,
    resolve_agent: FlowAgentResolver,
    apply_attachment_defaults: FlowAttachmentDefaultApplier,
    phase: ValidationPhase = "proposal",
    hydrate_attachment_defaults: bool = True,
    enforce_agent_references: bool = True,
    enforce_agent_step_policy: bool = True,
    entries_by_node: Mapping[str, Mapping[str, Any] | None] | None = None,
    contract_findings: Sequence[AuthoringValidationFinding] = (),
) -> AuthoringValidationResult:
    """Validate one exact full ``FlowDefinition`` without writing or applying it."""

    try:
        flow_definition = FlowDefinition.model_validate(candidate).model_copy(deep=True)
    except ValidationError as exc:
        return AuthoringValidationResult(
            artifact_kind="flow",
            phase=phase,
            findings=tuple(_pydantic_flow_findings(exc, candidate)),
        )

    findings: list[AuthoringValidationFinding] = list(contract_findings)
    stale_finding = _stale_draft_finding(
        context,
        path="flow_definition.draft_fingerprint",
    )
    if stale_finding is not None:
        findings.append(stale_finding)
    if hydrate_attachment_defaults:
        try:
            flow_definition = apply_attachment_defaults(flow_definition)
        except ValueError:
            findings.append(
                AuthoringValidationFinding(
                    code="invalid_validation_attachment_configuration",
                    severity="error",
                    path="flow_definition.nodes",
                    message=(
                        "Validation attachment selections do not match the current "
                        "authenticated authoring catalog."
                    ),
                    fix_hint="Refresh the current attachment catalog and correct the selected validation settings.",
                )
            )

    entries: dict[str, Mapping[str, Any] | None] = {}
    for node in flow_definition.nodes:
        agent_id = str(node.data.agent_id or "").strip()
        if not agent_id or agent_id == "task_input":
            continue
        entry = (
            entries_by_node[node.id]
            if entries_by_node is not None and node.id in entries_by_node
            else resolve_agent(agent_id, context)
        )
        entries[node.id] = entry
        if not enforce_agent_references:
            continue
        has_attachment_contract = not agent_id.startswith("ca_") or not (
            node.data.validation_attachments
            and not (
                isinstance(entry, Mapping)
                and isinstance(entry.get("curation"), Mapping)
                and str(entry["curation"].get("domain_pack_id") or "").strip()
            )
        )
        if entry is None or not has_attachment_contract:
            findings.append(
                AuthoringValidationFinding(
                    code="unavailable_agent",
                    severity="error",
                    path=f"flow_definition.nodes.{node.id}.data.agent_id",
                    node_id=node.id,
                    message="This agent is not available to the current curator.",
                    fix_hint="Choose an agent from the current authenticated authoring catalog.",
                )
            )

    nodes_by_id = {node.id: node for node in flow_definition.nodes}
    if enforce_agent_step_policy:
        for edge in flow_definition.edges:
            if edge.role != OUTPUT_ATTACHMENT_EDGE_ROLE:
                continue
            source = nodes_by_id.get(edge.source)
            target = nodes_by_id.get(edge.target)
            source_entry = entries.get(source.id) if source is not None else None
            if source is None or not agent_can_source_output_attachment(source_entry):
                findings.append(
                    AuthoringValidationFinding(
                        code="incompatible_output_source",
                        severity="error",
                        path=f"flow_definition.edges.{edge.id}.source",
                        edge_id=edge.id,
                        node_id=source.id if source is not None else None,
                        message="This output attachment source is not an extraction agent or a typed validation agent.",
                        fix_hint="Connect an available extraction or typed validation node.",
                    )
                )
            if target is None or target.data.agent_id not in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS:
                findings.append(
                    AuthoringValidationFinding(
                        code="incompatible_output_target",
                        severity="error",
                        path=f"flow_definition.edges.{edge.id}.target",
                        edge_id=edge.id,
                        node_id=target.id if target is not None else None,
                        message="This output attachment target is not an output formatter supported by the runtime.",
                        fix_hint="Choose an output formatter from the current authoring catalog.",
                    )
                )

        validation_targets = {
            edge.target
            for edge in flow_definition.edges
            if edge.role == VALIDATION_ATTACHMENT_EDGE_ROLE
        }
        output_sources = {
            edge.source
            for edge in flow_definition.edges
            if edge.role == OUTPUT_ATTACHMENT_EDGE_ROLE
        }
        control_edges: dict[str, list[str]] = {}
        for edge in flow_definition.edges:
            if edge.role != DEFAULT_FLOW_EDGE_ROLE:
                continue
            control_edges.setdefault(edge.source, []).append(edge.id)
            control_edges.setdefault(edge.target, []).append(edge.id)

        for node in flow_definition.nodes:
            if node.data.agent_id == "task_input":
                continue
            entry = entries.get(node.id)
            connected_control_edges = control_edges.get(node.id, [])
            category = str(entry.get("category") or "").lower() if entry else ""
            subcategory = str(entry.get("subcategory") or "").lower() if entry else ""
            is_formatter = node.data.agent_id in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS or (
                bool(entry)
                and (
                    "output" in category
                    or "output" in subcategory
                    or "format" in subcategory
                )
            )
            if connected_control_edges and is_formatter:
                findings.append(
                    AuthoringValidationFinding(
                        code="formatter_in_control_flow",
                        severity="error",
                        path=f"flow_definition.nodes.{node.id}",
                        node_id=node.id,
                        message="Formatter nodes must be connected only by output-attachment edges.",
                        fix_hint="Remove ordinary control-flow edges from this formatter node.",
                    )
                )
                continue
            if node.id in output_sources and agent_can_source_output_attachment(entry):
                continue
            if entry is None or agent_allows_ordinary_flow_step(node.data.agent_id, entry):
                continue
            if node.id in validation_targets and not connected_control_edges:
                continue
            findings.append(
                AuthoringValidationFinding(
                    code="attachment_only_agent_in_control_flow",
                    severity="error",
                    path=(
                        f"flow_definition.edges.{connected_control_edges[0]}"
                        if connected_control_edges
                        else f"flow_definition.nodes.{node.id}"
                    ),
                    node_id=node.id,
                    edge_id=(
                        connected_control_edges[0]
                        if connected_control_edges
                        else None
                    ),
                    message=attachment_only_validator_reason("This validation agent"),
                    fix_hint="Connect it only as a validation attachment target.",
                )
            )

    return AuthoringValidationResult(
        artifact_kind="flow",
        phase=phase,
        findings=tuple(findings),
        candidate=flow_definition,
        projection_fields_by_node={
            node_id: {"execution_receipt": entry.get("execution_receipt"), "fields": entry["projection_fields"]}
            for node_id, entry in entries.items()
            if entry is not None and "projection_fields" in entry
        },
    )


class CustomAgentDraft(BaseModel):
    """Complete save-equivalent general custom-agent draft."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    custom_prompt: str = ""
    group_prompt_overrides: dict[str, str] = Field(default_factory=dict)
    icon: str | None = Field(None, max_length=10)
    visibility: Literal["private", "project"] = "private"
    allowed_group_ids: list[str] = Field(default_factory=list)
    inherited_allowed_group_ids: list[str] = Field(default_factory=list)
    include_group_rules: bool = True
    model_id: str = Field(..., min_length=1, max_length=100)
    model_reasoning: str | None = Field(None, max_length=20)
    model_temperature: float | None = None
    tool_ids: list[str] = Field(default_factory=list)
    output_schema_key: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)

    @field_validator("output_schema_key", mode="before")
    @classmethod
    def normalize_no_output_contract(cls, value: Any) -> str | None:
        """An empty/cleared schema is the explicit `none` contract."""

        normalized = str(value or "").strip()
        return normalized or None


@dataclass(frozen=True)
class AgentModelValidationRecord:
    model_id: str
    curator_visible: bool
    supports_reasoning: bool
    reasoning_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentToolValidationRecord:
    tool_id: str
    attachable: bool
    installed: bool
    system_managed: bool = False


@dataclass(frozen=True)
class AgentValidationSources:
    """Read-only live catalog snapshot used by general-agent validation."""

    models: Mapping[str, AgentModelValidationRecord]
    tools: Mapping[str, AgentToolValidationRecord]
    output_schema_keys: frozenset[str]
    group_ids: frozenset[str]
    builder_finalization_tool_ids: frozenset[str]


class AgentDraftExtensionValidator(Protocol):
    """Typed boundary for future domain/profile validators (not implemented here)."""

    validator_id: str

    def validate(
        self,
        candidate: CustomAgentDraft,
        context: AuthoringValidationContext,
    ) -> Sequence[AuthoringValidationFinding]: ...


def validate_custom_agent_authoring_draft(
    candidate: Mapping[str, Any] | CustomAgentDraft,
    *,
    context: AuthoringValidationContext,
    sources: AgentValidationSources,
    phase: ValidationPhase = "proposal",
    extension_validators: Sequence[AgentDraftExtensionValidator] = (),
) -> AuthoringValidationResult:
    """Validate a complete general custom-agent draft without persistence."""

    try:
        draft = CustomAgentDraft.model_validate(candidate)
    except ValidationError as exc:
        findings = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False):
            path, _, _ = _path_from_location(
                error.get("loc", ()), candidate, prefix="custom_agent"
            )
            findings.append(
                AuthoringValidationFinding(
                    code="invalid_custom_agent_draft",
                    severity="error",
                    path=path,
                    message=str(error.get("msg") or "The custom-agent draft is invalid."),
                    fix_hint="Correct the exact draft field identified by path and validate again.",
                )
            )
        return AuthoringValidationResult(
            artifact_kind="custom_agent", phase=phase, findings=tuple(findings)
        )

    findings: list[AuthoringValidationFinding] = []
    stale_finding = _stale_draft_finding(
        context,
        path="custom_agent.draft_fingerprint",
    )
    if stale_finding is not None:
        findings.append(stale_finding)
    if not draft.name.strip():
        findings.append(
            AuthoringValidationFinding(
                code="invalid_agent_name",
                severity="error",
                path="custom_agent.name",
                message="The custom-agent name cannot be blank.",
                fix_hint="Provide a concise name for this draft.",
            )
        )
    for path, prompt in [
        ("custom_agent.custom_prompt", draft.custom_prompt),
        *[
            (f"custom_agent.group_prompt_overrides.{group_id}", prompt)
            for group_id, prompt in draft.group_prompt_overrides.items()
        ],
    ]:
        if any(
            marker.casefold() in str(prompt or "").casefold()
            for marker in LOCKED_PROMPT_MARKERS
        ):
            findings.append(
                AuthoringValidationFinding(
                    code="locked_prompt_layer",
                    severity="error",
                    path=path,
                    message="Editable prompt text cannot copy a locked core or generated prompt layer.",
                    fix_hint="Keep only curator-authored instructions in the editable prompt.",
                )
            )
    model = sources.models.get(draft.model_id)
    if model is None or not model.curator_visible:
        findings.append(
            AuthoringValidationFinding(
                code="unavailable_model",
                severity="error",
                path="custom_agent.model_id",
                message="This model is not available to the current curator.",
                fix_hint="Choose a model from the current authenticated authoring catalog.",
            )
        )
    elif draft.model_reasoning:
        normalized_reasoning = draft.model_reasoning.strip().lower()
        if not model.supports_reasoning or normalized_reasoning not in model.reasoning_options:
            findings.append(
                AuthoringValidationFinding(
                    code="unsupported_reasoning_effort",
                    severity="error",
                    path="custom_agent.model_reasoning",
                    message="This reasoning effort is not supported by the selected model.",
                    fix_hint="Choose a reasoning value advertised for the selected model, or clear it.",
                )
            )

    seen_tools: set[str] = set()
    normalized_tool_ids: list[str] = []
    for index, raw_tool_id in enumerate(draft.tool_ids):
        tool_id = str(raw_tool_id or "").strip()
        if not tool_id or tool_id in seen_tools:
            continue
        seen_tools.add(tool_id)
        normalized_tool_ids.append(tool_id)
        tool = sources.tools.get(tool_id)
        if (
            tool is None
            or not tool.installed
            or (not tool.attachable and not tool.system_managed)
        ):
            findings.append(
                AuthoringValidationFinding(
                    code="unavailable_tool",
                    severity="error",
                    path=f"custom_agent.tool_ids.{index}",
                    message="This tool is not available for attachment to the current draft.",
                    fix_hint="Choose a tool from the current authenticated authoring catalog.",
                )
            )

    schema_key = draft.output_schema_key
    if schema_key is not None and schema_key not in sources.output_schema_keys:
        findings.append(
            AuthoringValidationFinding(
                code="unavailable_output_contract",
                severity="error",
                path="custom_agent.output_schema_key",
                message="This structured output contract is not available.",
                fix_hint="Choose a current output contract or clear the field for the `none` contract.",
            )
        )
    if schema_key is not None and not (
        set(normalized_tool_ids) & set(sources.builder_finalization_tool_ids)
    ):
        findings.append(
            AuthoringValidationFinding(
                code="missing_output_finalizer",
                severity="error",
                path="custom_agent.tool_ids",
                message="A structured output contract requires a compatible finalization tool.",
                fix_hint="Attach the matching finalization tool or clear the output contract.",
            )
        )

    normalized_allowed = list(dict.fromkeys(draft.allowed_group_ids))
    normalized_inherited = list(dict.fromkeys(draft.inherited_allowed_group_ids))
    malformed_group = any(
        not isinstance(group_id, str)
        or not group_id
        or group_id != group_id.strip()
        for group_id in [*draft.allowed_group_ids, *draft.inherited_allowed_group_ids]
    )
    duplicate_group = (
        len(normalized_allowed) != len(draft.allowed_group_ids)
        or len(normalized_inherited) != len(draft.inherited_allowed_group_ids)
    )
    if malformed_group or duplicate_group:
        findings.append(
            AuthoringValidationFinding(
                code="invalid_group_reference",
                severity="error",
                path="custom_agent.allowed_group_ids",
                message="Group selections must contain unique canonical group IDs.",
                fix_hint="Choose groups from the current authenticated authoring catalog.",
            )
        )
    if any(
        group_id not in sources.group_ids
        for group_id in [*normalized_allowed, *normalized_inherited]
    ):
        findings.append(
            AuthoringValidationFinding(
                code="unavailable_group",
                severity="error",
                path="custom_agent.allowed_group_ids",
                message="One or more selected groups are not available.",
                fix_hint="Choose groups from the current authenticated authoring catalog.",
            )
        )
    for group_id in draft.group_prompt_overrides:
        if group_id not in sources.group_ids:
            findings.append(
                AuthoringValidationFinding(
                    code="unavailable_group",
                    severity="error",
                    path=f"custom_agent.group_prompt_overrides.{group_id}",
                    message="This group prompt target is not available.",
                    fix_hint="Choose a group from the current authenticated authoring catalog.",
                )
            )
    if normalized_inherited and (
        not normalized_allowed
        or not set(normalized_allowed).issubset(set(normalized_inherited))
    ):
        findings.append(
            AuthoringValidationFinding(
                code="widened_inherited_access",
                severity="error",
                path="custom_agent.allowed_group_ids",
                message="The draft cannot widen its inherited group restriction.",
                fix_hint="Keep the selected groups within the inherited access boundary.",
            )
        )

    for validator in extension_validators:
        findings.extend(validator.validate(draft, context))

    return AuthoringValidationResult(
        artifact_kind="custom_agent",
        phase=phase,
        findings=tuple(findings),
        candidate=draft,
    )
