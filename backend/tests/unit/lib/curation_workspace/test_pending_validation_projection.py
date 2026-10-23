"""Unselected proposals must not become the current curator-facing verdict."""
import pytest

from src.lib.curation_workspace.validation_runtime import domain_envelope_field_validation_results
from src.schemas.domain_envelope import (
    DomainEnvelope, CuratableObjectEnvelope, ValidationFinding, FieldRef, ObjectRef,
)


@pytest.mark.parametrize("status,severity", [("resolved", "info"), ("open", "error")])
def test_pending_proposal_preserves_history_without_changing_current_field_status(status, severity):
    finding = ValidationFinding(
        code="domain_pack.validator_lookup_resolved", message="Unselected proposal",
        severity=severity, status=status,
        field_ref=FieldRef(object_ref=ObjectRef(object_id="one", object_type="entity"), field_path="name"),
        details={"machine_review": "pending", "selected_value": False,
                 "lookup_status": "success", "resolved_id": "NEW:1"},
    )
    envelope = DomainEnvelope(
        envelope_id="pending", domain_pack_id="fixture",
        extracted_objects=[CuratableObjectEnvelope(object_id="one", object_type="entity", payload={"name": "paper"})],
        validation_findings=[finding],
    )
    results, _ = domain_envelope_field_validation_results(
        envelope, envelope_revision=1, object_id="one", field_keys=["name"],
    )
    assert results["name"].status.value == "skipped"
    assert not results["name"].candidate_matches
    assert envelope.validation_findings == [finding]
