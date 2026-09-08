"""Curator-voice keys on active validator bindings (ALL-1026).

``curator_label`` and ``when_off`` are optional, additive binding keys. They
load through the domain-pack schema, reach ``ValidatorBinding``, and surface on
``ValidationAttachmentOption`` so Agent Studio can show them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.loader import (
    DomainPackMetadataError,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_pack_metadata import DomainPackActiveValidatorBinding


CURATOR_LABEL = "Confirm the gene identifier in the Alliance records"
WHEN_OFF = (
    "The gene identifier stays as the extractor wrote it, "
    "and review rows show it unconfirmed."
)


def _pack_text(*, opt_out_binding_extra: str = "", locked_binding_extra: str = "") -> str:
    return f"""
pack_id: fixture.curator_keys
display_name: Fixture Curator Keys Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    metadata:
      object_role: curatable_unit
      supervisor_manifest:
        primary_label_field: gene.symbol
        secondary_label_field: gene.identifier
    fields:
      - field_path: gene.identifier
        field_type: string
        required: true
      - field_path: gene.symbol
        field_type: string
metadata:
  validators:
    active:
      - validator_id: fixture.shape
        display_name: Fixture data structure
        curator_label: Confirm each record has the expected structure
        description: Checks that each record has the expected structure.
  validator_bindings:
    active:
      - binding_id: fixture.identifier_lookup
        display_name: Identifier lookup
{opt_out_binding_extra}
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.curator_keys
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        input_fields:
          gene_id:
            source: payload
            path: gene.identifier
        expected_result_fields:
          curie: gene.identifier
        required: true
        blocking: false
        allow_opt_out: true
      - binding_id: fixture.symbol_lookup
        display_name: Symbol lookup
{locked_binding_extra}
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.curator_keys
          object_types:
            - GeneAssertion
          field_paths:
            - gene.symbol
        input_fields:
          symbol:
            source: payload
            path: gene.symbol
        expected_result_fields:
          symbol: gene.symbol
        required: true
        blocking: true
        allow_opt_out: false
    under_development: []
""".strip()


def _load(tmp_path: Path, text: str) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.curator_keys"
    pack_path.mkdir(exist_ok=True)
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(text, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.owner",
    )


def _binding(registry: DomainPackValidationRegistry, binding_id: str):
    matches = [b for b in registry.bindings if b.binding_id == binding_id]
    assert len(matches) == 1
    return matches[0]


def _options(registry: DomainPackValidationRegistry, binding_id: str | None, validator_id: str | None = None):
    return [
        option
        for option in registry.validation_attachment_options()
        if (binding_id is not None and option.validator_binding_id == binding_id)
        or (validator_id is not None and option.validator_id == validator_id)
    ]


def test_curator_keys_load_and_reach_attachment_options(tmp_path: Path):
    text = _pack_text(
        opt_out_binding_extra=(
            f"        curator_label: {CURATOR_LABEL}\n"
            "        description: Checks the gene identifier against the Alliance records.\n"
            f"        when_off: {WHEN_OFF}\n"
            "        definition_notes:\n"
            "          - Runs through the shared identifier validator."
        ),
        locked_binding_extra="        curator_label: Confirm the gene symbol in the Alliance records",
    )
    registry = DomainPackValidationRegistry.from_domain_pack(_load(tmp_path, text))

    opt_out = _binding(registry, "fixture.identifier_lookup")
    assert opt_out.state is ValidationBindingState.ACTIVE
    assert opt_out.curator_label == CURATOR_LABEL
    assert opt_out.when_off == WHEN_OFF
    assert opt_out.raw["definition_notes"] == [
        "Runs through the shared identifier validator."
    ]

    locked = _binding(registry, "fixture.symbol_lookup")
    assert locked.curator_label == "Confirm the gene symbol in the Alliance records"
    assert locked.when_off is None

    opt_out_options = _options(registry, "fixture.identifier_lookup")
    assert opt_out_options
    for option in opt_out_options:
        assert option.allow_opt_out is True
        assert option.curator_label == CURATOR_LABEL
        assert option.when_off == WHEN_OFF
        payload = option.to_dict()
        assert payload["curator_label"] == CURATOR_LABEL
        assert payload["when_off"] == WHEN_OFF

    locked_options = _options(registry, "fixture.symbol_lookup")
    assert locked_options
    for option in locked_options:
        assert option.allow_opt_out is False
        assert option.curator_label == "Confirm the gene symbol in the Alliance records"
        assert option.when_off is None
        assert "when_off" not in option.to_dict()

    shape_options = _options(registry, None, validator_id="fixture.shape")
    assert len(shape_options) == 1
    assert shape_options[0].curator_label == "Confirm each record has the expected structure"
    assert shape_options[0].when_off is None


def test_curator_keys_are_optional_and_absent_by_default(tmp_path: Path):
    registry = DomainPackValidationRegistry.from_domain_pack(
        _load(tmp_path, _pack_text())
    )

    for binding in registry.bindings:
        assert binding.curator_label is None
        assert binding.when_off is None

    for option in registry.validation_attachment_options():
        if option.validator_binding_id is not None:
            assert option.curator_label is None
        assert option.when_off is None
        payload = option.to_dict()
        assert "when_off" not in payload
        if option.validator_binding_id is not None:
            assert "curator_label" not in payload


@pytest.mark.parametrize(
    ("extra_line", "expected_error"),
    [
        ("        curator_label: 12", "curator_label"),
        ("        when_off: false", "when_off"),
        ("        curator_label: ''", "must not be empty"),
        ("        when_off: '  padded  '", "surrounding whitespace"),
        ("        definition_notes: not-a-list", "definition_notes"),
    ],
)
def test_non_string_or_blank_curator_keys_are_rejected(
    tmp_path: Path, extra_line: str, expected_error: str
):
    with pytest.raises(DomainPackMetadataError, match=expected_error):
        _load(tmp_path, _pack_text(opt_out_binding_extra=extra_line))


def test_when_off_requires_allow_opt_out(tmp_path: Path):
    with pytest.raises(
        DomainPackMetadataError,
        match="cannot set when_off unless allow_opt_out: true",
    ):
        _load(
            tmp_path,
            _pack_text(locked_binding_extra=f"        when_off: {WHEN_OFF}"),
        )


def test_schema_model_accepts_and_rejects_curator_keys_directly():
    base = {
        "binding_id": "fixture.identifier_lookup",
        "validator_agent": {"package_id": "org.validators", "agent_id": "v"},
        "applies_to": {"object_types": ["GeneAssertion"]},
        "required": True,
        "allow_opt_out": True,
    }
    binding = DomainPackActiveValidatorBinding.model_validate(
        {**base, "curator_label": CURATOR_LABEL, "when_off": WHEN_OFF}
    )
    assert binding.curator_label == CURATOR_LABEL
    assert binding.when_off == WHEN_OFF
    assert binding.definition_notes == []

    absent = DomainPackActiveValidatorBinding.model_validate(base)
    assert absent.curator_label is None
    assert absent.when_off is None

    with pytest.raises(ValueError):
        DomainPackActiveValidatorBinding.model_validate(
            {**base, "curator_label": ["not", "a", "string"]}
        )
    with pytest.raises(ValueError):
        DomainPackActiveValidatorBinding.model_validate(
            {**base, "when_off": {"text": WHEN_OFF}}
        )
    with pytest.raises(ValueError, match="unless allow_opt_out: true"):
        DomainPackActiveValidatorBinding.model_validate(
            {**base, "allow_opt_out": False, "when_off": WHEN_OFF}
        )
