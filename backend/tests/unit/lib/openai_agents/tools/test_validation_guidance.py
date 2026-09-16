"""Shared optional guidance tool contracts, without model or database calls."""

from importlib import import_module

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize("domain,tool_name", [
    ("allele", "stage_allele_observation"),
    ("gene", "stage_gene_mention_evidence"),
    ("disease", "stage_disease_observation"),
    ("phenotype", "stage_phenotype_observation"),
    ("generic", "stage_generic_object"),
    ("go", "stage_go_recommendation"),
])
def test_stage_tools_expose_guidance_without_an_extra_call(domain, tool_name):
    module = import_module(f"agr_ai_curation_alliance.tools.{domain}_builder_tools")
    tool = getattr(module, tool_name)
    assert "validation_guidance" in tool.params_json_schema["properties"]
    assert "configured prompt" in tool.params_json_schema["properties"]["validation_guidance"]["description"]


def test_guidance_is_not_an_arbitrary_payload_bag():
    from src.schemas.domain_envelope import CuratableObjectEnvelope
    from agr_ai_curation_alliance.tools.generic_builder_tools import GenericPatchUpdateInput
    assert CuratableObjectEnvelope(object_type="fixture", pending_ref_id="one").validation_guidance is None
    with pytest.raises(ValidationError):
        CuratableObjectEnvelope(object_type="fixture", pending_ref_id="one", validation_guidance={"resolved": "invented"})
    with pytest.raises(ValidationError):
        GenericPatchUpdateInput(field_path="validation_guidance", value={"resolved": "invented"})


def test_generic_guidance_patch_is_separate_from_attributes(monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace
    workspace = ExtractionBuilderWorkspace(run_id="guidance-generic")
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    result = tools._stage_generic_object_impl(class_key="generic:generic_object", label="reagent",
        evidence_record_ids=["e1"], classification_notes=["fixture"],
        validation_guidance="Check the supplied species context.")
    assert result.status == "ok"
    candidate_id = result.data["candidate_id"]
    result = tools._patch_generic_object_impl(candidate_id=candidate_id,
        updates=[{"field_path": "validation_guidance", "value": "Check source attribution too."}])
    assert result.status == "ok"
    fields = workspace.candidates[candidate_id].staged_fields
    assert fields["validation_guidance"] == "Check source attribution too."
    assert "validation_guidance" not in fields.get("attributes", {})
    assert "validation_guidance" not in fields.get("payload", {})
