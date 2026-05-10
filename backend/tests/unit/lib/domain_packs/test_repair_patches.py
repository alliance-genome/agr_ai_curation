"""Unit tests for validation-driven domain-envelope repair patches."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.repair_patches import (
    REPAIR_CONTEXT_METADATA_KEY,
    DomainEnvelopeRepairPatch,
    RepairFinalClassification,
    RepairFinalStatus,
    RepairPatchStatus,
    apply_repair_patch,
    build_repair_request,
    record_repair_final_classification,
    record_repair_request,
    record_validator_rerun_request,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
)


def _pack_text() -> str:
    return """
pack_id: fixture.repair
display_name: Fixture Repair Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    fields:
      - field_path: gene.symbol
        field_type: string
        required: true
        metadata:
          repairable: true
      - field_path: gene.identifier
        field_type: string
        required: true
        metadata:
          editable: true
      - field_path: protected_note
        field_type: string
        metadata:
          repairable: true
          protected: true
      - field_path: stable_note
        field_type: string
      - field_path: draft_value
        field_type: string
        definition_state: in_development
""".strip()


def _loaded_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.repair"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _object_ref() -> ObjectRef:
    return ObjectRef(pending_ref_id="gene-assertion-1", object_type="GeneAssertion")


def _envelope(*, metadata: dict | None = None) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.repair",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="gene-assertion-1",
                payload={
                    "gene": {"symbol": "abc-1"},
                    "protected_note": "do not touch",
                    "stable_note": "fixed",
                    "draft_value": "draft",
                },
            )
        ],
        metadata=metadata or {},
    )


def _finding() -> ValidationFinding:
    return ValidationFinding(
        finding_id="validation:symbol",
        severity=ValidationFindingSeverity.ERROR,
        code="fixture.symbol_mismatch",
        message="Gene symbol does not match validator result.",
        field_ref=FieldRef(object_ref=_object_ref(), field_path="gene.symbol"),
    )


def _patch(field_path: str, *, before: object, after: object) -> DomainEnvelopeRepairPatch:
    return DomainEnvelopeRepairPatch(
        patch_id="repair-patch:test",
        envelope_id="env-1",
        expected_revision=1,
        source_finding_ids=["validation:symbol"],
        operations=[
            {
                "object_ref": _object_ref(),
                "field_path": field_path,
                "expected_before": before,
                "after": after,
                "reason": "Validator supplied a better grounded value.",
            }
        ],
        rationale="Bounded validator repair.",
    )


def test_build_and_record_repair_request_tracks_budget_and_context(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    request = build_repair_request(
        _envelope(),
        pack,
        findings=[_finding()],
        expected_revision=1,
    )

    assert request.repair_action == "repair_request"
    assert request.targets[0].field_path == "gene.symbol"
    assert request.targets[0].current_value == "abc-1"
    assert request.targets[0].repairable is True
    assert request.targets[0].retry_budget.remaining_attempts == 2

    updated = record_repair_request(_envelope(), request)

    assert updated.history[-1].event_type is HistoryEventKind.REPAIR_REQUESTED
    assert updated.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == "requested"
    assert "Repair requested" in updated.metadata[REPAIR_CONTEXT_METADATA_KEY][
        "latest_chat_summary"
    ]


def test_apply_repair_patch_accepts_repairable_field_and_records_history(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch("gene.symbol", before="abc-1", after="abc-2"),
        current_revision=1,
    )

    assert result.accepted is True
    assert result.status is RepairPatchStatus.ACCEPTED
    assert result.envelope.objects[0].payload["gene"]["symbol"] == "abc-2"
    assert [event.event_type for event in result.envelope.history] == [
        HistoryEventKind.FIELD_UPDATED,
        HistoryEventKind.REPAIR_PATCH_ACCEPTED,
    ]
    assert result.envelope.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == (
        "accepted"
    )
    assert result.retry_budget.used_attempts == 1


def test_apply_repair_patch_can_fill_missing_repairable_required_field(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch("gene.identifier", before=None, after="AGR:0000001"),
        current_revision=1,
    )

    assert result.accepted is True
    assert result.envelope.objects[0].payload["gene"]["identifier"] == "AGR:0000001"


@pytest.mark.parametrize("field_path", ["protected_note", "stable_note"])
def test_apply_repair_patch_rejects_protected_or_uneditable_fields(
    tmp_path: Path,
    field_path: str,
):
    pack = _loaded_pack(tmp_path)

    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch(field_path, before="do not touch" if field_path == "protected_note" else "fixed", after="new"),
        current_revision=1,
    )

    assert result.accepted is False
    assert result.status is RepairPatchStatus.REJECTED
    assert result.envelope.objects[0].payload.get(field_path) in {"do not touch", "fixed"}
    assert result.envelope.history[-1].event_type is HistoryEventKind.REPAIR_PATCH_REJECTED
    assert result.envelope.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == "rejected"


def test_apply_repair_patch_rejects_expected_before_mismatch(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch("gene.symbol", before="stale-symbol", after="abc-2"),
        current_revision=1,
    )

    assert result.status is RepairPatchStatus.REJECTED
    assert result.envelope.objects[0].payload["gene"]["symbol"] == "abc-1"
    assert "expected_before" in result.errors[0]


def test_apply_repair_patch_rejects_stale_revision_without_consuming_retry(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch("gene.symbol", before="abc-1", after="abc-2"),
        current_revision=2,
    )

    assert result.status is RepairPatchStatus.STALE_REVISION
    assert result.retry_budget.used_attempts == 0
    assert result.retry_budget.remaining_attempts == 2
    assert result.envelope.objects[0].payload["gene"]["symbol"] == "abc-1"


def test_apply_repair_patch_retry_exhaustion_records_final_classification(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    envelope = _envelope()
    bad_patch = _patch("gene.symbol", before="stale-symbol", after="abc-2")

    first = apply_repair_patch(envelope, pack, bad_patch, current_revision=1, max_attempts=2)
    second = apply_repair_patch(
        first.envelope,
        pack,
        bad_patch,
        current_revision=1,
        max_attempts=2,
    )
    exhausted = apply_repair_patch(
        second.envelope,
        pack,
        bad_patch,
        current_revision=1,
        max_attempts=2,
    )

    assert exhausted.status is RepairPatchStatus.RETRY_EXHAUSTED
    assert exhausted.retry_budget.exhausted is True
    assert exhausted.envelope.history[-1].event_type is HistoryEventKind.REPAIR_FINAL_CLASSIFIED
    assert exhausted.envelope.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == (
        "retry_exhausted"
    )
    assert exhausted.envelope.metadata[REPAIR_CONTEXT_METADATA_KEY]["classifications"][
        -1
    ]["status"] == "retry_exhausted"


@pytest.mark.parametrize(
    "status",
    [
        RepairFinalStatus.NO_REPAIR_POSSIBLE,
        RepairFinalStatus.UNDER_DEVELOPMENT,
        RepairFinalStatus.TRUE_NOT_FOUND,
        RepairFinalStatus.TRANSIENT_SERVICE_FAILURE,
        RepairFinalStatus.BLOCKED_VALIDATOR,
    ],
)
def test_final_classifications_are_visible_in_history_and_context(
    status: RepairFinalStatus,
):
    updated = record_repair_final_classification(
        _envelope(),
        RepairFinalClassification(
            repair_action=(
                "mark_under_development"
                if status is RepairFinalStatus.UNDER_DEVELOPMENT
                else "no_repair_possible"
                if status is RepairFinalStatus.NO_REPAIR_POSSIBLE
                else "final_classification"
            ),
            envelope_id="env-1",
            expected_revision=1,
            status=status,
            reason=f"Classified as {status.value}.",
            finding_ids=["validation:symbol"],
            object_ref=_object_ref(),
            field_path="gene.symbol",
        ),
    )

    assert updated.history[-1].event_type is HistoryEventKind.REPAIR_FINAL_CLASSIFIED
    assert updated.history[-1].details["status"] == status.value
    assert updated.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == status.value


def test_validator_rerun_request_uses_dedicated_history_event():
    updated = record_validator_rerun_request(
        _envelope(),
        RepairFinalClassification(
            repair_action="validator_rerun",
            envelope_id="env-1",
            expected_revision=2,
            status=RepairFinalStatus.VALIDATOR_RERUN_REQUESTED,
            reason="Patch accepted; rerun validator.",
            finding_ids=["validation:symbol"],
            validator_binding_ids=["fixture.symbol_validator"],
            object_ref=_object_ref(),
            field_path="gene.symbol",
        ),
    )

    assert updated.history[-1].event_type is HistoryEventKind.VALIDATION_RERUN_REQUESTED
    assert updated.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == (
        "validator_rerun_requested"
    )
