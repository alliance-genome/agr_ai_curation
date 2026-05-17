"""Unit tests for metadata-driven domain-pack validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agr_ai_curation_runtime import agr_lookup
from src.lib import lookup_status
from src.lib.domain_packs.loader import (
    DomainPackMetadataError,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationRegistryError,
    ValidationBindingState,
    validate_active_validator_agent_references,
)
from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFindingSeverity,
)
from src.schemas.domain_validator import DomainValidatorResultBase


class BindingReadyValidatorResult(DomainValidatorResultBase):
    """Fixture validator result that satisfies active binding schema checks."""


class SummaryOnlyValidatorResult(BaseModel):
    """Fixture chat-era schema lacking dispatcher-required validator fields."""

    summary: str


def _validator_agent(
    *,
    package_id: str = "org.validators",
    agent_id: str = "shared_validator",
    output_schema: str = "BindingReadyValidatorResult",
) -> SimpleNamespace:
    return SimpleNamespace(
        package_id=package_id,
        agent_id=agent_id,
        output_schema=output_schema,
    )


def _validator_schema_resolver(schema_key: str):
    if schema_key == "BindingReadyValidatorResult":
        return BindingReadyValidatorResult
    if schema_key == "SummaryOnlyValidatorResult":
        return SummaryOnlyValidatorResult
    return None


def test_lookup_status_constants_are_shared_with_agr_lookup_contract():
    assert agr_lookup.LOOKUP_STATUS_BLOCKED == lookup_status.LOOKUP_STATUS_BLOCKED
    assert (
        agr_lookup.LOOKUP_STATUS_UNDER_DEVELOPMENT
        == lookup_status.LOOKUP_STATUS_UNDER_DEVELOPMENT
    )


def _validation_pack_text() -> str:
    return """
pack_id: fixture.validation
display_name: Fixture Validation Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
enum_definitions:
  - enum_id: ConfidenceLevel
    display_name: Confidence level
    values:
      - value: high
      - value: low
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    metadata:
      object_role: curatable_unit
      provider_refs:
        fixture_provider:
          class: GeneAssertion
    fields:
      - field_path: gene.symbol
        field_type: string
        required: true
        metadata:
          provider_refs:
            fixture_provider:
              slot: symbol
      - field_path: gene.identifier
        field_type: string
        required: true
        metadata:
          export_blocking: true
          provider_refs:
            fixture_provider:
              slot: identifier
      - field_path: confidence
        field_type: enum
        enum_ref: ConfidenceLevel
metadata:
  validators:
    active:
      - validator_id: fixture.shape
        display_name: Fixture envelope shape
        description: Envelope shape validation is active.
    under_development:
      - validator_id: fixture.future_lookup
        display_name: Future lookup
        description: Future lookup validator is under development.
      - validator_id: fixture.export_projection
        display_name: Export projection
        blocked_by: ALL-999
        description: Export projection validator is under development.
  validator_bindings:
    active:
      - binding_id: fixture.agent_validator
        display_name: Envelope validation
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
      - binding_id: fixture.identifier_prefix
        display_name: Identifier lookup
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          object_roles:
            - curatable_unit
          field_paths:
            - gene.identifier
          field_types:
            - string
        input_fields:
          gene_id:
            source: payload
            path: gene.identifier
        expected_result_fields:
          curie: gene.identifier
        required: true
        blocking: true
        allow_opt_out: true
      - binding_id: fixture.optional_confidence_check
        display_name: Confidence check
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        required: false
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          field_paths:
            - confidence
        input_fields:
          confidence:
            source: payload
            path: confidence
        expected_result_fields:
          confidence: confidence
    under_development:
      - binding_id: fixture.symbol_lookup
        display_name: Gene symbol lookup
        state_explanation: Gene symbol lookup waits for package-scoped dispatch.
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          field_paths:
            - gene.symbol
        definition_state: in_development
      - binding_id: fixture.export_validator
        display_name: Export projection
        state_explanation: Export projection adapter is not available.
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
""".strip()


def _loaded_pack(tmp_path: Path, metadata_text: str | None = None) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text or _validation_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _loaded_owned_pack(
    tmp_path: Path,
    metadata_text: str,
    *,
    package_id: str = "org.owner",
) -> LoadedDomainPack:
    pack = _loaded_pack(tmp_path, metadata_text)
    return LoadedDomainPack(
        pack_id=pack.pack_id,
        display_name=pack.display_name,
        version=pack.version,
        pack_path=pack.pack_path,
        metadata_path=pack.metadata_path,
        metadata=pack.metadata,
        package_id=package_id,
        package_display_name=package_id,
        package_version="1.0.0",
    )


def _package_registry(
    *package_ids: str, dependencies: set[tuple[str, str]] | None = None
):
    dependency_pairs = dependencies or set()

    class _Registry:
        def get_package(self, package_id: str):
            if package_id not in package_ids:
                return None
            return SimpleNamespace(package_id=package_id)

        def package_declares_dependency(
            self,
            source_package_id: str,
            target_package_id: str,
        ) -> bool:
            return (
                source_package_id == target_package_id
                or (source_package_id, target_package_id) in dependency_pairs
            )

    return _Registry()


def _validator_agent_pack_text() -> str:
    return """
pack_id: fixture.validation
display_name: Fixture Validation Pack
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
  validator_bindings:
    active:
      - binding_id: fixture.agent_validator
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
""".strip()


def _metadata_validator_agent_pack_text() -> str:
    return """
pack_id: fixture.validation
display_name: Fixture Validation Pack
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
  validators:
    active:
      - validator_id: fixture.agent_validator
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
""".strip()


def test_active_validator_agent_reference_validates_package_agent_and_dependency(
    tmp_path: Path,
):
    pack = _loaded_owned_pack(tmp_path, _validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    validate_active_validator_agent_references(
        [registry],
        _package_registry(
            "org.owner",
            "org.validators",
            dependencies={("org.owner", "org.validators")},
        ),
        agent_resolver=lambda package_id, agent_id: (
            _validator_agent(package_id=package_id, agent_id=agent_id)
            if (package_id, agent_id) == ("org.validators", "shared_validator")
            else None
        ),
        output_schema_resolver=_validator_schema_resolver,
    )

    option = registry.validation_attachment_options()[0].to_dict()
    assert option["validator_id"] == "org.validators:shared_validator"
    assert option["validator_package_id"] == "org.validators"
    assert option["validator_agent_id"] == "shared_validator"


def test_active_validator_agent_reference_requires_binding_ready_schema(tmp_path: Path):
    pack = _loaded_owned_pack(tmp_path, _validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry(
                "org.owner",
                "org.validators",
                dependencies={("org.owner", "org.validators")},
            ),
            agent_resolver=lambda _package_id, _agent_id: _validator_agent(
                output_schema="SummaryOnlyValidatorResult"
            ),
            output_schema_resolver=_validator_schema_resolver,
        )

    assert "must inherit from or embed DomainValidatorResultBase" in str(exc_info.value)


def test_active_validator_agent_reference_requires_output_schema(tmp_path: Path):
    pack = _loaded_owned_pack(tmp_path, _validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry(
                "org.owner",
                "org.validators",
                dependencies={("org.owner", "org.validators")},
            ),
            agent_resolver=lambda _package_id, _agent_id: _validator_agent(
                output_schema=""
            ),
            output_schema_resolver=_validator_schema_resolver,
        )

    assert "without an output_schema" in str(exc_info.value)


def test_active_validator_agent_reference_fails_for_undeclared_cross_package_dependency(
    tmp_path: Path,
):
    pack = _loaded_owned_pack(tmp_path, _validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry("org.owner", "org.validators"),
            agent_resolver=lambda _package_id, _agent_id: _validator_agent(),
            output_schema_resolver=_validator_schema_resolver,
        )

    assert "must declare dependency 'org.validators'" in str(exc_info.value)


def test_active_metadata_validator_agent_reference_requires_declared_dependency(
    tmp_path: Path,
):
    pack = _loaded_owned_pack(tmp_path, _metadata_validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry("org.owner", "org.validators"),
            agent_resolver=lambda _package_id, _agent_id: _validator_agent(),
            output_schema_resolver=_validator_schema_resolver,
        )

    message = str(exc_info.value)
    assert "must declare dependency 'org.validators'" in message
    assert "validator 'fixture.agent_validator'" in message


def test_active_validator_agent_reference_fails_for_missing_agent(tmp_path: Path):
    pack = _loaded_owned_pack(tmp_path, _validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry(
                "org.owner",
                "org.validators",
                dependencies={("org.owner", "org.validators")},
            ),
            agent_resolver=lambda _package_id, _agent_id: None,
            output_schema_resolver=_validator_schema_resolver,
        )

    assert (
        "references missing validator agent 'org.validators:shared_validator'"
        in str(exc_info.value)
    )


def test_active_metadata_validator_agent_reference_fails_for_missing_package(
    tmp_path: Path,
):
    pack = _loaded_owned_pack(tmp_path, _metadata_validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry("org.owner"),
            agent_resolver=lambda _package_id, _agent_id: _validator_agent(),
            output_schema_resolver=_validator_schema_resolver,
        )

    message = str(exc_info.value)
    assert "references missing validator package 'org.validators'" in message
    assert "validator 'fixture.agent_validator'" in message


def test_active_metadata_validator_agent_reference_fails_for_missing_agent(
    tmp_path: Path,
):
    pack = _loaded_owned_pack(tmp_path, _metadata_validator_agent_pack_text())
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    with pytest.raises(ValidationRegistryError) as exc_info:
        validate_active_validator_agent_references(
            [registry],
            _package_registry(
                "org.owner",
                "org.validators",
                dependencies={("org.owner", "org.validators")},
            ),
            agent_resolver=lambda _package_id, _agent_id: None,
            output_schema_resolver=_validator_schema_resolver,
        )

    message = str(exc_info.value)
    assert (
        "references missing validator agent 'org.validators:shared_validator'"
        in message
    )
    assert "validator 'fixture.agent_validator'" in message


def test_validator_agent_reference_rejects_bare_agent_id(tmp_path: Path):
    bare_ref_text = _validator_agent_pack_text().replace(
        "validator_agent:\n"
        "          package_id: org.validators\n"
        "          agent_id: shared_validator",
        "validator_agent: shared_validator",
    )

    with pytest.raises(DomainPackMetadataError, match="validator_agent"):
        _loaded_owned_pack(tmp_path, bare_ref_text)


def _envelope(
    *,
    payload: dict | None = None,
    object_role: str = "curatable_unit",
) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.validation",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_role=object_role,
                pending_ref_id="gene-assertion-1",
                model_ref="GeneAssertionPayload",
                payload=(
                    payload
                    if payload is not None
                    else {
                        "gene": {
                            "symbol": "abc-1",
                            "identifier": "AGR:0000001",
                        },
                        "confidence": "high",
                    }
                ),
            )
        ],
    )


def test_registry_matches_bindings_by_state_field_type_and_object_role(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    matches = registry.match_bindings(
        _envelope(),
        states=[ValidationBindingState.ACTIVE],
    )
    by_binding = {match.binding.binding_id: match for match in matches}

    assert by_binding["fixture.agent_validator"].object_type == "GeneAssertion"
    assert by_binding["fixture.identifier_prefix"].field_path == "gene.identifier"
    assert (
        by_binding["fixture.identifier_prefix"].field_definition.field_type.value
        == "string"
    )
    identifier_policy = registry.policy_for("GeneAssertion", "gene.identifier")
    assert identifier_policy is not None
    assert identifier_policy.required is True
    assert identifier_policy.blocking is True
    assert identifier_policy.allow_opt_out is True

    metadata_only_matches = registry.match_bindings(
        _envelope(object_role="metadata_only"),
        states=[ValidationBindingState.ACTIVE],
    )
    assert {match.binding.binding_id for match in metadata_only_matches} == {
        "fixture.agent_validator",
        "fixture.optional_confidence_check",
    }


def test_registry_exposes_under_development_binding_metadata(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    metadata_by_state: dict[ValidationBindingState, set[str]] = {}
    for entry in registry.validator_metadata:
        metadata_by_state.setdefault(entry.state, set()).add(entry.validator_id)
    binding_states = {
        binding.binding_id: binding.state for binding in registry.bindings
    }

    assert metadata_by_state[ValidationBindingState.UNDER_DEVELOPMENT] == {
        "fixture.future_lookup",
        "fixture.export_projection",
    }
    assert (
        binding_states["fixture.symbol_lookup"]
        is ValidationBindingState.UNDER_DEVELOPMENT
    )
    assert (
        binding_states["fixture.export_validator"]
        is ValidationBindingState.UNDER_DEVELOPMENT
    )


def test_registry_builds_flow_validation_attachment_options(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    options = registry.validation_attachment_options()
    by_id = {option.attachment_id: option for option in options}

    identifier_option = by_id[
        "fixture.validation:binding:fixture.identifier_prefix:field:GeneAssertion:gene.identifier"
    ]
    assert identifier_option.state is ValidationBindingState.ACTIVE
    assert identifier_option.field_path == "gene.identifier"
    assert identifier_option.required is True
    assert identifier_option.export_blocking is True
    assert identifier_option.default_enabled is True
    assert identifier_option.allow_opt_out is True

    callable_option = by_id[
        "fixture.validation:binding:fixture.agent_validator:object:GeneAssertion:*"
    ]
    assert callable_option.required is False
    assert callable_option.default_enabled is True
    assert callable_option.allow_opt_out is False

    optional_option = by_id[
        "fixture.validation:binding:fixture.optional_confidence_check:field:GeneAssertion:confidence"
    ]
    assert optional_option.required is False
    assert optional_option.default_enabled is True
    assert optional_option.allow_opt_out is False

    under_development_option = by_id[
        "fixture.validation:binding:fixture.symbol_lookup:field:GeneAssertion:gene.symbol"
    ]
    assert under_development_option.state is ValidationBindingState.UNDER_DEVELOPMENT
    assert under_development_option.default_enabled is False
    assert under_development_option.required is False
    assert under_development_option.export_blocking is False
    assert under_development_option.allow_opt_out is False
    assert under_development_option.label == "Gene symbol lookup"
    assert (
        under_development_option.state_explanation
        == "Gene symbol lookup waits for package-scoped dispatch."
    )
    assert under_development_option.affected_fields == ("gene.symbol",)

    metadata_only_option = by_id[
        "fixture.validation:binding:fixture.export_validator:object:GeneAssertion:*"
    ]
    assert metadata_only_option.state is ValidationBindingState.UNDER_DEVELOPMENT
    assert metadata_only_option.default_enabled is False

    metadata_option = by_id["fixture.validation:metadata:fixture.shape:pack:*:*"]
    assert metadata_option.label == "Fixture envelope shape"

    callable_option = by_id[
        "fixture.validation:binding:fixture.agent_validator:object:GeneAssertion:*"
    ]
    assert callable_option.label == "Gene assertion data check"


def test_registry_rejects_conflicting_status_and_state_metadata(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "validator_id: fixture.shape\n        display_name: Fixture envelope shape\n        description:",
        "\n".join(
            (
                "validator_id: fixture.shape",
                "        display_name: Fixture envelope shape",
                "        status: under_development",
                "        state: active",
                "        description:",
            )
        ),
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    with pytest.raises(
        ValidationRegistryError,
        match="validators item declares conflicting status/state values",
    ):
        DomainPackValidationRegistry.from_domain_pack(pack)


@pytest.mark.parametrize("legacy_bucket", ["planned", "blocked"])
def test_registry_rejects_legacy_validator_metadata_buckets(
    tmp_path: Path,
    legacy_bucket: str,
):
    metadata_text = _validation_pack_text().replace(
        "    under_development:",
        f"    {legacy_bucket}:",
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    with pytest.raises(
        ValidationRegistryError,
        match=(
            "validators supports only active and under_development buckets; "
            f"found legacy bucket\\(s\\): {legacy_bucket}"
        ),
    ):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_registry_required_ids_report_edge_whitespace(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "validator_id: fixture.shape",
        "validator_id: ' fixture.shape'",
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    with pytest.raises(
        ValidationRegistryError,
        match="validators.validator_id must not have leading or trailing whitespace",
    ):
        DomainPackValidationRegistry.from_domain_pack(pack)


@pytest.mark.parametrize(
    ("legacy_yaml", "expected_error"),
    [
        (
            """
  validator_bindings:
    - binding_id: fixture.legacy_list
      validator_agent:
        package_id: org.validators
        agent_id: shared_validator
      applies_to:
        domain_pack_id: fixture.validation
""",
            "validator_bindings",
        ),
        (
            """
  validator_bindings:
    binding_id: fixture.singleton
    validator_agent:
      package_id: org.validators
      agent_id: shared_validator
    applies_to:
      domain_pack_id: fixture.validation
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    planned:
      - binding_id: fixture.planned
        state_explanation: Legacy planned bucket.
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    blocked:
      - binding_id: fixture.blocked
        state_explanation: Legacy blocked bucket.
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    deprecated:
      - binding_id: fixture.deprecated
        state_explanation: Legacy deprecated bucket.
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    active:
      - binding_id: fixture.item_state
        status: planned
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    active:
      - binding_id: fixture.direct_tool
        validator: fixture.validators.validate
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
""",
            "Extra inputs are not permitted",
        ),
        (
            """
  validator_bindings:
    active:
      - binding_id: fixture.tool_method
        validation_kind: db_backed_reference_lookup
        tool_name: agr_curation_query
        tool_method: get_gene_by_id
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
""",
            "Extra inputs are not permitted",
        ),
    ],
)
def test_validator_bindings_legacy_shapes_fail_at_load_time(
    tmp_path: Path,
    legacy_yaml: str,
    expected_error: str,
):
    metadata_text = (
        _validation_pack_text().split("\nmetadata:\n", 1)[0]
        + "\nmetadata:\n"
        + legacy_yaml.strip("\n")
        + "\n"
    )
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text, encoding="utf-8")

    with pytest.raises(DomainPackMetadataError, match=expected_error):
        load_domain_pack_metadata(metadata_path)


def test_under_development_binding_rejects_runtime_policy(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "state_explanation: Gene symbol lookup waits for package-scoped dispatch.",
        "\n".join(
            (
                "state_explanation: Gene symbol lookup waits for package-scoped dispatch.",
                "        required: true",
                "        blocking: true",
                "        allow_opt_out: true",
            )
        ),
        1,
    )
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text, encoding="utf-8")

    with pytest.raises(DomainPackMetadataError, match="Extra inputs are not permitted"):
        load_domain_pack_metadata(metadata_path)


def test_active_binding_rejects_blocking_without_required(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "        required: true\n        blocking: true",
        "        required: false\n        blocking: true",
        1,
    )
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text, encoding="utf-8")

    with pytest.raises(
        DomainPackMetadataError,
        match="blocking: true unless required: true",
    ):
        load_domain_pack_metadata(metadata_path)


def test_under_development_binding_requires_display_name(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "        display_name: Gene symbol lookup\n"
        "        state_explanation: Gene symbol lookup waits for package-scoped dispatch.",
        "        state_explanation: Gene symbol lookup waits for package-scoped dispatch.",
        1,
    )
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text, encoding="utf-8")

    with pytest.raises(DomainPackMetadataError, match="display_name"):
        load_domain_pack_metadata(metadata_path)


def test_structural_checks_keep_under_development_bindings_metadata_only(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    envelope = _envelope(payload={"gene": {"symbol": "abc-1"}, "confidence": "high"})

    result = run_domain_envelope_structural_checks(envelope, pack)
    findings_by_code = {
        finding.code: finding for finding in result.envelope.validation_findings
    }

    required_finding = findings_by_code["domain_pack.required_field_missing"]
    assert required_finding.severity is ValidationFindingSeverity.BLOCKER
    assert required_finding.field_ref.field_path == "gene.identifier"
    assert (
        required_finding.details["validation_metadata"]["metadata_source"]
        == "field_policy"
    )
    assert (
        required_finding.details["validation_metadata"]["field_policy"]["policy_source"]
        == "field_policy"
    )
    assert (
        required_finding.details["validation_metadata"]["field_policy"][
            "blocking"
        ]
        is True
    )

    assert {
        finding.code for finding in result.envelope.validation_findings
    }.isdisjoint({"domain_pack.validator_binding_under_development"})
    assert len(result.envelope.history) == len(result.appended_findings)
    assert {event.event_type.value for event in result.envelope.history} == {
        "validation_finding_added"
    }
    assert any(
        event.details["target"].get("field_path") == "gene.identifier"
        for event in result.envelope.history
    )


def test_structural_checks_mark_field_definition_source_when_policy_absent(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    envelope = _envelope(payload={"gene": {"symbol": "abc-1"}, "confidence": "high"})

    class RegistryWithoutFormalFieldPolicies:
        validator_metadata = ()

        def __init__(self, delegate: DomainPackValidationRegistry) -> None:
            self.object_definitions_by_type = delegate.object_definitions_by_type

        def match_bindings(self, _envelope: DomainEnvelope) -> tuple[object, ...]:
            return ()

        def policy_for(self, _object_type: str, _field_path: str) -> None:
            return None

    result = run_domain_envelope_structural_checks(
        envelope,
        pack,
        registry=RegistryWithoutFormalFieldPolicies(registry),
    )

    required_finding = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code == "domain_pack.required_field_missing"
    ][0]
    metadata = required_finding.details["validation_metadata"]

    assert metadata["metadata_source"] == "field_definition"
    assert metadata["field_policy"]["policy_source"] == "field_definition"
    assert metadata["field_policy"]["field_path"] == "gene.identifier"
