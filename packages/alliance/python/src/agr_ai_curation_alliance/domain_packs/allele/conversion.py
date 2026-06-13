"""Builder-pattern materializer for the allele extractor (Phase 4 migration).

Mirrors ``phenotype``'s ``materialize_phenotype_builder_state`` (the closest multi-object
reference) and ``gene``'s mention-only posture: read finalized builder-workspace candidates and
emit the shared extraction-output payload (``curatable_objects[]`` + ``metadata`` with RELATIVE
``metadata_refs``). The generic converter (``_domain_envelope_from_extraction_result``) turns that
payload into a DomainEnvelope, nesting ``metadata`` under ``metadata.extraction_metadata``.

POSTURE (preserve the existing pack — runbook §3): the migration changes the EXTRACTION
MECHANISM, not the curation target. This materializer emits the SAME 4-object pending association
graph the existing envelope converter
(``__init__.build_pending_allele_envelope_from_tool_verified_fixture``) produced:

  * one shared ``Reference`` (the source paper),
  * one ``AlleleMention`` per retained candidate (the validator-binding input object),
  * one or more ``EvidenceQuote`` (one per verified evidence record),
  * one ``AllelePaperEvidenceAssociation`` curatable_unit with ``object_refs[]`` wired to those
    pending objects, ``association_kind = "allele_paper_evidence"``, NO ``allele_identifier``
    (the active allele validator resolves identity), and BLOCKED write/export metadata.

MENTION-ONLY: the extractor NEVER materializes an ``Allele`` object or an allele identifier; the
active ``allele_mention_reference_validation`` binding fires on ``AlleleMention.mention.text`` and
materializes the validator-owned Allele identity scalars (curie/symbol/taxon). There are NO
resolver-backed controlled fields and NO ``materializes_to_field_paths`` mirror (allele declares
none). The intentional ``alliance.allele.write_blocked`` BLOCKER is a domain finding (surfaced by
the existing submission adapter on the blocked write metadata), NOT one of the four structural
codes.

Output is validated by ``AlleleBuilderExtractionOutput`` (subclasses the proven
``AlleleExtractionResultEnvelope`` schema.py validator) so the builder path produces the same
structurally-clean envelope the envelope path does. ``metadata_refs`` are RELATIVE
(``raw_mentions[N]`` / ``evidence_records[N]``); never absolute; never rewritten in a converter.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable, Sequence

from pydantic import ValidationError, model_validator

from src.lib.openai_agents.models import (
    AlleleExtractionResultEnvelope as RuntimeAlleleExtractionResultEnvelope,
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
    ALLELE_ASSOCIATION_KIND,
    ALLELE_ASSOCIATION_LINKML_SCHEMA_ID,
    ALLELE_ASSOCIATION_MODEL_ID,
    ALLELE_ASSOCIATION_OBJECT_ROLE,
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
    ALLELE_DOMAIN_PACK_VERSION,
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
    ALLELE_LINKML_SCHEMA_SOURCE_FILE,
    ALLELE_MATERIALIZER_ID,
    ALLELE_MENTION_OBJECT_TYPE,
    ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID,
    ALLELE_REFERENCE_LINKML_SCHEMA_ID,
    ALLELE_REFERENCE_OBJECT_TYPE,
    ALLELE_REFERENCE_SCHEMA_SOURCE_FILE,
)

# Shared pending Reference across all candidates (mirrors the envelope converter's single
# paper-reference-1 object). The reference validator is under_development; the Reference is emitted
# pending without a durable reference_id (see approach-doc Open Question 4 — preserve posture).
_SHARED_REFERENCE_REF_ID = "paper-reference-1"
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
            message=str(error.get("msg") or "Invalid materialized allele envelope"),
        )
        for error in exc.errors()
    ]


def _linkml_uri(source_file: str) -> str:
    return (
        "https://github.com/alliance-genome/agr_curation_schema/blob/"
        f"{ALLIANCE_LINKML_COMMIT}/{source_file}"
    )


def _association_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=ALLELE_ASSOCIATION_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="AlleleAssociation",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(ALLELE_LINKML_SCHEMA_SOURCE_FILE),
        definition_state=DefinitionState.IN_DEVELOPMENT,
        definition_notes=[
            "Abstract LinkML target used only for grounded pending-envelope metadata; "
            "writes are blocked.",
        ],
        metadata={
            PROVIDER_REFS_METADATA_KEY: {
                ALLIANCE_LINKML_PROVIDER_KEY: {
                    "schema_ref": "alliance.linkml",
                    "commit": ALLIANCE_LINKML_COMMIT,
                    "source_file": ALLELE_LINKML_SCHEMA_SOURCE_FILE,
                    "class": "AlleleAssociation",
                }
            }
        },
    )


def _reference_schema_ref() -> SchemaRef:
    return SchemaRef(
        schema_id=ALLELE_REFERENCE_LINKML_SCHEMA_ID,
        provider=ALLIANCE_LINKML_PROVIDER_KEY,
        name="Reference",
        version=ALLIANCE_LINKML_COMMIT,
        uri=_linkml_uri(ALLELE_REFERENCE_SCHEMA_SOURCE_FILE),
    )


def _blocked_export_behavior() -> dict[str, Any]:
    """Preserve the existing-pack blocked export posture verbatim."""

    return {
        "status": "blocked",
        "mode": "verified_association_targets_only",
        "reason": (
            "Allele association export requires durable allele, reference, and evidence IDs "
            "before any verified target operation can be emitted."
        ),
        "verified_targets": [
            "public.allele_reference",
            "public.allelegeneassociation",
            "public.allelegeneassociation_informationcontententity",
        ],
        "blocked_targets": [
            "public.allele_reference",
            "public.allelegeneassociation_informationcontententity",
        ],
    }


def _blocked_write_behavior() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": (
            "Reference materialization and non-mutating allele association writes are not "
            "verified for this pack."
        ),
    }


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


def _mention_payload(staged_fields: Mapping[str, Any], *, source_mentions: Sequence[str]) -> dict[str, Any]:
    """Build the AlleleMention payload (the active validator-binding input object).

    Mention text is the source anchor (protected). normalized_hint / associated_gene / taxon are
    OPTIONAL supplemental validator context, exactly as the envelope path emitted them.
    """

    mention_text = _clean_text(staged_fields.get("mention"))
    payload: dict[str, Any] = {"mention": {"text": mention_text}}
    normalized_hint = _clean_text(staged_fields.get("normalized_hint"))
    if normalized_hint is not None:
        payload["mention"]["normalized_hint"] = normalized_hint
    associated_gene = _clean_text(staged_fields.get("associated_gene"))
    if associated_gene is not None:
        payload["associated_gene"] = {"symbol": associated_gene}
    taxon = _clean_text(staged_fields.get("taxon"))
    if taxon is not None:
        payload["taxon"] = {"curie": taxon}
    payload["source_mentions"] = list(source_mentions)
    return payload


def _evidence_quote_payload(evidence_record: Mapping[str, Any]) -> dict[str, Any]:
    """Build one EvidenceQuote payload (schema.py requires evidence_record_id/verified_quote/
    page/section/chunk_id; subsection/figure_reference optional)."""

    payload: dict[str, Any] = {
        "evidence_record_id": _clean_text(evidence_record.get("evidence_record_id")),
        "verified_quote": _clean_text(evidence_record.get("verified_quote")),
    }
    for field_name in ("entity", "page", "section", "subsection", "chunk_id", "figure_reference"):
        value = evidence_record.get(field_name)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            payload[field_name] = value
    return payload


# Object roles for the 4-object graph (mirrors schema.py `_EXPECTED_OBJECT_ROLES`).
_EXPECTED_OBJECT_ROLES = {
    ALLELE_ASSOCIATION_OBJECT_TYPE: ALLELE_ASSOCIATION_OBJECT_ROLE,
    ALLELE_REFERENCE_OBJECT_TYPE: "validated_reference",
    ALLELE_MENTION_OBJECT_TYPE: "metadata_only",
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE: "metadata_only",
}
# The extractor must NEVER emit Allele (the active validator materializes allele identity).
_VALIDATOR_MATERIALIZED_OBJECT_TYPES = {"Allele"}
_REQUIRED_ASSOCIATION_REF_TYPES = {
    ALLELE_REFERENCE_OBJECT_TYPE,
    ALLELE_MENTION_OBJECT_TYPE,
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
}
_REQUIRED_EVIDENCE_QUOTE_PAYLOAD_FIELDS = (
    "evidence_record_id",
    "verified_quote",
    "page",
    "section",
    "chunk_id",
)


def validate_allele_builder_objects(
    output: RuntimeAlleleExtractionResultEnvelope,
) -> tuple[str, ...]:
    """Return structural-contract error messages for builder-materialized allele output.

    Inline structural contract for the 4-object pending association graph, replicating the proven
    ``schema.py`` ``AlleleExtractionResultEnvelope`` model-validator's intent without re-importing
    the agent bundle (which is loaded by file-path discovery, not as a package module): object
    roles + definition_state for every object, NO validator-materialized ``Allele``, only allele
    domain-pack object types, per-association required object_refs resolving to emitted objects, no
    extractor-owned ``allele_identifier``, evidence_record_ids aligned (payload == object) and
    resolving to verified ``metadata.evidence_records[]``, EvidenceQuote required payload fields,
    and BLOCKED write/export metadata.
    """

    errors: list[str] = []
    evidence_by_id = {
        record.evidence_record_id: record
        for record in output.metadata.evidence_records
        if record.evidence_record_id
    }
    declared_evidence_ids = set(evidence_by_id)
    declared_pending_refs = {
        (obj.pending_ref_id, obj.object_type)
        for obj in output.curatable_objects
        if obj.pending_ref_id
    }

    object_types = {obj.object_type for obj in output.curatable_objects}
    validator_materialized = sorted(object_types & _VALIDATOR_MATERIALIZED_OBJECT_TYPES)
    if validator_materialized:
        errors.append(
            "curatable_objects must not contain validator-materialized object types: "
            + ", ".join(validator_materialized)
        )
    unsupported = sorted(object_types - set(_EXPECTED_OBJECT_ROLES))
    if unsupported:
        errors.append(
            "curatable_objects may only contain allele domain-pack object types; unsupported: "
            + ", ".join(unsupported)
        )

    associations = [
        obj
        for obj in output.curatable_objects
        if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    ]
    if not associations:
        errors.append(
            "curatable_objects must contain at least one AllelePaperEvidenceAssociation"
        )

    for obj in output.curatable_objects:
        if obj.object_type not in _EXPECTED_OBJECT_ROLES:
            continue
        location = f"curatable_objects[{obj.object_type}:{obj.pending_ref_id}]"
        expected_role = _EXPECTED_OBJECT_ROLES[obj.object_type]
        role = obj.object_role or (
            obj.metadata.get("object_role") if isinstance(obj.metadata, Mapping) else None
        )
        if role != expected_role:
            errors.append(f"{location}.object_role must be '{expected_role}'")
        if obj.definition_state != DefinitionState.IN_DEVELOPMENT:
            errors.append(f"{location}.definition_state must be 'in_development'")
        if obj.object_type == ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE:
            payload = obj.payload if isinstance(obj.payload, Mapping) else {}
            missing = [
                field_name
                for field_name in _REQUIRED_EVIDENCE_QUOTE_PAYLOAD_FIELDS
                if _is_missing(payload.get(field_name))
            ]
            if missing:
                errors.append(
                    f"{location}.payload missing required field(s): " + ", ".join(missing)
                )

    for index, obj in enumerate(associations):
        location = f"curatable_objects[AllelePaperEvidenceAssociation#{index}]"
        if obj.schema_ref is None or obj.schema_ref.schema_id != ALLELE_ASSOCIATION_LINKML_SCHEMA_ID:
            errors.append(
                f"{location}.schema_ref.schema_id must be {ALLELE_ASSOCIATION_LINKML_SCHEMA_ID}"
            )

        payload = obj.payload if isinstance(obj.payload, Mapping) else {}
        if payload.get("association_kind") != ALLELE_ASSOCIATION_KIND:
            errors.append(
                f"{location}.payload.association_kind must be {ALLELE_ASSOCIATION_KIND}"
            )
        if not _is_missing(payload.get("allele_identifier")):
            errors.append(
                f"{location}.payload.allele_identifier must be left for the active allele validator"
            )

        ref_types = {ref.object_type for ref in obj.object_refs}
        validator_ref_types = sorted(ref_types & _VALIDATOR_MATERIALIZED_OBJECT_TYPES)
        if validator_ref_types:
            errors.append(
                f"{location}.object_refs must not include validator-materialized types: "
                + ", ".join(validator_ref_types)
            )
        missing_ref_types = _REQUIRED_ASSOCIATION_REF_TYPES - ref_types
        if missing_ref_types:
            errors.append(
                f"{location}.object_refs missing types: " + ", ".join(sorted(missing_ref_types))
            )
        unresolved_refs = [
            f"{ref.object_type}:{ref.pending_ref_id}"
            for ref in obj.object_refs
            if ref.pending_ref_id
            and ref.object_type in _REQUIRED_ASSOCIATION_REF_TYPES
            and (ref.pending_ref_id, ref.object_type) not in declared_pending_refs
        ]
        if unresolved_refs:
            errors.append(
                f"{location}.object_refs must resolve to emitted curatable_objects[]: "
                + ", ".join(sorted(unresolved_refs))
            )

        payload_evidence_ids = [
            str(item) for item in (payload.get("evidence_record_ids") or []) if str(item).strip()
        ]
        object_evidence_ids = [str(item) for item in obj.evidence_record_ids]
        if not object_evidence_ids:
            errors.append(f"{location}.evidence_record_ids must not be empty")
        if payload_evidence_ids != object_evidence_ids:
            errors.append(
                f"{location}.payload.evidence_record_ids must match curatable object "
                "evidence_record_ids"
            )
        for evidence_id in object_evidence_ids:
            if evidence_id not in declared_evidence_ids:
                errors.append(
                    f"{location}.evidence_record_ids[{evidence_id}] must resolve in "
                    "metadata.evidence_records[]"
                )
            elif _clean_text(evidence_by_id[evidence_id].verified_quote) is None:
                errors.append(
                    f"{location}.evidence_record {evidence_id} must include verified_quote"
                )

        write_behavior = obj.metadata.get("write_behavior") if isinstance(obj.metadata, Mapping) else None
        if not isinstance(write_behavior, Mapping) or write_behavior.get("status") != "blocked":
            errors.append(f"{location}.metadata.write_behavior.status must be 'blocked'")
        export_behavior = obj.metadata.get("export_behavior") if isinstance(obj.metadata, Mapping) else None
        if not isinstance(export_behavior, Mapping) or export_behavior.get("status") != "blocked":
            errors.append(f"{location}.metadata.export_behavior.status must be 'blocked'")

    return tuple(errors)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


class AlleleBuilderExtractionOutput(RuntimeAlleleExtractionResultEnvelope):
    """Validated builder output for one allele extraction run.

    Subclasses the proven ``AlleleExtractionResultEnvelope`` (the existing schema.py model-validator
    that enforces object roles, no validator-materialized Allele, association refs, no extractor
    allele_identifier, evidence-record alignment, and blocked write behavior) and adds the
    builder-graph completeness checks in ``validate_allele_builder_objects`` so the builder path
    produces the same structurally-clean shape as the envelope path. ``AlleleExtractionResultEnvelope``
    lives in the allele extractor agent bundle (loaded by file-path discovery), so the inline checks
    here avoid importing the bundle as a package module.
    """

    @model_validator(mode="after")
    def _validate_allele_builder_objects(self) -> "AlleleBuilderExtractionOutput":
        # Re-run the proven envelope-schema model-validator first (subclass inherits it), then add
        # builder-graph completeness checks.
        errors = validate_allele_builder_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class AlleleMaterializationResult:
    """Outcome from materializing staged allele builder candidates into envelope output.

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


def _mention_object_metadata() -> dict[str, Any]:
    return {OBJECT_ROLE_METADATA_KEY: "metadata_only"}


def _evidence_quote_object_metadata() -> dict[str, Any]:
    return {OBJECT_ROLE_METADATA_KEY: "metadata_only"}


def _reference_object_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: "validated_reference",
        "validation_state": _REFERENCE_PENDING_STATE,
    }


def _association_object_metadata() -> dict[str, Any]:
    return {
        OBJECT_ROLE_METADATA_KEY: ALLELE_ASSOCIATION_OBJECT_ROLE,
        "association_kind": ALLELE_ASSOCIATION_KIND,
        "export_behavior": _blocked_export_behavior(),
        "write_behavior": _blocked_write_behavior(),
        "materialized_by": ALLELE_MATERIALIZER_ID,
        "validator_binding_id": ALLELE_MENTION_REFERENCE_VALIDATOR_BINDING_ID,
        PROVIDER_REFS_METADATA_KEY: {
            ALLIANCE_LINKML_PROVIDER_KEY: {
                "schema_ref": "alliance.linkml",
                "commit": ALLIANCE_LINKML_COMMIT,
                "source_file": ALLELE_LINKML_SCHEMA_SOURCE_FILE,
                "class": "AlleleAssociation",
            }
        },
    }


def materialize_allele_builder_state(
    *,
    workspace: Any,
    candidate_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] | None = None,
    resolver_entry_lookup: Callable[[str], Any] | None = None,
    produced_by: str = "allele_extractor",
) -> AlleleMaterializationResult:
    """Build canonical AlleleExtractionResultEnvelope output from finalized builder state.

    One retained candidate -> one ``AlleleMention`` + one-or-more ``EvidenceQuote`` + one
    ``AllelePaperEvidenceAssociation`` curatable_unit, all sharing one ``Reference``. Mirrors the
    existing envelope converter's object graph and BLOCKED write/export posture. ``metadata_refs``
    are RELATIVE. The extractor NEVER emits an Allele object or an allele_identifier (mention-only;
    the active validator owns identity).
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
    retained_count = 0

    # One shared Reference across candidates (created lazily so an all-skipped run emits nothing).
    reference_paper_title: str | None = None
    reference_paper_filename: str | None = None
    reference_emitted = False

    for candidate in candidates:
        staged_fields = copy.deepcopy(dict(getattr(candidate, "staged_fields", {}) or {}))
        mention_text = _clean_text(staged_fields.get("mention"))
        if mention_text is None:
            issues.append(
                _materialization_issue(
                    field_path="mention",
                    reason="missing_allele_mention",
                    message="Finalized allele candidates require a non-empty mention.",
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
                    message="Finalized allele candidates require non-empty evidence_record_ids.",
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

        retained_count += 1
        source_mentions = _unique_strings(staged_fields.get("source_mentions")) or [mention_text]

        # Capture paper context for the shared Reference from the first retained candidate.
        if not reference_emitted:
            reference_paper_title = _clean_text(staged_fields.get("reference_title"))
            reference_paper_filename = _clean_text(staged_fields.get("reference_filename"))
            reference_payload: dict[str, Any] = {}
            if reference_paper_title is not None:
                reference_payload["title"] = reference_paper_title
            if reference_paper_filename is not None:
                reference_payload["filename"] = reference_paper_filename
            curatable_objects.append(
                CuratableObjectEnvelope(
                    object_type=ALLELE_REFERENCE_OBJECT_TYPE,
                    object_role="validated_reference",
                    pending_ref_id=_SHARED_REFERENCE_REF_ID,
                    schema_ref=_reference_schema_ref(),
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    definition_notes=[
                        "Pending source-paper reference; durable reference_id is resolved by the "
                        "under-development reference validator before export.",
                    ],
                    payload=reference_payload,
                    metadata=_reference_object_metadata(),
                )
            )
            reference_emitted = True

        mention_ref_id = f"allele-mention-{retained_count}"
        association_ref_id = f"allele-paper-evidence-association-{retained_count}"

        mention_payload = _mention_payload(staged_fields, source_mentions=source_mentions)
        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=ALLELE_MENTION_OBJECT_TYPE,
                object_role="metadata_only",
                pending_ref_id=mention_ref_id,
                definition_state=DefinitionState.IN_DEVELOPMENT,
                payload=mention_payload,
                evidence_record_ids=evidence_ids,
                metadata=_mention_object_metadata(),
            )
        )

        association_object_refs: list[ObjectRef] = [
            ObjectRef(pending_ref_id=_SHARED_REFERENCE_REF_ID, object_type=ALLELE_REFERENCE_OBJECT_TYPE),
            ObjectRef(pending_ref_id=mention_ref_id, object_type=ALLELE_MENTION_OBJECT_TYPE),
        ]
        association_evidence_ids: list[str] = []
        for evidence_index, evidence_record in enumerate(resolved_evidence, start=1):
            evidence_id = _clean_text(evidence_record.get("evidence_record_id"))
            if evidence_id is None:
                raise ValueError(
                    "Allele builder materialization requires every resolved "
                    "evidence record to include evidence_record_id."
                )
            evidence_ref_id = f"evidence-quote-{retained_count}-{evidence_index}"
            association_evidence_ids.append(evidence_id)
            association_object_refs.append(
                ObjectRef(
                    pending_ref_id=evidence_ref_id,
                    object_type=ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
                )
            )
            curatable_objects.append(
                CuratableObjectEnvelope(
                    object_type=ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
                    object_role="metadata_only",
                    pending_ref_id=evidence_ref_id,
                    definition_state=DefinitionState.IN_DEVELOPMENT,
                    payload=_evidence_quote_payload(evidence_record),
                    evidence_record_ids=[evidence_id] if evidence_id else [],
                    metadata=_evidence_quote_object_metadata(),
                )
            )

        association_payload: dict[str, Any] = {
            "association_kind": ALLELE_ASSOCIATION_KIND,
            "allele_label": mention_text,
            "evidence_record_ids": association_evidence_ids,
        }
        if reference_paper_title is not None:
            association_payload["reference_title"] = reference_paper_title

        metadata_refs = [
            {"metadata_path": f"raw_mentions[{retained_count - 1}]", "role": "source_mention"}
        ]
        for evidence_id in association_evidence_ids:
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
                "entity_type": "allele",
                "evidence_record_ids": association_evidence_ids,
            }
        )
        retained_evidence_ids.extend(association_evidence_ids)

        curatable_objects.append(
            CuratableObjectEnvelope(
                object_type=ALLELE_ASSOCIATION_OBJECT_TYPE,
                object_role=ALLELE_ASSOCIATION_OBJECT_ROLE,
                pending_ref_id=association_ref_id,
                model_ref=ALLELE_ASSOCIATION_MODEL_ID,
                schema_ref=_association_schema_ref(),
                definition_state=DefinitionState.IN_DEVELOPMENT,
                definition_notes=[
                    "Pending only; write behavior is blocked until reference IDs and write targets "
                    "are verified.",
                    "Evidence and pending references are materialized by backend builder "
                    "finalization.",
                ],
                payload=association_payload,
                object_refs=association_object_refs,
                evidence_record_ids=association_evidence_ids,
                metadata_refs=metadata_refs,
                metadata=_association_object_metadata(),
            )
        )

    provenance = {
        "source": ALLELE_MATERIALIZER_ID,
        "produced_by": produced_by,
        "builder_run_id": getattr(workspace, "run_id", None),
        "source_candidate_ids": list(normalized_candidate_ids),
    }
    output_payload = {
        "summary": "Finalized allele extraction from builder-staged mentions.",
        "curatable_objects": [
            obj.model_dump(mode="json", exclude_none=True) for obj in curatable_objects
        ],
        "metadata": {
            "raw_mentions": raw_mentions,
            "evidence_records": normalized_evidence_records,
            "normalization_notes": [
                "Allele paper/evidence associations were assembled by backend materialization "
                "from builder state."
            ],
            "exclusions": [],
            "ambiguities": [],
            "notes": [],
            "provenance": provenance,
        },
        "run_summary": {
            "candidate_count": len(normalized_candidate_ids),
            "kept_count": retained_count,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
        "schema_ref": _association_schema_ref().model_dump(mode="json", exclude_none=True),
    }

    if retained_count == 0 and not issues:
        issues.append(
            _materialization_issue(
                field_path="curatable_objects",
                reason="no_retained_candidates",
                message=(
                    "Finalized allele extraction produced no retained "
                    "AllelePaperEvidenceAssociation objects."
                ),
            )
        )

    if not issues:
        try:
            output = AlleleBuilderExtractionOutput.model_validate(output_payload)
        except ValidationError as exc:
            issues.extend(_pydantic_issues(exc))
        else:
            output_payload = output.model_dump(mode="json", exclude_none=True)

    return AlleleMaterializationResult(
        payload=None if issues else output_payload,
        issues=tuple(issues),
        source_candidate_ids=normalized_candidate_ids,
        evidence_record_ids=tuple(_unique_strings(retained_evidence_ids)),
    )


__all__ = [
    "ALLELE_DOMAIN_PACK_ID",
    "ALLELE_DOMAIN_PACK_VERSION",
    "AlleleBuilderExtractionOutput",
    "AlleleMaterializationResult",
    "materialize_allele_builder_state",
    "validate_allele_builder_objects",
]
