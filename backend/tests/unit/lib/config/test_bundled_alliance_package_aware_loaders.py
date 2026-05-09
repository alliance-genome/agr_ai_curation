"""Bundled Alliance-default loader coverage for shipped repo packages."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.lib.config import agent_loader, agent_sources, prompt_loader, schema_discovery
from src.schemas.models import DomainEnvelopeExtractionResult

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def test_bundled_alliance_load_agent_definitions_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)

    assert "gene_validation" in agents
    assert agents["gene_validation"].folder_name == "gene"
    assert agents["gene_validation"].output_schema == "GeneResultEnvelope"


def test_bundled_alliance_gene_extractor_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    gene_extractor = agents["gene_extractor"]

    assert gene_extractor.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_gene_expression_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    gene_expression = agents["gene_expression_extraction"]

    assert gene_expression.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_allele_extractor_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    allele_extractor = agents["allele_extractor"]

    assert allele_extractor.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_bundled_alliance_gene_extractor_prompt_teaches_verified_evidence_flow(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    source = next(
        item
        for item in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if item.folder_name == "gene_extractor"
    )

    prompt_payload = yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))
    prompt_content = str(prompt_payload["content"])

    assert "<few_shot_examples>" in prompt_content
    assert prompt_content.count("record_evidence(") >= 3
    assert '"status": "verified"' in prompt_content
    assert "`verified_quote`" in prompt_content
    assert "`chunk_id`" in prompt_content
    assert "Do not call `record_evidence` for every gene mentioned anywhere in the paper." in prompt_content
    assert "Do not place free-text evidence summaries inside these fields." in prompt_content


def test_bundled_alliance_load_prompts_tracks_package_paths(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    db = MagicMock()
    captured_calls = []

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    result = prompt_loader.load_prompts(db=db, force_reload=True)

    assert result["base_prompts"] >= 1
    assert result["group_rules"] >= 1
    assert any(
        call["source_file"] == "packages/agr.alliance/agents/gene/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "packages/agr.alliance/agents/gene/group_rules/fb.yaml"
        and call["prompt_type"] == "group_rules"
        and call["group_id"] == "FB"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "config/agents/supervisor/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )


def test_bundled_alliance_discover_agent_schemas_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    assert "GeneValidationEnvelope" in schemas
    assert schema_discovery.get_schema_for_agent("gene").__name__ == "GeneValidationEnvelope"


def test_bundled_alliance_first_pass_extractors_share_domain_envelope_schema(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    expected = {
        "gene_expression": "GeneExpressionEnvelope",
        "gene_extractor": "GeneExtractionResultEnvelope",
        "allele_extractor": "AlleleExtractionResultEnvelope",
        "disease_extractor": "DiseaseExtractionResultEnvelope",
        "chemical_extractor": "ChemicalExtractionResultEnvelope",
        "phenotype_extractor": "PhenotypeResultEnvelope",
    }
    for agent_name, schema_name in expected.items():
        discovered_schema = schema_discovery.get_schema_for_agent(agent_name)

        assert schema_name in schemas
        assert discovered_schema is not None
        assert discovered_schema.__name__ == schema_name
        assert issubclass(discovered_schema, DomainEnvelopeExtractionResult)
        assert "curatable_objects" in discovered_schema.model_fields
        assert "metadata" in discovered_schema.model_fields
