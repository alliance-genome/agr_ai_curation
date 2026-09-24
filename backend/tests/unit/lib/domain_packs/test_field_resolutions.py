"""Composite validators decide each value they validate (ALL-1299 ``field_resolutions``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import (
    NOT_VALIDATED_EXPLANATION,
    OUTCOME_MATCHED,
    OUTCOME_NOT_VALIDATED,
    RESOLVED,
    UNRESOLVED,
    unresolved_value,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)
from src.schemas.domain_validator import DomainValidatorResultBase, ValidatorFieldResolution


TERM_KEYS = ("curie", "name")
EXPECTED = {
    "class_curie": "setting.kind.curie",
    "class_name": "setting.kind.name",
    "agent_curie": "setting.agent.curie",
    "host_curie": "setting.host.curie",
    "note": "setting.note",
}


def _metadata() -> DomainPackMetadata:
    paths = ["setting", "setting.kind", "setting.kind.curie", "setting.kind.name", "setting.kind.mention",
             "setting.agent", "setting.agent.curie", "setting.agent.mention",
             "setting.host", "setting.host.curie", "setting.host.mention", "setting.note"]
    return DomainPackMetadata(
        pack_id="fixture.composite",
        display_name="Fixture Composite Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.setting_lookup",
                        "display_name": "Setting lookup",
                        "validator_agent": {"package_id": "fixture.validators", "agent_id": "setting_validator"},
                        "applies_to": {"domain_pack_id": "fixture.composite", "object_types": ["Observation"]},
                        "input_fields": {"mention": {"source": "payload", "path": "setting.kind.mention"}},
                        "expected_result_fields": EXPECTED,
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
                    DomainPackFieldDefinition(
                        field_path=path,
                        field_type=(
                            DomainPackFieldType.OBJECT
                            if path.count(".") < 2 and not path.endswith("note")
                            else DomainPackFieldType.STRING
                        ),
                        # The three values are declared resolvable (ALL-1283).
                        metadata=(
                            {"display": {"label": "name", "id": "curie", "mention": "mention"}}
                            if path in ("setting.kind", "setting.agent", "setting.host")
                            else {}
                        ),
                    )
                    for path in paths
                ],
            )
        ],
    )


def _envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="composite-env",
        domain_pack_id="fixture.composite",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Observation",
                pending_ref_id="observation-1",
                status=CuratableObjectStatus.PENDING,
                payload={
                    "setting": {
                        "kind": unresolved_value("heat shock", identity_keys=TERM_KEYS),
                        "agent": unresolved_value("compound X", identity_keys=("curie",)),
                        "host": unresolved_value("the host", identity_keys=("curie",)),
                    }
                },
            )
        ],
    )


def _item(metadata, envelope, *, status, field_resolutions, resolved_values=None, outcome="not_found"):
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
            pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
        )
    )
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]
    request = build_domain_validation_request(match).request
    assert request is not None
    result = DomainValidatorResultBase.model_validate(
        {
            "status": status,
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
            "resolved_values": resolved_values or {},
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [
                {"provider": "fixture", "method": "search", "query": {}, "result_count": 0, "outcome": outcome}
            ],
            "curator_message": None,
            "explanation": "Overall composite decision.",
            "field_resolutions": field_resolutions,
        }
    )
    return ValidatorResultMaterializationInput(match=match, request=request, result=result)


def _materialize(**kwargs):
    metadata, envelope = _metadata(), _envelope()
    result = materialize_validator_results_into_envelope(envelope, metadata, [_item(metadata, envelope, **kwargs)])
    return result, result.envelope.extracted_objects[0]


def test_each_value_takes_its_own_decision_and_unlisted_values_get_no_write():
    result, patched = _materialize(
        status="unresolved",
        field_resolutions={
            # Keyed by an expected-result field.
            "class_curie": {
                "status": "resolved", "lookup_outcome": "matched",
                "resolved_values": {"class_curie": "ONT:1", "class_name": "heat"},
                "explanation": "Exact class match.",
            },
            # Keyed by the resolvable value's payload path.
            "setting.agent": {
                "status": "unresolved", "lookup_outcome": "not_found",
                "explanation": "No compound matched.", "curator_message": "Check the compound name.",
            },
        },
    )
    setting = patched.payload["setting"]
    assert setting["kind"] == {
        "curie": "ONT:1", "name": "heat", "mention": "heat shock",
        "resolution_state": RESOLVED, "lookup_outcome": OUTCOME_MATCHED,
        "validator_explanation": "Exact class match.", "validator_curator_message": None,
    }
    assert setting["agent"] == {
        "curie": None, "mention": "compound X",
        "resolution_state": UNRESOLVED, "lookup_outcome": "not_found",
        "validator_explanation": "No compound matched.",
        "validator_curator_message": "Check the compound name.",
    }
    # The overall unresolved status does not touch a value without its own decision.
    assert setting["host"] == unresolved_value("the host", identity_keys=("curie",))
    assert setting["host"]["validator_explanation"] == NOT_VALIDATED_EXPLANATION
    event = patched.metadata["validator_resolved_value_materialization"][-1]
    assert event["materialized_field_paths"] == ["setting.kind.curie", "setting.kind.name"]

    by_path = {
        finding.field_ref.field_path: finding
        for finding in result.appended_findings
        if finding.field_ref is not None
    }
    # The unlisted host value gets no finding; the plain note keeps the overall one.
    assert set(by_path) == {"setting.kind.curie", "setting.kind.name", "setting.agent.curie", "setting.note"}
    assert by_path["setting.note"].status.value == "open"
    assert by_path["setting.kind.curie"].status.value == "resolved"
    assert "failure_classification" not in by_path["setting.kind.curie"].details
    agent = by_path["setting.agent.curie"]
    assert agent.status.value == "open"
    assert agent.details["failure_classification"] == setting["agent"]["lookup_outcome"]


def test_an_overall_resolved_result_never_overwrites_a_value_with_its_own_decision():
    _result, patched = _materialize(
        status="resolved",
        outcome="success",
        resolved_values={"class_curie": "ONT:9", "class_name": "wrong", "note": "kept"},
        field_resolutions={
            "setting.kind": {"status": "unresolved", "lookup_outcome": "rejected_candidates",
                             "explanation": "No class fits."},
        },
    )
    setting = patched.payload["setting"]
    assert setting["kind"]["curie"] is None
    assert setting["kind"]["lookup_outcome"] == "rejected_candidates"
    # Plain fields still take the overall resolved values.
    assert setting["note"] == "kept"
    assert setting["agent"]["lookup_outcome"] == OUTCOME_NOT_VALIDATED


def test_a_resolved_decision_missing_one_of_its_values_stays_unresolved():
    _result, patched = _materialize(
        status="resolved",
        outcome="success",
        field_resolutions={
            "class_curie": {"status": "resolved", "lookup_outcome": "matched",
                            "resolved_values": {"class_curie": "ONT:1"}},
        },
    )
    kind = patched.payload["setting"]["kind"]
    assert (kind["resolution_state"], kind["lookup_outcome"], kind["curie"]) == (
        UNRESOLVED, "missing_expected_result_field", None)


@pytest.mark.parametrize("key", ["unknown_field", "setting.note", "setting.elsewhere"])
def test_a_decision_for_a_value_the_binding_does_not_write_is_a_materialization_problem(key):
    result, patched = _materialize(
        status="unresolved",
        field_resolutions={key: {"status": "unresolved", "lookup_outcome": "not_found"}},
    )
    assert patched.payload == _envelope().extracted_objects[0].payload
    [finding] = result.appended_findings
    assert finding.code == "domain_pack.validator_materialization_invalid"
    assert "names no resolvable value" in finding.details["materialization_error"]


def test_two_decisions_for_one_value_are_rejected():
    result, _patched = _materialize(
        status="unresolved",
        field_resolutions={
            "class_curie": {"status": "unresolved", "lookup_outcome": "not_found"},
            "setting.kind": {"status": "unresolved", "lookup_outcome": "ambiguous"},
        },
    )
    assert "more than once" in result.appended_findings[0].details["materialization_error"]


@pytest.mark.parametrize(
    "resolution",
    [
        {"status": "unresolved", "lookup_outcome": "nothing_like_this"},
        {"status": "unresolved", "lookup_outcome": "matched"},
        {"status": "unresolved", "lookup_outcome": "legacy_unverified"},
        {"status": "resolved", "lookup_outcome": "not_found", "resolved_values": {"a": "b"}},
        {"status": "unresolved", "lookup_outcome": "not_found", "resolved_values": {"a": "b"}},
    ],
)
def test_field_resolutions_use_the_lookup_outcome_vocabulary(resolution):
    with pytest.raises(ValidationError):
        ValidatorFieldResolution.model_validate(resolution)


def test_each_resolved_decision_obeys_the_allowed_term_list():
    metadata, envelope = _metadata(), _envelope()
    item = _item(
        metadata, envelope, status="unresolved",
        field_resolutions={
            "class_curie": {"status": "resolved", "lookup_outcome": "matched",
                            "resolved_values": {"class_curie": "ONT:1", "class_name": "heat"}},
            "setting.agent": {"status": "unresolved", "lookup_outcome": "not_found"},
        },
    )
    request = item.request.model_copy(
        update={"selected_inputs": {**item.request.selected_inputs, "allowed_term_curies": ["ONT:9"]}}
    )
    result = materialize_validator_results_into_envelope(
        envelope, metadata, [ValidatorResultMaterializationInput(match=item.match, request=request,
                                                                 result=item.result)],
    )

    setting = result.envelope.extracted_objects[0].payload["setting"]
    assert (setting["kind"]["resolution_state"], setting["kind"]["lookup_outcome"], setting["kind"]["curie"]) == (
        UNRESOLVED, "invalid_schema", None)
    # The other decision is still written, and the violation is reported.
    assert setting["agent"]["lookup_outcome"] == "not_found"
    [finding] = [f for f in result.appended_findings if f.code == "domain_pack.validator_materialization_invalid"]
    assert "allowed term list" in finding.details["materialization_error"]


def _binding_finding(result):
    [finding] = [f for f in result.appended_findings if f.field_ref is None or f.field_ref.field_path == "setting"
                 if f.code in ("domain_pack.validator_unresolved", "domain_pack.curator_override")]
    return finding


def test_a_composite_result_is_settled_when_its_unresolved_values_are_overridden_or_absent():
    """B3: a curator override of the one value a composite validator cannot find clears its blocker.

    The absent host value (no decision for it) does not keep the binding open.
    """

    from src.lib.domain_packs.resolvable_values import apply_curator_identity

    metadata = _metadata()
    envelope = _envelope()
    setting = envelope.extracted_objects[0].payload["setting"]
    apply_curator_identity(setting["kind"], {"curie": "ONT:9", "name": "heat"}, identity_keys=TERM_KEYS,
                           id_key="curie", label_key="name", actor_id="curator-1", actor_display_name="curator-1", at="2026-09-24T00:00:00+00:00")
    del setting["host"]
    decisions = {
        "setting.kind": {"status": "unresolved", "lookup_outcome": "not_found", "explanation": "No class."},
        "setting.agent": {"status": "resolved", "lookup_outcome": "matched",
                          "resolved_values": {"agent_curie": "CHEM:1"}, "explanation": "Found."},
    }

    settled = materialize_validator_results_into_envelope(envelope, metadata, [
        _item(metadata, envelope, status="unresolved", field_resolutions=decisions)])
    assert _binding_finding(settled).code == "domain_pack.curator_override"

    # A present value it did not resolve and no override covers still blocks.
    decisions["setting.agent"] = {"status": "unresolved", "lookup_outcome": "not_found", "explanation": "No."}
    open_result = materialize_validator_results_into_envelope(envelope, metadata, [
        _item(metadata, envelope, status="unresolved", field_resolutions=decisions)])
    assert _binding_finding(open_result).code == "domain_pack.validator_unresolved"


def test_an_override_settles_a_composite_blocker_at_patch_time():
    """Fix wave S2: the patch resolves the binding finding by the rule the materializer uses."""

    from src.lib.domain_envelopes.patches import (
        EnvelopeFieldPatch, EnvelopeFieldPatchOperation, apply_curator_field_patch,
    )
    from src.schemas.domain_envelope import ValidationFindingStatus

    metadata = _metadata()
    definition = metadata.object_definitions[0]
    editable = {"setting.kind.curie", "setting.kind.name"}
    metadata = metadata.model_copy(update={"object_definitions": [definition.model_copy(update={"fields": [
        field.model_copy(update={"metadata": {**field.metadata, "editable": True}})
        if field.field_path in editable else field
        for field in definition.fields
    ]})]})
    envelope = _envelope()
    decisions = {
        "setting.kind": {"status": "unresolved", "lookup_outcome": "not_found", "explanation": "No class."},
        "setting.agent": {"status": "resolved", "lookup_outcome": "matched",
                          "resolved_values": {"agent_curie": "CHEM:1"}, "explanation": "Found."},
        "setting.host": {"status": "resolved", "lookup_outcome": "matched",
                         "resolved_values": {"host_curie": "H:1"}, "explanation": "Found."},
    }
    validated = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", field_resolutions=decisions)],
    ).envelope

    def binding_status(envelope):
        [finding] = [f for f in envelope.validation_findings if f.code == "domain_pack.validator_unresolved"
                     and (f.field_ref is None or f.field_ref.field_path == "setting")]
        return finding.status

    assert binding_status(validated) is ValidationFindingStatus.OPEN
    pack = LoadedDomainPack(pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
                            pack_path=Path("."), metadata_path=Path("."), metadata=metadata)
    patched = apply_curator_field_patch(
        validated, pack,
        EnvelopeFieldPatch(envelope_id=validated.envelope_id, expected_revision=1, object_id="observation-1",
                           field_path="setting.kind.curie", before={"curie": None, "name": None},
                           value={"curie": "ONT:9", "name": "heat"},
                           operation=EnvelopeFieldPatchOperation.REPLACE_IDENTITY),
        current_revision=1, actor_id="curator-1", actor_display_name="Curator One",
    )
    assert patched.accepted, patched.errors
    assert binding_status(patched.envelope) is ValidationFindingStatus.RESOLVED
