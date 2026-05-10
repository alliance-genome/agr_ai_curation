"""Submission planning for allele paper/evidence association envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    ObjectRef,
)


ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY = "allele_verified_association_targets"
ALLELE_PAPER_EVIDENCE_ASSOCIATION_OBJECT_TYPE = "AllelePaperEvidenceAssociation"
VERIFIED_ALLELE_ASSOCIATION_TARGETS = {
    "public.allele_reference": {
        "operation": "insert",
        "columns": ("allele_id", "references_id"),
        "constraints": (
            "allele_reference_allele_id_fk references public.allele(id)",
            "allele_reference_references_id_fk references public.reference(id)",
        ),
        "mutates_base_rows": False,
    },
    "public.allelegeneassociation": {
        "operation": "insert",
        "columns": (
            "alleleassociationsubject_id",
            "allelegeneassociationobject_id",
        ),
        "constraints": (
            "allelegeneassociation_aasubject_id_fk references public.allele(id)",
            "allelegeneassociation_agaobject_id_fk references public.gene(id)",
        ),
        "mutates_base_rows": False,
    },
    "public.allelegeneassociation_informationcontententity": {
        "operation": "insert",
        "columns": ("association_id", "evidence_id"),
        "constraints": (
            "allelegeneassociation_ice_association_id_fk references public.allelegeneassociation(id)",
            "allelegeneassociation_ice_evidence_id_fk references public.informationcontententity(id)",
        ),
        "mutates_base_rows": False,
    },
}
BASE_ROW_TABLES = ("public.allele", "public.gene")


def build_allele_association_submission_plan(
    envelope: DomainEnvelope,
    *,
    selected_object_ids: Sequence[str] | None = None,
    target_key: str = ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
) -> dict[str, Any]:
    """Build a non-mutating allele association write plan or blockers."""

    selected = set(selected_object_ids or ())
    blockers: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []

    if target_key != ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY:
        blockers.append(
            _blocker(
                code="alliance.allele.unknown_write_target",
                message=f"Allele submission target is not verified: {target_key}.",
                details={
                    "requested_target_key": target_key,
                    "supported_target_key": ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
                    "verified_targets": sorted(VERIFIED_ALLELE_ASSOCIATION_TARGETS),
                },
            )
        )
        return _plan(target_key=target_key, operations=(), blockers=blockers)

    objects_by_ref = _objects_by_ref(envelope)
    association_objects = [
        domain_object
        for domain_object in envelope.objects
        if domain_object.object_type == ALLELE_PAPER_EVIDENCE_ASSOCIATION_OBJECT_TYPE
        and (not selected or _stable_object_id(domain_object) in selected)
    ]
    if not association_objects:
        blockers.append(
            _blocker(
                code="alliance.allele.no_association_objects",
                message="No AllelePaperEvidenceAssociation objects are selected for submission.",
            )
        )

    for association in association_objects:
        object_blockers, object_operations = _association_submission_operations(
            association=association,
            objects_by_ref=objects_by_ref,
        )
        blockers.extend(object_blockers)
        operations.extend(object_operations)

    return _plan(target_key=target_key, operations=operations, blockers=blockers)


def _association_submission_operations(
    *,
    association: CuratableObjectEnvelope,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    object_id = _stable_object_id(association)
    blockers: list[dict[str, Any]] = []
    candidate_operations: list[dict[str, Any]] = []

    if association.definition_state is not DefinitionState.STABLE:
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.definition_state_blocked",
                message=(
                    "Allele paper/evidence association definition is not stable "
                    "for submission."
                ),
                details={"definition_state": association.definition_state.value},
            )
        )

    write_behavior = association.metadata.get("write_behavior")
    if isinstance(write_behavior, Mapping) and write_behavior.get("status") == "blocked":
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.write_behavior_blocked",
                message=str(
                    write_behavior.get("reason")
                    or "Allele association write behavior is blocked."
                ),
                details={"write_behavior": dict(write_behavior)},
            )
        )

    allele = _referenced_object(
        association,
        object_type="Allele",
        objects_by_ref=objects_by_ref,
    )
    reference = _referenced_object(
        association,
        object_type="Reference",
        objects_by_ref=objects_by_ref,
    )
    evidence_quotes = _referenced_objects(
        association,
        object_type="EvidenceQuote",
        objects_by_ref=objects_by_ref,
    )
    if allele is None:
        blockers.append(
            _missing_reference_blocker(object_id=object_id, object_type="Allele")
        )
    if reference is None:
        blockers.append(
            _missing_reference_blocker(object_id=object_id, object_type="Reference")
        )
    if not evidence_quotes:
        blockers.append(
            _missing_reference_blocker(object_id=object_id, object_type="EvidenceQuote")
        )

    allele_db_id = _db_id(allele.payload, "allele_id", "id") if allele is not None else None
    reference_id = (
        _db_id(reference.payload, "reference_id", "id")
        if reference is not None
        else None
    )
    if allele is not None and allele_db_id is None:
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.allele_db_id_unresolved",
                message=(
                    "Allele association submission requires a durable public.allele.id "
                    "resolved from the validated allele reference."
                ),
                details={"target_table": "public.allele_reference"},
            )
        )
    if reference is not None and reference_id is None:
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.reference_id_unresolved",
                message=(
                    "Allele association submission requires a durable "
                    "public.reference.id/reference_id for the source paper."
                ),
                details={"target_table": "public.allele_reference"},
            )
        )

    if allele_db_id is not None and reference_id is not None:
        candidate_operations.append(
            {
                "operation": "insert",
                "target_table": "public.allele_reference",
                "values": {
                    "allele_id": allele_db_id,
                    "references_id": reference_id,
                },
                "mutates_base_rows": False,
            }
        )

    association_id = _db_id(association.payload, "association_id", "id")
    evidence_ids = [
        evidence_id
        for evidence_id in (
            _db_id(quote.payload, "information_content_entity_id", "evidence_id", "id")
            for quote in evidence_quotes
        )
        if evidence_id is not None
    ]
    if evidence_quotes and len(evidence_ids) != len(evidence_quotes):
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.evidence_target_unresolved",
                message=(
                    "Evidence association submission requires each EvidenceQuote to "
                    "resolve to public.informationcontententity.id."
                ),
                details={
                    "target_table": (
                        "public.allelegeneassociation_informationcontententity"
                    )
                },
            )
        )
    if evidence_ids and association_id is None:
        blockers.append(
            _blocker(
                object_id=object_id,
                code="alliance.allele.association_id_unresolved",
                message=(
                    "Evidence association submission requires a durable "
                    "public.allelegeneassociation.id before evidence rows can be linked."
                ),
                details={
                    "target_table": (
                        "public.allelegeneassociation_informationcontententity"
                    )
                },
            )
        )
    if association_id is not None and evidence_ids:
        candidate_operations.extend(
            {
                "operation": "insert",
                "target_table": "public.allelegeneassociation_informationcontententity",
                "values": {
                    "association_id": association_id,
                    "evidence_id": evidence_id,
                },
                "mutates_base_rows": False,
            }
            for evidence_id in evidence_ids
        )

    return blockers, [] if blockers else candidate_operations


def _plan(
    *,
    target_key: str,
    operations: Sequence[Mapping[str, Any]],
    blockers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "blocked" if blockers else "ready",
        "target_key": target_key,
        "verified_targets": VERIFIED_ALLELE_ASSOCIATION_TARGETS,
        "operations": [dict(operation) for operation in operations],
        "blockers": [dict(blocker) for blocker in blockers],
        "mutates_base_rows": {table: False for table in BASE_ROW_TABLES},
    }


def _objects_by_ref(
    envelope: DomainEnvelope,
) -> dict[tuple[str, str], CuratableObjectEnvelope]:
    objects: dict[tuple[str, str], CuratableObjectEnvelope] = {}
    for domain_object in envelope.objects:
        if domain_object.object_id is not None:
            objects[("object_id", domain_object.object_id)] = domain_object
        if domain_object.pending_ref_id is not None:
            objects[("pending_ref_id", domain_object.pending_ref_id)] = domain_object
    return objects


def _referenced_object(
    association: CuratableObjectEnvelope,
    *,
    object_type: str,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> CuratableObjectEnvelope | None:
    objects = _referenced_objects(
        association,
        object_type=object_type,
        objects_by_ref=objects_by_ref,
    )
    return objects[0] if objects else None


def _referenced_objects(
    association: CuratableObjectEnvelope,
    *,
    object_type: str,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> list[CuratableObjectEnvelope]:
    refs = [
        ref
        for ref in association.object_refs
        if ref.object_type is None or ref.object_type == object_type
    ]
    objects = [
        referenced
        for referenced in (_object_for_ref(ref, objects_by_ref) for ref in refs)
        if referenced is not None and referenced.object_type == object_type
    ]
    return objects


def _object_for_ref(
    ref: ObjectRef,
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
) -> CuratableObjectEnvelope | None:
    return objects_by_ref.get(ref.ref_key())


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise ValueError("Domain envelope object is missing object_id and pending_ref_id")


def _db_id(payload: Mapping[str, Any], *field_names: str) -> int | None:
    for field_name in field_names:
        raw_value = payload.get(field_name)
        if raw_value is None:
            continue
        if isinstance(raw_value, bool):
            raise ValueError(f"{field_name} must be an integer identifier")
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip().isdigit():
            return int(raw_value.strip())
        raise ValueError(f"{field_name} must be an integer identifier")
    return None


def _missing_reference_blocker(*, object_id: str, object_type: str) -> dict[str, Any]:
    return _blocker(
        object_id=object_id,
        code="alliance.allele.association_refs_missing",
        message=f"AllelePaperEvidenceAssociation is missing {object_type} object ref.",
        details={"missing_object_type": object_type},
    )


def _blocker(
    *,
    code: str,
    message: str,
    object_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_id": object_id,
        "severity": "blocker",
        "status": "blocked",
        "code": code,
        "message": message,
        "details": dict(details or {}),
    }


__all__ = [
    "ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY",
    "ALLELE_PAPER_EVIDENCE_ASSOCIATION_OBJECT_TYPE",
    "BASE_ROW_TABLES",
    "VERIFIED_ALLELE_ASSOCIATION_TARGETS",
    "build_allele_association_submission_plan",
]
