"""An object a package validator marks not validatable gets that one finding only."""

from __future__ import annotations

from pathlib import Path

from src.lib.domain_packs.not_validatable import (
    NOT_VALIDATABLE_DETAIL_KEY,
    not_validatable_object_keys,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


def _pack() -> LoadedDomainPack:
    metadata = DomainPackMetadata(
        pack_id="fixture.flagged",
        display_name="Fixture",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.term_lookup",
                        "display_name": "Term lookup",
                        "validator_agent": {"package_id": "fixture.validators", "agent_id": "term_validator"},
                        "applies_to": {"domain_pack_id": "fixture.flagged", "object_types": ["Observation"]},
                        "input_fields": {"label": {"source": "payload", "path": "term.label"}},
                        "expected_result_fields": {"curie": "term.curie"},
                    }
                ],
                "under_development": [],
            }
        },
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Observation",
                display_name="Observation",
                metadata={"object_role": "curatable_unit"},
                fields=[
                    DomainPackFieldDefinition(field_path="term", field_type=DomainPackFieldType.OBJECT),
                    DomainPackFieldDefinition(field_path="term.label", field_type=DomainPackFieldType.STRING,
                                              required=True),
                    DomainPackFieldDefinition(field_path="term.curie", field_type=DomainPackFieldType.STRING),
                ],
            )
        ],
    )
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def _envelope(*, flagged_status=ValidationFindingStatus.OPEN) -> DomainEnvelope:
    # Both objects are in an old format the pack no longer reads: no term.label.
    return DomainEnvelope(
        envelope_id="flagged-env",
        domain_pack_id="fixture.flagged",
        extracted_objects=[
            CuratableObjectEnvelope(object_type="Observation", object_id="old-1", payload={"old_term": "x"}),
            CuratableObjectEnvelope(object_type="Observation", object_id="old-2", payload={"old_term": "y"}),
        ],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                status=flagged_status,
                code="fixture.previous_format",
                message="This record uses a previous format and cannot be validated.",
                object_ref=ObjectRef(object_id="old-1", object_type="Observation"),
                details={NOT_VALIDATABLE_DETAIL_KEY: True, "previous_format": "v1"},
            )
        ],
    )


def _object_ids(findings):
    ids = set()
    for finding in findings:
        ref = finding.object_ref or (finding.field_ref.object_ref if finding.field_ref else None)
        if ref is not None:
            ids.add(ref.object_id)
    return ids


def test_open_flag_findings_name_the_objects():
    assert not_validatable_object_keys(_envelope()) == {("object_id", "old-1")}
    assert not_validatable_object_keys(_envelope(flagged_status=ValidationFindingStatus.RESOLVED)) == set()


def test_structural_checks_skip_a_not_validatable_object():
    result = run_domain_envelope_structural_checks(_envelope(), _pack())
    assert _object_ids(result.appended_findings) == {"old-2"}


def test_dispatch_skips_a_not_validatable_object():
    calls = []

    def runner(request, *, binding):
        calls.append(request)
        raise AssertionError("no validator should run: neither object selects a label")

    result = dispatch_active_validator_bindings(_envelope(), _pack(), runner=runner)

    assert calls == []
    # Only the unflagged object reports its missing selector input.
    assert _object_ids(result.appended_findings) == {"old-2"}


def test_a_resolved_flag_no_longer_skips_the_object():
    result = run_domain_envelope_structural_checks(
        _envelope(flagged_status=ValidationFindingStatus.RESOLVED), _pack(),
    )
    assert _object_ids(result.appended_findings) == {"old-1", "old-2"}
