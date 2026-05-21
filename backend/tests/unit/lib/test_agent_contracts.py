"""Unit tests for shared agent contract detail service."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from src.lib.agent_contracts import (
    get_agent_contract,
    get_domain_pack_field_info,
    get_extraction_contract,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


class FixtureEnvelope(BaseModel):
    assertion_id: str
    assertion_label: str | None = None


def _fixture_registry(tmp_path: Path) -> DomainPackValidationRegistry:
    pack_path = tmp_path / "fixture_contract_pack"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.contract
display_name: Fixture Contract Pack
version: 0.1.0
metadata_api_version: 1.0.0
description: Provider-neutral contract fixture.
status: active
schema_refs:
  - schema_id: fixture.schema
    provider: fixture_provider
    name: Fixture schema
    version: 1.0.0
model_definitions:
  - model_id: AssertionPayload
    display_name: Assertion payload
object_definitions:
  - object_type: Assertion
    display_name: Assertion
    description: One provider-neutral assertion.
    model_ref: AssertionPayload
    fields:
      - field_path: assertion.curie
        display_name: Assertion CURIE
        description: Provider assertion identifier.
        field_type: string
        required: true
        metadata:
          source_of_truth: fixture_schema
          provider_refs:
            fixture_provider:
              slot: assertion_curie
metadata:
  validator_bindings:
    active:
      - binding_id: fixture_assertion_lookup
        display_name: Fixture assertion lookup
        validator_agent:
          package_id: org.validators
          agent_id: fixture_validator
        applies_to:
          domain_pack_id: fixture.contract
          object_types: [Assertion]
          field_paths: [assertion.curie]
        input_fields:
          curie:
            source: payload
            path: assertion.curie
        expected_result_fields:
          curie: assertion.curie
        required: true
        blocking: false
        allow_opt_out: true
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    loaded_pack = LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.extractors",
    )
    return DomainPackValidationRegistry.from_domain_pack(loaded_pack)


def _agent_registry() -> dict[str, dict]:
    return {
        "fixture_extractor": {
            "name": "Fixture Extractor",
            "category": "Extraction",
            "package_id": "org.extractors",
            "tools": ["fixture_lookup"],
            "output_schema": "FixtureEnvelope",
            "curation": {"domain_pack_id": "fixture.contract"},
        },
        "fixture_validator": {
            "name": "Fixture Validator",
            "category": "Validation",
            "package_id": "org.validators",
            "tools": ["fixture_lookup"],
            "output_schema": "FixtureEnvelope",
            "curation": {},
        },
    }


def _tool_details(_agent_id: str, tool_id: str) -> dict:
    return {
        "name": "Fixture Lookup",
        "description": "Resolve fixture identifiers.",
        "category": "Lookup",
        "required_context": [],
        "package_backed": True,
        "documentation": {"summary": "Fixture lookup documentation."},
        "agent_context": {"methods": ["resolve_fixture"]},
    }


def _schema_resolver(schema_name: str):
    if schema_name == "FixtureEnvelope":
        return FixtureEnvelope
    return None


def test_compact_summary_response_is_read_only_and_deterministic(tmp_path):
    registry = _fixture_registry(tmp_path)

    result = get_agent_contract(
        "fixture_extractor",
        "tools",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
        tool_details_resolver=_tool_details,
    )

    assert result["success"] is True
    assert result["read_only"] is True
    assert result["deterministic"] is True
    assert result["live_state"] is False
    assert result["writes"] is False
    assert result["tools"] == [
        {
            "tool_id": "fixture_lookup",
            "name": "Fixture Lookup",
            "category": "Lookup",
            "description": "Resolve fixture identifiers.",
            "required_context": [],
            "agent_methods": ["resolve_fixture"],
        }
    ]


def test_tools_topic_uses_default_catalog_resolver_for_real_agent():
    result = get_agent_contract("gene_extractor", "tools")

    assert result["success"] is True
    assert result["tools"]
    assert all(tool.get("resolved", True) is True for tool in result["tools"])
    tool_ids = {tool["tool_id"] for tool in result["tools"]}
    assert "get_agent_contract" in tool_ids
    assert "search_document" in tool_ids


def test_builder_tools_topic_uses_real_allele_domain_pack():
    tools = get_agent_contract("allele_extractor", "tools")
    tool_ids = {tool["tool_id"] for tool in tools["tools"]}
    assert "stage_allele_paper_evidence" in tool_ids
    assert "finalize_allele_extraction" in tool_ids

    result = get_agent_contract(
        "allele_extractor",
        "builder_tools",
        detail_level="detail",
    )

    assert result["success"] is True
    pack = result["domain_packs"][0]
    assert pack["domain_pack_id"] == "agr.alliance.allele"
    assert pack["enabled"] is True
    assert pack["stage_tool"] == "stage_allele_paper_evidence"
    assert pack["finalize_tool"] == "finalize_allele_extraction"
    assert pack["model_final_ack_schema"] == "ExtractionToolFinalizationAck"
    assert pack["curation_output_schema"] == "AlleleExtractionResultEnvelope"
    assert "mention_text" in pack["stage_required_fields"]
    assert "evidence_record_ids" in pack["stage_required_fields"]
    assert pack["object_graph"]["validator_target"] == {
        "object_type": "AlleleMention",
        "field_path": "mention.text",
    }


def test_builder_tools_topic_uses_real_disease_domain_pack():
    tools = get_agent_contract("disease_extractor", "tools")
    tool_ids = {tool["tool_id"] for tool in tools["tools"]}
    assert "stage_disease_assertion_evidence" in tool_ids
    assert "finalize_disease_extraction" in tool_ids

    result = get_agent_contract(
        "disease_extractor",
        "builder_tools",
        detail_level="detail",
    )

    assert result["success"] is True
    pack = result["domain_packs"][0]
    assert pack["domain_pack_id"] == "agr.alliance.disease"
    assert pack["stage_tool"] == "stage_disease_assertion_evidence"
    assert pack["finalize_tool"] == "finalize_disease_extraction"
    assert pack["curation_output_schema"] == "DiseaseExtractionResultEnvelope"
    assert {
        "mention",
        "disease_name",
        "disease_relation_name",
        "data_provider_abbreviation",
        "evidence_record_ids",
    }.issubset(set(pack["stage_required_fields"]))
    assert {
        target["binding_id"]
        for target in pack["object_graph"]["validator_targets"]
    } >= {
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "disease_data_provider_lookup",
    }


def test_builder_tools_topic_uses_real_chemical_domain_pack():
    tools = get_agent_contract("chemical_extractor", "tools")
    tool_ids = {tool["tool_id"] for tool in tools["tools"]}
    assert "stage_chemical_condition_evidence" in tool_ids
    assert "finalize_chemical_extraction" in tool_ids

    result = get_agent_contract(
        "chemical_extractor",
        "builder_tools",
        detail_level="detail",
    )

    assert result["success"] is True
    pack = result["domain_packs"][0]
    assert pack["domain_pack_id"] == "agr.alliance.chemical_condition"
    assert pack["stage_tool"] == "stage_chemical_condition_evidence"
    assert pack["finalize_tool"] == "finalize_chemical_extraction"
    assert pack["curation_output_schema"] == "ChemicalExtractionResultEnvelope"
    assert {
        "source_chemical_mention",
        "condition_chemical_name",
        "evidence_record_ids",
        "role",
        "confidence",
    }.issubset(set(pack["stage_required_fields"]))
    assert {
        target["binding_id"]
        for target in pack["object_graph"]["validator_targets"]
    } >= {
        "chemical_condition.chebi_api_lookup",
        "chemical_condition.term_chebi_api_lookup",
        "chemical_condition.condition_ontology_lookup",
        "chemical_condition.condition_relation_type_lookup",
    }


def test_builder_tools_topic_uses_real_phenotype_domain_pack():
    tools = get_agent_contract("phenotype_extractor", "tools")
    tool_ids = {tool["tool_id"] for tool in tools["tools"]}
    assert "stage_phenotype_assertion_evidence" in tool_ids
    assert "finalize_phenotype_extraction" in tool_ids

    result = get_agent_contract(
        "phenotype_extractor",
        "builder_tools",
        detail_level="detail",
    )

    assert result["success"] is True
    pack = result["domain_packs"][0]
    assert pack["domain_pack_id"] == "agr.alliance.phenotype"
    assert pack["stage_tool"] == "stage_phenotype_assertion_evidence"
    assert pack["finalize_tool"] == "finalize_phenotype_extraction"
    assert pack["curation_output_schema"] == "PhenotypeResultEnvelope"
    assert {
        "phenotype_statement",
        "phenotype_term_label",
        "subject_label",
        "data_provider_hint",
        "taxon_hint",
        "evidence_record_ids",
    }.issubset(set(pack["stage_required_fields"]))
    assert {
        target["binding_id"]
        for target in pack["object_graph"]["validator_targets"]
    } >= {
        "phenotype_term_ontology_validator",
    }


def test_field_specific_detail_response_uses_domain_pack_metadata(tmp_path):
    registry = _fixture_registry(tmp_path)

    result = get_agent_contract(
        "fixture_extractor",
        "field",
        field_path="Assertion.assertion.curie",
        detail_level="detail",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )

    assert result["success"] is True
    field = result["matches"][0]["fields"][0]["field"]
    assert field["field_path"] == "assertion.curie"
    assert field["required"] is True
    assert field["source_of_truth"] == "fixture_schema"
    assert field["provider_refs"]["fixture_provider"]["slot"] == "assertion_curie"
    binding = result["matches"][0]["fields"][0]["validator_bindings"][0]
    assert binding["validator_agent"] == {
        "package_id": "org.validators",
        "agent_id": "fixture_validator",
    }


def test_output_schema_detail_can_focus_field(tmp_path):
    registry = _fixture_registry(tmp_path)

    result = get_agent_contract(
        "fixture_extractor",
        "output_schema",
        field_path="assertion_id",
        detail_level="detail",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
        output_schema_resolver=_schema_resolver,
    )

    assert result["success"] is True
    assert result["output_schema"] == "FixtureEnvelope"
    assert result["schema_resolved"] is True
    assert result["field"]["field_path"] == "assertion_id"
    assert result["field"]["required"] is True


def test_invalid_topic_and_missing_field_paths_return_structured_errors(tmp_path):
    registry = _fixture_registry(tmp_path)

    invalid = get_agent_contract(
        "fixture_extractor",
        "live_state",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )
    missing = get_agent_contract(
        "fixture_extractor",
        "field",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )
    unknown = get_agent_contract(
        "fixture_extractor",
        "field",
        field_path="missing.path",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )

    assert invalid["success"] is False
    assert "allowed_topics" in invalid
    assert missing["success"] is False
    assert "field_path is required" in missing["error"]
    assert unknown["success"] is False
    assert "was not found" in unknown["error"]


def test_invalid_detail_level_and_unresolved_tool_details_are_explicit(tmp_path):
    registry = _fixture_registry(tmp_path)

    invalid_detail_level = get_agent_contract(
        "fixture_extractor",
        "tools",
        detail_level="  ",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )
    unresolved_tool = get_agent_contract(
        "fixture_extractor",
        "tools",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
        tool_details_resolver=lambda _agent_id, _tool_id: None,
    )

    assert invalid_detail_level["success"] is False
    assert "Unsupported detail_level" in invalid_detail_level["error"]
    assert unresolved_tool["success"] is True
    assert unresolved_tool["tools"] == [
        {
            "tool_id": "fixture_lookup",
            "resolved": False,
            "error": "Tool details were not found.",
        }
    ]


def test_partial_resolved_tool_details_keep_missing_metadata_visible(tmp_path):
    registry = _fixture_registry(tmp_path)

    result = get_agent_contract(
        "fixture_extractor",
        "tools",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
        tool_details_resolver=lambda _agent_id, _tool_id: {
            "name": "Partial Fixture Lookup"
        },
    )

    assert result["success"] is True
    assert result["tools"] == [
        {
            "tool_id": "fixture_lookup",
            "name": "Partial Fixture Lookup",
            "category": None,
            "description": None,
            "required_context": [],
        }
    ]


def test_validator_agent_contract_is_project_agnostic_and_uses_same_service(tmp_path):
    registry = _fixture_registry(tmp_path)

    result = get_agent_contract(
        "fixture_validator",
        "validator_bindings",
        detail_level="detail",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )
    alias_result = get_domain_pack_field_info(
        "fixture_extractor",
        "assertion.curie",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )
    extraction_alias = get_extraction_contract(
        "fixture_extractor",
        "domain_envelope",
        agent_registry=_agent_registry(),
        registries={"fixture.contract": registry},
    )

    assert result["success"] is True
    assert result["domain_packs"][0]["targeted_to_agent"] is True
    assert result["domain_packs"][0]["bindings"][0]["validator_binding_id"] == (
        "fixture_assertion_lookup"
    )
    assert "Alliance" not in json.dumps(result)
    assert "agr" + ".alliance" not in json.dumps(result)
    assert alias_result["success"] is True
    assert extraction_alias["success"] is True
