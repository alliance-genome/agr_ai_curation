"""Structured builder tools for the RGD GO paper-curation specialist."""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Sequence

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

from agr_ai_curation_alliance.domain_packs.go import (
    GO_DOMAIN_PACK_ID,
    GO_MATERIALIZER_ID,
    GO_OBJECT_TYPE,
    materialize_go_builder_state,
)
from agr_ai_curation_alliance.domain_packs.go.values import (
    BUILDER_OWNED_KEYS,
    GENE_PRODUCT_IDENTITY,
    GO_TERM_IDENTITY,
    REFERENCE_IDENTITY,
    evidence_code_value,
    gene_product_value,
    go_term_value,
    qualifier_values,
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

_GO_ASPECT_VALUES = frozenset(
    {"molecular_function", "biological_process", "cellular_component"}
)
# A paper-stated identifier of each kind, recorded only as the extractor's proposal.
_RGD_CURIE = re.compile(r"^RGD:\d+$")
_GO_CURIE = re.compile(r"^GO:\d{7}$")
_CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:[^\s:]+$")


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


def _paper_curie(value: Optional[str], pattern: re.Pattern[str], kind: str) -> Optional[str]:
    cleaned = _clean_optional(value)
    if cleaned is not None and not pattern.fullmatch(cleaned):
        raise ValueError(f"a paper-stated {kind} must be written as the paper prints it, e.g. {pattern.pattern}")
    return cleaned


class GOWithFromEntry(_StrictToolModel):
    """One With/From entry: the paper's wording and any identifier the paper itself prints."""

    mention: StrictStr = Field(
        description="The With/From entry as the paper words it, for example the partner gene or allele name."
    )
    proposed_curie: Optional[StrictStr] = Field(
        default=None,
        description="An identifier for the entry only when the paper itself prints it; never one looked up.",
    )

    @field_validator("mention")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("proposed_curie")
    @classmethod
    def _paper_identifier(cls, value: Optional[str]) -> Optional[str]:
        return _paper_curie(value, _CURIE, "identifier")


class GOGeneProductInput(_StrictToolModel):
    mention: StrictStr
    entity_type: StrictStr
    taxon_curie: StrictStr
    proposed_curie: Optional[StrictStr] = None

    @field_validator("mention", "entity_type", "taxon_curie")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("proposed_curie")
    @classmethod
    def _paper_identifier(cls, value: Optional[str]) -> Optional[str]:
        return _paper_curie(value, _RGD_CURIE, "RGD identifier")

    def value(self) -> dict[str, Any]:
        return gene_product_value(
            self.mention,
            proposed_curie=self.proposed_curie,
            entity_type=self.entity_type,
            taxon_curie=self.taxon_curie,
        )


class GOTermInput(_StrictToolModel):
    mention: StrictStr
    aspect: StrictStr
    proposed_curie: Optional[StrictStr] = None

    @field_validator("mention", "aspect")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("proposed_curie")
    @classmethod
    def _paper_identifier(cls, value: Optional[str]) -> Optional[str]:
        return _paper_curie(value, _GO_CURIE, "GO identifier")

    @model_validator(mode="after")
    def _known_aspect(self) -> "GOTermInput":
        if self.aspect not in _GO_ASPECT_VALUES:
            raise ValueError("go_term_aspect is not a canonical GO aspect")
        return self

    def value(self) -> dict[str, Any]:
        return go_term_value(self.mention, proposed_curie=self.proposed_curie, aspect=self.aspect)


class GOReferenceInput(_StrictToolModel):
    mention: StrictStr
    proposed_curie: Optional[StrictStr] = None

    @field_validator("mention")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("proposed_curie")
    @classmethod
    def _paper_identifier(cls, value: Optional[str]) -> Optional[str]:
        return _paper_curie(value, _CURIE, "reference identifier")

    def value(self) -> dict[str, Any]:
        return reference_value(self.mention, proposed_curie=self.proposed_curie)


def _with_from_values(entries: Sequence[GOWithFromEntry]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for entry in entries:
        value = with_from_value(entry.mention, proposed_curie=entry.proposed_curie)
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
    hierarchy_limitations: List[StrictStr] = Field(default_factory=list)
    section_limitations: List[StrictStr] = Field(default_factory=list)

    @field_validator("pending_ref_id", "evidence_code")
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
            "qualifiers": qualifier_values(
                stage_input.qualifiers, aspect=stage_input.go_term.aspect
            ),
            "annotation_extensions": list(stage_input.annotation_extensions),
            "negated": stage_input.negated,
            "rationale": stage_input.rationale,
            "provider_context": {
                "provider_key": "RGD",
                "taxon_curie": stage_input.gene_product.taxon_curie,
                "review_lane": "rgd_go_curator_review",
                "hierarchy_limitations": list(stage_input.hierarchy_limitations),
                "section_limitations": list(stage_input.section_limitations),
            },
            "blocking_reasons": list(stage_input.blocking_reasons),
        },
        "evidence_record_ids": list(stage_input.evidence_record_ids),
    }


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
    evidence_record_ids: List[str],
    gene_product_proposed_curie: Optional[str] = None,
    go_term_proposed_curie: Optional[str] = None,
    reference_proposed_curie: Optional[str] = None,
    with_from: Optional[List[GOWithFromEntry]] = None,
    qualifiers: Optional[List[str]] = None,
    annotation_extensions: Optional[List[str]] = None,
    negated: bool = False,
    blocking_reasons: Optional[List[str]] = None,
    hierarchy_limitations: Optional[List[str]] = None,
    section_limitations: Optional[List[str]] = None,
    validation_guidance: Optional[str] = None,
) -> AgrQueryResult:
    """Stage one evidence-backed GO recommendation for canonical finalization.

    Record what the paper says; never look anything up. The gene product, GO term,
    reference and each with/from entry keep the paper's wording, and an identifier
    only when the paper itself prints it, as a proposal for validation. They are
    staged unresolved and not yet validated; validation finds and confirms the
    identities, and curators see UNRESOLVED next to the paper wording until it
    does. The evidence code and qualifiers are fixed choices the builder maps with
    its own tables.

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
        go_term_aspect: The GO aspect the claim is about: molecular_function,
            biological_process, or cellular_component.
        evidence_code: The GO experimental evidence code you chose for the experiment:
            EXP, IDA, IPI, IMP, IGI, or IEP. The builder maps it to its ECO class.
        reference_mention: How the paper identifies itself, for example its title or a
            PMID or DOI printed on it.
        evidence_record_ids: Verified evidence record IDs supporting this recommendation.
        gene_product_proposed_curie: An RGD identifier (RGD:<digits>) only when the paper
            itself prints it for this gene product; leave it out otherwise.
        go_term_proposed_curie: A GO identifier (GO:<7 digits>) only when the paper itself
            prints it; leave it out otherwise.
        reference_proposed_curie: A PMID or DOI only when the paper itself prints it, for
            example PMID:12345678; leave it out otherwise.
        with_from: With/From entries, each with the paper's wording and, only when the
            paper prints one, its identifier.
        qualifiers: GO relation qualifiers the evidence supports, chosen from the
            relations allowed for the aspect: enables or contributes_to (molecular
            function); involved_in, acts_upstream_of, acts_upstream_of_positive_effect,
            acts_upstream_of_negative_effect, acts_upstream_of_or_within,
            acts_upstream_of_or_within_positive_effect or
            acts_upstream_of_or_within_negative_effect (biological process); part_of,
            colocalizes_with, is_active_in or located_in (cellular component). Negation is
            its own field.
        blocking_reasons: Concrete reasons a curator must settle before acceptance, for
            example a mature-RNA product the paper does not tie to one locus.
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
                "proposed_curie": gene_product_proposed_curie,
            },
            go_term={
                "mention": go_term_mention,
                "aspect": go_term_aspect,
                "proposed_curie": go_term_proposed_curie,
            },
            evidence_code=evidence_code,
            reference={"mention": reference_mention, "proposed_curie": reference_proposed_curie},
            rationale=rationale,
            evidence_record_ids=evidence_record_ids,
            with_from=with_from or [],
            qualifiers=qualifiers or [],
            annotation_extensions=annotation_extensions or [],
            negated=negated,
            blocking_reasons=blocking_reasons or [],
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
    workspace = get_active_extraction_builder_workspace()
    candidate_id = _go_candidate_id(workspace, stage_input.pending_ref_id)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=[stage_input.pending_ref_id],
        evidence_record_ids=list(stage_input.evidence_record_ids),
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "builder": _builder_summary(workspace),
    }
    _emit_go_builder_event(
        "go_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


class _PatchValueError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# Keys the extractor never writes: validation (or a curator override) fills them in.
_EXTRACTOR_FORBIDDEN_KEYS = frozenset(
    {
        *BUILDER_OWNED_KEYS,
        *GENE_PRODUCT_IDENTITY,
        *GO_TERM_IDENTITY,
        *REFERENCE_IDENTITY,
        "code",
        "eco_curie",
        "name",
    }
)


def _patched_resolvable_value(field_path: str, value: Any) -> Any:
    """Rebuild a GO value from staging-shaped input: paper wording plus paper-stated proposals.

    The value is staged unresolved again; a patch never carries an identity or a state.
    """

    entries = value if field_path == "with_from" else [value]
    if not isinstance(entries, list):
        raise _PatchValueError("invalid_type", "with_from takes a list of entries")
    for entry in entries:
        if isinstance(entry, Mapping) and _EXTRACTOR_FORBIDDEN_KEYS.intersection(entry):
            raise _PatchValueError(
                "validation_owned_field",
                "Validation finds and confirms identities; give the paper wording and, only "
                "when the paper prints one, its identifier as proposed_curie.",
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
            same shape as staging: the paper wording as `mention`, plus `proposed_curie`
            only when the paper prints the identifier, and is staged unresolved again for
            validation. evidence_code takes the chosen code and qualifiers the list of
            chosen relations; the builder maps them with its own tables.
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
    qualifier_mentions = [
        entry["mention"] for entry in payload.get("qualifiers") or [] if isinstance(entry, Mapping)
    ]
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
        elif update.field_path == "qualifiers":
            if not isinstance(update.value, list) or not all(
                isinstance(item, str) and item.strip() for item in update.value
            ):
                return _go_validation_result(
                    message="patch_go_recommendation rejected a GO value update.",
                    issues=[
                        {
                            "field_path": "qualifiers",
                            "reason": "invalid_value",
                            "message": (
                                "qualifiers takes the chosen relations, a list of non-empty "
                                "strings; the builder maps each one for the term's aspect."
                            ),
                        }
                    ],
                    method="patch_go_recommendation",
                    attempted_query=attempted_query,
                )
            qualifier_mentions = [item.strip() for item in update.value]
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
    go_term = payload.get("go_term")
    if isinstance(go_term, Mapping):
        # Qualifiers are mapped again against the term's aspect, which a patch may change.
        payload["qualifiers"] = qualifier_values(qualifier_mentions, aspect=go_term.get("aspect"))
    staged_fields["payload"] = payload
    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=staged_fields,
        pending_ref_ids=list(candidate.pending_ref_ids),
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
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
        resolver_entry_lookup=None,
        materialized_candidate_prefix="rgd-go-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=False,
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
