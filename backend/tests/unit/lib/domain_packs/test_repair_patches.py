"""Unit tests for validation-driven domain-envelope repair patches."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.repair_patches import (
    REPAIR_CONTEXT_METADATA_KEY,
    DomainEnvelopeExtractorFinalClassification,
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


REPO_ROOT = Path(__file__).resolve().parents[5]


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
      - field_path: ungrounded_name
        field_type: string
        metadata:
          repair:
            repairable: true
            source_of_truth: fixture_source
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


def _loaded_alliance_pack(relative_path: str) -> LoadedDomainPack:
    metadata_path = REPO_ROOT / relative_path
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=metadata_path.parent,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _field_metadata(
    pack: LoadedDomainPack,
    *,
    object_type: str,
    field_path: str,
) -> dict:
    object_definition = next(
        item for item in pack.metadata.object_definitions if item.object_type == object_type
    )
    field_definition = next(
        item for item in object_definition.fields if item.field_path == field_path
    )
    return field_definition.metadata


def _repair_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    repair_metadata = metadata.get("repair")
    if isinstance(repair_metadata, Mapping):
        return repair_metadata
    return metadata


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
                    "ungrounded_name": "Display value",
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


def _identifier_finding() -> ValidationFinding:
    return ValidationFinding(
        finding_id="validation:identifier",
        severity=ValidationFindingSeverity.ERROR,
        code="fixture.identifier_missing",
        message="Gene identifier is missing.",
        field_ref=FieldRef(object_ref=_object_ref(), field_path="gene.identifier"),
    )


def _ungrounded_finding() -> ValidationFinding:
    return ValidationFinding(
        finding_id="validation:ungrounded-name",
        severity=ValidationFindingSeverity.ERROR,
        code="fixture.ungrounded_name_mismatch",
        message="Display value lacks source-of-truth grounding.",
        field_ref=FieldRef(object_ref=_object_ref(), field_path="ungrounded_name"),
    )


def _alliance_envelope(object_type: str) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-alliance",
        domain_pack_id="agr.alliance.fixture",
        objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                pending_ref_id="alliance-object-1",
                payload={},
            )
        ],
    )


def _alliance_finding(object_type: str, field_path: str) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"validation:{object_type}:{field_path}",
        severity=ValidationFindingSeverity.ERROR,
        code="alliance.fixture",
        message="Alliance field needs repair.",
        field_ref=FieldRef(
            object_ref=ObjectRef(
                pending_ref_id="alliance-object-1",
                object_type=object_type,
            ),
            field_path=field_path,
        ),
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


@pytest.mark.parametrize(
    ("pack_path", "object_type", "field_path"),
    [
        (
            "packages/alliance/domain_packs/gene/domain_pack.yaml",
            "gene_mention_evidence",
            "primary_external_id",
        ),
        (
            "packages/alliance/domain_packs/disease/domain_pack.yaml",
            "DiseaseAnnotation",
            "disease_annotation_object.curie",
        ),
        (
            "packages/alliance/domain_packs/chemical_condition/domain_pack.yaml",
            "ChemicalCondition",
            "condition_chemical.curie",
        ),
        (
            "packages/alliance/domain_packs/allele/domain_pack.yaml",
            "AllelePaperEvidenceAssociation",
            "allele_identifier",
        ),
        (
            "packages/alliance/domain_packs/phenotype/domain_pack.yaml",
            "PhenotypeAnnotation",
            "phenotype_terms[0].curie",
        ),
        (
            "packages/alliance/domain_packs/gene_expression/domain_pack.yaml",
            "GeneExpressionAnnotation",
            "expression_annotation_subject.primary_external_id",
        ),
    ],
)
def test_alliance_repairable_fields_are_declared_in_domain_pack_metadata(
    pack_path: str,
    object_type: str,
    field_path: str,
):
    pack = _loaded_alliance_pack(pack_path)
    request = build_repair_request(
        _alliance_envelope(object_type),
        pack,
        findings=[_alliance_finding(object_type, field_path)],
        expected_revision=1,
    )

    assert request.targets[0].repairable is True
    repair_metadata = _field_metadata(
        pack,
        object_type=object_type,
        field_path=field_path,
    )["repair"]
    assert repair_metadata["repairable"] is True
    assert repair_metadata["source_of_truth"] == "alliance_linkml"


def test_source_of_truth_repair_targets_require_provider_ref_grounding(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    request = build_repair_request(
        _envelope(),
        pack,
        findings=[_ungrounded_finding()],
        expected_revision=1,
    )
    result = apply_repair_patch(
        _envelope(),
        pack,
        _patch(
            "ungrounded_name",
            before="Display value",
            after="Updated display value",
        ),
        current_revision=1,
    )

    field_policy = request.targets[0].metadata["field_policy"]
    assert request.targets[0].repairable is False
    assert field_policy["repairable"] is False
    assert field_policy["declared_repairable"] is True
    assert field_policy["source_of_truth"] == "fixture_source"
    assert field_policy["provider_ref_grounded"] is False
    assert "metadata.provider_refs.fixture_source" in field_policy["blocked_reason"]
    assert result.status is RepairPatchStatus.REJECTED
    assert any(
        "metadata.provider_refs.fixture_source" in error
        for error in result.errors
    )


def test_alliance_repairable_source_of_truth_fields_have_provider_refs():
    failures: list[str] = []
    for metadata_path in sorted(
        (REPO_ROOT / "packages/alliance/domain_packs").glob("*/domain_pack.yaml")
    ):
        pack = _loaded_alliance_pack(str(metadata_path.relative_to(REPO_ROOT)))
        for object_definition in pack.metadata.object_definitions:
            for field_definition in object_definition.fields:
                metadata = field_definition.metadata
                repair_metadata = _repair_metadata(metadata)
                source_of_truth = repair_metadata.get("source_of_truth")
                if not isinstance(source_of_truth, str):
                    continue
                if not (
                    repair_metadata.get("repairable") is True
                    or repair_metadata.get("editable") is True
                ):
                    continue
                provider_refs = metadata.get("provider_refs")
                if not (
                    isinstance(provider_refs, Mapping)
                    and isinstance(provider_refs.get(source_of_truth), Mapping)
                ):
                    failures.append(
                        f"{metadata_path.relative_to(REPO_ROOT)} "
                        f"{object_definition.object_type}.{field_definition.field_path} "
                        f"missing metadata.provider_refs.{source_of_truth}"
                    )

    assert failures == []


def test_phenotype_term_label_is_not_a_linkml_repair_target():
    object_type = "PhenotypeAnnotation"
    field_path = "phenotype_terms[0].label"
    pack = _loaded_alliance_pack(
        "packages/alliance/domain_packs/phenotype/domain_pack.yaml"
    )

    request = build_repair_request(
        _alliance_envelope(object_type),
        pack,
        findings=[_alliance_finding(object_type, field_path)],
        expected_revision=1,
    )

    field_policy = request.targets[0].metadata["field_policy"]
    assert request.targets[0].repairable is False
    assert field_policy["protected"] is True
    assert field_policy["declared_repairable"] is False


@pytest.mark.parametrize(
    ("pack_path", "object_type", "field_path"),
    [
        (
            "packages/alliance/domain_packs/gene/domain_pack.yaml",
            "gene_mention_evidence",
            "mention",
        ),
        (
            "packages/alliance/domain_packs/disease/domain_pack.yaml",
            "DiseaseAnnotation",
            "disease_annotation_subject.subject_identifier",
        ),
        (
            "packages/alliance/domain_packs/chemical_condition/domain_pack.yaml",
            "ChemicalCondition",
            "source_chemical_mention",
        ),
        (
            "packages/alliance/domain_packs/allele/domain_pack.yaml",
            "AllelePaperEvidenceAssociation",
            "association_kind",
        ),
        (
            "packages/alliance/domain_packs/phenotype/domain_pack.yaml",
            "PhenotypeAnnotation",
            "annotation_kind",
        ),
        (
            "packages/alliance/domain_packs/gene_expression/domain_pack.yaml",
            "GeneExpressionAnnotation",
            "unique_id",
        ),
    ],
)
def test_alliance_protected_fields_are_rejected_by_repair_patch_validation(
    pack_path: str,
    object_type: str,
    field_path: str,
):
    pack = _loaded_alliance_pack(pack_path)
    patch = DomainEnvelopeRepairPatch(
        patch_id="repair-patch:protected-alliance",
        envelope_id="env-alliance",
        expected_revision=1,
        operations=[
            {
                "object_ref": {
                    "pending_ref_id": "alliance-object-1",
                    "object_type": object_type,
                },
                "field_path": field_path,
                "expected_before": None,
                "after": "new-value",
                "reason": "Attempted patch of a protected field.",
            }
        ],
        rationale="Protected field test.",
    )

    result = apply_repair_patch(
        _alliance_envelope(object_type),
        pack,
        patch,
        current_revision=1,
    )

    assert result.status is RepairPatchStatus.REJECTED
    assert any("protected" in error for error in result.errors)


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


def test_rejected_multi_target_patch_consumes_budget_for_each_target(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    envelope = _envelope().model_copy(
        update={"validation_findings": [_finding(), _identifier_finding()]}
    )
    patch = DomainEnvelopeRepairPatch(
        patch_id="repair-patch:multi-target",
        envelope_id="env-1",
        expected_revision=1,
        source_finding_ids=["validation:symbol", "validation:identifier"],
        operations=[
            {
                "object_ref": _object_ref(),
                "field_path": "gene.symbol",
                "expected_before": "stale-symbol",
                "after": "abc-2",
                "reason": "Try the validator-proposed symbol.",
            },
            {
                "object_ref": _object_ref(),
                "field_path": "gene.identifier",
                "expected_before": None,
                "after": "AGR:0000001",
                "reason": "Fill the missing validator-proposed identifier.",
            },
        ],
        rationale="Multi-target validator repair.",
    )

    before_request = build_repair_request(
        envelope,
        pack,
        findings=envelope.validation_findings,
        expected_revision=1,
    )
    result = apply_repair_patch(
        envelope,
        pack,
        patch,
        current_revision=1,
        max_attempts=2,
    )
    after_request = build_repair_request(
        result.envelope,
        pack,
        findings=result.envelope.validation_findings,
        expected_revision=1,
    )

    before_budget = [
        (target.finding_id, target.retry_budget.used_attempts)
        for target in before_request.targets
    ]
    assert before_budget == [
        ("validation:symbol", 0),
        ("validation:identifier", 0),
    ]
    assert result.status is RepairPatchStatus.REJECTED
    assert result.retry_budget.used_attempts == 1
    assert [
        (
            target.finding_id,
            target.retry_budget.used_attempts,
            target.retry_budget.remaining_attempts,
        )
        for target in after_request.targets
    ] == [
        ("validation:symbol", 1, 1),
        ("validation:identifier", 1, 1),
    ]

    attempt = result.envelope.metadata[REPAIR_CONTEXT_METADATA_KEY]["attempts"][-1]
    assert attempt["retry_consumed"] is True
    assert len(attempt["target_retry_keys"]) == 2
    assert set(attempt["target_retry_keys"]).issubset(set(attempt["retry_keys"]))
    assert {
        budget["used_attempts"]
        for budget in attempt["retry_budgets_by_key"].values()
    } == {1}


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
    assert updated.history[-1].field_ref is not None
    assert updated.history[-1].details["status"] == status.value
    assert updated.metadata[REPAIR_CONTEXT_METADATA_KEY]["latest_status"] == status.value


def test_missing_field_final_classification_round_trips_and_keeps_target_context():
    updated = record_repair_final_classification(
        _envelope(),
        RepairFinalClassification(
            repair_action="no_repair_possible",
            envelope_id="env-1",
            expected_revision=1,
            status=RepairFinalStatus.NO_REPAIR_POSSIBLE,
            reason="Identifier could not be repaired from available evidence.",
            finding_ids=["validation:identifier"],
            object_ref=_object_ref(),
            field_path="gene.identifier",
        ),
    )

    event = updated.history[-1]
    assert event.event_type is HistoryEventKind.REPAIR_FINAL_CLASSIFIED
    assert event.object_ref == _object_ref()
    assert event.field_ref is None
    assert event.details["field_path"] == "gene.identifier"

    latest_classification = updated.metadata[REPAIR_CONTEXT_METADATA_KEY][
        "classifications"
    ][-1]
    assert latest_classification["object_ref"] == _object_ref().model_dump(mode="json")
    assert latest_classification["field_path"] == "gene.identifier"

    reparsed = DomainEnvelope.model_validate(updated.model_dump(mode="json"))
    assert reparsed.history[-1].details["field_path"] == "gene.identifier"


def test_extractor_final_classification_rejects_action_status_mismatch():
    with pytest.raises(ValueError, match="mark_under_development"):
        DomainEnvelopeExtractorFinalClassification(
            repair_action="mark_under_development",
            envelope_id="env-1",
            expected_revision=1,
            status=RepairFinalStatus.NO_REPAIR_POSSIBLE,
            reason="Mismatched status must not be accepted.",
        )


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
