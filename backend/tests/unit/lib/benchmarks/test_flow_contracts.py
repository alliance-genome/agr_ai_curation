"""Declared output discovery must not guess fields or bypass saved-revision auth."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field

from src.lib.benchmarks import flow_contracts as contracts
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.flow_contracts import (
    PackStructureSource,
    ProfileStructureSource,
    step_output_kind,
)
from src.schemas.agent_execution_revision import AgentOutputContract, DomainExtractionRef
from tests.unit.lib.benchmarks.test_source_revisions import source_receipt


@pytest.fixture
def curator():
    return BenchmarkCuratorContext(
        subject="curator", auth_provider="oidc", db_user_id=42, active_groups=("group-a",),
    )


def test_declared_schema_preserves_nested_fields_and_nullability(monkeypatch, curator):
    class Gene(BaseModel):
        gene_a: str | None = Field(description="The reported gene symbol")

    class Output(BaseModel):
        results: list[Gene]

    monkeypatch.setattr(contracts, "resolve_output_schema", lambda key: Output)
    result = contracts.discover_output_contract(
        Mock(), curator, agent_id="extractor", metadata={"output_schema_key": "custom_output"},
    )
    assert result.status == "verified"
    assert result.schema_definition == Output.model_json_schema()
    assert result.schema_definition["$defs"]["Gene"]["properties"]["gene_a"]["description"] == "The reported gene symbol"
    assert result.semantic_mapping_required is True


def test_no_schema_does_not_guess_from_names_or_example(monkeypatch, curator):
    monkeypatch.setattr(contracts, "packaged_domain_pack", lambda *args: None)
    result = contracts.discover_output_contract(
        Mock(), curator, agent_id="gene_extractor", metadata={
            "name": "gene_a", "example": {"gene_a": "rutabaga"},
        },
    )
    assert result.status == "not_verified"
    assert result.schema_definition is None
    assert result.schema_digest is None


def test_foreign_revision_denied_before_schema_lookup(monkeypatch, curator):
    authorize = Mock(side_effect=ValueError("not accessible"))
    schema = Mock()
    monkeypatch.setattr(contracts, "authorize_execution_receipt", authorize)
    monkeypatch.setattr(contracts, "resolve_output_schema", schema)
    with pytest.raises(ValueError, match="not accessible"):
        contracts.discover_output_contract(
            Mock(), curator, agent_id="ca_private", metadata={
                "execution_receipt": {"foreign": True}, "output_schema_key": "secret",
            },
        )
    schema.assert_not_called()
    assert authorize.call_args.kwargs == {"active_group_ids": ["group-a"]}
    assert authorize.call_args.args[2] == 42


def test_custom_contract_uses_authorized_revision_not_supplied_metadata(monkeypatch, curator):
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="domain", output_schema_key="saved_schema",
    )})
    class Output(BaseModel):
        saved_field: str

    lookup = Mock(return_value=Output)
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(contracts, "resolve_output_schema", lookup)
    result = contracts.discover_output_contract(
        Mock(), curator, agent_id=receipt.agent_key, metadata={"output_schema_key": "mutable_head"},
    )
    lookup.assert_called_once_with("saved_schema")
    assert result.execution_receipt == receipt


def test_receipt_for_another_agent_rejected(monkeypatch, curator):
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *args, **kwargs: source_receipt())
    with pytest.raises(ValueError, match="another agent"):
        contracts.discover_output_contract(Mock(), curator, agent_id="ca_other", metadata={})


def test_unprofiled_generic_is_not_verified(monkeypatch, curator):
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="unprofiled_generic",
    )})
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *args, **kwargs: receipt)
    result = contracts.discover_output_contract(Mock(), curator, agent_id=receipt.agent_key, metadata={})
    assert result.status == "not_verified"
    assert result.execution_receipt == receipt


def test_exact_profile_schema_keeps_descriptions_nested_types_and_semantics(monkeypatch, curator):
    from uuid import uuid4
    from src.schemas.generic_extraction_profile import GenericProfileContract
    from src.schemas.agent_execution_revision import GenericProfilePin
    from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile

    contract = GenericProfileContract.model_validate({
        "name": "Paper entities", "semantic_class": "experimentally relevant genes",
        "fields": [{"key": "observations", "description": "Reported entities", "value_schema": {
            "kind": "array", "items": {"kind": "object", "fields": [{
                "key": "gene_a", "description": "Reported symbol", "nullable": True,
                "value_schema": {"kind": "string"},
            }]},
        }}],
    })
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=3, fingerprint=contract.fingerprint())
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="profile_bound_generic", generic_profile_ref=pin,
    )})
    profile = ResolvedGenericProfile(pin, contract)
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(contracts, "resolve_receipt_profile", lambda db, supplied: profile)
    result = contracts.discover_output_contract(Mock(), curator, agent_id=receipt.agent_key, metadata={})
    assert result.representation == "profile_attributes"
    assert result.schema_definition == profile.attributes_schema()
    assert result.declared_semantic_class == contract.semantic_class
    assert result.execution_receipt.output_contract.generic_profile_ref == pin
    assert result.semantic_mapping_required is True


def test_system_pack_step_returns_the_benchmark_catalog(curator):
    result = contracts.discover_output_contract(
        Mock(), curator, agent_id="disease_extractor",
        metadata={"curation": {"domain_pack_id": "agr.alliance.disease"}},
    )
    assert result.status == "verified" and result.representation == "pack_fields"
    assert result.schema_definition is not None
    assert set(result.schema_definition) == {
        "pack_id", "pack_version", "pack_label", "record_kinds", "families", "fields",
        "default_fields"}
    assert result.structure_source == PackStructureSource(
        pack_id="agr.alliance.disease", pack_version="0.1.0",
        pack_label="Alliance Disease Domain Pack")
    assert result.domain_pack_id == "agr.alliance.disease"


def test_custom_builder_step_returns_the_benchmark_catalog(monkeypatch, curator):
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="domain",
        domain_extraction_ref=DomainExtractionRef(
            package_id="agr.alliance", agent_id="phenotype_extractor",
            domain_pack_id="agr.alliance.phenotype"),
    )})
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *a, **k: receipt)
    result = contracts.discover_output_contract(Mock(), curator, agent_id=receipt.agent_key,
                                                metadata={})
    assert result.representation == "pack_fields"
    assert result.schema_definition is not None and result.structure_source is not None
    assert result.schema_definition["pack_id"] == "agr.alliance.phenotype"
    assert result.structure_source.kind == "pack"


def test_profile_contract_names_its_structure_source(monkeypatch, curator):
    from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile
    from src.schemas.agent_execution_revision import GenericProfilePin
    from src.schemas.generic_extraction_profile import GenericProfileContract

    contract = GenericProfileContract.model_validate({
        "name": "Stock inventory", "semantic_class": "stock",
        "fields": [{"key": "stock_name", "value_schema": {"kind": "string"}}]})
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=3,
                            fingerprint=contract.fingerprint())
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="profile_bound_generic",
        generic_profile_ref=pin)})
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *a, **k: receipt)
    monkeypatch.setattr(contracts, "resolve_receipt_profile",
                        lambda db, supplied: ResolvedGenericProfile(pin, contract))
    result = contracts.discover_output_contract(Mock(), curator, agent_id=receipt.agent_key,
                                                metadata={})
    assert result.structure_source == ProfileStructureSource(
        profile_id=pin.profile_id, profile_revision_id=pin.profile_revision_id,
        revision=3, name="Stock inventory")


def _custom(mode=None, *, schema_key=None, builder=False):
    if mode is None:
        output = AgentOutputContract(output_state="none")
    elif builder:
        output = AgentOutputContract(
            output_state="structured_extraction", output_mode="domain",
            domain_extraction_ref=DomainExtractionRef(
                package_id="p", agent_id="a", domain_pack_id="agr.alliance.disease"))
    else:
        output = AgentOutputContract(output_state="structured_extraction", output_mode=mode,
                                     output_schema_key=schema_key)
    receipt = source_receipt().model_copy(update={"output_contract": output})
    return contracts.BenchmarkFlowOutputContract(status="not_verified", execution_receipt=receipt)


@pytest.mark.parametrize("contract,validator,expected", [
    (_custom(None), False, "text"),
    (_custom("unprofiled_generic"), False, "flexible"),
    (_custom("domain", builder=True), False, "pack_fields"),
    (_custom("domain", schema_key="LegacyEnvelope"), False, "envelope_legacy"),
    (_custom("domain", schema_key="DiseaseValidatorResult"), True, "validator_result"),
])
def test_custom_step_output_kind_comes_from_the_authorized_receipt(
        monkeypatch, contract, validator, expected):
    monkeypatch.setattr(contracts, "resolve_output_schema", lambda key: object())
    monkeypatch.setattr(contracts, "is_domain_validator_result_schema", lambda model: validator)
    assert step_output_kind("ca_saved", {"output_schema_key": "ignored"}, contract) == expected


def test_custom_profile_step_output_kind():
    from src.schemas.agent_execution_revision import GenericProfilePin
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
                            fingerprint="sha256:" + "c" * 64)
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="profile_bound_generic",
        generic_profile_ref=pin)})
    contract = contracts.BenchmarkFlowOutputContract(status="not_verified",
                                                     execution_receipt=receipt)
    assert step_output_kind("ca_saved", {}, contract) == "profile_attributes"


@pytest.mark.parametrize("agent_id,metadata,representation,validator,expected", [
    ("csv_formatter", {}, None, False, "formatter"),
    ("pdf_extraction", {"curation": {"domain_pack_id": "generic"}}, None, False, "pdf_extraction"),
    ("disease_term_check", {"output_schema_key": "DiseaseTermResult"}, None, True, "validator_result"),
    ("go_annotations", {"output_schema_key": "GoAnnotations"}, "json_schema", False, "other"),
    ("disease_extractor", {"curation": {"domain_pack_id": "agr.alliance.disease"}},
     "pack_fields", False, "pack_fields"),
    ("chat_output", {}, None, False, "text"),
    ("chat_output", {"curation": None}, None, False, "text"),
    ("retired_extractor", {"curation": {"domain_pack_id": "agr.alliance.not_loaded"}},
     None, False, "other"),
])
def test_system_step_output_kind_comes_from_metadata(
        monkeypatch, agent_id, metadata, representation, validator, expected):
    monkeypatch.setattr(contracts, "resolve_output_schema", lambda key: object())
    monkeypatch.setattr(contracts, "is_domain_validator_result_schema", lambda model: validator)
    contract = contracts.BenchmarkFlowOutputContract(
        status="not_verified" if representation is None else "verified",
        representation=representation,
        schema_definition=None if representation is None else {"x": 1},
        schema_digest=None if representation is None else "sha256:" + "d" * 64)
    assert step_output_kind(agent_id, metadata, contract) == expected


def test_custom_output_kind_requires_the_authorized_receipt():
    with pytest.raises(ValueError, match="authorized receipt"):
        step_output_kind("ca_saved", {}, contracts.BenchmarkFlowOutputContract(
            status="not_verified"))


def test_custom_formatter_clone_is_a_formatter_before_its_receipt_is_read():
    # A saved copy of a file formatter keeps its declared format on the entry.
    assert step_output_kind("ca_saved", {"output_formatter_format": "tsv"},
                            contracts.BenchmarkFlowOutputContract(status="not_verified")) == "formatter"


def test_system_metadata_with_null_curation_is_read_as_no_pack(curator):
    result = contracts.discover_output_contract(
        Mock(), curator, agent_id="chat_output", metadata={"curation": None})
    assert result.status == "not_verified"
    assert result.structure_source is None


def test_custom_builder_with_missing_pack_is_still_pack_fields(monkeypatch, curator):
    receipt = source_receipt().model_copy(update={"output_contract": AgentOutputContract(
        output_state="structured_extraction", output_mode="domain",
        domain_extraction_ref=DomainExtractionRef(
            package_id="p", agent_id="a", domain_pack_id="agr.alliance.not_loaded"),
    )})
    monkeypatch.setattr(contracts, "authorize_execution_receipt", lambda *a, **k: receipt)
    contract = contracts.discover_output_contract(Mock(), curator, agent_id=receipt.agent_key,
                                                  metadata={})
    assert contract.status == "not_verified" and contract.structure_source is None
    # The authorized receipt still declares a pack builder; the missing pack is
    # reported by the unverified contract, not by relabelling the step's kind.
    assert step_output_kind(receipt.agent_key, {}, contract) == "pack_fields"
