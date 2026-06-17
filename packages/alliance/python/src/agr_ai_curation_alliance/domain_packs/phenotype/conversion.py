"""Builder-pattern materializer for the phenotype extractor (Phase 3 migration).

Mirrors ``gene_expression``'s ``materialize_gene_expression_builder_state``: read finalized
builder-workspace candidates and emit the shared extraction-output payload
(``curatable_objects[]`` + ``metadata`` with RELATIVE ``metadata_refs``). The generic converter
``domain_envelope_from_extraction_result`` turns that payload into a DomainEnvelope, nesting
``metadata`` under ``metadata.extraction_metadata``.

POSTURE (preserve the existing pack — runbook §3): the migration changes the EXTRACTION
MECHANISM, not the curation target. This materializer emits the SAME object graph the existing
envelope converter (``__init__.build_pending_phenotype_envelope_from_tool_verified_fixture``)
produced — one ``PhenotypeAnnotation`` curatable_unit per candidate, plus pending
``PhenotypeSubject`` / ``PhenotypeTerm`` / ``Reference`` / ``EvidenceQuote`` objects — with the
SAME blocked export/write metadata, the SAME pending ontology/subject resolution states, and the
SAME validator-binding ids. No new ontology/provider pairs are activated; the active
``phenotype_term_ontology_validator`` resolves the staged label/CURIE candidate inline.

NO ``materializes_to_field_paths`` mirror: the phenotype subject IS the canonical subject; there is
no second field that must mirror it (confirmed against the existing ``domain_pack.yaml``).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable, Sequence

from pydantic import ValidationError, model_validator

from src.lib.openai_agents.models import (
    PhenotypeResultEnvelope as RuntimePhenotypeResultEnvelope,
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
    PHENOTYPE_ANNOTATION_KIND,
    PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID,
    PHENOTYPE_ANNOTATION_MODEL_ID,
    PHENOTYPE_ANNOTATION_OBJECT_ROLE,
    PHENOTYPE_CORE_SCHEMA_SOURCE_FILE,
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_DOMAIN_PACK_VERSION,
    PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE,
    PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
    PHENOTYPE_MATERIALIZER_ID,
    PHENOTYPE_OBJECT_TYPE,
    PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE,
    PHENOTYPE_REFERENCE_LINKML_SCHEMA_ID,
    PHENOTYPE_REFERENCE_OBJECT_TYPE,
    PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE,
    PHENOTYPE_REFERENCE_VALIDATOR_BINDING_ID,
    PHENOTYPE_SUBJECT_LINKML_SCHEMA_ID,
    PHENOTYPE_SUBJECT_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
    PHENOTYPE_TERM_LINKML_SCHEMA_ID,
    PHENOTYPE_TERM_OBJECT_TYPE,
    PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
)

# Pending-resolution sentinels (preserve the existing-pack posture verbatim).
_SUBJECT_PENDING_STATE = "pending_entity_resolution"
_SUBJECT_BLOCKED_STATE = "blocked_missing_subject"
_TERM_PENDING_STATE = "pending_ontology_resolution"
_TERM_EXPORT_BLOCKED = "blocked_pending_ontology_resolution"
_TERM_WRITE_BLOCKED_REASON = "phenotype term CURIE unresolved"
_REFERENCE_PENDING_STATE = "pending_reference_resolution"


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
            message=str(error.get("msg") or "Invalid materialized phenotype envelope"),
        )
        for error in exc.errors()
    ]


def _linkml_uri(source_file: str) -> str:
    return (
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLIANCE_LINKML_COMMIT}/{source_file}"
    )


def _phenotype_annotation_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="PhenotypeAnnotation",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Pending envelope target; concrete phenotype annotation subtype is unresolved.",
        ],
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
                    "class": "PhenotypeAnnotation",
                }
            }
        },
    )


def _phenotype_subject_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=PHENOTYPE_SUBJECT_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="BiologicalEntity",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(PHENOTYPE_CORE_SCHEMA_SOURCE_FILE),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Generic subject placeholder until the Gene, Allele, or AGM subtype is resolved."
        ],
    )


def _phenotype_term_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=PHENOTYPE_TERM_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="PhenotypeTerm",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(PHENOTYPE_ONTOLOGY_TERM_SCHEMA_SOURCE_FILE),
    )


def _reference_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=PHENOTYPE_REFERENCE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="Reference",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(PHENOTYPE_REFERENCE_SCHEMA_SOURCE_FILE),
    )


def _blocked_export_behavior() -> dict[str, Any]:
    return {"status": "blocked", "exportable": False, "submit": False}


def _blocked_write_behavior() -> dict[str, Any]:
    return {"status": "blocked"}


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
    return f"phenotype-annotation-{index + 1}"


def _subject_payload(staged_fields: Mapping[str, Any]) -> dict[str, Any]:
    """Build the pending PhenotypeSubject payload (preserve existing-pack resolution logic)."""

    subject_identifier = _clean_text(staged_fields.get("subject_identifier"))
    subject_label = _clean_text(staged_fields.get("subject_label"))
    subject_type = _clean_text(staged_fields.get("subject_type"))
    taxon = _clean_text(staged_fields.get("subject_taxon")) or _clean_text(staged_fields.get("taxon"))

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
    if taxon:
        payload["taxon"] = taxon
    if resolution_state == _SUBJECT_BLOCKED_STATE:
        payload["resolution_note"] = (
            "Tool-verified phenotype extraction did not provide a durable "
            "phenotype_annotation_subject identifier and subtype."
        )
    return payload


def _ontology_lookup_hint(
    staged_fields: Mapping[str, Any],
    primary_evidence_record_id: str | None,
) -> dict[str, str]:
    hint: dict[str, str] = {}
    data_provider = _clean_text(staged_fields.get("data_provider"))
    taxon_id = (
        _clean_text(staged_fields.get("term_taxon_id"))
        or _clean_text(staged_fields.get("subject_taxon"))
        or _clean_text(staged_fields.get("taxon"))
    )
    if data_provider:
        hint["data_provider"] = data_provider
    if taxon_id:
        hint["taxon_id"] = taxon_id
    if primary_evidence_record_id:
        hint["evidence_record_id"] = primary_evidence_record_id
    return hint


def _phenotype_term_payload(
    *,
    statement: str,
    term_curie: str | None,
    term_label: str | None,
    source_mentions: Sequence[str],
    ontology_lookup_hint: Mapping[str, str],
) -> dict[str, Any]:
    """Pending PhenotypeTerm payload aligned to the active ontology validator binding inputs."""

    return {
        "resolution_state": _TERM_PENDING_STATE,
        "curie": term_curie,
        "label": term_label or statement,
        "source_mentions": list(source_mentions),
        "ontology_lookup_hint": dict(ontology_lookup_hint),
        "export_state": _TERM_EXPORT_BLOCKED,
        "write_blocked_reason": _TERM_WRITE_BLOCKED_REASON,
    }


def _evidence_quote_payload(
    evidence_record: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_record_id": _clean_text(evidence_record.get("evidence_record_id")),
        "verified_quote": _clean_text(evidence_record.get("verified_quote")),
    }
    for field_name in ("entity", "page", "section", "subsection", "chunk_id", "figure_reference"):
        value = evidence_record.get(field_name)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            payload[field_name] = value
    return payload


def _condition_relations_payload(raw_relations: Any) -> list[dict[str, Any]]:
    """Materialize staged condition_relations into the concrete nested annotation shape.

    Maps each staged ``{condition_relation_type, conditions: [{condition_*_curie, ...}]}`` into
    ``{condition_relation_type: {name}, conditions: [{condition_class: {curie}, ...}]}`` — the exact
    target paths the active bindings read (``condition_relations.condition_relation_type.name`` and
    ``condition_relations.conditions.condition_<x>.curie``). Empty leaves are dropped; a relation
    with no resolvable conditions is dropped entirely. Only invoked when conditions were staged, so
    absent conditions leave the payload untouched (mirrors the optional-field pattern).
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


class PhenotypeBuilderExtractionOutput(RuntimePhenotypeResultEnvelope):
    """Validated builder output for one phenotype extraction run.

    Validates ``curatable_objects`` against the phenotype object contract inline so the builder
    path produces the same structurally-clean shape as the envelope path (subject/term/reference/
    evidence object refs present, free-text statement present, evidence resolves to verified
    metadata.evidence_records[]).
    """

    @model_validator(mode="after")
    def _validate_phenotype_objects(self) -> "PhenotypeBuilderExtractionOutput":
        errors = validate_phenotype_builder_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validate_phenotype_builder_objects(
    output: RuntimePhenotypeResultEnvelope,
) -> tuple[str, ...]:
    """Return structural-contract error messages for builder-materialized phenotype output."""

    errors: list[str] = []
    evidence_by_id = {
        record.evidence_record_id: record
        for record in output.metadata.evidence_records
        if record.evidence_record_id
    }
    annotations = [
        obj for obj in output.curatable_objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    ]
    if not annotations:
        errors.append("curatable_objects must contain at least one PhenotypeAnnotation")

    for index, obj in enumerate(annotations):
        location = f"curatable_objects[PhenotypeAnnotation#{index}]"
        if obj.object_role != PHENOTYPE_ANNOTATION_OBJECT_ROLE:
            errors.append(f"{location}.object_role must be {PHENOTYPE_ANNOTATION_OBJECT_ROLE}")
        if obj.model_ref != PHENOTYPE_ANNOTATION_MODEL_ID:
            errors.append(f"{location}.model_ref must be {PHENOTYPE_ANNOTATION_MODEL_ID}")
        if obj.schema_ref is None or obj.schema_ref.schema_id != PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID:
            errors.append(
                f"{location}.schema_ref.schema_id must be {PHENOTYPE_ANNOTATION_LINKML_SCHEMA_ID}"
            )

        payload = obj.payload if isinstance(obj.payload, Mapping) else {}
        if not _clean_text(payload.get("phenotype_annotation_object")):
            errors.append(f"{location}.payload.phenotype_annotation_object is required")
        if not isinstance(payload.get("phenotype_annotation_subject"), Mapping):
            errors.append(f"{location}.payload.phenotype_annotation_subject is required")
        terms = payload.get("phenotype_terms")
        if not isinstance(terms, list) or not terms or not isinstance(terms[0], Mapping):
            errors.append(f"{location}.payload.phenotype_terms[0] is required")

        ref_types = {ref.object_type for ref in obj.object_refs}
        missing_ref_types = {
            PHENOTYPE_SUBJECT_OBJECT_TYPE,
            PHENOTYPE_TERM_OBJECT_TYPE,
            PHENOTYPE_REFERENCE_OBJECT_TYPE,
            PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE,
        } - ref_types
        if missing_ref_types:
            errors.append(
                f"{location}.object_refs missing types: " + ", ".join(sorted(missing_ref_types))
            )

        if not obj.evidence_record_ids:
            errors.append(f"{location}.evidence_record_ids must not be empty")
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


class PhenotypeMaterializationResult:
    """Outcome from materializing staged phenotype builder candidates into envelope output.

    Structurally matches ``GeneExpressionMaterializationResult`` so it plugs into the generic
    ``finalize_builder_extraction`` orchestration without bespoke handling.
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


def _annotation_object_metadata(subject_resolution_state: str) -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: PHENOTYPE_ANNOTATION_OBJECT_ROLE,
        "association_kind": PHENOTYPE_ANNOTATION_KIND,
        "validation_state": subject_resolution_state,
        "export_behavior": _blocked_export_behavior(),
        "write_behavior": _blocked_write_behavior(),
        "materialized_by": PHENOTYPE_MATERIALIZER_ID,
        PROVIDER_REFS_METADATA_KEY: {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": PHENOTYPE_LINKML_SCHEMA_SOURCE_FILE,
                "class": "PhenotypeAnnotation",
            }
        },
    }


def materialize_phenotype_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    resolver_entry_lookup: Callable[[str], Any] | None = None,
    produced_by: str = "phenotype_extractor",
) -> PhenotypeMaterializationResult:
    """Build canonical PhenotypeResultEnvelope output from finalized builder state.

    One candidate -> one ``PhenotypeAnnotation`` curatable_unit plus pending ``PhenotypeSubject`` /
    ``PhenotypeTerm`` / ``Reference`` / ``EvidenceQuote`` objects, mirroring the existing envelope
    converter's object graph and blocked posture. ``metadata_refs`` are RELATIVE.
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
        statement = _clean_text(staged_fields.get("phenotype_annotation_object"))
        if statement is None:
            issues.append(
                _materialization_issue(
                    field_path="phenotype_annotation_object",
                    reason="missing_phenotype_statement",
                    message="Finalized phenotype candidates require a free-text phenotype statement.",
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
                    message="Finalized phenotype candidates require non-empty evidence_record_ids.",
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

        source_mentions = _unique_strings(staged_fields.get("source_mentions")) or [statement]
        negated = bool(staged_fields.get("negated"))
        condition_relations = _condition_relations_payload(staged_fields.get("condition_relations"))
        term_curie = _clean_text(staged_fields.get("term_curie"))
        term_label = _clean_text(staged_fields.get("term_label"))
        primary_evidence_id = _clean_text(resolved_evidence[0].get("evidence_record_id"))
        ontology_lookup_hint = _ontology_lookup_hint(staged_fields, primary_evidence_id)
        subject_payload = _subject_payload(staged_fields)
        subject_resolution_state = subject_payload["resolution_state"]

        subject_ref_id = f"phenotype-subject-{annotation_index + 1}"
        term_ref_id = f"phenotype-term-{annotation_index + 1}"
        reference_ref_id = f"phenotype-reference-{annotation_index + 1}"

        term_payload = _phenotype_term_payload(
            statement=statement,
            term_curie=term_curie,
            term_label=term_label,
            source_mentions=source_mentions,
            ontology_lookup_hint=ontology_lookup_hint,
        )
        reference_payload: dict[str, Any] = {}
        for field_name in ("reference_id", "title", "filename", "pmid", "doi", "curie"):
            value = _clean_text(staged_fields.get(field_name))
            if value is not None:
                reference_payload[field_name] = value

        # Pending PhenotypeSubject (validated_reference; routes to gene/allele/AGM validation).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=subject_ref_id,
                schema_ref=_phenotype_subject_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending subject reference; concrete Gene, Allele, or AGM subtype must be "
                    "resolved before export."
                ],
                payload=copy.deepcopy(subject_payload),
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": subject_resolution_state,
                    "validator_binding_id": PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
                },
            )
        )
        # Pending PhenotypeTerm (validated_reference; the active ontology validator resolves it).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=term_ref_id,
                schema_ref=_phenotype_term_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload=copy.deepcopy(term_payload),
                evidence_record_ids=[primary_evidence_id] if primary_evidence_id else [],
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": _TERM_PENDING_STATE,
                    "validator_binding_id": PHENOTYPE_TERM_VALIDATOR_BINDING_ID,
                    "export_state": _TERM_EXPORT_BLOCKED,
                    "write_blocked_reason": _TERM_WRITE_BLOCKED_REASON,
                },
            )
        )
        # Pending Reference (validated_reference; reference validator is under development).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_REFERENCE_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=reference_ref_id,
                schema_ref=_reference_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload=copy.deepcopy(reference_payload),
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": _REFERENCE_PENDING_STATE,
                    "validator_binding_id": PHENOTYPE_REFERENCE_VALIDATOR_BINDING_ID,
                },
            )
        )

        # EvidenceQuote metadata_only objects + annotation object_refs.
        annotation_object_refs: list[ObjectRef] = [
            ObjectRef(pending_ref_id=subject_ref_id, object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE),
            ObjectRef(pending_ref_id=term_ref_id, object_type=PHENOTYPE_TERM_OBJECT_TYPE),
            ObjectRef(pending_ref_id=reference_ref_id, object_type=PHENOTYPE_REFERENCE_OBJECT_TYPE),
        ]
        evidence_payload_refs: list[dict[str, str]] = []
        annotation_evidence_ids: list[str] = []
        for evidence_index, evidence_record in enumerate(resolved_evidence, start=1):
            evidence_id = _clean_text(evidence_record.get("evidence_record_id"))
            evidence_ref_id = f"evidence-quote-{annotation_index + 1}-{evidence_index}"
            quote_payload = _evidence_quote_payload(evidence_record)
            annotation_evidence_ids.append(evidence_id)
            evidence_payload_refs.append({"evidence_record_id": evidence_id})
            annotation_object_refs.append(
                ObjectRef(
                    pending_ref_id=evidence_ref_id,
                    object_type=PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE,
                )
            )
            curatable_objects.append(
                CuratableObjectEnvelope(
                    object_type=PHENOTYPE_EVIDENCE_QUOTE_OBJECT_TYPE,
                    object_role="metadata_only",
                    pending_ref_id=evidence_ref_id,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=quote_payload,
                    evidence_record_ids=[evidence_id] if evidence_id else [],
                    metadata={OBJECT_ROLE_METADATA_KEY: "metadata_only"},
                )
            )

        annotation_payload: dict[str, Any] = {
            "annotation_kind": PHENOTYPE_ANNOTATION_KIND,
            "phenotype_annotation_object": statement,
            "phenotype_annotation_subject": copy.deepcopy(subject_payload),
            "phenotype_terms": [copy.deepcopy(term_payload)],
            "single_reference": copy.deepcopy(reference_payload),
            "evidence_quote": evidence_payload_refs[0],
            "evidence_record_ids": annotation_evidence_ids,
            "source_mentions": list(source_mentions),
            "negated": negated,
        }
        # EXPERIMENTAL CONDITIONS: nested condition_relations[].conditions[]. Only carried when the
        # extractor staged them. Each condition references the annotation's evidence
        # (evidence_record_ids on the annotation) per the evidence contract — no condition-level
        # quote text is materialized. The active experimental_condition_validation binding fans out
        # one composite validation per condition_relations[i].conditions[j].
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
                "mention": source_mentions[0],
                "entity_type": "phenotype",
                "evidence_record_ids": annotation_evidence_ids,
            }
        )
        retained_evidence_ids.extend(annotation_evidence_ids)

        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_OBJECT_TYPE,
                object_role=PHENOTYPE_ANNOTATION_OBJECT_ROLE,
                pending_ref_id=annotation_ref,
                model_ref=PHENOTYPE_ANNOTATION_MODEL_ID,
                schema_ref=_phenotype_annotation_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending only; export is blocked until subject, reference, ontology, and "
                    "write targets are resolved.",
                    "Evidence and pending references are materialized by backend builder "
                    "finalization.",
                ],
                payload=annotation_payload,
                object_refs=annotation_object_refs,
                evidence_record_ids=annotation_evidence_ids,
                metadata_refs=metadata_refs,
                metadata=_annotation_object_metadata(subject_resolution_state),
            )
        )
        annotation_index += 1

    provenance = {
        "source": PHENOTYPE_MATERIALIZER_ID,
        "produced_by": produced_by,
        "builder_run_id": getattr(workspace, "run_id", None),
        "source_candidate_ids": list(normalized_candidate_ids),
    }
    output_payload = {
        "summary": "Finalized phenotype extraction from builder-staged assertions.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in curatable_objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence_records,
            "normalization_notes": [
                "Phenotype annotation envelope was assembled by backend materialization from "
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
        "schema_ref": _phenotype_annotation_schema_ref().model_dump(mode="json", exclude_none=True),
    }

    if annotation_index == 0 and not issues:
        issues.append(
            _materialization_issue(
                field_path="curatable_objects",
                reason="no_retained_candidates",
                message="Finalized phenotype extraction produced no retained PhenotypeAnnotation objects.",
            )
        )

    if not issues:
        try:
            output = PhenotypeBuilderExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_pydantic_issues(exc))
        else:
            output_payload = output.model_dump(mode="json", exclude_none=True)

    return PhenotypeMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_unique_strings(retained_evidence_ids)),
    )


__all__ = [
    "PHENOTYPE_DOMAIN_PACK_ID",
    "PHENOTYPE_DOMAIN_PACK_VERSION",
    "PhenotypeBuilderExtractionOutput",
    "PhenotypeMaterializationResult",
    "materialize_phenotype_builder_state",
    "validate_phenotype_builder_objects",
]
