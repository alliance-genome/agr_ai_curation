"""Materialize generic builder candidates into canonical extraction envelopes."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    SchemaRef,
)
from src.schemas.models.base import EvidenceRecord
from src.schemas.evidence_workspace import normalize_workspace_records
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

from .attributes import normalize_generic_attributes
from .catalog import GenericClassCatalogEntry, load_generic_class_catalog
from .constants import GENERIC_MATERIALIZER_ID
from .values import EVIDENCE_SOURCE_FIELDS, extractor_payload_issues, unresolved_payload


class GenericBuilderExtractionOutput(DomainEnvelopeExtractionResult):
    """Validated generic builder extraction output."""


class GenericMaterializationResult:
    """Outcome from materializing staged generic builder candidates."""

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


def materialize_generic_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    produced_by: str = "pdf_extraction",
) -> GenericMaterializationResult:
    """Build canonical generic DomainEnvelopeExtractionResult output from builder state."""

    profile = getattr(workspace, "generic_profile", None)
    if profile is not None:
        produced_by = workspace.agent_id
        if not produced_by:
            raise ValueError("Profile-bound materialization requires canonical agent identity")
    catalog = load_generic_class_catalog()
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
                _issue(
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
    curatable_objects: list[CuratableObjectEnvelope] = []
    raw_mentions: list[dict[str, Any]] = []
    retained_evidence_ids: list[str] = []
    object_definitions_by_type = {
        object_definition.object_type: object_definition
        for object_definition in catalog.generated_domain_pack.metadata.object_definitions
    }

    for index, candidate in enumerate(candidates, start=1):
        staged_fields = copy.deepcopy(dict(getattr(candidate, "staged_fields", {}) or {}))
        candidate_id = str(getattr(candidate, "candidate_id", "") or "")
        if profile is not None:
            profile_issues = profile.validate_candidate(staged_fields, candidate_id=candidate_id)
            if profile_issues:
                issues.extend(profile_issues)
                continue
        class_key = _clean_text(staged_fields.get("class_key"))
        label = _clean_text(staged_fields.get("label"))
        if class_key is None:
            issues.append(
                _issue(
                    field_path="class_key",
                    reason="missing_class_key",
                    message="Generic candidates require an explicit class_key.",
                    candidate_id=candidate_id,
                )
            )
            continue
        try:
            entry = catalog.require_stageable(class_key)
        except (KeyError, ValueError) as exc:
            issues.append(
                _issue(
                    field_path="class_key",
                    reason="invalid_class_key",
                    message=str(exc),
                    candidate_id=candidate_id,
                    class_key=class_key,
                )
            )
            continue
        if label is None:
            issues.append(
                _issue(
                    field_path="label",
                    reason="missing_label",
                    message="Generic candidates require a non-empty label.",
                    candidate_id=candidate_id,
                    class_key=class_key,
                )
            )
            continue
        if _clean_text(staged_fields.get("rationale")) is None:
            issues.append(
                _issue(
                    field_path="rationale",
                    reason="missing_rationale",
                    message=(
                        "Finalized generic candidates require a rationale; "
                        "patch the candidate with a rationale saying why you selected it."
                    ),
                    candidate_id=candidate_id,
                    class_key=class_key,
                )
            )
            continue

        evidence_ids = _unique_strings(
            getattr(candidate, "evidence_record_ids", None)
            or staged_fields.get("evidence_record_ids")
        )
        if not evidence_ids:
            issues.append(
                _issue(
                    field_path="evidence_record_ids",
                    reason="missing_evidence_record_ids",
                    message="Finalized generic candidates require evidence_record_ids.",
                    candidate_id=candidate_id,
                    class_key=class_key,
                )
            )
            continue
        unknown_evidence = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in evidence_records_by_id
        ]
        if unknown_evidence:
            issues.append(
                _issue(
                    field_path="evidence_record_ids",
                    reason="unknown_evidence_record_id",
                    message=(
                        "evidence_record_ids must reference verified active-run "
                        "metadata.evidence_records entries."
                    ),
                    candidate_id=candidate_id,
                    class_key=class_key,
                    evidence_record_ids=unknown_evidence,
                )
            )
            continue

        raw_payload_keys = _raw_payload_keys(staged_fields)
        allowed_payload_fields = set(entry.payload_fields)
        unknown_payload_keys = sorted(raw_payload_keys - allowed_payload_fields)
        if unknown_payload_keys:
            for payload_key in unknown_payload_keys:
                issues.append(
                    _issue(
                        field_path=f"payload.{payload_key}",
                        reason="unknown_payload_field",
                        message=(
                            "Generic payload fields must be declared by the selected "
                            "class. Put extra freeform details under attributes."
                        ),
                        candidate_id=candidate_id,
                        class_key=class_key,
                    )
                )
            continue

        raw_payload = staged_fields.get("payload")
        payload_issues = extractor_payload_issues(
            raw_payload if isinstance(raw_payload, Mapping) else {},
            resolvable_fields=entry.resolvable_fields,
            validator_owned_fields=entry.validator_owned_fields,
            payload_fields=entry.payload_fields,
        )
        if payload_issues:
            for issue in payload_issues:
                issues.append(
                    _issue(
                        field_path=issue["field_path"],
                        reason=issue["reason"],
                        message=issue["message"],
                        candidate_id=candidate_id,
                        class_key=class_key,
                    )
                )
            continue

        raw_attributes = staged_fields.get("attributes")
        if raw_attributes not in (None, "", []) and not isinstance(raw_attributes, Mapping):
            issues.append(
                _issue(
                    field_path="attributes",
                    reason="invalid_attributes",
                    message="Generic attributes must be an object of keyed values.",
                    candidate_id=candidate_id,
                    class_key=class_key,
                )
            )
            continue
        if profile is not None:
            # Staged paper wording enters validation as unresolved, not yet validated.
            normalized_attributes, attribute_issues = profile.unresolved_attributes(raw_attributes or {}), []
        else:
            normalized_attributes, attribute_issues = normalize_generic_attributes(
                raw_attributes if isinstance(raw_attributes, Mapping) else {}
            )
        if attribute_issues:
            for issue in attribute_issues:
                issues.append(
                    _issue(
                        field_path=str(issue.get("field_path") or "attributes"),
                        reason=str(issue.get("reason") or "invalid_attribute"),
                        message=str(issue.get("message") or "Invalid generic attribute."),
                        candidate_id=candidate_id,
                        class_key=class_key,
                    )
                )
            continue
        if normalized_attributes:
            staged_fields["attributes"] = normalized_attributes
        else:
            staged_fields.pop("attributes", None)

        first_evidence_record = evidence_records_by_id.get(evidence_ids[0])
        payload = _payload_for_entry(
            staged_fields,
            entry=entry,
            label=label,
            first_evidence_record=first_evidence_record,
        )
        final_payload_keys = set(payload)
        undeclared_final_payload_keys = sorted(final_payload_keys - allowed_payload_fields)
        if undeclared_final_payload_keys:
            for payload_key in undeclared_final_payload_keys:
                issues.append(
                    _issue(
                        field_path=f"payload.{payload_key}",
                        reason="undeclared_materialized_payload_field",
                        message=(
                            "Materialized generic payload fields must be declared "
                            "by the selected class."
                        ),
                        candidate_id=candidate_id,
                        class_key=class_key,
                    )
                )
            continue
        missing_required_fields = _missing_required_payload_fields(
            payload,
            required_payload_fields=entry.required_payload_fields,
        )
        if missing_required_fields:
            for field_path in missing_required_fields:
                issues.append(
                    _issue(
                        field_path=f"payload.{field_path}",
                        reason="missing_required_payload_field",
                        message=(
                            "Generic payload is missing a required field declared "
                            "by the selected class."
                        ),
                        candidate_id=candidate_id,
                        class_key=class_key,
                    )
                )
            continue
        pending_ref_id = _pending_ref_id(candidate, staged_fields, index)
        paper_wording = _paper_wording(staged_fields, payload, entry=entry)
        metadata_refs = (
            [{"metadata_path": f"raw_mentions[{len(raw_mentions)}]", "role": "source_mention"}]
            if paper_wording is not None
            else []
        )
        for evidence_id in evidence_ids:
            evidence_position = next(
                (
                    position
                    for position, record in enumerate(normalized_evidence_records)
                    if record.get("evidence_record_id") == evidence_id
                ),
                None,
            )
            if evidence_position is not None:
                metadata_refs.append(
                    {
                        "metadata_path": f"evidence_records[{evidence_position}]",
                        "role": "verified_evidence",
                    }
                )
        if paper_wording is not None:
            raw_mentions.append(
                {
                    "mention": paper_wording,
                    "entity_type": entry.display_name,
                    "evidence_record_ids": list(evidence_ids),
                }
            )
        retained_evidence_ids.extend(evidence_ids)
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=entry.generic_object_type,
                object_role=_object_role(entry),
                pending_ref_id=pending_ref_id,
                validation_guidance=staged_fields.get("validation_guidance"),
                model_ref=_model_ref(entry),
                schema_ref=_schema_ref_for_entry(
                    entry,
                    object_definitions_by_type=object_definitions_by_type,
                ),
                definition_state=DefinitionState(entry.definition_state),
                definition_notes=list(entry.notes),
                payload=payload,
                evidence_record_ids=list(evidence_ids),
                metadata_refs=metadata_refs,
                metadata={
                    **({"generic_profile_ref": profile.receipt} if profile is not None else {}),
                    "generic_extraction": {
                        "class_key": entry.class_key,
                        "label": label,
                        "source_domain_pack_id": entry.source_domain_pack_id,
                        "source_object_type": entry.source_object_type,
                        "class_display_name": entry.display_name,
                        "source_is_generic_native": entry.source_is_generic_native,
                    }
                },
            )
        )

    provenance = {
        **({"execution_receipt": copy.deepcopy(workspace.execution_receipt)}
           if getattr(workspace, "execution_receipt", None) is not None else {}),
        **({"generic_profile_ref": profile.receipt, "profile_conformance": "passed"}
           if profile is not None and not issues else {}),
        "source": GENERIC_MATERIALIZER_ID,
        "produced_by": produced_by,
        "builder_run_id": getattr(workspace, "run_id", None),
        "source_candidate_ids": list(normalized_candidate_ids),
    }
    output_payload = {
        "summary": "Finalized generic extraction from builder-staged objects.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in curatable_objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence_records,
            "normalization_notes": [
                "Generic extraction was assembled by backend materialization from builder state."
            ],
            "exclusions": [],
            "ambiguities": [],
            "notes": [],
            "provenance": provenance,
        },
        "run_summary": {
            "candidate_count": len(normalized_candidate_ids),
            "kept_count": len(curatable_objects),
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
        "schema_ref": _generic_schema_ref().model_dump(mode="json", exclude_none=True),
    }
    if not issues:
        try:
            output = GenericBuilderExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_pydantic_issues(exc))
        else:
            output_payload = output.model_dump(mode="json", exclude_none=True)

    return GenericMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_unique_strings(retained_evidence_ids)),
    )


def _payload_for_entry(
    staged_fields: Mapping[str, Any],
    *,
    entry: GenericClassCatalogEntry,
    label: str,
    first_evidence_record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw_payload = staged_fields.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    payload_fields = set(entry.payload_fields)
    _set_declared_payload_value(
        payload,
        "label",
        label,
        payload_fields=payload_fields,
    )
    _set_declared_payload_value(
        payload,
        "class_key",
        entry.class_key,
        payload_fields=payload_fields,
    )
    for key in (
        "source_label",
        "description",
        "confidence",
        "classification_notes",
        "rationale",
        "semantic_class",
    ):
        value = staged_fields.get(key)
        if value not in (None, "", []) and key in payload_fields:
            payload[key] = value
    if "semantic_class" in payload_fields:
        payload.setdefault("semantic_class", entry.source_object_type)
    attributes = staged_fields.get("attributes")
    if isinstance(attributes, Mapping) and attributes and "attributes" in payload_fields:
        payload.setdefault("attributes", dict(attributes))
    _copy_evidence_source_fields(
        payload,
        payload_fields=payload_fields,
        first_evidence_record=first_evidence_record,
    )
    # Staged paper wording enters validation as unresolved, not yet validated.
    return unresolved_payload(payload, resolvable_fields=entry.resolvable_fields)


def missing_staged_payload_fields(
    staged_fields: Mapping[str, Any],
    *,
    entry: GenericClassCatalogEntry,
) -> list[str]:
    """Required class fields a staged candidate lacks, checked when it is staged.

    Evidence location fields are left out: they come from the verified
    evidence record when the extraction is finalized.
    """

    payload = _payload_for_entry(
        staged_fields,
        entry=entry,
        label=_clean_text(staged_fields.get("label")) or "",
        first_evidence_record=None,
    )
    return [
        field_path
        for field_path in _missing_required_payload_fields(
            payload, required_payload_fields=entry.required_payload_fields
        )
        if field_path not in EVIDENCE_SOURCE_FIELDS
    ]


def _set_declared_payload_value(
    payload: dict[str, Any],
    key: str,
    value: Any,
    *,
    payload_fields: set[str],
) -> None:
    if key in payload_fields and value not in (None, "", []):
        payload[key] = value


def _copy_evidence_source_fields(
    payload: dict[str, Any],
    *,
    payload_fields: set[str],
    first_evidence_record: Mapping[str, Any] | None,
) -> None:
    """Evidence location fields a class declares come only from its verified evidence record."""

    if not first_evidence_record:
        return
    for payload_field, evidence_field in EVIDENCE_SOURCE_FIELDS.items():
        if payload_field not in payload_fields:
            continue
        value = first_evidence_record.get(evidence_field)
        if value not in (None, "", []):
            payload[payload_field] = value


def _paper_wording(
    staged_fields: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    entry: GenericClassCatalogEntry,
) -> str | None:
    """The object's own paper wording: the class's mention field when it declares one, else source_label."""

    value = payload.get("mention") if "mention" in entry.payload_fields else staged_fields.get("source_label")
    return _clean_text(value)


def _raw_payload_keys(staged_fields: Mapping[str, Any]) -> set[str]:
    raw_payload = staged_fields.get("payload")
    if not isinstance(raw_payload, Mapping):
        return set()
    return {str(key) for key in raw_payload}


def _missing_required_payload_fields(
    payload: Mapping[str, Any],
    *,
    required_payload_fields: Sequence[str],
) -> list[str]:
    return [
        field_path
        for field_path in required_payload_fields
        if _missing_payload_value(_payload_path_value(payload, field_path))
    ]


def _payload_path_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in str(field_path).split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _missing_payload_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


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


def _pending_ref_id(candidate: Any, staged_fields: Mapping[str, Any], index: int) -> str:
    pending_ref_id = _clean_text(staged_fields.get("pending_ref_id"))
    if pending_ref_id:
        return pending_ref_id
    pending_ref_ids = getattr(candidate, "pending_ref_ids", None) or []
    if pending_ref_ids:
        pending_ref_id = _clean_text(pending_ref_ids[0])
        if pending_ref_id:
            return pending_ref_id
    return f"generic-object-{index}"


def _object_role(entry: GenericClassCatalogEntry) -> str | None:
    if entry.source_is_generic_native:
        return entry.source_object_type
    return "generic_proxy_object"


def _model_ref(entry: GenericClassCatalogEntry) -> str | None:
    return None if not entry.source_is_generic_native else {
        "generic_object": "GenericExtractedObjectPayload",
        "generic_claim": "GenericClaimPayload",
        "generic_reagent_candidate": "GenericReagentCandidatePayload",
    }.get(entry.source_object_type)


def _generic_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id="generic.domain_pack",
        provider="agr_ai_curation",
        name="Generic extraction domain pack",
        version="0.1.0",
    )


def _schema_ref_for_entry(
    entry: GenericClassCatalogEntry,
    *,
    object_definitions_by_type: Mapping[str, Any],
) -> SchemaRef:
    object_definition = object_definitions_by_type.get(entry.generic_object_type)
    schema_ref = getattr(object_definition, "schema_ref", None)
    if isinstance(schema_ref, SchemaRef):
        return schema_ref
    if schema_ref is not None:
        return SchemaRef.model_validate(schema_ref)
    return _generic_schema_ref()


def _issue(
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
        _issue(
            field_path=".".join(str(part) for part in error.get("loc", ())),
            reason=str(error.get("type") or "invalid"),
            message=str(error.get("msg") or "Invalid materialized generic envelope"),
        )
        for error in exc.errors()
    ]


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


__all__ = [
    "GenericBuilderExtractionOutput",
    "GenericMaterializationResult",
    "materialize_generic_builder_state",
    "missing_staged_payload_fields",
]
