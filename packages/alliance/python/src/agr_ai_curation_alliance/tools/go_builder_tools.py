"""Structured builder tools for the RGD GO paper-curation specialist."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agents import function_tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_SUCCESS,
    attempt_query as _attempt_query,
)
from agr_ai_curation_runtime.evidence_workspace import (
    get_active_evidence_records_snapshot,
)
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event
from agr_ai_curation_runtime.resolver_call_ledger import get_active_resolver_call_ledger

from agr_ai_curation_alliance.domain_packs.go import (
    GO_DOMAIN_PACK_ID,
    GO_MATERIALIZER_ID,
    GO_OBJECT_TYPE,
    materialize_go_builder_state,
)
from agr_ai_curation_alliance.domain_packs.go.values import (
    BUILDER_OWNED_KEYS,
    evidence_code_value,
    is_resolved,
    gene_product_value,
    record_holds,
    go_term_value,
    reference_value,
    with_from_value,
)

from .agr_curation import (
    AgrQueryResult,
    _BUILDER_LIST_DEFAULT_LIMIT,
    _builder_candidate_list,
    _builder_finalization_summary,
    _builder_summary,
    _ok,
    _search_builder_candidates,
)
from .builder_finalization import finalize_builder_extraction
from .builder_rationale import document_rationale_arg, normalize_rationale


_GO_PATCH_FIELD_PATHS = frozenset(
    {
        "validation_guidance",
        "gene_product",
        "go_term",
        "evidence_code",
        "reference_curie",
        "with_from",
        "qualifiers",
        "annotation_extensions",
        "negated",
        "rationale",
        "provider_context",
        "blocking_reasons",
        "evidence_record_ids",
    }
)

_IDENTITY_TOOLS = {"resolve_gene_product"}
_TERM_TOOLS = {"quickgo_api_call"}
_ANNOTATION_TOOLS = {"go_api_call"}
_REFERENCE_TOOLS = {"agr_literature_reference_lookup"}
# A With/From identifier is confirmed only by a lookup, never by document text.
_WITH_FROM_TOOLS = {
    *_IDENTITY_TOOLS,
    *_TERM_TOOLS,
    *_ANNOTATION_TOOLS,
    *_REFERENCE_TOOLS,
}
_CONTROLLED_VALUE_TOOLS = {
    *_IDENTITY_TOOLS,
    *_TERM_TOOLS,
    *_ANNOTATION_TOOLS,
    "search_document",
    "read_chunk",
    "read_section",
    "read_subsection",
    "record_evidence",
}
_GO_ASPECT_VALUES = frozenset(
    {"molecular_function", "biological_process", "cellular_component"}
)


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean_required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("value must be non-empty")
    return cleaned


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


class GOWithFromEntry(_StrictToolModel):
    """One With/From entry: the paper's wording and, when a lookup returned it, its identifier."""

    mention: StrictStr = Field(
        description="The With/From entry as the paper words it, for example the partner gene or allele name."
    )
    curie: Optional[StrictStr] = Field(
        default=None,
        description="Identifier for the entry exactly as a lookup tool returned it; leave it out when no lookup confirmed one.",
    )

    @field_validator("mention")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("curie")
    @classmethod
    def _optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _clean_optional(value)


class GOGeneProductInput(_StrictToolModel):
    mention: StrictStr
    entity_type: StrictStr
    taxon_curie: StrictStr
    curie: Optional[StrictStr] = None
    label: Optional[StrictStr] = None

    @field_validator("mention", "entity_type", "taxon_curie")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("curie", "label")
    @classmethod
    def _optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _clean_optional(value)

    @model_validator(mode="after")
    def _label_needs_curie(self) -> "GOGeneProductInput":
        if self.label and not self.curie:
            raise ValueError(
                "a gene-product label comes only with the CURIE the resolver returned"
            )
        return self

    def value(self) -> dict[str, Any]:
        return gene_product_value(
            self.mention,
            curie=self.curie,
            label=self.label,
            entity_type=self.entity_type,
            taxon_curie=self.taxon_curie,
        )


class GOTermInput(_StrictToolModel):
    mention: StrictStr
    aspect: StrictStr
    curie: Optional[StrictStr] = None
    label: Optional[StrictStr] = None

    @field_validator("mention", "aspect")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("curie", "label")
    @classmethod
    def _optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _clean_optional(value)

    @model_validator(mode="after")
    def _term_identity(self) -> "GOTermInput":
        if self.aspect not in _GO_ASPECT_VALUES:
            raise ValueError("go_term_aspect is not a canonical GO aspect")
        if bool(self.curie) != bool(self.label):
            raise ValueError(
                "a GO term CURIE and its label come together from quickgo_api_call"
            )
        return self

    def value(self) -> dict[str, Any]:
        return go_term_value(
            self.mention, curie=self.curie, label=self.label, aspect=self.aspect
        )


class GOReferenceInput(_StrictToolModel):
    mention: StrictStr
    curie: Optional[StrictStr] = None

    @field_validator("mention")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("curie")
    @classmethod
    def _optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _clean_optional(value)

    def value(self) -> dict[str, Any]:
        return reference_value(self.mention, curie=self.curie)


def _with_from_values(entries: Sequence[GOWithFromEntry]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for entry in entries:
        value = with_from_value(entry.mention, curie=entry.curie)
        if value not in values:
            values.append(value)
    return values


class GOStageInput(_StrictToolModel):
    validation_guidance: Optional[StrictStr] = Field(
        default=None,
        description="One short advisory sentence conveying relevant configured validation rules and evidence-backed context for this finding; not source evidence or a resolved identity",
    )
    pending_ref_id: StrictStr
    gene_product: GOGeneProductInput
    go_term: GOTermInput
    evidence_code: StrictStr
    reference: GOReferenceInput
    rationale: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1)
    with_from: List[GOWithFromEntry] = Field(default_factory=list)
    qualifiers: List[StrictStr] = Field(default_factory=list)
    annotation_extensions: List[StrictStr] = Field(default_factory=list)
    negated: StrictBool = False
    blocking_reasons: List[StrictStr] = Field(default_factory=list)
    existing_annotation_status: StrictStr
    existing_annotations: List[Dict[str, Any]] = Field(default_factory=list)
    existing_annotation_provenance: Dict[str, Any] = Field(default_factory=dict)
    existing_annotation_note: Optional[StrictStr] = None
    identity_resolution: Dict[str, Any] = Field(default_factory=dict)
    hierarchy_limitations: List[StrictStr] = Field(default_factory=list)
    section_limitations: List[StrictStr] = Field(default_factory=list)

    @field_validator(
        "pending_ref_id",
        "evidence_code",
        "existing_annotation_status",
    )
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("rationale")
    @classmethod
    def _rationale(cls, value: str) -> str:
        return normalize_rationale(value)

    @field_validator(
        "evidence_record_ids",
        "qualifiers",
        "annotation_extensions",
        "blocking_reasons",
        "hierarchy_limitations",
        "section_limitations",
    )
    @classmethod
    def _clean_string_list(cls, value: List[str]) -> List[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _validate_identity_and_context(self) -> "GOStageInput":
        if self.existing_annotation_status not in {
            "available",
            "not_found",
            "unavailable",
        }:
            raise ValueError(
                "existing_annotation_status must be available, not_found, or unavailable"
            )
        if not self.gene_product.curie and not self.blocking_reasons:
            raise ValueError(
                "a gene product without a resolver-confirmed CURIE requires blocking_reasons"
            )
        if (
            self.existing_annotation_status == "unavailable"
            and not self.existing_annotation_note
        ):
            raise ValueError(
                "unavailable existing annotations require existing_annotation_note"
            )
        return self


class GOPatchUpdateInput(_StrictToolModel):
    field_path: StrictStr
    value: Any = None
    evidence_record_ids: Optional[List[StrictStr]] = None

    @field_validator("field_path")
    @classmethod
    def _known_field_path(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in _GO_PATCH_FIELD_PATHS:
            raise ValueError(
                f"field_path must be one of {sorted(_GO_PATCH_FIELD_PATHS)}"
            )
        return cleaned


class GOPatchInput(_StrictToolModel):
    candidate_id: StrictStr
    updates: List[GOPatchUpdateInput] = Field(min_length=1)


class GODiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr] = None


class GOListInput(_StrictToolModel):
    include_discarded: bool
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GOFindInput(_StrictToolModel):
    field_value_contains: Optional[StrictStr] = None
    pending_ref_id: Optional[StrictStr] = None
    evidence_record_id: Optional[StrictStr] = None
    candidate_id: Optional[StrictStr] = None
    has_validation_errors: Optional[StrictBool] = None
    include_discarded: bool = False
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class GOFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1)


def _emit_go_builder_event(
    event_type: str,
    *,
    action: str,
    input_summary: Any = None,
    output_summary: Any = None,
    validation: Optional[Mapping[str, Any]] = None,
) -> None:
    workspace = None
    try:
        workspace = get_active_extraction_builder_workspace()
    except RuntimeError:
        pass
    write_extraction_trace_event(
        event_type=event_type,
        trace_id=getattr(workspace, "run_id", None),
        domain_pack_id=GO_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": GO_OBJECT_TYPE,
        },
    )


def _model_validation_issues(exc: ValidationError) -> List[dict[str, Any]]:
    return [
        {
            "field_path": ".".join(str(part) for part in error.get("loc", ())),
            "reason": str(error.get("type") or "invalid"),
            "message": str(error.get("msg") or "Invalid value"),
        }
        for error in exc.errors()
    ]


def _go_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_go_builder_event(
        "go_builder.validation_failed",
        action=method,
        input_summary=attempted_query,
        output_summary={"message": message, "validation_issues": issue_list},
        validation={"status": "failed", "issues": issue_list},
    )
    return AgrQueryResult(
        status="error",
        data={"validation_issues": issue_list},
        count=len(issue_list),
        message=message,
        lookup_status=LOOKUP_STATUS_BLOCKED,
        failure_classification="validation_failed",
        explanation=message,
    )


def _go_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"rgd-go-candidate-{len(workspace.candidates) + 1}"


def _stage_payload(stage_input: GOStageInput) -> dict[str, Any]:
    return {
        "domain_pack_id": GO_DOMAIN_PACK_ID,
        "object_type": GO_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "validation_guidance": stage_input.validation_guidance,
        "payload": {
            "gene_product": stage_input.gene_product.value(),
            "go_term": stage_input.go_term.value(),
            "evidence_code": evidence_code_value(stage_input.evidence_code),
            "reference_curie": stage_input.reference.value(),
            "with_from": _with_from_values(stage_input.with_from),
            "qualifiers": list(stage_input.qualifiers),
            "annotation_extensions": list(stage_input.annotation_extensions),
            "negated": stage_input.negated,
            "rationale": stage_input.rationale,
            "provider_context": {
                "provider_key": "RGD",
                "taxon_curie": stage_input.gene_product.taxon_curie,
                "review_lane": "rgd_go_curator_review",
                "existing_annotation_context": {
                    "status": stage_input.existing_annotation_status,
                    "annotations": list(stage_input.existing_annotations),
                    "provenance": dict(stage_input.existing_annotation_provenance),
                    "note": stage_input.existing_annotation_note,
                },
                "identity_resolution": dict(stage_input.identity_resolution),
                "hierarchy_limitations": list(stage_input.hierarchy_limitations),
                "section_limitations": list(stage_input.section_limitations),
            },
            "blocking_reasons": list(stage_input.blocking_reasons),
        },
        "evidence_record_ids": list(stage_input.evidence_record_ids),
    }


def _grounding_leaf_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return [
            leaf
            for nested in value.values()
            for leaf in _grounding_leaf_values(nested)
        ]
    if isinstance(value, list):
        return [leaf for nested in value for leaf in _grounding_leaf_values(nested)]
    if value in (None, "", [], {}) or isinstance(value, bool):
        return []
    return [value]


def _append_grounding_requirements(
    requirements: list[dict[str, Any]],
    *,
    field_path: str,
    tool_names: set[str],
    values: Any,
) -> None:
    for value in _grounding_leaf_values(values):
        requirement = {
            "field_path": field_path,
            "tool_names": sorted(tool_names),
            "value": value,
        }
        if requirement not in requirements:
            requirements.append(requirement)


def _resolved_identity(value: Any, keys: Sequence[str]) -> list[Any]:
    """The identity leaves a resolved value claims came from a lookup; never its mention."""

    if not is_resolved(value):
        return []
    return [value.get(key) for key in keys if value.get(key) not in (None, "")]


def _append_record_requirement(
    requirements: list[dict[str, Any]],
    *,
    field_path: str,
    tool_names: set[str],
    identity: list[Any],
) -> None:
    """A resolved value's identifier and label must come from ONE lookup record."""

    if not identity:
        return
    if len(identity) == 1:
        _append_grounding_requirements(
            requirements, field_path=field_path, tool_names=tool_names, values=identity
        )
        return
    requirements.append(
        {"field_path": field_path, "tool_names": sorted(tool_names), "record": identity}
    )


def _grounding_requirements(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    gene_product = payload.get("gene_product") or {}
    provider_context = payload.get("provider_context") or {}
    existing = provider_context.get("existing_annotation_context") or {}
    identity = provider_context.get("identity_resolution") or {}
    requirements: list[dict[str, Any]] = []

    gene_identity = _resolved_identity(gene_product, ("curie", "label"))
    _append_record_requirement(
        requirements,
        field_path="gene_product",
        tool_names=_IDENTITY_TOOLS,
        identity=gene_identity,
    )
    if identity:
        _append_grounding_requirements(
            requirements,
            field_path="provider_context.identity_resolution",
            tool_names=_IDENTITY_TOOLS,
            values=identity,
        )
    _append_record_requirement(
        requirements,
        field_path="go_term",
        tool_names=_TERM_TOOLS,
        identity=_resolved_identity(payload.get("go_term"), ("curie", "label", "aspect")),
    )
    if gene_identity:
        _append_grounding_requirements(
            requirements,
            field_path="provider_context.existing_annotation_context",
            tool_names=_ANNOTATION_TOOLS,
            values=[
                gene_product.get("curie"),
                existing.get("annotations"),
                existing.get("provenance"),
            ],
        )
    _append_grounding_requirements(
        requirements,
        field_path="reference_curie",
        tool_names=_REFERENCE_TOOLS,
        values=_resolved_identity(payload.get("reference_curie"), ("curie",)),
    )
    with_from = payload.get("with_from")
    for index, entry in enumerate(with_from if isinstance(with_from, list) else []):
        _append_grounding_requirements(
            requirements,
            field_path=f"with_from[{index}]",
            tool_names=_WITH_FROM_TOOLS,
            values=_resolved_identity(entry, ("curie",)),
        )
    for field_path in ("qualifiers", "annotation_extensions"):
        value = payload.get(field_path)
        if value in (None, "", []):
            continue
        _append_grounding_requirements(
            requirements,
            field_path=field_path,
            tool_names=_CONTROLLED_VALUE_TOOLS,
            values=value,
        )
    return requirements


def _matching_tool_output(ledger: Any, requirement: Mapping[str, Any]) -> Any:
    tool_names = set(requirement["tool_names"])
    if "record" not in requirement:
        return ledger.find_tool_output_containing(
            tool_names=tool_names, value=requirement["value"]
        )
    for tool_call_id in ledger.snapshot()["tool_output_ids"]:
        entry = ledger.get_tool_output(tool_call_id)
        if entry.tool_name in tool_names and record_holds(
            entry.raw_output, requirement["record"]
        ):
            return entry
    return None


def _ground_payload(
    payload: Mapping[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    requirements = _grounding_requirements(payload)
    try:
        ledger = get_active_resolver_call_ledger()
    except RuntimeError as exc:
        return (
            [],
            requirements,
            [
                {
                    "field_path": "source_grounding",
                    "reason": "tool_output_ledger_unavailable",
                    "message": str(exc),
                }
            ],
        )

    refs: list[str] = []
    issues: list[dict[str, Any]] = []
    for requirement in requirements:
        entry = _matching_tool_output(ledger, requirement)
        if entry is None:
            issues.append(
                {
                    "field_path": requirement["field_path"],
                    "reason": "unobserved_tool_value",
                    "message": (
                        "The staged identifier does not match one record of a lookup "
                        "result from this run. Copy the identifier and its label together, "
                        "exactly as one lookup result returned them. Stage a value without "
                        "an identifier only when no lookup returned one for it."
                    ),
                }
            )
        elif entry.tool_call_id not in refs:
            refs.append(entry.tool_call_id)
    return refs, requirements, issues


@document_rationale_arg
def _stage_go_recommendation_impl(
    pending_ref_id: str,
    gene_product_mention: str,
    gene_product_entity_type: str,
    gene_product_taxon_curie: str,
    go_term_mention: str,
    go_term_aspect: str,
    evidence_code: str,
    reference_mention: str,
    rationale: str,
    existing_annotation_status: str,
    evidence_record_ids: List[str],
    gene_product_curie: Optional[str] = None,
    gene_product_label: Optional[str] = None,
    go_term_curie: Optional[str] = None,
    go_term_label: Optional[str] = None,
    reference_curie: Optional[str] = None,
    with_from: Optional[List[GOWithFromEntry]] = None,
    qualifiers: Optional[List[str]] = None,
    annotation_extensions: Optional[List[str]] = None,
    negated: bool = False,
    blocking_reasons: Optional[List[str]] = None,
    existing_annotations: Optional[List[Dict[str, Any]]] = None,
    existing_annotation_provenance: Optional[Dict[str, Any]] = None,
    existing_annotation_note: Optional[str] = None,
    identity_resolution: Optional[Dict[str, Any]] = None,
    hierarchy_limitations: Optional[List[str]] = None,
    section_limitations: Optional[List[str]] = None,
    validation_guidance: Optional[str] = None,
) -> AgrQueryResult:
    """Stage one evidence-backed GO recommendation for canonical finalization.

    Each GO value keeps the paper's wording apart from the identifier a lookup
    confirmed. Always give the paper wording. Give an identifier only when a
    lookup tool returned it in this run; the value is then recorded as matched.
    Without an identifier the value is still kept, marked unresolved and not yet
    validated, so curators see it as UNRESOLVED next to the paper wording.

    For IMP annotations, the rationale must name the perturbation and the phenotype in
    the paper's exact wording.

    Args:
        pending_ref_id: Stable reference for this recommendation; evidence records
            attach to it.
        gene_product_mention: The gene or gene product exactly as the paper words it.
        gene_product_entity_type: The kind of product the paper describes, for example
            protein, gene, or mature_miRNA.
        gene_product_taxon_curie: The NCBI Taxon CURIE of the organism.
        go_term_mention: The GO process, function, or location as the paper words it.
        go_term_aspect: The GO aspect: molecular_function, biological_process, or
            cellular_component.
        evidence_code: The GO experimental evidence code for the experiment, for
            example IDA, IPI, IMP, IGI, IEP, or EXP. Its ECO class is looked up for you.
        reference_mention: How you identified the paper for the reference lookup, for
            example its PMID or title.
        existing_annotation_status: Whether the existing-annotation lookup returned
            annotations (available), returned none (not_found), or could not run
            (unavailable).
        evidence_record_ids: Verified evidence record IDs supporting this recommendation.
        gene_product_curie: The RGD CURIE exactly as resolve_gene_product returned it;
            leave it out when the identity is unresolved or ambiguous.
        gene_product_label: The gene-product symbol resolve_gene_product returned with
            that CURIE; leave it out without a CURIE.
        go_term_curie: The GO term CURIE exactly as quickgo_api_call returned it; leave
            it out when no term was confirmed.
        go_term_label: The GO term name quickgo_api_call returned with that CURIE.
        reference_curie: The reference CURIE exactly as agr_literature_reference_lookup
            returned it; leave it out when the paper could not be matched.
        with_from: With/From entries, each with the paper's wording and, when a lookup
            returned one, its identifier.
        blocking_reasons: Concrete reasons a curator must resolve before acceptance;
            required when the gene product has no confirmed CURIE.
        validation_guidance: Optional short sentence forwarding relevant rules from your
            configured prompt and case-specific paper context to this finding's validators.
            Distinguish domain rules from paper facts. Do not copy whole prompts, quote
            document instructions, guess an identity, or replace verified evidence.
    """

    attempted_query = _attempt_query(
        "stage_go_recommendation",
        pending_ref_id=pending_ref_id,
        gene_product_mention=gene_product_mention,
        go_term_mention=go_term_mention,
        evidence_record_ids=evidence_record_ids,
    )
    _emit_go_builder_event(
        "go_builder.stage_requested", action="stage", input_summary=attempted_query
    )
    try:
        stage_input = GOStageInput(
            validation_guidance=validation_guidance,
            pending_ref_id=pending_ref_id,
            gene_product={
                "mention": gene_product_mention,
                "entity_type": gene_product_entity_type,
                "taxon_curie": gene_product_taxon_curie,
                "curie": gene_product_curie,
                "label": gene_product_label,
            },
            go_term={
                "mention": go_term_mention,
                "aspect": go_term_aspect,
                "curie": go_term_curie,
                "label": go_term_label,
            },
            evidence_code=evidence_code,
            reference={"mention": reference_mention, "curie": reference_curie},
            rationale=rationale,
            evidence_record_ids=evidence_record_ids,
            with_from=with_from or [],
            qualifiers=qualifiers or [],
            annotation_extensions=annotation_extensions or [],
            negated=negated,
            blocking_reasons=blocking_reasons or [],
            existing_annotation_status=existing_annotation_status,
            existing_annotations=existing_annotations or [],
            existing_annotation_provenance=existing_annotation_provenance or {},
            existing_annotation_note=existing_annotation_note,
            identity_resolution=identity_resolution or {},
            hierarchy_limitations=hierarchy_limitations or [],
            section_limitations=section_limitations or [],
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="stage_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_go_recommendation",
            attempted_query=attempted_query,
        )

    staged_fields = _stage_payload(stage_input)
    grounding_refs, grounding_requirements, grounding_issues = _ground_payload(
        staged_fields["payload"]
    )
    if grounding_issues:
        return _go_validation_result(
            message="stage_go_recommendation rejected ungrounded source values.",
            issues=grounding_issues,
            method="stage_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields["source_grounding"] = {
        "payload": copy.deepcopy(staged_fields["payload"]),
        "requirements": grounding_requirements,
    }

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _go_candidate_id(workspace, stage_input.pending_ref_id)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=[stage_input.pending_ref_id],
        evidence_record_ids=list(stage_input.evidence_record_ids),
        resolver_selection_refs=grounding_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "resolution": _resolution_summary(staged_fields["payload"]),
        "builder": _builder_summary(workspace),
    }
    _emit_go_builder_event(
        "go_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _resolution_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Each GO value's resolution state, so the extractor sees what stayed unresolved."""

    summary: dict[str, Any] = {
        field_path: payload[field_path].get("resolution_state")
        for field_path in ("gene_product", "go_term", "evidence_code", "reference_curie")
        if isinstance(payload.get(field_path), Mapping)
    }
    summary["with_from"] = [
        entry.get("resolution_state")
        for entry in payload.get("with_from") or []
        if isinstance(entry, Mapping)
    ]
    return summary


class _PatchValueError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _patched_resolvable_value(field_path: str, value: Any) -> Any:
    """Rebuild a resolvable GO value from staging-shaped input.

    The builder derives the resolution state again from the supplied
    identifier, so a patch can never mark a value resolved on its own.
    """

    entries = value if field_path == "with_from" else [value]
    if not isinstance(entries, list):
        raise _PatchValueError("invalid_type", "with_from takes a list of entries")
    for entry in entries:
        if isinstance(entry, Mapping) and BUILDER_OWNED_KEYS.intersection(entry):
            raise _PatchValueError(
                "builder_owned_resolution_state",
                "The builder derives resolution state; give the paper wording and, "
                "when a lookup returned one, the identifier only.",
            )
    try:
        if field_path == "gene_product":
            return GOGeneProductInput.model_validate(value).value()
        if field_path == "go_term":
            return GOTermInput.model_validate(value).value()
        if field_path == "reference_curie":
            return GOReferenceInput.model_validate(value).value()
        if field_path == "evidence_code":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("evidence_code takes the evidence code as a non-empty string")
            return evidence_code_value(value.strip())
        return _with_from_values(
            [GOWithFromEntry.model_validate(entry) for entry in entries]
        )
    except ValidationError as exc:
        raise _PatchValueError(
            "invalid_value", "; ".join(str(error.get("msg")) for error in exc.errors())
        ) from exc
    except ValueError as exc:
        raise _PatchValueError("invalid_value", str(exc)) from exc


_RESOLVABLE_PATCH_FIELDS = frozenset(
    {"gene_product", "go_term", "evidence_code", "reference_curie", "with_from"}
)


def _patch_go_recommendation_impl(
    candidate_id: str,
    updates: List[Mapping[str, Any]],
) -> AgrQueryResult:
    """Correct allowed fields on one staged GO recommendation.

    Args:
        candidate_id: The staged candidate to correct.
        updates: Field corrections, each with field_path and value (or evidence_record_ids).
            A GO value (gene_product, go_term, reference_curie, each with_from entry) takes the
            same shape as staging: the paper wording as `mention` plus the identifier only
            when a lookup returned it; evidence_code takes the code. The builder works out
            again whether each value is matched or unresolved.
            A `rationale` update must be non-empty; it cannot be cleared.
    """

    attempted_query = _attempt_query(
        "patch_go_recommendation",
        candidate_id=candidate_id,
        updates=list(updates or []),
    )
    _emit_go_builder_event(
        "go_builder.patch_requested", action="patch", input_summary=attempted_query
    )
    try:
        patch_input = GOPatchInput.model_validate(
            {"candidate_id": candidate_id, "updates": list(updates or [])}
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="patch_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _go_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                }
            ],
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields = dict(candidate.staged_fields)
    payload = dict(staged_fields.get("payload") or {})
    evidence_ids = list(candidate.evidence_record_ids)
    for update in patch_input.updates:
        if update.field_path == "evidence_record_ids":
            evidence_ids = [
                str(item).strip()
                for item in (update.evidence_record_ids or [])
                if str(item).strip()
            ]
            if not evidence_ids:
                return _go_validation_result(
                    message="evidence_record_ids patch requires verified evidence IDs.",
                    issues=[
                        {
                            "field_path": "evidence_record_ids",
                            "reason": "missing_evidence_record_ids",
                            "message": "At least one evidence ID is required.",
                        }
                    ],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
        elif update.field_path == "validation_guidance":
            if update.value is not None and not isinstance(update.value, str):
                return _go_validation_result(
                    message="validation_guidance must be a string or null.",
                    issues=[{"field_path": "validation_guidance", "reason": "invalid_type"}],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
            staged_fields["validation_guidance"] = update.value
        elif update.field_path == "rationale":
            try:
                if not isinstance(update.value, str):
                    raise ValueError("rationale must be a non-empty string")
                payload["rationale"] = normalize_rationale(update.value)
            except ValueError as exc:
                return _go_validation_result(
                    message="patch_go_recommendation rejected the rationale update.",
                    issues=[
                        {
                            "field_path": "rationale",
                            "reason": "invalid_rationale",
                            "message": str(exc),
                        }
                    ],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
        elif update.field_path in _RESOLVABLE_PATCH_FIELDS:
            try:
                payload[update.field_path] = _patched_resolvable_value(
                    update.field_path, update.value
                )
            except _PatchValueError as exc:
                return _go_validation_result(
                    message="patch_go_recommendation rejected a GO value update.",
                    issues=[
                        {
                            "field_path": update.field_path,
                            "reason": exc.reason,
                            "message": str(exc),
                        }
                    ],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
        elif update.value in (None, ""):
            payload.pop(update.field_path, None)
        else:
            payload[update.field_path] = update.value
    staged_fields["payload"] = payload
    grounding_refs, grounding_requirements, grounding_issues = _ground_payload(payload)
    if grounding_issues:
        return _go_validation_result(
            message="patch_go_recommendation rejected ungrounded source values.",
            issues=grounding_issues,
            method="patch_go_recommendation",
            attempted_query=attempted_query,
        )
    staged_fields["source_grounding"] = {
        "payload": copy.deepcopy(payload),
        "requirements": grounding_requirements,
    }
    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=list(candidate.pending_ref_ids),
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=grounding_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
        "resolution": _resolution_summary(payload),
        "builder": _builder_summary(workspace),
    }
    _emit_go_builder_event(
        "go_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_go_recommendation_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "discard_go_recommendation", candidate_id=candidate_id, reason=reason
    )
    _emit_go_builder_event(
        "go_builder.discard_requested",
        action="discard",
        input_summary=attempted_query,
    )
    try:
        discard_input = GODiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _go_validation_result(
            message="discard_go_recommendation failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_go_recommendation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(
            discard_input.candidate_id, reason=discard_input.reason
        )
    except (KeyError, ExtractionBuilderError) as exc:
        return _go_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "discard_failed",
                    "message": str(exc),
                }
            ],
            method="discard_go_recommendation",
            attempted_query=attempted_query,
        )
    summary = {
        **_builder_summary(workspace, include_discarded=True),
        "discarded_candidate_id": discard_input.candidate_id,
    }
    _emit_go_builder_event(
        "go_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _list_staged_go_recommendations_impl(
    include_discarded: bool,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "list_staged_go_recommendations",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_go_builder_event(
        "go_builder.list_requested", action="list", input_summary=attempted_query
    )
    try:
        list_input = GOListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="list_staged_go_recommendations failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_go_recommendations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
    )
    _emit_go_builder_event(
        "go_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _find_staged_go_recommendations_impl(
    field_value_contains: Optional[str] = None,
    pending_ref_id: Optional[str] = None,
    evidence_record_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    has_validation_errors: Optional[bool] = None,
    include_discarded: bool = False,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "find_staged_go_recommendations",
        field_value_contains=field_value_contains,
        pending_ref_id=pending_ref_id,
        evidence_record_id=evidence_record_id,
        candidate_id=candidate_id,
        has_validation_errors=has_validation_errors,
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit_go_builder_event(
        "go_builder.find_requested", action="find", input_summary=attempted_query
    )
    try:
        find_input = GOFindInput(
            field_value_contains=field_value_contains,
            pending_ref_id=pending_ref_id,
            evidence_record_id=evidence_record_id,
            candidate_id=candidate_id,
            has_validation_errors=has_validation_errors,
            include_discarded=include_discarded,
            limit=limit,
            offset=offset,
        )
    except ValidationError as exc:
        return _go_validation_result(
            message="find_staged_go_recommendations failed input validation.",
            issues=_model_validation_issues(exc),
            method="find_staged_go_recommendations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _search_builder_candidates(
        workspace,
        field_value_contains=find_input.field_value_contains,
        pending_ref_id=find_input.pending_ref_id,
        evidence_record_id=find_input.evidence_record_id,
        candidate_id=find_input.candidate_id,
        has_validation_errors=find_input.has_validation_errors,
        include_discarded=find_input.include_discarded,
        limit=find_input.limit,
        offset=find_input.offset,
    )
    _emit_go_builder_event(
        "go_builder.find_completed",
        action="find",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=summary["matched_candidate_count"],
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


def _materialize_go_with_events(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]],
    resolver_entry_lookup: Optional[Any],
) -> Any:
    materialization = materialize_go_builder_state(
        workspace=workspace,
        candidate_ids=candidate_ids,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_entry_lookup,
    )
    _emit_go_builder_event(
        "go_materializer.completed"
        if materialization.ok
        else "go_materializer.validation_failed",
        action="materialize",
        input_summary={
            "candidate_ids": list(candidate_ids),
            "materializer_id": GO_MATERIALIZER_ID,
        },
        output_summary=materialization.summary(),
        validation=(
            None
            if materialization.ok
            else {"status": "failed", "issues": list(materialization.issues)}
        ),
    )
    return materialization


def _finalize_go_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    attempted_query = _attempt_query(
        "finalize_go_extraction", candidate_ids=candidate_ids
    )
    _emit_go_builder_event(
        "go_builder.finalize_requested",
        action="finalize",
        input_summary=attempted_query,
    )
    try:
        GOFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _go_validation_result(
            message="finalize_go_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_go_extraction",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        evidence_records = get_active_evidence_records_snapshot()
    except RuntimeError:
        evidence_records = []
    outcome = finalize_builder_extraction(
        workspace=workspace,
        candidate_ids=candidate_ids,
        materialize=_materialize_go_with_events,
        evidence_records=evidence_records,
        resolver_entry_lookup=get_active_resolver_call_ledger().get_tool_output,
        materialized_candidate_prefix="rgd-go-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=True,
    )
    if not outcome.ok:
        return _go_validation_result(
            message=f"finalize_go_extraction {outcome.message}",
            issues=list(outcome.issues),
            method="finalize_go_extraction",
            attempted_query=attempted_query,
        )
    finalization = outcome.finalization
    if finalization is None:
        return _go_validation_result(
            message="finalize_go_extraction did not produce a finalization payload.",
            issues=[
                {
                    "field_path": "builder_finalization",
                    "reason": "missing_finalization",
                    "message": "Builder finalization payload is missing.",
                }
            ],
            method="finalize_go_extraction",
            attempted_query=attempted_query,
        )
    summary = {
        "builder_finalization": _builder_finalization_summary(finalization.summary()),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_go_builder_event(
        "go_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=finalization.finalized_candidate_count,
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


stage_go_recommendation = function_tool(
    strict_mode=False, name_override="stage_go_recommendation"
)(_stage_go_recommendation_impl)
patch_go_recommendation = function_tool(
    strict_mode=False, name_override="patch_go_recommendation"
)(_patch_go_recommendation_impl)
discard_go_recommendation = function_tool(
    strict_mode=False, name_override="discard_go_recommendation"
)(_discard_go_recommendation_impl)
list_staged_go_recommendations = function_tool(
    strict_mode=False, name_override="list_staged_go_recommendations"
)(_list_staged_go_recommendations_impl)
find_staged_go_recommendations = function_tool(
    strict_mode=False, name_override="find_staged_go_recommendations"
)(_find_staged_go_recommendations_impl)
finalize_go_extraction = function_tool(
    strict_mode=False, name_override="finalize_go_extraction"
)(_finalize_go_extraction_impl)


__all__ = [
    "discard_go_recommendation",
    "finalize_go_extraction",
    "find_staged_go_recommendations",
    "list_staged_go_recommendations",
    "patch_go_recommendation",
    "stage_go_recommendation",
]
