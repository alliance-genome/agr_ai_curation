"""Which validator bindings mean a record has a validation result to wait for."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.profile_validation import envelope_is_profile_bound
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.domain_envelope import DomainEnvelope


def _binding(binding_id: str, applies_to: str, extra: str = "") -> str:
    return f"""
      - binding_id: {binding_id}
        display_name: {binding_id}
{extra}
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.active_validators
{applies_to}
        input_fields:
          value:
            source: payload
            path: value
        expected_result_fields:
          curie: value
"""


OBJECT_TARGET = """          object_types:
            - Checked"""
FIELD_TARGET = """          object_types:
            - Checked
          field_paths:
            - value"""
ROLE_TARGET = """          object_roles:
            - curatable_unit"""


def _registry(tmp_path: Path, *, active: str = "", under_development: str = "") -> DomainPackValidationRegistry:
    text = f"""
pack_id: fixture.active_validators
display_name: Fixture pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: Payload
    display_name: Payload
object_definitions:
  - object_type: Checked
    display_name: Checked
    model_ref: Payload
    metadata:
      object_role: curatable_unit
      supervisor_manifest:
        primary_label_field: value
    fields:
      - field_path: value
        field_type: string
  - object_type: Unchecked
    display_name: Unchecked
    model_ref: Payload
    metadata:
      supervisor_manifest:
        primary_label_field: value
    fields:
      - field_path: value
        field_type: string
metadata:
  validator_bindings:
    active: {active or "[]"}
    under_development: {under_development or "[]"}
""".strip()
    pack_path = tmp_path / "fixture.active_validators"
    pack_path.mkdir(exist_ok=True)
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(text, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=pack_path,
            metadata_path=metadata_path,
            metadata=metadata,
            package_id="org.owner",
        )
    )


def test_a_pack_with_no_bindings_has_nothing_to_wait_for(tmp_path):
    assert _registry(tmp_path).has_active_validators_for_object("Checked") is False


@pytest.mark.parametrize("target", [OBJECT_TARGET, FIELD_TARGET], ids=["object", "field"])
def test_a_targeted_binding_only_counts_for_its_own_object(tmp_path, target):
    registry = _registry(tmp_path, active=_binding("targeted", target))

    assert registry.has_active_validators_for_object("Checked") is True
    assert registry.has_active_validators_for_object("Unchecked") is False


def test_a_binding_under_development_does_not_count(tmp_path):
    registry = _registry(
        tmp_path,
        under_development=_binding(
            "planned", OBJECT_TARGET, "        state_explanation: Not ready yet."
        ),
    )

    assert registry.has_active_validators_for_object("Checked") is False


def test_a_role_only_binding_counts_whatever_role_the_object_declares(tmp_path):
    # The envelope's role decides at dispatch, so the object's declared role cannot rule it out.
    registry = _registry(tmp_path, active=_binding("by_role", ROLE_TARGET))

    assert registry.has_active_validators_for_object("Checked") is True
    assert registry.has_active_validators_for_object("Unchecked") is True


def _envelope(**overrides) -> DomainEnvelope:
    return DomainEnvelope(
        **{
            "envelope_id": "envelope",
            "domain_pack_id": "fixture.active_validators",
            "extracted_objects": [
                {"object_type": "Checked", "object_id": "one", "payload": {"value": "x"}}
            ],
            "metadata": {},
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({}, False),
        ({"execution_receipt": {"output_contract": {"output_mode": "builder"}}}, False),
        ({"execution_receipt": {"output_contract": {"output_mode": "profile_bound_generic"}}}, True),
        ({"extraction_metadata": {"provenance": {"generic_profile_ref": {"id": "p"}}}}, True),
        ({"execution_receipt": "not a receipt"}, True),
        ({"execution_receipt": {"output_contract": {}}}, True),
    ],
    ids=["none", "other-mode", "profile-mode", "provenance-marker", "unreadable", "no-mode"],
)
def test_profile_binding_is_read_without_failing_the_workspace(metadata, expected):
    assert envelope_is_profile_bound(_envelope(metadata=metadata)) is expected


def test_a_profile_marked_object_counts_as_profile_bound():
    envelope = _envelope(
        extracted_objects=[
            {
                "object_type": "Checked",
                "object_id": "one",
                "payload": {"value": "x"},
                "metadata": {"generic_profile_ref": {"id": "p"}},
            }
        ]
    )

    assert envelope_is_profile_bound(envelope) is True
