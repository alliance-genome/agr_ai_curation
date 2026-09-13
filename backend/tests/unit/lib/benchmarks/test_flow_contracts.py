"""Declared output discovery must not guess fields or bypass saved-revision auth."""

from unittest.mock import Mock

import pytest
from pydantic import BaseModel, Field

from src.lib.benchmarks import flow_contracts as contracts
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.schemas.agent_execution_revision import AgentOutputContract
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
    monkeypatch.setattr(contracts, "packaged_export_fields", lambda *args: [])
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
