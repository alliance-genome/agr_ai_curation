"""Materialize one staged topic-entity-tag proposal into canonical extraction output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    SchemaRef,
)
from src.schemas.evidence_workspace import normalize_workspace_records
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from .constants import (
    ABC_LITERATURE_SERVICE_COMMIT,
    TOPIC_ENTITY_TAG_DEFINITION_NOTES,
    TOPIC_ENTITY_TAG_MATERIALIZER_ID,
    TOPIC_ENTITY_TAG_MODEL_ID,
    TOPIC_ENTITY_TAG_MOD_ABBREVIATION,
    TOPIC_ENTITY_TAG_OBJECT_ROLE,
    TOPIC_ENTITY_TAG_OBJECT_TYPE,
    TOPIC_ENTITY_TAG_PENDING_REF_ID,
    TOPIC_ENTITY_TAG_SCHEMA_ID,
    TOPIC_ENTITY_TAG_SCHEMA_NAME,
    TOPIC_ENTITY_TAG_SCHEMA_PROVIDER,
    TOPIC_ENTITY_TAG_SCHEMA_URI,
    TOPIC_ENTITY_TAG_TOPIC,
)


def _schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=TOPIC_ENTITY_TAG_SCHEMA_ID,
        provider=TOPIC_ENTITY_TAG_SCHEMA_PROVIDER,
        name=TOPIC_ENTITY_TAG_SCHEMA_NAME,
        version=ABC_LITERATURE_SERVICE_COMMIT,
        uri=TOPIC_ENTITY_TAG_SCHEMA_URI,
    )


@dataclass(frozen=True)
class TopicEntityTagMaterializationResult:
    """Outcome the shared ``finalize_builder_extraction`` orchestration consumes."""

    payload: dict[str, Any] | None
    issues: tuple[dict[str, Any], ...]
    source_candidate_ids: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.payload is not None and not self.issues

    def summary(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "source_candidate_ids": list(self.source_candidate_ids),
            "evidence_record_ids": list(self.evidence_record_ids),
            "validation_issues": [dict(issue) for issue in self.issues],
        }


def _failure(
    candidate_ids: tuple[str, ...],
    *,
    field_path: str,
    reason: str,
    message: str,
    **details: Any,
) -> TopicEntityTagMaterializationResult:
    return TopicEntityTagMaterializationResult(
        payload=None,
        issues=({"field_path": field_path, "reason": reason, "message": message, **details},),
        source_candidate_ids=candidate_ids,
        evidence_record_ids=(),
    )


def _output_payload(
    *,
    curatable_objects: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
    candidate_ids: tuple[str, ...],
    builder_run_id: Any,
    produced_by: str,
) -> dict[str, Any]:
    return {
        "summary": (
            "Finalized gene-expression topic-tag proposal for curator review."
            if curatable_objects
            else "Finalized with no topic-tag proposal because the paper could not be assessed."
        ),
        "curatable_objects": curatable_objects,
        "metadata": {
            "evidence_records": evidence_records,
            "provenance": {
                "source": TOPIC_ENTITY_TAG_MATERIALIZER_ID,
                "produced_by": produced_by,
                "builder_run_id": builder_run_id,
                "source_candidate_ids": list(candidate_ids),
            },
        },
        "run_summary": {
            "candidate_count": len(candidate_ids),
            "kept_count": len(curatable_objects),
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
        "schema_ref": _schema_ref().model_dump(mode="json", exclude_none=True),
    }


def materialize_topic_entity_tag_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    produced_by: str = "gene_expression_topic_tag",
) -> TopicEntityTagMaterializationResult:
    """Build the single proposal for this paper, or an empty result for an empty finalize."""

    ids = tuple(value.strip() for value in candidate_ids if isinstance(value, str) and value.strip())
    if candidate_ids and not ids:
        return _failure(
            ids,
            field_path="candidate_ids",
            reason="blank_candidate_id",
            message="candidate_ids must contain non-empty candidate IDs.",
        )
    if len(ids) > 1:
        return _failure(
            ids,
            field_path="candidate_ids",
            reason="multiple_topic_entity_tags",
            message="One paper gets one topic-tag proposal; finalize a single candidate.",
        )

    normalized_records = normalize_workspace_records(evidence_records or [])
    curatable_objects: list[dict[str, Any]] = []
    retained_evidence_ids: tuple[str, ...] = ()

    if ids:
        try:
            candidate = workspace.get_candidate(ids[0])
        except KeyError as exc:
            return _failure(
                ids, field_path="candidate_ids", reason="unknown_candidate_id", message=str(exc)
            )
        staged = dict(candidate.staged_fields)
        negated = staged.get("negated")
        rationale = staged.get("rationale")
        if not isinstance(negated, bool):
            return _failure(
                ids,
                field_path="negated",
                reason="missing_negated",
                message="A proposal must say whether the paper has data for this topic.",
            )
        if not isinstance(rationale, str) or not rationale.strip():
            return _failure(
                ids,
                field_path="rationale",
                reason="missing_rationale",
                message="A proposal must say why the agent reached its answer.",
            )
        evidence_ids = tuple(dict.fromkeys(candidate.evidence_record_ids))
        positions = {
            record["evidence_record_id"]: position
            for position, record in enumerate(normalized_records)
        }
        unknown = [evidence_id for evidence_id in evidence_ids if evidence_id not in positions]
        if unknown:
            return _failure(
                ids,
                field_path="evidence_record_ids",
                reason="unknown_evidence_record_id",
                message="evidence_record_ids must reference records verified in this run.",
                evidence_record_ids=unknown,
            )
        if not negated and not evidence_ids:
            return _failure(
                ids,
                field_path="evidence_record_ids",
                reason="missing_evidence_record_ids",
                message="A positive proposal must cite at least one verified evidence record.",
            )

        payload: dict[str, Any] = {
            "topic": TOPIC_ENTITY_TAG_TOPIC,
            "mod_abbreviation": TOPIC_ENTITY_TAG_MOD_ABBREVIATION,
            "negated": negated,
            "rationale": rationale.strip(),
        }

        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=TOPIC_ENTITY_TAG_OBJECT_TYPE,
                object_role=TOPIC_ENTITY_TAG_OBJECT_ROLE,
                pending_ref_id=TOPIC_ENTITY_TAG_PENDING_REF_ID,
                model_ref=TOPIC_ENTITY_TAG_MODEL_ID,
                schema_ref=_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=list(TOPIC_ENTITY_TAG_DEFINITION_NOTES),
                payload=payload,
                evidence_record_ids=list(evidence_ids),
                metadata_refs=[
                    {
                        "metadata_path": f"evidence_records[{positions[evidence_id]}]",
                        "role": "verified_evidence",
                    }
                    for evidence_id in evidence_ids
                ],
                metadata={"object_role": TOPIC_ENTITY_TAG_OBJECT_ROLE},
            ).model_dump(mode="json", exclude_none=True)
        )
        retained_evidence_ids = evidence_ids

    output_payload = _output_payload(
        curatable_objects=curatable_objects,
        evidence_records=normalized_records,
        candidate_ids=ids,
        builder_run_id=getattr(workspace, "run_id", None),
        produced_by=produced_by,
    )
    try:
        validated = DomainEnvelopeExtractionResult.model_validate(output_payload)
    except ValidationError as exc:
        return TopicEntityTagMaterializationResult(
            payload=None,
            issues=tuple(
                {
                    "field_path": ".".join(str(part) for part in error.get("loc", ())),
                    "reason": str(error.get("type") or "invalid"),
                    "message": str(error.get("msg") or "Invalid topic-tag output"),
                }
                for error in exc.errors()
            ),
            source_candidate_ids=ids,
            evidence_record_ids=(),
        )
    return TopicEntityTagMaterializationResult(
        payload=validated.model_dump(mode="json", exclude_none=True),
        issues=(),
        source_candidate_ids=ids,
        evidence_record_ids=retained_evidence_ids,
    )


__all__ = [
    "TopicEntityTagMaterializationResult",
    "materialize_topic_entity_tag_builder_state",
]
