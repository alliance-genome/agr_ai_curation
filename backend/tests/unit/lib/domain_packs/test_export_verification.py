"""Tests for shared runtime-validator export verification."""

from src.lib.domain_packs.export_verification import (
    runtime_validator_resolved_object_ids,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)


def _finding(
    *,
    binding_id: str,
    pending_ref_id: str,
    code: str = "domain_pack.validator_resolved",
    status: ValidationFindingStatus = ValidationFindingStatus.RESOLVED,
) -> ValidationFinding:
    return ValidationFinding(
        severity=ValidationFindingSeverity.INFO,
        status=status,
        code=code,
        message="Runtime validator result.",
        field_ref=FieldRef(
            object_ref=ObjectRef(pending_ref_id=pending_ref_id),
            field_path="identifier",
        ),
        details={
            "validation_metadata": {
                "validator_binding_id": binding_id,
                "binding_state": "active",
            }
        },
    )


def test_runtime_validator_resolved_object_ids_matches_code_status_and_binding():
    envelope = DomainEnvelope(
        envelope_id="verification-envelope",
        domain_pack_id="fixture.pack",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="FixtureMention",
                pending_ref_id="mention-1",
            ),
            CuratableObjectEnvelope(
                object_type="FixtureMention",
                pending_ref_id="mention-2",
            ),
        ],
        validation_findings=[
            _finding(binding_id="fixture.intended", pending_ref_id="mention-1"),
            _finding(binding_id="fixture.unrelated", pending_ref_id="mention-2"),
            _finding(
                binding_id="fixture.intended",
                pending_ref_id="mention-2",
                code="fixture.legacy_verified",
            ),
            _finding(
                binding_id="fixture.intended",
                pending_ref_id="mention-2",
                status=ValidationFindingStatus.OPEN,
            ),
        ],
    )

    assert runtime_validator_resolved_object_ids(
        envelope,
        validator_binding_id="fixture.intended",
    ) == {"mention-1"}
