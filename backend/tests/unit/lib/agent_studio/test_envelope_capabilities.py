"""Capability summaries preserve mixed maturity and operation-specific facts."""

from pathlib import Path

import pytest

from src.lib.domain_packs.capabilities import (
    object_capabilities,
    registry_object_capabilities,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


ROOT = Path(__file__).resolve().parents[5]


def _pack(name):
    path = ROOT / "packages/alliance/domain_packs" / name
    metadata = load_domain_pack_metadata(path / "domain_pack.yaml")
    return DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=path,
            metadata_path=path / "domain_pack.yaml",
            metadata=metadata,
            package_id="alliance",
        )
    )


def test_reagent_definition_and_staging_are_not_demoted_by_missing_schema_or_validators():
    registry = _pack("generic")
    obj = registry.object_definitions_by_type["generic_reagent_candidate"]
    result = object_capabilities(
        registry.domain_pack.metadata,
        obj,
        active_validators=0,
        development_validators=0,
    )
    assert result["pack_state"] == "in_development"
    assert result["definition_state"] == "stable"
    assert result["extract"]["state"] == "available"
    assert result["schema_ref"] is None
    assert result["validate"]["state"] == "none"
    assert "supported" not in result
    assert "selectable" not in result


def test_mixed_gene_and_allele_capabilities_keep_operation_blockers():
    for name in ("gene", "allele"):
        registry = _pack(name)
        attachments = [
            item.to_dict() for item in registry.validation_attachment_options()
        ]
        for obj in registry.domain_pack.metadata.object_definitions:
            result = registry_object_capabilities(registry, obj, attachments)
            for operation in ("export", "write"):
                declared = obj.metadata.get(f"{operation}_behavior")
                assert result[operation]["declared_behavior"] == declared
                if declared and declared.get("status") == "blocked":
                    assert result[operation]["state"] == "blocked"
            if name == "gene" and obj.object_type == "gene_mention_evidence":
                assert result["pack_state"] == "in_development"
                assert result["extract"]["state"] == "available"
                assert result["validate"]["state"] == "active"
                assert result["export"]["state"] == "ready"


def test_general_pdf_guidance_checks_fields_before_preference():
    prompt = (ROOT / "packages/alliance/agents/pdf/prompt.yaml").read_text()
    assert "only when its declared fields fit the requested record" in prompt
    assert "`synonym` or `source_status`" in prompt
    assert "separate profile-bound custom agent" in prompt


@pytest.mark.parametrize("name", ["gene_expression", "disease", "phenotype", "go"])
def test_pack_summary_preserves_each_object_definition_and_binding_state(name):
    registry = _pack(name)
    attachments = [item.to_dict() for item in registry.validation_attachment_options()]
    for obj in registry.domain_pack.metadata.object_definitions:
        result = registry_object_capabilities(registry, obj, attachments)
        assert result["pack_state"] == registry.domain_pack.metadata.status.value
        assert result["definition_state"] == obj.definition_state.value
        for state, key in (
            ("active", "active_bindings"),
            ("under_development", "under_development_bindings"),
        ):
            expected = {
                item["validator_binding_id"]
                for item in attachments
                if item.get("validator_binding_id")
                and item["state"] == state
                and (
                    item["scope"] == "pack"
                    or item.get("object_type") == obj.object_type
                )
            }
            assert result["validate"][key] == len(expected)
        assert not {"supported", "selectable", "production_ready"} & result.keys()


def test_capability_guidance_is_available_without_flow_context(monkeypatch):
    from src.lib.agent_studio import prompt_builder

    monkeypatch.setattr(
        prompt_builder, "build_package_diagnostic_tools_prompt", lambda: ""
    )
    prompt = prompt_builder.build_opus_system_prompt(
        None,
        load_template=lambda: "Test instructions",
        list_model_definitions=lambda: [],
        get_prompt_catalog=lambda: None,
        prepare_trace_context=lambda _: None,
    )
    assert "In-development envelopes remain selectable" in prompt
    assert "Missing LinkML or validators does not prohibit extraction" in prompt


def test_agent_metadata_response_preserves_object_capabilities():
    from src.api.agent_studio_schemas import AgentMetadata
    from src.lib.agent_studio.domain_envelope_metadata import _domain_envelope_metadata

    envelope = _domain_envelope_metadata(_pack("generic"))
    response = AgentMetadata(
        name="Example",
        icon="science",
        category="extraction",
        domain_envelope=envelope,
    ).model_dump(mode="json")
    reagent = next(
        item
        for item in response["domain_envelope"]["object_definitions"]
        if item["object_type"] == "generic_reagent_candidate"
    )
    assert reagent["capabilities"]["extract"]["state"] == "available"
    assert reagent["capabilities"]["schema_ref"] is None
    assert reagent["capabilities"]["validate"]["state"] == "none"
