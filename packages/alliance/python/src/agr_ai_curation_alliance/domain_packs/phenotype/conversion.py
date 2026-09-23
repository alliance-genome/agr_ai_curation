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
SAME blocked export/write metadata. No new ontology/provider pairs are activated.

EXTRACTED VS VALIDATED (ALL-1283): every term, subject and data provider is staged as one
resolvable value (``_resolvable_payloads.staged_value``): the paper wording in ``mention``,
anything the extractor proposed under ``proposed_<key>``, and the shared unresolved /
``not_validated`` state. Only a validator fills the id/label keys: the active
``phenotype_term_ontology_validator`` resolves each ``phenotype_terms[i]`` in place.

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
from src.schemas.evidence_workspace import normalize_workspace_records

from .._resolvable_payloads import (
    clean_text,
    condition_relations_payload,
    staged_value,
)
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
)

# Object-level workflow states (object metadata only). The values themselves carry the
# shared extracted-vs-validated state (``resolution_state`` / ``lookup_outcome``).
_SUBJECT_PENDING_STATE = "pending_entity_resolution"
_SUBJECT_BLOCKED_STATE = "blocked_missing_subject"
_SUBJECT_BLOCKED_NOTE = (
    "Phenotype extraction did not stage the subject the paper names; "
    "phenotype_annotation_subject is absent."
)
_TERM_PENDING_STATE = "pending_ontology_resolution"
_TERM_EXPORT_BLOCKED = "blocked_pending_ontology_resolution"
_TERM_WRITE_BLOCKED_REASON = "phenotype term CURIE unresolved"
_REFERENCE_PENDING_STATE = "pending_reference_resolution"

# A phenotype term's validated identity; everything the extractor proposed for it
# stays under proposed_curie / proposed_label.
PHENOTYPE_TERM_IDENTITY_KEYS = ("curie", "label")
PHENOTYPE_SUBJECT_IDENTITY_KEYS = ("subject_identifier", "subject_label")
DATA_PROVIDER_IDENTITY_KEYS = ("abbreviation",)


def normalize_phenotype_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Materialize standalone PhenotypeTerm support objects from nested terms."""

    objects = payload.get("curatable_objects")
    if not isinstance(objects, list):
        return payload

    existing_pending_ref_ids = {
        str(obj.get("pending_ref_id"))
        for obj in objects
        if isinstance(obj, Mapping)
        and isinstance(obj.get("pending_ref_id"), str)
        and str(obj.get("pending_ref_id")).strip()
    }
    existing_term_refs = _phenotype_term_refs_by_signature(objects)
    sanitized_objects: list[Any] = []
    synthesized_terms: list[dict[str, Any]] = []

    for object_index, obj in enumerate(objects, start=1):
        if not (
            isinstance(obj, Mapping)
            and obj.get("object_type") == PHENOTYPE_OBJECT_TYPE
        ):
            sanitized_objects.append(obj)
            continue

        annotation_payload = obj.get("payload")
        if not isinstance(annotation_payload, Mapping):
            sanitized_objects.append(obj)
            continue
        phenotype_terms = annotation_payload.get("phenotype_terms")
        if not isinstance(phenotype_terms, list):
            sanitized_objects.append(obj)
            continue

        object_refs = [
            ref
            for ref in (obj.get("object_refs") or [])
            if isinstance(ref, Mapping)
        ]
        updated_refs = list(object_refs)
        annotation_changed = False
        for term_index, raw_term in enumerate(phenotype_terms, start=1):
            if not isinstance(raw_term, Mapping):
                continue
            term_payload = _normalized_phenotype_term_payload(raw_term)
            if term_payload is None:
                continue
            term_signature = _phenotype_term_signature(
                term_payload,
                fallback_evidence_ids=_string_list(obj.get("evidence_record_ids")),
            )
            term_ref_id = existing_term_refs.get(term_signature)
            if term_ref_id is None:
                term_ref_id = _next_phenotype_term_ref_id(
                    existing_pending_ref_ids,
                    annotation_index=object_index,
                    term_index=term_index,
                )
                existing_pending_ref_ids.add(term_ref_id)
                existing_term_refs[term_signature] = term_ref_id
                synthesized_terms.append(
                    _phenotype_term_support_object(
                        term_ref_id,
                        term_payload,
                        fallback_evidence_ids=_string_list(
                            obj.get("evidence_record_ids")
                        ),
                    )
                )
            term_ref = {
                "pending_ref_id": term_ref_id,
                "object_type": PHENOTYPE_TERM_OBJECT_TYPE,
            }
            if term_ref not in updated_refs:
                updated_refs.append(term_ref)
                annotation_changed = True

        if annotation_changed:
            updated_obj = dict(obj)
            updated_obj["object_refs"] = updated_refs
            sanitized_objects.append(updated_obj)
        else:
            sanitized_objects.append(obj)

    if not synthesized_terms:
        return payload

    sanitized = dict(payload)
    sanitized["curatable_objects"] = [*sanitized_objects, *synthesized_terms]

    metadata = dict(sanitized.get("metadata") or {})
    notes = list(metadata.get("notes") or [])
    notes.append(
        "Phenotype adapter materialized standalone PhenotypeTerm support objects "
        "from nested PhenotypeAnnotation phenotype_terms[] so active ontology "
        "validators can run."
    )
    metadata["notes"] = notes
    sanitized["metadata"] = metadata

    run_summary = dict(sanitized.get("run_summary") or {})
    warnings = list(run_summary.get("warnings") or [])
    warnings.append(
        f"materialized_nested_phenotype_terms:{len(synthesized_terms)}"
    )
    run_summary["warnings"] = warnings
    sanitized["run_summary"] = run_summary
    return sanitized


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
    """Project workspace evidence records into canonical provenance.

    Shared with every other domain pack and the validator write-back; see
    src/schemas/models/evidence_workspace.py. This pack uses the baseline
    policy: skip discarded records, require a unique evidence_record_id, and
    drop anything that fails canonical validation.
    """
    return normalize_workspace_records(evidence_records)


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


def _subject_payload(staged_fields: Mapping[str, Any]) -> dict[str, Any] | None:
    """The staged subject value, or None when the extractor staged no subject.

    ``subject_label`` is the subject as the paper names it (the value's paper
    wording); a staged ``subject_identifier`` is the extractor's proposal and
    stays under ``proposed_subject_identifier`` until a validator resolves it.
    """

    mention = clean_text(staged_fields.get("subject_label"))
    if mention is None:
        return None
    extra: dict[str, Any] = {}
    subject_type = clean_text(staged_fields.get("subject_type"))
    if subject_type is not None:
        extra["subject_type"] = subject_type
    taxon = clean_text(staged_fields.get("subject_taxon"))
    if taxon is not None:
        extra["taxon"] = taxon
    return staged_value(
        mention,
        identity_keys=PHENOTYPE_SUBJECT_IDENTITY_KEYS,
        proposals={"subject_identifier": staged_fields.get("subject_identifier")},
        **extra,
    )


def _ontology_lookup_hint(
    staged_fields: Mapping[str, Any],
    primary_evidence_record_id: str | None,
) -> dict[str, str]:
    hint: dict[str, str] = {}
    data_provider = clean_text(staged_fields.get("data_provider"))
    taxon_id = clean_text(staged_fields.get("term_taxon_id"))
    if data_provider:
        hint["data_provider"] = data_provider
    if taxon_id:
        hint["taxon_id"] = taxon_id
    if primary_evidence_record_id:
        hint["evidence_record_id"] = primary_evidence_record_id
    return hint


def _phenotype_term_payload(
    *,
    term_mention: str,
    term_curie: str | None,
    term_label: str | None,
    source_mentions: Sequence[str],
    ontology_lookup_hint: Mapping[str, str],
) -> dict[str, Any]:
    """The staged phenotype term: paper wording, extractor proposals, no validated identity."""

    return staged_value(
        term_mention,
        identity_keys=PHENOTYPE_TERM_IDENTITY_KEYS,
        proposals={"curie": term_curie, "label": term_label},
        source_mentions=list(source_mentions),
        ontology_lookup_hint=dict(ontology_lookup_hint),
    )


def _normalized_phenotype_term_payload(
    raw_term: Mapping[str, Any],
) -> dict[str, Any] | None:
    """A nested phenotype term as a support-object payload, or None without paper wording.

    Only a term that carries its paper wording (``mention``) is a phenotype term
    value; its stored state is kept exactly as written.
    """

    if clean_text(raw_term.get("mention")) is None:
        return None
    return dict(raw_term)


def _phenotype_term_support_object(
    pending_ref_id: str,
    term_payload: Mapping[str, Any],
    *,
    fallback_evidence_ids: Sequence[str],
) -> dict[str, Any]:
    evidence_record_ids = _phenotype_term_evidence_record_ids(
        term_payload,
        fallback_evidence_ids=fallback_evidence_ids,
    )
    return {
        "object_type": PHENOTYPE_TERM_OBJECT_TYPE,
        "object_role": "validated_reference",
        "pending_ref_id": pending_ref_id,
        "model_ref": "PhenotypeTermPayload",
        "status": "pending",
        "definition_state": "in_development",
        "definition_notes": [
            "Materialized from nested PhenotypeAnnotation.phenotype_terms[] as a "
            "structural copy; the ontology validator resolves the annotation's own terms."
        ],
        "payload": dict(term_payload),
        "evidence_record_ids": evidence_record_ids,
        "metadata": _term_support_metadata(),
    }


def _term_support_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "validation_state": _TERM_PENDING_STATE,
        "export_state": _TERM_EXPORT_BLOCKED,
        "write_blocked_reason": _TERM_WRITE_BLOCKED_REASON,
    }


def _phenotype_term_refs_by_signature(
    objects: Sequence[Any],
) -> dict[tuple[Any, ...], str]:
    refs: dict[tuple[Any, ...], str] = {}
    for obj in objects:
        if not (
            isinstance(obj, Mapping)
            and obj.get("object_type") == PHENOTYPE_TERM_OBJECT_TYPE
            and isinstance(obj.get("pending_ref_id"), str)
        ):
            continue
        payload = obj.get("payload")
        if not isinstance(payload, Mapping):
            continue
        term_payload = _normalized_phenotype_term_payload(payload)
        if term_payload is None:
            continue
        refs.setdefault(
            _phenotype_term_signature(
                term_payload,
                fallback_evidence_ids=_string_list(obj.get("evidence_record_ids")),
            ),
            str(obj["pending_ref_id"]),
        )
    return refs


def _phenotype_term_signature(
    term_payload: Mapping[str, Any],
    *,
    fallback_evidence_ids: Sequence[str],
) -> tuple[Any, ...]:
    hint = term_payload.get("ontology_lookup_hint")
    hint = hint if isinstance(hint, Mapping) else {}
    return (
        _clean_text(term_payload.get("mention")),
        _clean_text(term_payload.get("proposed_curie")),
        _clean_text(hint.get("data_provider")),
        _clean_text(hint.get("taxon_id")),
        tuple(
            _phenotype_term_evidence_record_ids(
                term_payload,
                fallback_evidence_ids=fallback_evidence_ids,
            )
        ),
    )


def _phenotype_term_evidence_record_ids(
    term_payload: Mapping[str, Any],
    *,
    fallback_evidence_ids: Sequence[str],
) -> list[str]:
    hint = term_payload.get("ontology_lookup_hint")
    hint = hint if isinstance(hint, Mapping) else {}
    hint_evidence_id = _clean_text(hint.get("evidence_record_id"))
    if hint_evidence_id is not None:
        return [hint_evidence_id]
    return list(fallback_evidence_ids)


def _next_phenotype_term_ref_id(
    existing_pending_ref_ids: set[str],
    *,
    annotation_index: int,
    term_index: int,
) -> str:
    base = f"phenotype-term-{annotation_index}-{term_index}"
    if base not in existing_pending_ref_ids:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_pending_ref_ids:
        suffix += 1
    return f"{base}-{suffix}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text is not None:
            result.append(text)
    return result


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
        subject = payload.get("phenotype_annotation_subject")
        if subject is not None and not isinstance(subject, Mapping):
            errors.append(f"{location}.payload.phenotype_annotation_subject must be an object")
        terms = payload.get("phenotype_terms")
        if (
            not isinstance(terms, list)
            or not terms
            or not isinstance(terms[0], Mapping)
            or not _clean_text(terms[0].get("mention"))
        ):
            errors.append(f"{location}.payload.phenotype_terms[0].mention is required")

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

        rationale = _clean_text(staged_fields.get("rationale"))
        if rationale is None:
            issues.append(
                _materialization_issue(
                    field_path="rationale",
                    reason="missing_rationale",
                    message=(
                        "Finalized phenotype candidates require a rationale; "
                        "patch the candidate with a rationale saying why you selected it."
                    ),
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue

        # The builder workspace owns a candidate's evidence ids; no staged field stands in.
        evidence_ids = _unique_strings(getattr(candidate, "evidence_record_ids", None))
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

        source_mentions = _unique_strings(staged_fields.get("source_mentions"))
        if not source_mentions:
            issues.append(
                _materialization_issue(
                    field_path="source_mentions",
                    reason="missing_source_mentions",
                    message="Finalized phenotype candidates require source_mentions from the paper.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue
        term_mention = _clean_text(staged_fields.get("term_mention"))
        if term_mention is None:
            issues.append(
                _materialization_issue(
                    field_path="term_mention",
                    reason="missing_term_mention",
                    message=(
                        "Finalized phenotype candidates require term_mention, the phenotype "
                        "term as the paper words it."
                    ),
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue
        negated = bool(staged_fields.get("negated"))
        condition_relations = condition_relations_payload(staged_fields.get("condition_relations"))
        primary_evidence_id = _clean_text(resolved_evidence[0].get("evidence_record_id"))
        ontology_lookup_hint = _ontology_lookup_hint(staged_fields, primary_evidence_id)
        subject_payload = _subject_payload(staged_fields)
        subject_resolution_state = (
            _SUBJECT_BLOCKED_STATE if subject_payload is None else _SUBJECT_PENDING_STATE
        )

        subject_ref_id = f"phenotype-subject-{annotation_index + 1}"
        term_ref_id = f"phenotype-term-{annotation_index + 1}"
        reference_ref_id = f"phenotype-reference-{annotation_index + 1}"

        term_payload = _phenotype_term_payload(
            term_mention=term_mention,
            term_curie=_clean_text(staged_fields.get("term_curie")),
            term_label=_clean_text(staged_fields.get("term_label")),
            source_mentions=source_mentions,
            ontology_lookup_hint=ontology_lookup_hint,
        )

        # Pending PhenotypeSubject (validated_reference; routes to gene/allele/AGM validation).
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_SUBJECT_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=subject_ref_id,
                validation_guidance=staged_fields.get("validation_guidance"),
                schema_ref=_phenotype_subject_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending subject reference; concrete Gene, Allele, or AGM subtype must be "
                    "resolved before export."
                ],
                payload=(
                    copy.deepcopy(subject_payload)
                    if subject_payload is not None
                    else {"resolution_note": _SUBJECT_BLOCKED_NOTE}
                ),
                metadata={
                    OBJECT_ROLE_METADATA_KEY: "validated_reference",
                    "validation_state": subject_resolution_state,
                    "validator_binding_id": PHENOTYPE_SUBJECT_VALIDATOR_BINDING_ID,
                },
            )
        )
        # PhenotypeTerm support object: a structural copy of the staged term. The active
        # ontology validator resolves the annotation's own phenotype_terms[i] in place.
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=PHENOTYPE_TERM_OBJECT_TYPE,
                object_role="validated_reference",
                pending_ref_id=term_ref_id,
                validation_guidance=staged_fields.get("validation_guidance"),
                schema_ref=_phenotype_term_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload=copy.deepcopy(term_payload),
                evidence_record_ids=[primary_evidence_id] if primary_evidence_id else [],
                metadata=_term_support_metadata(),
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
                payload={},
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
            if evidence_id is None:
                raise ValueError(
                    "Resolved phenotype evidence records must include evidence_record_id."
                )
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

        # single_reference stays absent: the extractor stages no reference wording, and the
        # source paper is resolved downstream from the workspace document identity.
        annotation_payload: dict[str, Any] = {
            "annotation_kind": PHENOTYPE_ANNOTATION_KIND,
            "phenotype_annotation_object": statement,
            "phenotype_terms": [copy.deepcopy(term_payload)],
            "evidence_quote": evidence_payload_refs[0],
            "evidence_record_ids": annotation_evidence_ids,
            "source_mentions": list(source_mentions),
            "rationale": rationale,
            "negated": negated,
        }
        if subject_payload is not None:
            annotation_payload["phenotype_annotation_subject"] = copy.deepcopy(subject_payload)
        data_provider = _clean_text(staged_fields.get("data_provider"))
        if data_provider is not None:
            annotation_payload["data_provider"] = staged_value(
                data_provider, identity_keys=DATA_PROVIDER_IDENTITY_KEYS
            )
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
                validation_guidance=staged_fields.get("validation_guidance"),
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
    "normalize_phenotype_extraction_payload",
    "validate_phenotype_builder_objects",
]
