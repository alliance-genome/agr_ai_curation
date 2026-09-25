"""ALL-1297: searchable, bounded guidance for representative curator requests.

These tests validate the assistant's supplied guidance, not a model's obedience.
Canonical proposal behavior is exercised in test_execution_revisions.
"""
from pathlib import Path

import pytest

from src.lib.agent_studio.studio_guide import prepare_studio_prompt_template, read_studio_guide


ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("query,topic,requirements", [
    ("split", "output_values_and_columns", ["header_template", "{n}", "NOT JSON", "never extra rows", "not permission to discard"]),
    ("UNRESOLVED", "output_values_and_columns", ["paper wording is a separate", "not every cell", "declared label path"]),
    ("inspect_results", "bounded_results_and_contracts", ["NOT a callable Studio tool", "get_domain_envelope_state", "next_request/next_call", "not a free run"]),
    ("ca_", "bounded_results_and_contracts", ["exact revision", "output_profile", "prompt_manifest", "Do not invent a Studio get_agent_contract"]),
    ("namespace", "tool_discovery_and_limits", ["studio_saved_work", "flow_authoring", "different catalogs", "do not grant permission"]),
    ("reasoning", "tool_discovery_and_limits", ["reasoning_options/defaults", "pinned revision", "INCOMPLETE", "never PASS"]),
    ("phenotype_terms", "domain_workflows", ["complete declared phenotype_terms", "not the primary display label", "under development", "not a phenotype extraction or an automatic"]),
    ("perturbation", "domain_workflows", ["measured changes after perturbation", "Rescue-only", "reagent, specimen and allele validation remain under development"]),
])
def test_curator_question_finds_complete_relevant_guidance(monkeypatch, query, topic, requirements):
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    monkeypatch.setenv("AGENT_STUDIO_GUIDE_CHUNK_MAX_CHARS", "1000")
    index = read_studio_guide(template=template, render_diagnostic_tools=str, query=query)
    assert topic in {item["topic"] for item in index["topics"]}
    arguments = {"topic": topic}
    chunks = []
    while True:
        result = read_studio_guide(template=template, render_diagnostic_tools=str, **arguments)
        assert result["success"] and len(result["content"]) <= 1000
        chunks.append(result["content"])
        if result["complete"]:
            assert not result["next_call"]
            break
        arguments = result["next_call"]["arguments"]
    content = "".join(chunks)
    for requirement in requirements:
        assert requirement in content
    always_sent, _ = prepare_studio_prompt_template(template)
    assert content not in always_sent


def test_extraction_guidance_has_no_lookup_helper_exception():
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    assert "Only an explicit fixed `extraction_mapping`" in template
    assert "agr_species_context_lookup" in template
    assert "Domain-pack-declared extractor helper tools may provide" not in template
    assert "For current 0.7.x" not in template
    assert "record_evidence(span_ids=[...])" in template


def test_curator_domain_docs_match_shipped_scope():
    import yaml

    docs = {name: yaml.safe_load((ROOT / f"packages/alliance/agents/{name}/docs.yaml").read_text())
            for name in ("gene_expression", "phenotype_extractor", "disease_extractor")}
    ge = str(docs["gene_expression"])
    phenotype = str(docs["phenotype_extractor"])
    disease = str(docs["disease_extractor"])
    assert "only wild-type patterns" not in ge
    assert "Measured expression changes after perturbation" in ge
    assert "reagent, specimen and allele validation remain under development" in ge
    assert "subject and reference resolution remain under development" in phenotype
    assert "complete term list" in phenotype
    assert "contributes_to" not in disease
    assert "subject validation routes separately" in disease
