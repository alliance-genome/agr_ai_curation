"""Builder-pattern materializer for the disease extractor (Phase 2 migration, FULL LinkML alignment).

Mirrors ``materialize_phenotype_builder_state`` / ``materialize_gene_expression_builder_state``:
read finalized builder-workspace candidates and emit the shared extraction-output payload
(``curatable_objects[]`` + ``metadata`` with RELATIVE ``metadata_refs``). The generic converter
``domain_envelope_from_extraction_result`` turns that payload into a DomainEnvelope, nesting
``metadata`` under ``metadata.extraction_metadata``.

POSTURE — FULL LinkML ALIGNMENT (unlike phenotype/allele's preserve-existing-posture migration).
Per the approach-doc Decisions (D1-D6):

  * D1 CONCRETE SUBTYPES: each candidate materializes one CONCRETE
    ``GeneDiseaseAnnotation`` / ``AlleleDiseaseAnnotation`` / ``AGMDiseaseAnnotation`` curatable_unit
    selected by the staged ``subject_type``. The abstract ``DiseaseAnnotation`` object_type is emitted
    ONLY when the subject kind is unknown/missing (a genuine validator_unresolved situation, NOT a
    structural finding). Writes are NOT blocked — the pending/abstract/write-blocked placeholder
    posture is retired for disease.
  * D2 SUBJECT: the candidate carries a pending ``DiseaseAnnotationSubject`` sub-object plus the
    inline ``disease_annotation_subject`` payload; the active ``subject_entity_validation`` binding
    resolves concrete Gene/Allele/AGM identity. The resolved subject identity mirrors onto
    ``disease_annotation_object`` is NOT required; the DOID snapshot mirror is declared in
    ``domain_pack.yaml`` ``materializes_to_field_paths`` (NOT code special-casing, invariant §5.4).
  * D3 ECO: ``evidence_code_curies[]`` are staged and snapshotted; the active
    ``disease_evidence_code_lookup`` binding validates them.
  * D4 REFERENCE: BLOCKED. There is no durable Alliance reference identity available at
    chat-extraction time (the inline dispatch builds its candidate against a transient
    ``document_id="chat-runtime"`` record and ``pdf_documents`` carries no AGRKB/PMID/DOI), so
    ``single_reference`` stays PENDING. The reference validator returns ``validator_unresolved``,
    which is NOT structural. See the approach-doc open questions.
  * D5 RELATIONS: the staged ``disease_relation_name`` is validated against the subject-type CV
    subset by ``disease_relation_cv_lookup``.
  * D6: condition_relations are out of scope (deferred with host-annotation work).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable, Sequence

from pydantic import ValidationError, model_validator

from src.lib.openai_agents.models import (
    DiseaseExtractionResultEnvelope as RuntimeDiseaseResultEnvelope,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    ObjectRef,
    SchemaRef,
)
from src.schemas.models.base import EvidenceRecord

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from .constants import (
    DISEASE_ANNOTATION_KIND,
    DISEASE_ANNOTATION_OBJECT_ROLE,
    DISEASE_ANNOTATION_TYPE_CONSTANT,
    DISEASE_CORE_SCHEMA_SOURCE_FILE,
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_DOMAIN_PACK_VERSION,
    DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
    DISEASE_LINKML_SCHEMA_ID,
    DISEASE_LINKML_SCHEMA_SOURCE_FILE,
    DISEASE_MATERIALIZER_ID,
    DISEASE_MODEL_ID,
    DISEASE_OBJECT_TYPE,
    DISEASE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE,
    DISEASE_ONTOLOGY_TERM_VALIDATOR_BINDING_ID,
    DISEASE_REFERENCE_LINKML_SCHEMA_ID,
    DISEASE_REFERENCE_OBJECT_TYPE,
    DISEASE_REFERENCE_SCHEMA_SOURCE_FILE,
    DISEASE_REFERENCE_VALIDATOR_BINDING_ID,
    DISEASE_SUBJECT_LINKML_SCHEMA_ID,
    DISEASE_SUBJECT_OBJECT_TYPE,
    DISEASE_SUBJECT_SUBTYPES,
    DISEASE_SUBJECT_VALIDATOR_BINDING_ID,
    DISEASE_TERM_LINKML_SCHEMA_ID,
    DISEASE_TERM_OBJECT_TYPE,
)

# Pending-resolution sentinels.
_SUBJECT_PENDING_STATE = "pending_entity_resolution"
_SUBJECT_BLOCKED_STATE = "blocked_missing_subject"
_TERM_PENDING_STATE = "pending_ontology_resolution"
_REFERENCE_PENDING_STATE = "pending_reference_resolution"
_REFERENCE_BLOCKED_REASON = (
    "No durable Alliance reference identity (AGRKB/PMID/DOI) is available at chat-extraction time; "
    "single_reference resolution is deferred (see disease-approach.md open questions)."
)

_DISEASE_ASSERTION_ROLES = frozenset(
    {"primary", "background", "comparative", "model_context", "unspecified"}
)
_DISEASE_ASSERTION_CONFIDENCES = frozenset({"high", "medium", "low"})


def _clean_text(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text or None


def _unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _clean_text(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _materialization_issue(
    *,
    field_path: str,
    reason: str,
    message: str,
    candidate_id: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    issue = {"field_path": field_path, "reason": reason, "message": message}
    if candidate_id:
        issue["candidate_id"] = candidate_id
    issue.update({key: value for key, value in details.items() if value is not None})
    return issue


def _pydantic_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        _materialization_issue(
            field_path=".".join(str(part) for part in error.get("loc", ())),
            reason=str(error.get("type") or "invalid"),
            message=str(error.get("msg") or "Invalid materialized disease envelope"),
        )
        for error in exc.errors()
    ]


def _linkml_uri(source_file: str) -> str:
    return (
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLIANCE_LINKML_COMMIT}/{source_file}"
    )


def _subtype_for_subject(subject_type: str | None) -> tuple[str, str, str]:
    """Select the concrete (object_type, schema_id, class_name) for a staged subject kind (D1).

    Unknown/missing subject kinds fall back to the abstract DiseaseAnnotation; the active subject
    validator then surfaces a validator_unresolved (non-structural) finding.
    """
    normalized = (subject_type or "").strip().lower()
    return DISEASE_SUBJECT_SUBTYPES.get(
        normalized,
        (DISEASE_OBJECT_TYPE, DISEASE_LINKML_SCHEMA_ID, "DiseaseAnnotation"),
    )


def _annotation_schema_ref(schema_id: str, class_name: str) -> SchemaRef:
    return SchemaRef(
        schema_id=schema_id,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=class_name,
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(DISEASE_LINKML_SCHEMA_SOURCE_FILE),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            f"Concrete {class_name} materialized by subject kind (full LinkML alignment).",
        ],
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                    "class": class_name,
                }
            }
        },
    )


def _subject_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=DISEASE_SUBJECT_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="BiologicalEntity",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(DISEASE_CORE_SCHEMA_SOURCE_FILE),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Disease annotation subject; concrete Gene, Allele, or AGM identity is resolved by the "
            "active subject_entity_validation binding."
        ],
    )


def _term_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=DISEASE_TERM_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="DOTerm",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(DISEASE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE),
    )


def _reference_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=DISEASE_REFERENCE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="Reference",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(DISEASE_REFERENCE_SCHEMA_SOURCE_FILE),
    )


def _normalized_evidence_records(
    evidence_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_fields = set(EvidenceRecord.model_fields)
    for record in evidence_records:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("workspace_status") or record.get("status") or "").strip() == "discarded":
            continue
        payload = {
            key: value
            for key, value in record.items()
            if key in allowed_fields and value is not None
        }
        evidence_id = str(payload.get("evidence_record_id") or "").strip()
        if not evidence_id or evidence_id in seen:
            continue
        try:
            normalized_record = EvidenceRecord.model_validate(payload)
        except ValidationError:
            continue
        seen.add(evidence_id)
        normalized.append(normalized_record.model_dump(mode="json", exclude_none=True))
    return normalized


def _candidate_pending_ref_id(candidate: Any, staged_fields: Mapping[str, Any], index: int) -> str:
    pending_ref_id = _clean_text(staged_fields.get("pending_ref_id"))
    if pending_ref_id:
        return pending_ref_id
    pending_ref_ids = getattr(candidate, "pending_ref_ids", None) or []
    if pending_ref_ids:
        pending_ref_id = _clean_text(pending_ref_ids[0])
        if pending_ref_id:
            return pending_ref_id
    return f"disease-annotation-{index + 1}"


def _subject_payload(staged_fields: Mapping[str, Any]) -> dict[str, Any]:
    subject_identifier = _clean_text(staged_fields.get("subject_identifier"))
    subject_label = _clean_text(staged_fields.get("subject_label"))
    subject_type = _clean_text(staged_fields.get("subject_type"))

    if subject_identifier and subject_type:
        resolution_state = _SUBJECT_PENDING_STATE
    else:
        resolution_state = _SUBJECT_BLOCKED_STATE

    payload: dict[str, Any] = {"resolution_state": resolution_state}
    if subject_identifier:
        payload["subject_identifier"] = subject_identifier
    if subject_label:
        payload["subject_label"] = subject_label
    if subject_type:
        payload["subject_type"] = subject_type
    if resolution_state == _SUBJECT_BLOCKED_STATE:
        payload["resolution_note"] = (
            "Disease extraction did not provide a durable disease_annotation_subject "
            "identifier and subtype; the abstract DiseaseAnnotation is materialized."
        )
    return payload


def _disease_term_payload(
    *,
    mention: str,
    curie: str | None,
    name: str | None,
    source_mentions: Sequence[str],
) -> dict[str, Any]:
    return {
        "resolution_state": _TERM_PENDING_STATE,
        "curie": curie,
        "name": name or mention,
        "source_mentions": list(source_mentions),
    }


def _condition_relations_payload(raw_relations: Any) -> list[dict[str, Any]]:
    """Materialize staged condition_relations into the concrete nested annotation shape.

    Maps each staged ``{condition_relation_type, conditions: [{condition_*_curie, ...}]}`` into
    ``{condition_relation_type: {name}, conditions: [{condition_class: {curie}, ...}]}`` — the
    exact target paths the active bindings read (``condition_relations.condition_relation_type.name``
    and ``condition_relations.conditions.condition_<x>.curie``). Empty leaves are dropped; a
    relation with no resolvable conditions is dropped entirely. Only invoked when conditions were
    staged, so absent conditions leave the payload untouched (mirrors the optional-field pattern).
    """

    if not isinstance(raw_relations, Sequence) or isinstance(raw_relations, (str, bytes)):
        return []
    # The condition CURIE leaf is nested one object deep (e.g. condition_class.curie).
    _curie_leaf = {
        "condition_class_curie": "condition_class",
        "condition_id_curie": "condition_id",
        "condition_chemical_curie": "condition_chemical",
        "condition_taxon_curie": "condition_taxon",
    }
    relations: list[dict[str, Any]] = []
    for raw_relation in raw_relations:
        if not isinstance(raw_relation, Mapping):
            continue
        relation_type = _clean_text(raw_relation.get("condition_relation_type"))
        if not relation_type:
            continue
        conditions: list[dict[str, Any]] = []
        raw_conditions = raw_relation.get("conditions")
        if not isinstance(raw_conditions, Sequence) or isinstance(raw_conditions, (str, bytes)):
            raw_conditions = []
        for raw_condition in raw_conditions:
            if not isinstance(raw_condition, Mapping):
                continue
            condition: dict[str, Any] = {}
            for staged_key, leaf_key in _curie_leaf.items():
                curie = _clean_text(raw_condition.get(staged_key))
                if curie:
                    condition[leaf_key] = {"curie": curie}
            for text_key in ("condition_free_text", "condition_summary"):
                value = _clean_text(raw_condition.get(text_key))
                if value:
                    condition[text_key] = value
            if condition:
                conditions.append(condition)
        if conditions:
            relations.append(
                {
                    "condition_relation_type": {"name": relation_type},
                    "conditions": conditions,
                }
            )
    return relations


def _evidence_quote_payload(evidence_record: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_record_id": _clean_text(evidence_record.get("evidence_record_id")),
        "verified_quote": _clean_text(evidence_record.get("verified_quote")),
    }
    for field_name in ("entity", "page", "section", "subsection", "chunk_id", "figure_reference"):
        value = evidence_record.get(field_name)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            payload[field_name] = value
    return payload


class DiseaseBuilderExtractionOutput(RuntimeDiseaseResultEnvelope):
    """Validated builder output for one disease extraction run.

    Validates ``curatable_objects`` against the disease object contract inline so the builder path
    produces a structurally-clean concrete-subtype shape (subject/term/reference object refs present,
    DOID name present, role/confidence valid, evidence resolves to verified metadata.evidence_records).
    """

    @model_validator(mode="after")
    def _validate_disease_objects(self) -> "DiseaseBuilderExtractionOutput":
        errors = validate_disease_builder_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _is_concrete_or_abstract_annotation(object_type: str) -> bool:
    return object_type in {
        DISEASE_OBJECT_TYPE,
        *(subtype[0] for subtype in DISEASE_SUBJECT_SUBTYPES.values()),
    }


def validate_disease_builder_objects(
    output: RuntimeDiseaseResultEnvelope,
) -> tuple[str, ...]:
    """Return structural-contract error messages for builder-materialized disease output."""

    errors: list[str] = []
    evidence_by_id = {
        record.evidence_record_id: record
        for record in output.metadata.evidence_records
        if record.evidence_record_id
    }
    annotations = [
        obj for obj in output.curatable_objects if _is_concrete_or_abstract_annotation(obj.object_type)
    ]
    if not annotations:
        errors.append("curatable_objects must contain at least one disease annotation")

    for index, obj in enumerate(annotations):
        location = f"curatable_objects[disease#{index}]"
        if obj.object_role != DISEASE_ANNOTATION_OBJECT_ROLE:
            errors.append(f"{location}.object_role must be {DISEASE_ANNOTATION_OBJECT_ROLE}")
        if obj.model_ref != DISEASE_MODEL_ID:
            errors.append(f"{location}.model_ref must be {DISEASE_MODEL_ID}")
        if obj.schema_ref is None:
            errors.append(f"{location}.schema_ref is required")

        payload = obj.payload if isinstance(obj.payload, Mapping) else {}
        if not _clean_text(payload.get("mention")):
            errors.append(f"{location}.payload.mention is required")
        term = payload.get("disease_annotation_object")
        if not isinstance(term, Mapping) or not _clean_text(term.get("name")):
            errors.append(f"{location}.payload.disease_annotation_object.name is required")
        if not isinstance(payload.get("disease_annotation_subject"), Mapping):
            errors.append(f"{location}.payload.disease_annotation_subject is required")
        role = _clean_text(payload.get("role"))
        if role not in _DISEASE_ASSERTION_ROLES:
            errors.append(f"{location}.payload.role must be a valid DiseaseAssertionRole")
        confidence = _clean_text(payload.get("confidence"))
        if confidence not in _DISEASE_ASSERTION_CONFIDENCES:
            errors.append(f"{location}.payload.confidence must be a valid DiseaseAssertionConfidence")
        data_provider = payload.get("data_provider")
        if not isinstance(data_provider, Mapping) or not _clean_text(
            data_provider.get("abbreviation")
        ):
            errors.append(f"{location}.payload.data_provider.abbreviation is required")

        ref_types = {ref.object_type for ref in obj.object_refs}
        missing_ref_types = {
            DISEASE_SUBJECT_OBJECT_TYPE,
            DISEASE_TERM_OBJECT_TYPE,
            DISEASE_REFERENCE_OBJECT_TYPE,
            DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
        } - ref_types
        if missing_ref_types:
            errors.append(
                f"{location}.object_refs missing types: " + ", ".join(sorted(missing_ref_types))
            )

        if not obj.evidence_record_ids:
            errors.append(f"{location}.evidence_record_ids must not be empty")
        if payload.get("evidence_record_ids") != list(obj.evidence_record_ids):
            errors.append(
                f"{location}.payload.evidence_record_ids must match {location}.evidence_record_ids"
            )
        for evidence_id in obj.evidence_record_ids:
            evidence_record = evidence_by_id.get(evidence_id)
            if evidence_record is None:
                errors.append(
                    f"{location}.evidence_record_ids[{evidence_id}] must resolve in "
                    "metadata.evidence_records[]"
                )
            elif _clean_text(evidence_record.verified_quote) is None:
                errors.append(
                    f"{location}.evidence_record {evidence_id} must include verified_quote"
                )

    return tuple(errors)


class DiseaseMaterializationResult:
    """Outcome from materializing staged disease builder candidates into envelope output.

    Structurally matches ``GeneExpressionMaterializationResult`` / ``PhenotypeMaterializationResult``
    so it plugs into the generic ``finalize_builder_extraction`` orchestration without bespoke
    handling.
    """

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None,
        issues: tuple[dict[str, Any], ...],
        source_candidate_ids: tuple[str, ...],
        evidence_record_ids: tuple[str, ...],
    ) -> None:
        self._payload = payload
        self._issues = issues
        self._source_candidate_ids = source_candidate_ids
        self._evidence_record_ids = evidence_record_ids

    @property
    def ok(self) -> bool:
        return self._payload is not None and not self._issues

    @property
    def payload(self) -> dict[str, Any] | None:
        return self._payload

    @property
    def issues(self) -> tuple[dict[str, Any], ...]:
        return self._issues

    @property
    def evidence_record_ids(self) -> tuple[str, ...]:
        return self._evidence_record_ids

    def summary(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "source_candidate_ids": list(self._source_candidate_ids),
            "evidence_record_ids": list(self._evidence_record_ids),
            "validation_issues": [dict(issue) for issue in self._issues],
        }


def _annotation_object_metadata(
    *,
    subject_resolution_state: str,
    class_name: str,
) -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: DISEASE_ANNOTATION_OBJECT_ROLE,
        "assertion_kind": DISEASE_ANNOTATION_KIND,
        "validation_state": subject_resolution_state,
        "materialized_by": DISEASE_MATERIALIZER_ID,
        PROVIDER_REFS_METADATA_KEY: {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": DISEASE_LINKML_SCHEMA_SOURCE_FILE,
                "class": class_name,
            }
        },
    }


def materialize_disease_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    resolver_entry_lookup: Callable[[str], Any] | None = None,
    produced_by: str = "disease_extractor",
) -> DiseaseMaterializationResult:
    """Build canonical DiseaseExtractionResultEnvelope output from finalized builder state.

    One candidate -> one CONCRETE ``GeneDiseaseAnnotation`` / ``AlleleDiseaseAnnotation`` /
    ``AGMDiseaseAnnotation`` curatable_unit (abstract ``DiseaseAnnotation`` only on unknown subject)
    plus pending ``DiseaseAnnotationSubject`` / ``DOTerm`` / ``Reference`` / ``EvidenceQuote``
    objects. ``metadata_refs`` are RELATIVE.
    """

    normalized_candidate_ids = tuple(
        value.strip()
        for value in candidate_ids
        if isinstance(value, str) and value.strip()
    )
    issues: list[dict[str, Any]] = []
    candidates: list[Any] = []
    for candidate_id in normalized_candidate_ids:
        try:
            candidates.append(workspace.get_candidate(candidate_id))
        except KeyError as exc:
            issues.append(
                _materialization_issue(
                    field_path="candidate_ids",
                    reason="unknown_candidate_id",
                    message=str(exc),
                    candidate_id=candidate_id,
                )
            )

    normalized_evidence_records = _normalized_evidence_records(evidence_records or [])
    evidence_records_by_id = {
        record["evidence_record_id"]: record
        for record in normalized_evidence_records
        if isinstance(record.get("evidence_record_id"), str)
    }
    evidence_position_by_id = {
        record.get("evidence_record_id"): position
        for position, record in enumerate(normalized_evidence_records)
    }

    curatable_objects: list[CuratableObjectEnvelope] = []
    raw_mentions: list[dict[str, Any]] = []
    retained_evidence_ids: list[str] = []
    annotation_index = 0

    for candidate in candidates:
        staged_fields = copy.deepcopy(dict(getattr(candidate, "staged_fields", {}) or {}))
        annotation_ref = _candidate_pending_ref_id(candidate, staged_fields, annotation_index)
        mention = _clean_text(staged_fields.get("mention"))
        if mention is None:
            issues.append(
                _materialization_issue(
                    field_path="mention",
                    reason="missing_disease_mention",
                    message="Finalized disease candidates require a free-text disease mention.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue

        disease_name = _clean_text(staged_fields.get("disease_name")) or mention
        disease_curie = _clean_text(staged_fields.get("disease_curie"))
        role = _clean_text(staged_fields.get("role"))
        if role not in _DISEASE_ASSERTION_ROLES:
            issues.append(
                _materialization_issue(
                    field_path="role",
                    reason="invalid_role",
                    message="Finalized disease candidates require a valid DiseaseAssertionRole.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue
        confidence = _clean_text(staged_fields.get("confidence"))
        if confidence not in _DISEASE_ASSERTION_CONFIDENCES:
            issues.append(
                _materialization_issue(
                    field_path="confidence",
                    reason="invalid_confidence",
                    message="Finalized disease candidates require a valid DiseaseAssertionConfidence.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue
        data_provider_abbreviation = _clean_text(staged_fields.get("data_provider"))
        if data_provider_abbreviation is None:
            issues.append(
                _materialization_issue(
                    field_path="data_provider",
                    reason="missing_data_provider",
                    message="Finalized disease candidates require a data provider abbreviation.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue

        evidence_ids = _unique_strings(
            getattr(candidate, "evidence_record_ids", None)
            or staged_fields.get("evidence_record_ids")
        )
        if not evidence_ids:
            issues.append(
                _materialization_issue(
                    field_path="evidence_record_ids",
                    reason="missing_evidence_record_ids",
                    message="Finalized disease candidates require non-empty evidence_record_ids.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue

        resolved_evidence: list[dict[str, Any]] = []
        candidate_evidence_blocked = False
        for evidence_id in evidence_ids:
            evidence_record = evidence_records_by_id.get(evidence_id)
            if evidence_record is None:
                issues.append(
                    _materialization_issue(
                        field_path="evidence_record_ids",
                        reason="unknown_evidence_record_id",
                        message=(
                            "evidence_record_ids must reference verified active-run "
                            "metadata.evidence_records entries."
                        ),
                        candidate_id=getattr(candidate, "candidate_id", None),
                        evidence_record_id=evidence_id,
                    )
                )
                candidate_evidence_blocked = True
                continue
            if _clean_text(evidence_record.get("verified_quote")) is None:
                issues.append(
                    _materialization_issue(
                        field_path="evidence_record_ids",
                        reason="incomplete_evidence_record",
                        message="Verified evidence records must include verified_quote.",
                        candidate_id=getattr(candidate, "candidate_id", None),
                        evidence_record_id=evidence_id,
                    )
                )
                candidate_evidence_blocked = True
                continue
            resolved_evidence.append(evidence_record)
        if candidate_evidence_blocked or not resolved_evidence:
            continue

        source_mentions = _unique_strings(staged_fields.get("source_mentions")) or [mention]
        negated = bool(staged_fields.get("negated"))
        relation_name = _clean_text(staged_fields.get("disease_relation_name"))
        evidence_code_curies = _unique_strings(staged_fields.get("evidence_code_curies"))
        # R4 optional slots. genetic_sex is a single CV term; disease_qualifiers and with_or_from
        # are multivalued (validated/snapshotted at [0], full list carried in the payload).
        genetic_sex_name = _clean_text(staged_fields.get("genetic_sex_name"))
        disease_qualifier_names = _unique_strings(staged_fields.get("disease_qualifier_names"))
        with_gene_identifiers = _unique_strings(staged_fields.get("with_gene_identifiers"))
        condition_relations = _condition_relations_payload(staged_fields.get("condition_relations"))
        subject_payload = _subject_payload(staged_fields)
        subject_resolution_state = subject_payload["resolution_state"]
        subject_type = subject_payload.get("subject_type")
        object_type, schema_id, class_name = _subtype_for_subject(subject_type)

        term_payload = _disease_term_payload(
            mention=mention,
            curie=disease_curie,
            name=disease_name,
            source_mentions=source_mentions,
        )
        reference_payload: dict[str, Any] = {
            "resolution_state": _REFERENCE_PENDING_STATE,
            "resolution_note": _REFERENCE_BLOCKED_REASON,
        }
        for field_name in ("reference_id", "title", "filename", "pmid", "doi", "curie"):
            value = _clean_text(staged_fields.get(field_name))
            if value is not None:
                reference_payload[field_name] = value

        subject_ref_id = f"disease-subject-{annotation_index + 1}"
        term_ref_id = f"disease-term-{annotation_index + 1}"
        reference_ref_id = f"disease-reference-{annotation_index + 1}"
        primary_evidence_id = _clean_text(resolved_evidence[0].get("evidence_record_id"))

        # Pending DiseaseAnnotationSubject (validated_reference; subject_entity_validation resolves).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=DISEASE_SUBJECT_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=subject_ref_id,
                schema_ref=_subject_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Disease annotation subject; concrete Gene, Allele, or AGM identity is resolved "
                    "by the active subject_entity_validation binding."
                ],
                payload=copy.deepcopy(subject_payload),
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": subject_resolution_state,
                    "validator_binding_id": DISEASE_SUBJECT_VALIDATOR_BINDING_ID,
                },
            )
        )
        # Pending DOTerm (validated_reference; the active ontology validator resolves the DOID).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=DISEASE_TERM_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=term_ref_id,
                schema_ref=_term_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload=copy.deepcopy(term_payload),
                evidence_record_ids=[primary_evidence_id] if primary_evidence_id else [],
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": _TERM_PENDING_STATE,
                    "validator_binding_id": DISEASE_ONTOLOGY_TERM_VALIDATOR_BINDING_ID,
                },
            )
        )
        # Pending Reference (validated_reference; reference resolution is deferred — D4 blocked).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=DISEASE_REFERENCE_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=reference_ref_id,
                schema_ref=_reference_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[_REFERENCE_BLOCKED_REASON],
                payload=copy.deepcopy(reference_payload),
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": _REFERENCE_PENDING_STATE,
                    "validator_binding_id": DISEASE_REFERENCE_VALIDATOR_BINDING_ID,
                },
            )
        )

        # EvidenceQuote metadata_only objects + concrete-annotation object_refs.
        annotation_object_refs: list[ObjectRef] = [
            ObjectRef(pending_ref_id=subject_ref_id, object_type=DISEASE_SUBJECT_OBJECT_TYPE),
            ObjectRef(pending_ref_id=term_ref_id, object_type=DISEASE_TERM_OBJECT_TYPE),
            ObjectRef(pending_ref_id=reference_ref_id, object_type=DISEASE_REFERENCE_OBJECT_TYPE),
        ]
        annotation_evidence_ids: list[str] = []
        evidence_snapshot_records: list[dict[str, Any]] = []
        for evidence_index, evidence_record in enumerate(resolved_evidence, start=1):
            evidence_id = _clean_text(evidence_record.get("evidence_record_id"))
            evidence_ref_id = f"evidence-quote-{annotation_index + 1}-{evidence_index}"
            quote_payload = _evidence_quote_payload(evidence_record)
            annotation_evidence_ids.append(evidence_id)
            evidence_snapshot_records.append(quote_payload)
            annotation_object_refs.append(
                ObjectRef(
                    pending_ref_id=evidence_ref_id,
                    object_type=DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
                )
            )
            curatable_objects.append(
                CuratableObjectEnvelope(
                    object_type=DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
                    object_role="metadata_only",
                    pending_ref_id=evidence_ref_id,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=quote_payload,
                    evidence_record_ids=[evidence_id] if evidence_id else [],
                    metadata={OBJECT_ROLE_METADATA_KEY: "metadata_only"},
                )
            )

        annotation_payload: dict[str, Any] = {
            "annotation_kind": DISEASE_ANNOTATION_KIND,
            # R4: annotation_type is the curation method, fixed to manually_curated. It is NOT an
            # extractor edit target; the backend always materializes this constant.
            "annotation_type_name": DISEASE_ANNOTATION_TYPE_CONSTANT,
            "mention": mention,
            "disease_annotation_object": {"curie": disease_curie, "name": disease_name}
            if disease_curie
            else {"name": disease_name},
            "disease_annotation_subject": copy.deepcopy(subject_payload),
            "role": role,
            "confidence": confidence,
            "data_provider": {"abbreviation": data_provider_abbreviation},
            "single_reference": copy.deepcopy(reference_payload),
            "evidence_record_ids": annotation_evidence_ids,
            "evidence_records": evidence_snapshot_records,
            "source_mentions": list(source_mentions),
            "negated": negated,
        }
        if relation_name is not None:
            annotation_payload["disease_relation_name"] = relation_name
        if evidence_code_curies:
            annotation_payload["evidence_code_curies"] = list(evidence_code_curies)
        # R4 optional slots — only carried when the extractor staged them.
        if genetic_sex_name is not None:
            annotation_payload["genetic_sex_name"] = genetic_sex_name
        if disease_qualifier_names:
            annotation_payload["disease_qualifier_names"] = list(disease_qualifier_names)
        if with_gene_identifiers:
            annotation_payload["with_gene_identifiers"] = list(with_gene_identifiers)
        # EXPERIMENTAL CONDITIONS: nested condition_relations[].conditions[]. Only carried when
        # the extractor staged them. Each condition references the annotation's evidence
        # (evidence_record_ids on the annotation) per the evidence contract — no condition-level
        # quote text is materialized. The active experimental_condition_validation binding fans
        # out one composite validation per condition_relations[i].conditions[j].
        if condition_relations:
            annotation_payload["condition_relations"] = condition_relations

        metadata_refs = [
            {"metadata_path": f"raw_mentions[{annotation_index}]", "role": "source_mention"}
        ]
        for evidence_id in annotation_evidence_ids:
            position = evidence_position_by_id.get(evidence_id)
            if position is not None:
                metadata_refs.append(
                    {
                        "metadata_path": f"evidence_records[{position}]",
                        "role": "verified_evidence",
                    }
                )
        raw_mentions.append(
            {
                "mention": mention,
                "entity_type": "disease",
                "evidence_record_ids": annotation_evidence_ids,
            }
        )
        retained_evidence_ids.extend(annotation_evidence_ids)

        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=object_type,
                object_role=DISEASE_ANNOTATION_OBJECT_ROLE,
                pending_ref_id=annotation_ref,
                model_ref=DISEASE_MODEL_ID,
                schema_ref=_annotation_schema_ref(schema_id, class_name),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    f"Concrete {class_name} from builder-staged disease assertion (full LinkML "
                    "alignment).",
                    "Subject identity, DOID term, ECO codes, and relation are resolved by the active "
                    "validator bindings; single_reference resolution is deferred (D4).",
                ],
                payload=annotation_payload,
                object_refs=annotation_object_refs,
                evidence_record_ids=annotation_evidence_ids,
                metadata_refs=metadata_refs,
                metadata=_annotation_object_metadata(
                    subject_resolution_state=subject_resolution_state,
                    class_name=class_name,
                ),
            )
        )
        annotation_index += 1

    provenance = {
        "source": DISEASE_MATERIALIZER_ID,
        "produced_by": produced_by,
        "builder_run_id": getattr(workspace, "run_id", None),
        "source_candidate_ids": list(normalized_candidate_ids),
    }
    output_payload = {
        "summary": "Finalized disease extraction from builder-staged assertions.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in curatable_objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence_records,
            "normalization_notes": [
                "Disease annotation envelope was assembled by backend materialization from "
                "builder state."
            ],
            "exclusions": [],
            "ambiguities": [],
            "notes": [],
            "provenance": provenance,
        },
        "run_summary": {
            "candidate_count": len(normalized_candidate_ids),
            "kept_count": annotation_index,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
        "schema_ref": _annotation_schema_ref(
            DISEASE_LINKML_SCHEMA_ID, "DiseaseAnnotation"
        ).model_dump(mode="json", exclude_none=True),
    }

    if annotation_index == 0 and not issues:
        issues.append(
            _materialization_issue(
                field_path="curatable_objects",
                reason="no_retained_candidates",
                message="Finalized disease extraction produced no retained disease annotation objects.",
            )
        )

    if not issues:
        try:
            output = DiseaseBuilderExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_pydantic_issues(exc))
        else:
            output_payload = output.model_dump(mode="json", exclude_none=True)

    return DiseaseMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_unique_strings(retained_evidence_ids)),
    )


__all__ = [
    "DISEASE_DOMAIN_PACK_ID",
    "DISEASE_DOMAIN_PACK_VERSION",
    "DiseaseBuilderExtractionOutput",
    "DiseaseMaterializationResult",
    "materialize_disease_builder_state",
    "validate_disease_builder_objects",
]
