"""Validator results reach the objects that reference the validated target (ALL-1283).

A binding validates one target object. A pack declares, on another object's
fields, that the same result belongs there too: ``validation_result_binding_id``
names the binding, and a scalar field names the result field it receives
(``validation_result_field``); an object_ref field receives the validated
reference of its type. Only objects holding an object_ref to the target change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import unresolved_value
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ObjectRef,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackModelDefinition,
    DomainPackObjectDefinition,
)
from src.schemas.domain_validator import DomainValidatorResultBase


pytestmark = pytest.mark.provider_agnostic_domain_pack

BINDING_ID = "gallery.maker_lookup"
_WRITE_BACK = {"validation_result_binding_id": BINDING_ID}


def _field(path: str, field_type=DomainPackFieldType.STRING, **kwargs: Any) -> DomainPackFieldDefinition:
    return DomainPackFieldDefinition(field_path=path, field_type=field_type, **kwargs)


def _metadata(*, loan_binding_id: str = BINDING_ID) -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="gallery.catalog",
        display_name="Gallery catalog",
        version="0.1.0",
        metadata_api_version="1.0.0",
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": BINDING_ID,
                        "display_name": "Maker lookup",
                        "validator_agent": {"package_id": "gallery.validators", "agent_id": "maker"},
                        "applies_to": {
                            "domain_pack_id": "gallery.catalog",
                            "object_types": ["MakerMention"],
                            "field_paths": ["mention.text"],
                        },
                        "input_fields": {"mention": {"source": "payload", "path": "mention.text"}},
                        "expected_result_fields": {
                            "curie": "maker.primary_external_id",
                            "name": "maker.maker_name",
                        },
                    }
                ],
                "under_development": [],
            }
        },
        model_definitions=[
            # The acquisition root is a declared resolvable value (its maker).
            DomainPackModelDefinition(
                model_id="AcquisitionPayload",
                display_name="Acquisition payload",
                metadata={"display": {"label": "maker_name", "id": "maker_id", "mention": "mention"}},
            ),
        ],
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="MakerMention",
                display_name="Maker mention",
                metadata={"object_role": "metadata_only"},
                fields=[_field("mention.text")],
            ),
            DomainPackObjectDefinition(
                object_type="Maker",
                display_name="Maker",
                metadata={"object_role": "validated_reference"},
                fields=[
                    _field("primary_external_id", required=True),
                    _field("maker_name", required=True),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="Acquisition",
                display_name="Acquisition",
                model_ref="AcquisitionPayload",
                metadata={"object_role": "curatable_unit"},
                fields=[
                    _field("mention"),
                    _field(
                        "maker",
                        DomainPackFieldType.OBJECT_REF,
                        object_type_ref="Maker",
                        metadata=_WRITE_BACK,
                    ),
                    _field("maker_id", metadata={**_WRITE_BACK, "validation_result_field": "curie"}),
                    _field("maker_name", metadata={**_WRITE_BACK, "validation_result_field": "name"}),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="Loan",
                display_name="Loan",
                metadata={"object_role": "curatable_unit"},
                fields=[
                    _field(
                        "lender_id",
                        metadata={
                            "validation_result_binding_id": loan_binding_id,
                            "validation_result_field": "curie",
                        },
                    ),
                ],
            ),
        ],
    )


_MENTION_REF = ObjectRef(pending_ref_id="maker-mention-1", object_type="MakerMention")


def _envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="gallery-env",
        domain_pack_id="gallery.catalog",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="MakerMention",
                pending_ref_id="maker-mention-1",
                payload={"mention": {"text": "the Delft workshop"}},
            ),
            CuratableObjectEnvelope(
                object_type="Acquisition",
                pending_ref_id="acquisition-1",
                payload={
                    **unresolved_value(
                        "the Delft workshop", identity_keys=("maker_id", "maker_name")
                    ),
                    "year": 1702,
                },
                object_refs=[_MENTION_REF],
            ),
            # References the target, but its declared fields name another binding.
            CuratableObjectEnvelope(
                object_type="Loan",
                pending_ref_id="loan-1",
                payload={"lender": "a private collection"},
                object_refs=[_MENTION_REF],
            ),
            # Declares the binding but does not reference the validated target.
            CuratableObjectEnvelope(
                object_type="Acquisition",
                pending_ref_id="acquisition-unlinked",
                payload=unresolved_value(
                    "an unknown potter", identity_keys=("maker_id", "maker_name")
                ),
            ),
        ],
    )


def _materialize(
    envelope: DomainEnvelope,
    metadata: DomainPackMetadata,
    **result_fields: Any,
):
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
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent,
            "target": request.target,
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [],
            "curator_message": "Maker checked.",
            "explanation": "Fixture maker decision.",
            **result_fields,
        }
    )
    return materialize_validator_results_into_envelope(
        envelope,
        metadata,
        [ValidatorResultMaterializationInput(match=match, request=request, result=result)],
    )


def _by_ref(envelope: DomainEnvelope, pending_ref_id: str) -> CuratableObjectEnvelope:
    return next(obj for obj in envelope.extracted_objects if obj.pending_ref_id == pending_ref_id)


_RESOLVED = {
    "status": "resolved",
    "resolved_values": {"curie": "GAL:M0042", "name": "De Grieksche A"},
}


def test_resolved_result_writes_declared_fields_and_ref_onto_the_referencing_object():
    envelope = _envelope()
    result = _materialize(envelope, _metadata(), **_RESOLVED)
    acquisition = _by_ref(result.envelope, "acquisition-1")

    assert acquisition.payload == {
        "mention": "the Delft workshop",
        "maker_id": "GAL:M0042",
        "maker_name": "De Grieksche A",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": "Fixture maker decision.",
        "validator_curator_message": "Maker checked.",
        "year": 1702,
    }
    maker = next(obj for obj in result.envelope.extracted_objects if obj.object_type == "Maker")
    assert maker.to_object_ref() in acquisition.object_refs
    assert _MENTION_REF in acquisition.object_refs
    [event] = acquisition.metadata["validator_resolved_value_materialization"]
    assert event["source"] == "domain_validator_referenced_target"
    assert event["validator_binding_id"] == BINDING_ID
    assert event["validated_target"] == {
        "pending_ref_id": "maker-mention-1",
        "object_type": "MakerMention",
    }
    assert event["materialized_field_paths"] == ["maker_id", "maker_name"]


def test_only_objects_referencing_the_target_and_naming_the_binding_change():
    envelope = _envelope()
    result = _materialize(envelope, _metadata(loan_binding_id="gallery.other_lookup"), **_RESOLVED)

    for pending_ref_id in ("loan-1", "acquisition-unlinked"):
        assert _by_ref(result.envelope, pending_ref_id) == _by_ref(envelope, pending_ref_id)


def test_plain_scalar_fields_are_written_without_contract_state():
    envelope = _envelope()
    result = _materialize(envelope, _metadata(), **_RESOLVED)
    loan = _by_ref(result.envelope, "loan-1")

    assert loan.payload == {"lender": "a private collection", "lender_id": "GAL:M0042"}
    assert "resolution_state" not in loan.payload


def test_unresolved_result_records_only_the_outcome_on_the_referencing_value():
    envelope = _envelope()
    result = _materialize(
        envelope,
        _metadata(),
        status="unresolved",
        lookup_attempts=[
            {
                "provider": "fixture_lookup",
                "method": "maker_search",
                "query": {"mention": "the Delft workshop"},
                "result_count": 2,
                "outcome": "ambiguous",
            }
        ],
    )
    acquisition = _by_ref(result.envelope, "acquisition-1")
    before = _by_ref(envelope, "acquisition-1")

    assert acquisition.payload == {
        **before.payload,
        "resolution_state": "unresolved",
        "lookup_outcome": "ambiguous",
        "validator_explanation": "Fixture maker decision.",
        "validator_curator_message": "Maker checked.",
    }
    assert acquisition.payload["maker_id"] is None
    assert acquisition.payload["maker_name"] is None
    assert acquisition.object_refs == before.object_refs
    assert "validator_resolved_value_materialization" not in acquisition.metadata
    # A plain scalar receives nothing from an unresolved result.
    assert _by_ref(result.envelope, "loan-1") == _by_ref(envelope, "loan-1")


def test_resolved_result_missing_a_declared_field_writes_nothing_but_the_outcome():
    envelope = _envelope()
    result = _materialize(
        envelope,
        _metadata(),
        status="resolved",
        resolved_values={"curie": "GAL:M0042"},
        missing_expected_fields=["name"],
    )
    acquisition = _by_ref(result.envelope, "acquisition-1")

    assert acquisition.payload["resolution_state"] == "unresolved"
    assert acquisition.payload["lookup_outcome"] == "missing_expected_result_field"
    assert acquisition.payload["maker_id"] is None
    assert acquisition.object_refs == [_MENTION_REF]
    assert _by_ref(result.envelope, "loan-1") == _by_ref(envelope, "loan-1")


def test_a_later_unresolved_result_overrules_the_referencing_value():
    resolved = _materialize(_envelope(), _metadata(), **_RESOLVED).envelope
    result = _materialize(
        resolved,
        _metadata(),
        status="unresolved",
        lookup_attempts=[
            {
                "provider": "fixture_lookup",
                "method": "maker_search",
                "query": {"mention": "the Delft workshop"},
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
    )
    acquisition = _by_ref(result.envelope, "acquisition-1")

    assert acquisition.payload["resolution_state"] == "unresolved"
    assert acquisition.payload["lookup_outcome"] == "not_found"
    assert acquisition.payload["maker_id"] is None
    assert acquisition.payload["maker_name"] is None
    # The overruled identity is kept only as overruled_<key> hints.
    assert acquisition.payload["overruled_maker_id"] == "GAL:M0042"
    assert acquisition.payload["overruled_maker_name"] == "De Grieksche A"
    assert acquisition.payload["mention"] == "the Delft workshop"
    # The link to the validated Maker is stale on an unresolved value, so it is dropped.
    assert acquisition.object_refs == [_MENTION_REF]
    assert any(obj.object_type == "Maker" for obj in result.envelope.extracted_objects)


def test_a_stale_copied_override_on_the_referencing_value_follows_its_source():
    import copy

    from src.lib.domain_packs.resolvable_values import apply_curator_identity

    envelope = _envelope()
    objects = []
    for obj in envelope.extracted_objects:
        if obj.pending_ref_id == "acquisition-1":
            payload = copy.deepcopy(obj.payload)
            apply_curator_identity(
                payload,
                {"maker_id": "GAL:M0007", "maker_name": "De Porceleyne Fles"},
                identity_keys=("maker_id", "maker_name"),
                id_key="maker_id",
                label_key="maker_name",
                actor_id="curator-1", actor_display_name="curator-1",
                at="2026-09-23T20:00:00Z",
            )
            obj = obj.model_copy(update={"payload": payload})
        objects.append(obj)
    curated = envelope.model_copy(update={"extracted_objects": objects})

    result = _materialize(curated, _metadata(), **_RESOLVED)
    acquisition = _by_ref(result.envelope, "acquisition-1")

    # The referencing value only mirrors its source: it takes the validated identity.
    assert acquisition.payload["maker_id"] == "GAL:M0042"
    assert acquisition.payload["maker_name"] == "De Grieksche A"
    assert acquisition.payload["lookup_outcome"] == "matched"
    assert "curator_override" not in acquisition.payload


# --- A mirroring value is a pass-through edit surface ------------------------------------------


def _mirrored_metadata() -> DomainPackMetadata:
    """The maker mention holds a declared resolvable maker value the acquisition mirrors."""

    metadata = _metadata()
    objects = []
    for obj in metadata.object_definitions:
        if obj.object_type == "MakerMention":
            obj = obj.model_copy(update={"fields": [
                *obj.fields,
                _field("maker", DomainPackFieldType.OBJECT, metadata={"display": {
                    "label": "maker_name", "id": "primary_external_id", "mention": "mention",
                }}),
                _field("maker.mention"),
                _field("maker.primary_external_id", metadata={"editable": True}),
                _field("maker.maker_name", metadata={"editable": True}),
            ]})
        elif obj.object_type == "Acquisition":
            obj = obj.model_copy(update={"fields": [
                field.model_copy(update={"metadata": {**field.metadata, "editable": True}})
                if field.field_path in {"maker_id", "maker_name"}
                else field
                for field in obj.fields
            ]})
        objects.append(obj)
    return metadata.model_copy(update={"object_definitions": objects})


def _mirrored_envelope() -> DomainEnvelope:
    envelope = _envelope()
    objects = []
    for obj in envelope.extracted_objects:
        if obj.object_type == "MakerMention":
            obj = obj.model_copy(update={
                "object_id": "maker-mention-1",
                "payload": {
                    **obj.payload,
                    "maker": unresolved_value(
                        "the Delft workshop", identity_keys=("primary_external_id", "maker_name")
                    ),
                },
            })
        elif obj.pending_ref_id == "acquisition-1":
            obj = obj.model_copy(update={
                "object_id": "acquisition-1",
                "object_refs": [ObjectRef(object_id="maker-mention-1", object_type="MakerMention")],
            })
        else:
            continue
        objects.append(obj)
    return envelope.model_copy(update={"extracted_objects": objects})


def _loaded(metadata: DomainPackMetadata) -> LoadedDomainPack:
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=Path("."), metadata_path=Path("."), metadata=metadata,
    )


def _curator_patch(envelope, object_id, field_path, value, *, before, identity=True):
    from src.lib.domain_envelopes.patches import (
        EnvelopeFieldPatch,
        EnvelopeFieldPatchOperation,
        apply_curator_field_patch,
    )

    return apply_curator_field_patch(
        envelope,
        _loaded(_mirrored_metadata()),
        EnvelopeFieldPatch(
            envelope_id=envelope.envelope_id, expected_revision=1, object_id=object_id,
            field_path=field_path, before=before, value=value,
            operation=(
                EnvelopeFieldPatchOperation.REPLACE_IDENTITY if identity
                else EnvelopeFieldPatchOperation.REPLACE
            ),
        ),
        current_revision=1, actor_id="curator-7", actor_display_name="Curator Seven",
    )


def test_an_override_on_the_mirroring_value_is_applied_to_the_value_it_follows():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    identity = {"maker_id": "GAL:M0007", "maker_name": "De Porceleyne Fles"}
    result = _curator_patch(
        _mirrored_envelope(), "acquisition-1", "maker_id", identity,
        before={"maker_id": None, "maker_name": None},
    )

    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    mention = _by_ref(result.envelope, "maker-mention-1")
    acquisition = _by_ref(result.envelope, "acquisition-1")
    assert mention.payload["maker"]["primary_external_id"] == "GAL:M0007"
    assert mention.payload["maker"]["maker_name"] == "De Porceleyne Fles"
    assert mention.payload["maker"]["lookup_outcome"] == "curator_override"
    # The mirroring value follows straight away, with the same override record.
    assert {key: acquisition.payload[key] for key in identity} == identity
    assert acquisition.payload["lookup_outcome"] == "curator_override"
    assert acquisition.payload["curator_override"] == mention.payload["maker"]["curator_override"]
    assert mention.payload["maker"]["curator_override"]["actor_display_name"] == "Curator Seven"
    [event] = mention.metadata["curator_resolution_overrides"]
    assert (event["via_object_id"], event["via_field_path"]) == ("acquisition-1", "maker_id")


def test_a_mirroring_value_takes_no_edit_of_its_own():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _mirrored_envelope()
    leaf = _curator_patch(envelope, "acquisition-1", "maker_id", "GAL:M0007", before=None, identity=False)
    extra = _curator_patch(
        envelope, "acquisition-1", "maker_id",
        {"maker_id": "GAL:M0007", "maker_name": "De Porceleyne Fles", "year": 1703},
        before={"maker_id": None, "maker_name": None, "year": 1702},
    )

    assert leaf.status is EnvelopeFieldPatchStatus.REJECTED
    assert "follows a value validated on a linked object" in leaf.errors[0]
    assert extra.status is EnvelopeFieldPatchStatus.REJECTED
    assert "cannot change year" in extra.errors[0]


def test_no_validated_reference_is_added_for_a_value_a_curator_override_sets():
    """A resolved result for an overridden value appends and links no validated reference object."""

    from src.lib.domain_packs.resolvable_values import apply_curator_identity

    metadata = _metadata()
    mention_definition = metadata.object_definitions[0].model_copy(update={"fields": [
        _field("mention.text"),
        _field("maker", DomainPackFieldType.OBJECT,
               metadata={"display": {"label": "maker_name", "id": "primary_external_id", "mention": "mention"}}),
        _field("maker.primary_external_id"),
        _field("maker.maker_name"),
    ]})
    metadata = metadata.model_copy(update={
        "object_definitions": [mention_definition, *metadata.object_definitions[1:]],
    })

    def envelope_with(maker):
        base = _envelope()
        mention = base.extracted_objects[0].model_copy(update={"payload": {
            "mention": {"text": "the Delft workshop"}, "maker": maker}})
        return base.model_copy(update={"extracted_objects": [mention, *base.extracted_objects[1:]]})

    staged = unresolved_value("the Delft workshop", identity_keys=("primary_external_id", "maker_name"))
    validated = _materialize(envelope_with(staged), metadata, **_RESOLVED)
    assert any(obj.object_type == "Maker" for obj in validated.envelope.extracted_objects)

    overridden = unresolved_value("the Delft workshop", identity_keys=("primary_external_id", "maker_name"))
    apply_curator_identity(overridden, {"primary_external_id": "GAL:M0007", "maker_name": "De Porceleyne Fles"},
                           identity_keys=("primary_external_id", "maker_name"), id_key="primary_external_id",
                           label_key="maker_name", actor_id="curator-1", actor_display_name="curator-1", at="2026-09-24T00:00:00Z")
    result = _materialize(envelope_with(overridden), metadata, **_RESOLVED)
    assert not any(obj.object_type == "Maker" for obj in result.envelope.extracted_objects)
    assert result.envelope.extracted_objects[0].payload["maker"]["primary_external_id"] == "GAL:M0007"
