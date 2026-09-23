"""Extracted vs validated values: the shared resolvable-value contract (ALL-1283)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import (
    LEGACY_UNVERIFIED_SUFFIX,
    REASON_LEGACY_UNVERIFIED,
    REASON_NOT_FOUND,
    REASON_NOT_VALIDATED,
    RESOLVED,
    UNRESOLVED,
    ResolvableSpec,
    ResolvableValueError,
    check_resolvable_list,
    check_resolvable_value,
    effective_payload,
    effective_resolution,
    mark_resolved,
    mark_unresolved,
    resolvable_spec_from_display,
    resolved_value,
    unresolved_list,
    unresolved_positions,
    unresolved_value,
    validator_event_covers,
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
from src.schemas.domain_validator import DomainValidatorResultBase


TERM_KEYS = ("curie", "name")


def test_builders_write_mention_state_and_reason():
    value = unresolved_value("residual body structures", identity_keys=TERM_KEYS)
    assert value == {
        "curie": None,
        "name": None,
        "mention": "residual body structures",
        "resolution_state": UNRESOLVED,
        "resolution_reason": REASON_NOT_VALIDATED,
    }
    resolved = resolved_value("skin", {"curie": "ONT:1", "name": "epidermis"}, vocabulary="demo")
    assert resolved == {
        "vocabulary": "demo",
        "curie": "ONT:1",
        "name": "epidermis",
        "mention": "skin",
        "resolution_state": RESOLVED,
        "resolution_reason": None,
    }


@pytest.mark.parametrize(
    "value",
    [
        # Resolved without the identity a validator supplied.
        {"curie": None, "mention": "x", "resolution_state": "resolved", "resolution_reason": None},
        # Resolved with a reason.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "resolved", "resolution_reason": "not_found"},
        # Unresolved but carrying an identity.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "unresolved", "resolution_reason": "not_found"},
        # Unresolved without a stored reason (legacy_unverified is read-time only).
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "resolution_reason": None},
        {"curie": None, "mention": "x", "resolution_state": "unresolved", "resolution_reason": "legacy_unverified"},
        # Any other state.
        {"curie": "ONT:1", "mention": "x", "resolution_state": "validated", "resolution_reason": None},
        # An empty mention.
        {"curie": None, "mention": " ", "resolution_state": "unresolved", "resolution_reason": "not_validated"},
    ],
)
def test_invariant_violations_raise(value):
    with pytest.raises(ResolvableValueError):
        check_resolvable_value(value, identity_keys=TERM_KEYS)


def test_builders_require_mention():
    with pytest.raises(ResolvableValueError, match="paper wording"):
        unresolved_value("", identity_keys=TERM_KEYS)
    with pytest.raises(ResolvableValueError, match="paper wording"):
        resolved_value(None, {"curie": "ONT:1"})


def test_mark_unresolved_never_touches_identity_or_mention():
    value = {"mention": "skin", "curie": None, "name": None,
             "resolution_state": UNRESOLVED, "resolution_reason": REASON_NOT_VALIDATED}
    mark_unresolved(value, REASON_NOT_FOUND)
    assert value == {"mention": "skin", "curie": None, "name": None,
                     "resolution_state": UNRESOLVED, "resolution_reason": REASON_NOT_FOUND}
    mark_resolved(value, {"curie": "ONT:1", "name": "epidermis"})
    assert value["mention"] == "skin"
    assert (value["resolution_state"], value["resolution_reason"]) == (RESOLVED, None)
    # A later failure cannot un-resolve a value an earlier validator resolved.
    mark_unresolved(value, REASON_NOT_FOUND)
    assert value["resolution_state"] == RESOLVED
    with pytest.raises(ResolvableValueError):
        mark_unresolved(value, "legacy_unverified")


def test_list_helpers_keep_each_element_separate():
    values = unresolved_list(["IMP", "IDA"], identity_keys=("curie",))
    mark_resolved(values[0], {"curie": "ECO:1"})
    check_resolvable_list(values, identity_keys=("curie",))
    assert unresolved_positions(values) == [1]
    values[1]["curie"] = "ECO:2"
    with pytest.raises(ResolvableValueError, match="element 1"):
        check_resolvable_list(values, identity_keys=("curie",))


def test_display_spec_mention_role_declares_a_resolvable_value():
    assert resolvable_spec_from_display({"label": "name", "id": "curie"}) is None
    spec = resolvable_spec_from_display({"label": "name", "id": "curie", "mention": "mention"})
    assert spec == ResolvableSpec(id_key="curie", label_key="name")
    assert spec.identity_keys == ("curie", "name")


def test_legacy_values_are_resolved_only_with_an_identity_and_a_covering_event():
    metadata = {"validator_resolved_value_materialization": [
        {"original_values": {"terms[1].name": "skin"}},
        {"materialized_field_paths": ["site.curie"]},
    ]}
    assert validator_event_covers(metadata, "site")
    assert validator_event_covers(metadata, "terms[1]")
    assert not validator_event_covers(metadata, "terms[0]")
    assert not validator_event_covers(None, "site")

    assert effective_resolution({"curie": "ONT:1"}, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        RESOLVED, None)
    assert effective_resolution({"curie": "ONT:1"}, identity_keys=TERM_KEYS, covered_by_validator=False) == (
        UNRESOLVED, REASON_LEGACY_UNVERIFIED)
    assert effective_resolution({"name": None}, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        UNRESOLVED, REASON_LEGACY_UNVERIFIED)
    # An explicit state always reads as stored.
    stored = {"curie": None, "mention": "x", "resolution_state": UNRESOLVED, "resolution_reason": REASON_NOT_FOUND}
    assert effective_resolution(stored, identity_keys=TERM_KEYS, covered_by_validator=True) == (
        UNRESOLVED, REASON_NOT_FOUND)


def test_effective_payload_applies_the_legacy_rule_without_touching_storage():
    spec = ResolvableSpec(id_key="curie", label_key="name")
    payload = {
        "site": {"curie": "ONT:1", "name": "epidermis"},
        "terms": [{"curie": "ONT:2", "name": "gut"}, {"curie": None, "name": "unknown body part"}],
        "stage": {"curie": "ONT:3", "name": "adult"},
        "fresh": {"curie": None, "name": None, "mention": "tail",
                  "resolution_state": UNRESOLVED, "resolution_reason": REASON_NOT_FOUND},
    }
    metadata = {"validator_resolved_value_materialization": [
        {"original_values": {"site.name": "skin", "terms[0].name": "gut"}},
    ]}
    effective = effective_payload(
        payload,
        {"site": spec, "terms": spec, "stage": spec, "fresh": spec},
        object_metadata=metadata,
    )
    assert effective["site"]["resolution_state"] == RESOLVED
    assert effective["terms"][0]["resolution_state"] == RESOLVED
    assert effective["terms"][1] == {
        "curie": None, "name": None, "mention": f"unknown body part {LEGACY_UNVERIFIED_SUFFIX}",
        "resolution_state": UNRESOLVED, "resolution_reason": REASON_LEGACY_UNVERIFIED,
    }
    # No covering event: unverified, and its stored text is paper wording.
    assert effective["stage"]["mention"] == f"adult (ONT:3) {LEGACY_UNVERIFIED_SUFFIX}"
    assert effective["stage"]["curie"] is None
    assert effective["fresh"] == payload["fresh"]
    assert "resolution_state" not in payload["site"]


# --- Materializer write-back ---------------------------------------------------


def _metadata(*, expected=None, mirror=False, input_path="site.mention") -> DomainPackMetadata:
    fields = [
        DomainPackFieldDefinition(field_path="site", field_type=DomainPackFieldType.OBJECT),
        DomainPackFieldDefinition(
            field_path="site.curie",
            field_type=DomainPackFieldType.STRING,
            metadata={"materializes_to_field_paths": ["copy.curie"]} if mirror else {},
        ),
        DomainPackFieldDefinition(
            field_path="site.name",
            field_type=DomainPackFieldType.STRING,
            metadata={"materializes_to_field_paths": ["copy.name"]} if mirror else {},
        ),
        DomainPackFieldDefinition(field_path="site.mention", field_type=DomainPackFieldType.STRING),
        DomainPackFieldDefinition(field_path="copy.curie", field_type=DomainPackFieldType.STRING),
        DomainPackFieldDefinition(field_path="copy.name", field_type=DomainPackFieldType.STRING),
    ]
    return DomainPackMetadata(
        pack_id="fixture.resolvable",
        display_name="Fixture Resolvable Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "fixture.site_lookup",
                        "display_name": "Site lookup",
                        "validator_agent": {"package_id": "fixture.validators", "agent_id": "term_validator"},
                        "applies_to": {
                            "domain_pack_id": "fixture.resolvable",
                            "object_types": ["Observation"],
                        },
                        "input_fields": {"mention": {"source": "payload", "path": input_path}},
                        "expected_result_fields": expected or {"curie": "site.curie", "name": "site.name"},
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
                fields=fields,
            )
        ],
    )


def _envelope(payload) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="resolvable-env",
        domain_pack_id="fixture.resolvable",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Observation",
                pending_ref_id="observation-1",
                status=CuratableObjectStatus.PENDING,
                payload=payload,
            )
        ],
    )


def _item(metadata, envelope, *, status="resolved", values=None, outcome="success", missing=()):
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=Path("."),
            metadata_path=Path("."),
            metadata=metadata,
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
            "resolved_values": values or {},
            "resolved_objects": [],
            "missing_expected_fields": list(missing),
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "fixture_lookup",
                    "method": "exact_label",
                    "query": {"mention": "skin"},
                    "result_count": 1 if outcome == "success" else 0,
                    "outcome": outcome,
                }
            ],
            "curator_message": None,
            "explanation": "Fixture validator decision.",
        }
    )
    return ValidatorResultMaterializationInput(match=match, request=request, result=result)


def _staged_site():
    return unresolved_value("skin", identity_keys=TERM_KEYS)


def test_resolved_result_writes_identity_and_state_keeping_the_mention():
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    patched = result.envelope.extracted_objects[0]
    assert patched.payload["site"] == {
        "curie": "ONT:1", "name": "epidermis", "mention": "skin",
        "resolution_state": RESOLVED, "resolution_reason": None,
    }
    assert patched.status is CuratableObjectStatus.VALIDATED
    event = patched.metadata["validator_resolved_value_materialization"][-1]
    assert event["materialized_field_paths"] == ["site.curie", "site.name"]


@pytest.mark.parametrize(
    ("outcome", "missing", "reason"),
    [("not_found", (), "not_found"), ("ambiguous", (), "ambiguous"), ("error", (), "transient"),
     ("not_found", ("curie", "name"), "missing_expected_result_field")],
)
def test_unresolved_state_agrees_with_the_finding_classification(outcome, missing, reason):
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, status="unresolved", outcome=outcome, missing=missing)

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    site = result.envelope.extracted_objects[0].payload["site"]
    assert site == {"curie": None, "name": None, "mention": "skin",
                    "resolution_state": UNRESOLVED, "resolution_reason": reason}
    parent = next(finding for finding in result.appended_findings if finding.field_ref is None
                  or finding.field_ref.field_path == "site")
    assert parent.details["failure_classification"] == site["resolution_reason"]
    assert result.envelope.extracted_objects[0].status is CuratableObjectStatus.PENDING


def test_partial_resolved_result_leaves_the_value_unresolved():
    metadata = _metadata()
    envelope = _envelope({"site": _staged_site()})
    item = _item(metadata, envelope, values={"curie": "ONT:1"}, missing=("name",))

    result = materialize_validator_results_into_envelope(envelope, metadata, [item])

    site = result.envelope.extracted_objects[0].payload["site"]
    # A partial identity is never written into an unresolved value.
    assert site["curie"] is None
    assert (site["resolution_state"], site["resolution_reason"]) == (
        UNRESOLVED, "missing_expected_result_field")
    missing = [finding for finding in result.appended_findings
               if finding.code == "domain_pack.validator_expected_field_missing"]
    assert [finding.details["failure_classification"] for finding in missing] == [
        "missing_expected_result_field"]


def test_mirror_copies_take_the_source_value_state():
    metadata = _metadata(mirror=True)
    envelope = _envelope({"site": _staged_site(), "copy": unresolved_value("skin", identity_keys=TERM_KEYS)})

    resolved = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})],
    ).envelope.extracted_objects[0].payload
    assert resolved["copy"] == {**resolved["site"]}

    unresolved = materialize_validator_results_into_envelope(
        envelope, metadata, [_item(metadata, envelope, status="unresolved", outcome="not_found")],
    ).envelope.extracted_objects[0].payload
    assert unresolved["copy"]["resolution_reason"] == "not_found"


def test_plain_values_without_a_resolvable_container_patch_as_before():
    metadata = _metadata(input_path="site.name")
    envelope = _envelope({"site": {"curie": None, "name": "skin"}})
    item = _item(metadata, envelope, values={"curie": "ONT:1", "name": "epidermis"})

    payload = materialize_validator_results_into_envelope(
        envelope, metadata, [item]).envelope.extracted_objects[0].payload

    assert payload == {"site": {"curie": "ONT:1", "name": "epidermis"}}


def test_a_binding_skipped_for_missing_inputs_leaves_the_value_explicitly_not_validated():
    metadata = _metadata(input_path="site.name")
    binding = metadata.metadata["validator_bindings"]["active"][0]
    binding["input_fields"]["mention"]["required"] = False
    metadata = DomainPackMetadata.model_validate(metadata.model_dump(mode="json"))
    envelope = _envelope({"site": _staged_site()})
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
            pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
        )
    )
    match = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])[0]

    assert build_domain_validation_request(match).request is None
    site = envelope.extracted_objects[0].payload["site"]
    assert (site["resolution_state"], site["resolution_reason"]) == (UNRESOLVED, REASON_NOT_VALIDATED)
