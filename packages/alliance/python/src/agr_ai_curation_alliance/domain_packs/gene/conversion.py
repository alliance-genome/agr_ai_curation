"""Convert tool-verified gene extraction fixtures into domain envelopes."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from src.lib.openai_agents.models import (
    GeneExtractionResultEnvelope as RuntimeGeneExtractionResultEnvelope,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.models.base import EvidenceRecord

from ..schema_refs import (
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
)
from .constants import (
    GENE_DOMAIN_PACK_CONVERTER_ID,
    GENE_DOMAIN_PACK_ID,
    GENE_DOMAIN_PACK_VERSION,
    GENE_LINKML_SCHEMA_ID,
    GENE_LINKML_SCHEMA_NAME,
    GENE_LINKML_SCHEMA_URI,
    GENE_MATERIALIZER_ID,
    GENE_MENTION_EVIDENCE_DEFINITION_NOTES,
    GENE_MENTION_EVIDENCE_MODEL_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    GENE_OBJECT_ROLE,
    GENE_REFERENCE_TOOL_METHOD,
    GENE_REFERENCE_TOOL_NAME,
    GENE_REFERENCE_VALIDATOR_BINDING_ID,
)
from .export import GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY


_GENE_SOURCE_FILE = "model/schema/gene.yaml"
_CORE_SOURCE_FILE = "model/schema/core.yaml"

# Scalar gene-identity hint fields the builder stages for the validator handoff. These are
# evidence-backed PROPOSALS; the active gene validator binding owns final primary_external_id /
# gene_symbol / taxon. Evidence locator fields (verified_quote, page, ...) are copied from the
# verified evidence record, never authored by the model.
_GENE_IDENTITY_HINT_FIELDS = (
    "mention",
    "species",
    "taxon_hint",
    "data_provider_hint",
    "proposed_primary_external_id",
    "proposed_gene_symbol",
    "proposed_taxon",
    "confidence",
)
_GENE_EVIDENCE_LOCATOR_FIELDS = (
    "verified_quote",
    "page",
    "section",
    "subsection",
    "chunk_id",
    "figure_reference",
)


def _strip_required_string(value: object, field_name: str) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _strip_optional_string(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None


class ToolVerifiedGeneEvidenceRecord(BaseModel):
    """One quote verified by the document evidence tool."""

    model_config = ConfigDict(extra="forbid")

    evidence_record_id: StrictStr
    entity: StrictStr | None = None
    verified_quote: StrictStr
    page: int = Field(ge=1)
    section: StrictStr
    chunk_id: StrictStr
    subsection: StrictStr | None = None
    figure_reference: StrictStr | None = None

    @field_validator(
        "evidence_record_id",
        "verified_quote",
        "section",
        "chunk_id",
        mode="before",
    )
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("entity", "subsection", "figure_reference", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)


class ToolVerifiedGeneMention(BaseModel):
    """One normalized gene mention retained by the extractor."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    primary_external_id: StrictStr
    gene_symbol: StrictStr
    taxon: StrictStr
    species: StrictStr | None = None
    confidence: Literal["high", "medium", "low"]
    evidence_record_ids: list[StrictStr] = Field(min_length=1)
    identity_resolution_notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("mention", "primary_external_id", "gene_symbol", "taxon", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("species", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: object) -> object:
        return _strip_optional_string(value)

    @field_validator("evidence_record_ids")
    @classmethod
    def _validate_evidence_ids(cls, value: list[StrictStr]) -> list[StrictStr]:
        normalized: list[str] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for raw_item in value:
            item = str(raw_item).strip()
            if not item:
                raise ValueError("evidence_record_ids must not contain empty values")
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
            normalized.append(item)
        if duplicates:
            raise ValueError(
                "evidence_record_ids contains duplicate entries: "
                + ", ".join(sorted(duplicates))
            )
        return normalized

    @field_validator("identity_resolution_notes")
    @classmethod
    def _validate_identity_resolution_notes(
        cls,
        value: list[StrictStr],
    ) -> list[StrictStr]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError(
                    "identity_resolution_notes must not contain empty values"
                )
            normalized_notes.append(normalized)
        return normalized_notes


class ToolVerifiedGeneOutput(BaseModel):
    """Canonical fixture input produced after gene lookup and evidence verification."""

    model_config = ConfigDict(extra="forbid")

    envelope_id: StrictStr
    document_id: StrictStr
    produced_by: StrictStr
    produced_at: datetime
    gene_mentions: list[ToolVerifiedGeneMention] = Field(min_length=1)
    evidence_records: list[ToolVerifiedGeneEvidenceRecord] = Field(min_length=1)
    normalization_notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("envelope_id", "document_id", "produced_by", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: object, info) -> object:
        return _strip_required_string(value, info.field_name)

    @field_validator("normalization_notes")
    @classmethod
    def _validate_normalization_notes(cls, value: list[StrictStr]) -> list[StrictStr]:
        normalized_notes: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                raise ValueError("normalization_notes must not contain empty values")
            normalized_notes.append(normalized)
        return normalized_notes

    @model_validator(mode="after")
    def _validate_evidence_links(self) -> "ToolVerifiedGeneOutput":
        evidence_ids = [item.evidence_record_id for item in self.evidence_records]
        duplicate_ids = sorted(
            {
                evidence_id
                for evidence_id in evidence_ids
                if evidence_ids.count(evidence_id) > 1
            }
        )
        if duplicate_ids:
            raise ValueError(
                "evidence_records contains duplicate evidence_record_id entries: "
                + ", ".join(duplicate_ids)
            )

        evidence_id_set = set(evidence_ids)
        missing_links = sorted(
            {
                evidence_id
                for gene in self.gene_mentions
                for evidence_id in gene.evidence_record_ids
                if evidence_id not in evidence_id_set
            }
        )
        if missing_links:
            raise ValueError(
                "gene_mentions references unknown evidence_record_ids: "
                + ", ".join(missing_links)
            )
        return self


def _gene_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=GENE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name=GENE_LINKML_SCHEMA_NAME,
        version=ALLIANCE_LINKML_COMMIT,
        uri=GENE_LINKML_SCHEMA_URI,
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": _GENE_SOURCE_FILE,
                    "class": "Gene",
                }
            }
        },
    )


def _object_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "evidence_role": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
        "blocking_validation": False,
        "export_behavior": {
            "status": "ready",
            "mode": "validated_reference_evidence",
            "target_key": GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
            "exportable": True,
            "mutates_base_gene": False,
            "creates_paper_gene_association": False,
        },
        "write_behavior": "envelope_only",
        "provider_refs": {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": _GENE_SOURCE_FILE,
                "class": "Gene",
            }
        },
    }


def _payload_for_gene_evidence(
    gene: ToolVerifiedGeneMention,
    evidence: ToolVerifiedGeneEvidenceRecord,
    *,
    normalization_notes: Sequence[str] = (),
) -> dict[str, Any]:
    identity_resolution_notes = (
        list(gene.identity_resolution_notes)
        or [str(note).strip() for note in normalization_notes if str(note).strip()]
    )
    payload: dict[str, Any] = {
        "mention": gene.mention,
        "primary_external_id": gene.primary_external_id,
        "gene_symbol": gene.gene_symbol,
        "taxon": gene.taxon,
        "confidence": gene.confidence,
        "evidence_record_id": evidence.evidence_record_id,
        "verified_quote": evidence.verified_quote,
        "page": evidence.page,
        "section": evidence.section,
        "chunk_id": evidence.chunk_id,
    }
    if identity_resolution_notes:
        payload["identity_resolution_notes"] = identity_resolution_notes
    if gene.species is not None:
        payload["species"] = gene.species
    if evidence.subsection is not None:
        payload["subsection"] = evidence.subsection
    if evidence.figure_reference is not None:
        payload["figure_reference"] = evidence.figure_reference
    return payload


def _validation_finding(pending_ref_id: str) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationFindingSeverity.INFO,
        status=ValidationFindingStatus.RESOLVED,
        code="alliance.gene_reference.tool_verified",
        message=f"Gene reference resolved by {GENE_REFERENCE_TOOL_NAME} before envelope conversion.",
        field_ref=FieldRef(
            object_ref=ObjectRef(
                pending_ref_id=pending_ref_id,
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            ),
            field_path="primary_external_id",
        ),
        details={
            "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
            "source_tool": GENE_REFERENCE_TOOL_NAME,
            "source_method": GENE_REFERENCE_TOOL_METHOD,
            "blocking": False,
            "grounded_slots": {
                "primary_external_id": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "primary_external_id",
                    "range": "string",
                },
                "gene_symbol": {
                    "source_file": _GENE_SOURCE_FILE,
                    "slot": "gene_symbol",
                    "range": "GeneSymbolSlotAnnotation",
                },
                "taxon": {
                    "source_file": _CORE_SOURCE_FILE,
                    "slot": "taxon",
                    "range": "NCBITaxonTerm",
                },
            },
        },
    )


def tool_verified_gene_output_to_pending_envelope(
    payload: Mapping[str, Any] | ToolVerifiedGeneOutput,
) -> DomainEnvelope:
    """Build a pending-ref envelope from canonical tool-verified gene output."""

    source = (
        payload
        if isinstance(payload, ToolVerifiedGeneOutput)
        else ToolVerifiedGeneOutput.model_validate(payload)
    )
    evidence_by_id = {
        evidence.evidence_record_id: evidence
        for evidence in source.evidence_records
    }

    extracted_objects: list[CuratableObjectEnvelope] = []
    validation_findings: list[ValidationFinding] = []
    history: list[HistoryEvent] = [
        HistoryEvent(
            event_type=HistoryEventKind.CREATED,
            timestamp=source.produced_at,
            actor_type=HistoryActorType.SYSTEM,
            actor_id=GENE_DOMAIN_PACK_CONVERTER_ID,
            message="Converted tool-verified gene extraction output to pending domain envelope.",
        )
    ]

    object_index = 1
    for gene in source.gene_mentions:
        for evidence_id in gene.evidence_record_ids:
            evidence = evidence_by_id[evidence_id]
            pending_ref_id = f"gene-mention-evidence-{object_index}"
            object_ref = ObjectRef(
                pending_ref_id=pending_ref_id,
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            )
            extracted_objects.append(
                CuratableObjectEnvelope(
                    object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                    pending_ref_id=pending_ref_id,
                    schema_ref=_gene_schema_ref(),
                    definition_state=DefinitionState.STABLE,
                    definition_notes=list(GENE_MENTION_EVIDENCE_DEFINITION_NOTES),
                    payload=_payload_for_gene_evidence(
                        gene,
                        evidence,
                        normalization_notes=source.normalization_notes,
                    ),
                    metadata=_object_metadata(),
                )
            )
            validation_findings.append(_validation_finding(pending_ref_id))
            history.append(
                HistoryEvent(
                    event_type=HistoryEventKind.OBJECT_EXTRACTED,
                    timestamp=source.produced_at,
                    actor_type=HistoryActorType.SYSTEM,
                    actor_id=GENE_DOMAIN_PACK_CONVERTER_ID,
                    message="Added non-blocking gene mention evidence.",
                    object_ref=object_ref,
                    details={
                        "evidence_record_id": evidence.evidence_record_id,
                        "validator_binding_id": GENE_REFERENCE_VALIDATOR_BINDING_ID,
                    },
                )
            )
            object_index += 1

    return DomainEnvelope(
        envelope_id=source.envelope_id,
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        domain_pack_version=GENE_DOMAIN_PACK_VERSION,
        schema_ref=_gene_schema_ref(),
        extracted_objects=extracted_objects,
        validation_findings=validation_findings,
        history=history,
        metadata={
            "source_document_id": source.document_id,
            "source_agent": source.produced_by,
            "conversion": "tool_verified_gene_output_to_pending_envelope",
            "non_blocking_validation": True,
            "normalization_notes": source.normalization_notes,
        },
    )


# ---------------------------------------------------------------------------------------
# Builder-pattern materializer (Phase 1 migration).
#
# Mirrors gene_expression's ``materialize_gene_expression_builder_state``: read finalized
# builder-workspace candidates and emit the shared extraction-output payload
# (``curatable_objects[]`` of gene_mention_evidence + ``metadata`` with RELATIVE
# ``metadata_refs``). Unlike gene_expression there are NO resolver-backed controlled fields
# (the gene validator owns identity) and NO mirror/projection fields, so this materializer has
# no helper-selection or materializes_to_field_paths machinery.
#
# The envelope-pattern conversion above (``tool_verified_gene_output_to_pending_envelope``) is
# intentionally LEFT in place; envelope-legacy deletion is a later phase.
# ---------------------------------------------------------------------------------------


def validate_gene_builder_objects(
    output: RuntimeGeneExtractionResultEnvelope,
) -> tuple[str, ...]:
    """Return validation error messages for builder-materialized gene output.

    Inline structural contract for gene_mention_evidence objects (mirrors the
    ``GeneMentionEvidenceObjectEnvelope`` agent-bundle schema without importing the bundle):
    object_type/role, schema_ref pins, payload identity/evidence required fields, and
    evidence_record_ids == [payload.evidence_record_id] aligned to metadata.evidence_records[].
    """

    errors: list[str] = []
    evidence_by_id = {
        record.evidence_record_id: record
        for record in output.metadata.evidence_records
        if record.evidence_record_id
    }
    for index, obj in enumerate(output.curatable_objects):
        location = f"curatable_objects[{index}]"
        if obj.object_type != GENE_MENTION_EVIDENCE_OBJECT_TYPE:
            errors.append(f"{location}.object_type must be {GENE_MENTION_EVIDENCE_OBJECT_TYPE}")
        if obj.object_role != GENE_OBJECT_ROLE:
            errors.append(f"{location}.object_role must be {GENE_OBJECT_ROLE}")
        if obj.model_ref != GENE_MENTION_EVIDENCE_MODEL_ID:
            errors.append(f"{location}.model_ref must be {GENE_MENTION_EVIDENCE_MODEL_ID}")
        if obj.schema_ref is None or obj.schema_ref.schema_id != GENE_LINKML_SCHEMA_ID:
            errors.append(f"{location}.schema_ref.schema_id must be {GENE_LINKML_SCHEMA_ID}")

        payload = obj.payload if isinstance(obj.payload, Mapping) else {}
        if not _gene_clean_text(payload.get("mention")):
            errors.append(f"{location}.payload.mention is required")
        if not _gene_clean_text(payload.get("confidence")):
            errors.append(f"{location}.payload.confidence is required")
        notes = payload.get("identity_resolution_notes")
        if not isinstance(notes, list) or not [n for n in notes if _gene_clean_text(n)]:
            errors.append(f"{location}.payload.identity_resolution_notes must be non-empty")
        for field_name in ("evidence_record_id", "verified_quote", "section", "chunk_id"):
            if not _gene_clean_text(payload.get(field_name)):
                errors.append(f"{location}.payload.{field_name} is required")

        payload_evidence_id = _gene_clean_text(payload.get("evidence_record_id"))
        if obj.evidence_record_ids != ([payload_evidence_id] if payload_evidence_id else []):
            errors.append(
                f"{location}.evidence_record_ids must equal [payload.evidence_record_id]"
            )
        evidence_record = evidence_by_id.get(payload_evidence_id) if payload_evidence_id else None
        if evidence_record is None:
            errors.append(
                f"{location}.payload.evidence_record_id must resolve in metadata.evidence_records[]"
            )
        elif _gene_clean_text(evidence_record.verified_quote) != _gene_clean_text(
            payload.get("verified_quote")
        ):
            errors.append(
                f"{location}.payload.verified_quote must match metadata evidence verified_quote"
            )
    return tuple(errors)


class GeneBuilderExtractionOutput(RuntimeGeneExtractionResultEnvelope):
    """Validated builder output for one gene-mention-evidence extraction run.

    Validates ``curatable_objects`` against the gene_mention_evidence structural contract inline
    so the builder path produces the same structurally-clean shape as the envelope path, without
    importing the agent bundle (which is loaded by file-path discovery, not as a package module).
    """

    @model_validator(mode="after")
    def _validate_gene_objects(self) -> "GeneBuilderExtractionOutput":
        errors = validate_gene_builder_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class GeneMaterializationResult:
    """Outcome from materializing staged gene builder candidates into envelope output.

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


def _gene_materialization_issue(
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


def _gene_clean_text(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text or None


def _gene_unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _gene_clean_text(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _gene_normalized_evidence_records(
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


def _gene_pydantic_issues(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        _gene_materialization_issue(
            field_path=".".join(str(part) for part in error.get("loc", ())),
            reason=str(error.get("type") or "invalid"),
            message=str(error.get("msg") or "Invalid materialized gene envelope"),
        )
        for error in exc.errors()
    ]


def _gene_candidate_pending_ref_id(
    candidate: Any, staged_fields: Mapping[str, Any], index: int
) -> str:
    pending_ref_id = _gene_clean_text(staged_fields.get("pending_ref_id"))
    if pending_ref_id:
        return pending_ref_id
    pending_ref_ids = getattr(candidate, "pending_ref_ids", None) or []
    if pending_ref_ids:
        pending_ref_id = _gene_clean_text(pending_ref_ids[0])
        if pending_ref_id:
            return pending_ref_id
    return f"gene-mention-evidence-{index + 1}"


def _gene_evidence_object_payload(
    staged_fields: Mapping[str, Any],
    evidence_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one gene_mention_evidence payload from staged hints + one verified evidence record."""

    payload: dict[str, Any] = {}
    for field_name in _GENE_IDENTITY_HINT_FIELDS:
        value = staged_fields.get(field_name)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            payload[field_name] = value
    notes = staged_fields.get("identity_resolution_notes")
    payload["identity_resolution_notes"] = _gene_unique_strings(notes)
    payload["evidence_record_id"] = _gene_clean_text(evidence_record.get("evidence_record_id"))
    for field_name in _GENE_EVIDENCE_LOCATOR_FIELDS:
        value = evidence_record.get(field_name)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            payload[field_name] = value
    return payload


def materialize_gene_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    resolver_entry_lookup: Callable[[str], Any] | None = None,
    produced_by: str = "gene_extractor",
) -> GeneMaterializationResult:
    """Build canonical GeneExtractionResultEnvelope output from finalized builder state."""

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
                _gene_materialization_issue(
                    field_path="candidate_ids",
                    reason="unknown_candidate_id",
                    message=str(exc),
                    candidate_id=candidate_id,
                )
            )

    normalized_evidence_records = _gene_normalized_evidence_records(evidence_records or [])
    evidence_records_by_id = {
        record["evidence_record_id"]: record
        for record in normalized_evidence_records
        if isinstance(record.get("evidence_record_id"), str)
    }

    curatable_objects: list[CuratableObjectEnvelope] = []
    raw_mentions: list[dict[str, Any]] = []
    retained_evidence_ids: list[str] = []
    object_index = 0

    for candidate in candidates:
        staged_fields = copy.deepcopy(dict(getattr(candidate, "staged_fields", {}) or {}))
        candidate_pending_ref = _gene_candidate_pending_ref_id(
            candidate, staged_fields, object_index
        )
        evidence_ids = _gene_unique_strings(
            getattr(candidate, "evidence_record_ids", None)
            or staged_fields.get("evidence_record_ids")
        )
        if not evidence_ids:
            issues.append(
                _gene_materialization_issue(
                    field_path="evidence_record_ids",
                    reason="missing_evidence_record_ids",
                    message="Finalized gene candidates require non-empty evidence_record_ids.",
                    candidate_id=getattr(candidate, "candidate_id", None),
                )
            )
            continue

        for evidence_id in evidence_ids:
            evidence_record = evidence_records_by_id.get(evidence_id)
            if evidence_record is None:
                issues.append(
                    _gene_materialization_issue(
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
                continue
            if _gene_clean_text(evidence_record.get("verified_quote")) is None:
                issues.append(
                    _gene_materialization_issue(
                        field_path="evidence_record_ids",
                        reason="incomplete_evidence_record",
                        message="Verified evidence records must include verified_quote.",
                        candidate_id=getattr(candidate, "candidate_id", None),
                        evidence_record_id=evidence_id,
                    )
                )
                continue

            # One gene_mention_evidence object per (candidate, evidence) pairing, mirroring the
            # envelope-pattern converter so the object graph stays identical.
            pending_ref_id = (
                candidate_pending_ref
                if len(evidence_ids) == 1
                else f"{candidate_pending_ref}-{object_index + 1}"
            )
            payload = _gene_evidence_object_payload(staged_fields, evidence_record)
            evidence_position = next(
                (
                    position
                    for position, record in enumerate(normalized_evidence_records)
                    if record.get("evidence_record_id") == evidence_id
                ),
                None,
            )
            metadata_refs = [
                {"metadata_path": f"raw_mentions[{object_index}]", "role": "source_mention"}
            ]
            if evidence_position is not None:
                metadata_refs.append(
                    {
                        "metadata_path": f"evidence_records[{evidence_position}]",
                        "role": "verified_evidence",
                    }
                )
            raw_mentions.append(
                {
                    "mention": payload.get("mention") or candidate_pending_ref,
                    "entity_type": "gene",
                    "evidence_record_ids": [evidence_id],
                }
            )
            retained_evidence_ids.append(evidence_id)
            curatable_objects.append(
                CuratableObjectEnvelope(
                    object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                    object_role=GENE_OBJECT_ROLE,
                    pending_ref_id=pending_ref_id,
                    model_ref=GENE_MENTION_EVIDENCE_MODEL_ID,
                    schema_ref=_gene_schema_ref(),
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    definition_notes=list(GENE_MENTION_EVIDENCE_DEFINITION_NOTES),
                    payload=payload,
                    evidence_record_ids=[evidence_id],
                    metadata_refs=metadata_refs,
                    metadata=_object_metadata(),
                )
            )
            object_index += 1

    provenance = {
        "source": GENE_MATERIALIZER_ID,
        "produced_by": produced_by,
        "builder_run_id": getattr(workspace, "run_id", None),
        "source_candidate_ids": list(normalized_candidate_ids),
    }
    output_payload = {
        "summary": "Finalized gene extraction from builder-staged mentions.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in curatable_objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence_records,
            "normalization_notes": [
                "Gene mention evidence was assembled by backend materialization from builder state."
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
        "schema_ref": _gene_schema_ref().model_dump(mode="json", exclude_none=True),
    }

    if not curatable_objects and not issues:
        issues.append(
            _gene_materialization_issue(
                field_path="curatable_objects",
                reason="no_retained_candidates",
                message="Finalized gene extraction produced no retained gene_mention_evidence objects.",
            )
        )

    if not issues:
        try:
            output = GeneBuilderExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_gene_pydantic_issues(exc))
        else:
            output_payload = output.model_dump(mode="json", exclude_none=True)

    return GeneMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_gene_unique_strings(retained_evidence_ids)),
    )


__all__ = [
    "GeneBuilderExtractionOutput",
    "GeneMaterializationResult",
    "ToolVerifiedGeneEvidenceRecord",
    "ToolVerifiedGeneMention",
    "ToolVerifiedGeneOutput",
    "materialize_gene_builder_state",
    "tool_verified_gene_output_to_pending_envelope",
]
