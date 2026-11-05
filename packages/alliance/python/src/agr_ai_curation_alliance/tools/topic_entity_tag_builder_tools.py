"""Builder tools for the gene-expression topic-tag agent.

Thin domain adapter over the shared builder workspace and ``finalize_builder_extraction``.
The tools accept only what the agent decides: ``negated``, its ``rationale`` and evidence.
The topic and data provider come from the pack's configuration, and the paper's ABC
reference comes from the document record. One paper gets one proposal.
"""

from __future__ import annotations

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
)

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_SUCCESS,
    attempt_query as _attempt_query,
)
from agr_ai_curation_runtime.evidence_workspace import get_active_evidence_records_snapshot
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event

from agr_ai_curation_alliance.domain_packs.topic_entity_tag import (
    TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
    TOPIC_ENTITY_TAG_OBJECT_TYPE,
    TOPIC_ENTITY_TAG_PENDING_REF_ID,
    materialize_topic_entity_tag_builder_state,
)

from .agr_curation import (
    _BUILDER_LIST_DEFAULT_LIMIT,
    AgrQueryResult,
    _builder_candidate_list,
    _builder_finalization_summary,
    _builder_summary,
    _ok,
)
from .builder_finalization import finalize_builder_extraction
from .builder_rationale import document_rationale_arg, normalize_rationale

_DISCARDED = "discarded"


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicEntityTagInput(_StrictToolModel):
    negated: StrictBool
    rationale: StrictStr
    evidence_record_ids: List[StrictStr] = Field(default_factory=list)

    @field_validator("rationale")
    @classmethod
    def _valid_rationale(cls, value: str) -> str:
        return normalize_rationale(value)


class TopicEntityTagListInput(_StrictToolModel):
    include_discarded: bool = False
    limit: int = Field(default=_BUILDER_LIST_DEFAULT_LIMIT, ge=0)
    offset: int = Field(default=0, ge=0)


class TopicEntityTagFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr]


def _emit(
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
        domain_pack_id=TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": TOPIC_ENTITY_TAG_OBJECT_TYPE,
        },
    )


def _invalid(
    message: str,
    issues: Sequence[Mapping[str, Any]],
    *,
    action: str,
    attempted_query: Optional[dict[str, Any]],
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit(
        "topic_entity_tag_builder.validation_failed",
        action=action,
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


def _pydantic_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field_path": ".".join(str(part) for part in error.get("loc", ())),
            "reason": str(error.get("type") or "invalid"),
            "message": str(error.get("msg") or "Invalid value"),
        }
        for error in exc.errors()
    ]


def _validated_proposal(raw: Mapping[str, Any]) -> tuple[TopicEntityTagInput | None, list[dict[str, Any]]]:
    try:
        proposal = TopicEntityTagInput(**raw)
    except ValidationError as exc:
        return None, _pydantic_issues(exc)
    if not proposal.negated and not proposal.evidence_record_ids:
        return None, [
            {
                "field_path": "evidence_record_ids",
                "reason": "missing_evidence_record_ids",
                "message": "A positive proposal must cite at least one verified evidence record.",
            }
        ]
    return proposal, []


def _staged_fields(proposal: TopicEntityTagInput) -> dict[str, Any]:
    return {
        "domain_pack_id": TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
        "object_type": TOPIC_ENTITY_TAG_OBJECT_TYPE,
        "negated": proposal.negated,
        "rationale": proposal.rationale,
    }


@document_rationale_arg
def _stage_topic_entity_tag_impl(
    negated: bool,
    rationale: str,
    evidence_record_ids: Optional[List[str]] = None,
) -> AgrQueryResult:
    """Stage the single topic-tag proposal for this paper.

    Args:
        negated: False when the paper has data for this topic, true when it does not.
        evidence_record_ids: Verified evidence records. Required when negated is false.
            When negated is true, the absence of data cannot be quoted, so this is optional.
    """

    attempted_query = _attempt_query(
        "stage_topic_entity_tag",
        negated=negated,
        evidence_record_ids=evidence_record_ids,
    )
    _emit("topic_entity_tag_builder.stage_requested", action="stage", input_summary=attempted_query)
    proposal, issues = _validated_proposal(
        {
            "negated": negated,
            "rationale": rationale,
            "evidence_record_ids": evidence_record_ids or [],
        }
    )
    if proposal is None:
        return _invalid(
            "stage_topic_entity_tag failed input validation.",
            issues,
            action="stage",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    active = [
        candidate
        for candidate in workspace.candidates.values()
        if candidate.status != _DISCARDED
    ]
    if active:
        return _invalid(
            "One paper gets one topic-tag proposal; patch or discard the staged one.",
            [
                {
                    "field_path": "candidate_id",
                    "reason": "topic_entity_tag_already_staged",
                    "message": "A proposal is already staged for this paper.",
                    "candidate_id": active[0].candidate_id,
                }
            ],
            action="stage",
            attempted_query=attempted_query,
        )

    candidate = workspace.stage_candidate(
        id_prefix="topic-entity-tag-candidate",
        pending_ref_id=TOPIC_ENTITY_TAG_PENDING_REF_ID,
        staged_fields=_staged_fields(proposal),
        evidence_record_ids=proposal.evidence_record_ids,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "evidence_record_ids": candidate.evidence_record_ids,
        "builder": _builder_summary(workspace),
    }
    _emit(
        "topic_entity_tag_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


@document_rationale_arg
def _patch_topic_entity_tag_impl(
    candidate_id: str,
    negated: Optional[bool] = None,
    rationale: Optional[str] = None,
    evidence_record_ids: Optional[List[str]] = None,
) -> AgrQueryResult:
    """Change the staged proposal. Only the fields you pass are changed.

    Args:
        candidate_id: The staged proposal's candidate ID.
        negated: New value, if it changes.
        evidence_record_ids: Replacement list of verified evidence records.
    """

    attempted_query = _attempt_query(
        "patch_topic_entity_tag",
        candidate_id=candidate_id,
        negated=negated,
        evidence_record_ids=evidence_record_ids,
    )
    _emit("topic_entity_tag_builder.patch_requested", action="patch", input_summary=attempted_query)
    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(candidate_id)
    except KeyError as exc:
        return _invalid(
            str(exc),
            [{"field_path": "candidate_id", "reason": "unknown_candidate_id", "message": str(exc)}],
            action="patch",
            attempted_query=attempted_query,
        )

    if candidate.status == _DISCARDED:
        return _invalid(
            "This proposal was discarded; stage a new one.",
            [
                {
                    "field_path": "candidate_id",
                    "reason": "discarded_candidate",
                    "message": "A discarded proposal cannot be changed.",
                }
            ],
            action="patch",
            attempted_query=attempted_query,
        )

    current = candidate.staged_fields
    proposal, issues = _validated_proposal(
        {
            "negated": current.get("negated") if negated is None else negated,
            "rationale": current.get("rationale") if rationale is None else rationale,
            "evidence_record_ids": (
                list(candidate.evidence_record_ids)
                if evidence_record_ids is None
                else evidence_record_ids
            ),
        }
    )
    if proposal is None:
        return _invalid(
            "patch_topic_entity_tag failed input validation.",
            issues,
            action="patch",
            attempted_query=attempted_query,
        )

    workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=_staged_fields(proposal),
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=proposal.evidence_record_ids,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {"candidate_id": candidate_id, "builder": _builder_summary(workspace)}
    _emit(
        "topic_entity_tag_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _discard_topic_entity_tag_impl(
    candidate_id: str,
    reason: Optional[str] = None,
) -> AgrQueryResult:
    """Discard the staged proposal so you can stage a new one."""

    attempted_query = _attempt_query(
        "discard_topic_entity_tag", candidate_id=candidate_id, reason=reason
    )
    _emit("topic_entity_tag_builder.discard_requested", action="discard", input_summary=attempted_query)
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(candidate_id, reason=reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _invalid(
            str(exc),
            [{"field_path": "candidate_id", "reason": "discard_failed", "message": str(exc)}],
            action="discard",
            attempted_query=attempted_query,
        )
    summary = {
        **_builder_summary(workspace, include_discarded=True),
        "discarded_candidate_id": candidate_id,
    }
    _emit(
        "topic_entity_tag_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _list_staged_topic_entity_tags_impl(
    include_discarded: bool = False,
    limit: int = _BUILDER_LIST_DEFAULT_LIMIT,
    offset: int = 0,
    workspace_revision: Optional[int] = None,
) -> AgrQueryResult:
    """List the staged proposal for this paper."""

    attempted_query = _attempt_query(
        "list_staged_topic_entity_tags",
        include_discarded=include_discarded,
        limit=limit,
        offset=offset,
    )
    _emit("topic_entity_tag_builder.list_requested", action="list", input_summary=attempted_query)
    try:
        list_input = TopicEntityTagListInput(
            include_discarded=include_discarded, limit=limit, offset=offset
        )
    except ValidationError as exc:
        return _invalid(
            "list_staged_topic_entity_tags failed input validation.",
            _pydantic_issues(exc),
            action="list",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_candidate_list(
        workspace,
        include_discarded=list_input.include_discarded,
        limit=list_input.limit,
        offset=list_input.offset,
        workspace_revision=workspace_revision,
    )
    _emit(
        "topic_entity_tag_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


def _finalize_topic_entity_tag_extraction_impl(candidate_ids: List[str]) -> AgrQueryResult:
    """Finalize the staged proposal so a curator can review it.

    Pass the one staged candidate ID. Pass candidate_ids=[] only when the paper could
    not be assessed at all. A paper with no data for the topic is a negated proposal,
    not an empty finalize.
    """

    attempted_query = _attempt_query(
        "finalize_topic_entity_tag_extraction", candidate_ids=candidate_ids
    )
    _emit("topic_entity_tag_builder.finalize_requested", action="finalize", input_summary=attempted_query)
    try:
        TopicEntityTagFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _invalid(
            "finalize_topic_entity_tag_extraction failed input validation.",
            _pydantic_issues(exc),
            action="finalize",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    # Check before the shared finalize step: its failure path rewrites candidate status,
    # which would bring a discarded proposal back.
    discarded = [
        candidate_id
        for candidate_id in candidate_ids
        if (candidate := workspace.candidates.get(candidate_id.strip())) is not None
        and candidate.status == _DISCARDED
    ]
    if discarded:
        return _invalid(
            "A discarded proposal cannot be finalized; stage a new one.",
            [
                {
                    "field_path": "candidate_ids",
                    "reason": "discarded_candidate",
                    "message": "A discarded proposal cannot be finalized.",
                    "candidate_ids": discarded,
                }
            ],
            action="finalize",
            attempted_query=attempted_query,
        )

    try:
        evidence_records = get_active_evidence_records_snapshot()
    except RuntimeError:
        evidence_records = []

    outcome = finalize_builder_extraction(
        workspace=workspace,
        candidate_ids=candidate_ids,
        materialize=materialize_topic_entity_tag_builder_state,
        evidence_records=evidence_records,
        materialized_candidate_prefix="topic-entity-tag-envelope",
        # The materializer applies the rule: only a positive proposal requires evidence.
        require_evidence_record_ids=False,
    )
    if not outcome.ok:
        return _invalid(
            f"finalize_topic_entity_tag_extraction {outcome.message}",
            list(outcome.issues),
            action="finalize",
            attempted_query=attempted_query,
        )

    finalization = outcome.finalization
    summary = {
        "builder_finalization": _builder_finalization_summary(finalization.summary()),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit(
        "topic_entity_tag_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(
        data=summary,
        count=finalization.finalized_candidate_count,
        lookup_status=LOOKUP_STATUS_SUCCESS,
    )


stage_topic_entity_tag = function_tool(
    strict_mode=False, name_override="stage_topic_entity_tag"
)(_stage_topic_entity_tag_impl)
patch_topic_entity_tag = function_tool(
    strict_mode=False, name_override="patch_topic_entity_tag"
)(_patch_topic_entity_tag_impl)
discard_topic_entity_tag = function_tool(
    strict_mode=False, name_override="discard_topic_entity_tag"
)(_discard_topic_entity_tag_impl)
list_staged_topic_entity_tags = function_tool(
    strict_mode=False, name_override="list_staged_topic_entity_tags"
)(_list_staged_topic_entity_tags_impl)
finalize_topic_entity_tag_extraction = function_tool(
    strict_mode=False, name_override="finalize_topic_entity_tag_extraction"
)(_finalize_topic_entity_tag_extraction_impl)


__all__ = [
    "discard_topic_entity_tag",
    "finalize_topic_entity_tag_extraction",
    "list_staged_topic_entity_tags",
    "patch_topic_entity_tag",
    "stage_topic_entity_tag",
]
