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


def test_supervisor_uncertainty_is_explicit_and_blocks_submission_until_settled():
    from src.lib.curation_workspace.session_submission_service import _finding_blocks_readiness
    finding = ValidationFinding(code="validation_review.assessment_unresolved", message="Use not established",
        severity="warning", field_ref=FieldRef(object_ref=ObjectRef(object_id="one", object_type="entity"), field_path="name"))
    envelope = DomainEnvelope(envelope_id="reviewed", domain_pack_id="fixture",
        extracted_objects=[CuratableObjectEnvelope(object_id="one", object_type="entity", payload={"name": "paper"})],
        validation_findings=[finding])
    results, _ = domain_envelope_field_validation_results(envelope, envelope_revision=1, object_id="one", field_keys=["name"])
    assert results["name"].status.value == "unresolved"
    assert results["name"].warnings == ["Use not established"]
    assert _finding_blocks_readiness(finding)
    from src.lib.curation_workspace.session_submission_service import _validation_finding_blockers
    from src.schemas.domain_envelope import ValidationFindingStatus
    args = {"object_id": "one", "object_id_by_ref": {("object_id", "one"): "one"},
            "projection_ref": {"envelope_id": "reviewed", "envelope_revision": 1, "object_id": "one"}}
    assert len(_validation_finding_blockers(envelope=envelope, **args)) == 1
    resolved = envelope.model_copy(update={"validation_findings": [finding.model_copy(update={"status": ValidationFindingStatus.RESOLVED})]})
    assert _validation_finding_blockers(envelope=resolved, **args) == []
