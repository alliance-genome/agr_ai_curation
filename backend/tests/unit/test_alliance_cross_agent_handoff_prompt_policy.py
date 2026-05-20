"""Cross-agent extractor/validator handoff prompt policy coverage."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_AGENTS_PATH = REPO_ROOT / "packages" / "alliance" / "agents"
ALLIANCE_TOOLS_PATH = (
    REPO_ROOT
    / "packages"
    / "alliance"
    / "python"
    / "src"
    / "agr_ai_curation_alliance"
    / "tools"
)


def _prompt(agent_id: str) -> str:
    path = ALLIANCE_AGENTS_PATH / agent_id / "prompt.yaml"
    return str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])


def _group_rule(agent_id: str, group_id: str) -> str:
    path = ALLIANCE_AGENTS_PATH / agent_id / "group_rules" / f"{group_id}.yaml"
    return str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])


def test_non_gene_allele_extractors_preserve_context_for_naive_validators():
    extractor_ids = [
        "chemical_extractor",
        "disease_extractor",
        "phenotype_extractor",
        "gene_expression",
    ]

    for agent_id in extractor_ids:
        content = _prompt(agent_id)
        normalized = " ".join(content.split())

        assert "context-naive resolver agents" in normalized
        assert "have not read the paper" in normalized
        assert "exact source mention" in normalized
        assert "verified evidence record" in normalized
        assert "only when paper-supported" in normalized
        assert "Keep one semantic validation target per object" in normalized
        assert "Preserve the most specific searchable phrase" in normalized
        assert "Use normalized hints only when the paper supplies them" in normalized


def test_database_backed_validators_use_literal_first_compact_queries():
    validator_ids = [
        "disease",
        "ontology_term",
        "controlled_vocabulary",
        "data_provider",
        "reference",
        "agm",
        "subject_entity",
        "chemical",
        "experimental_condition",
        "gene_ontology",
        "go_annotations",
        "orthologs",
    ]

    for agent_id in validator_ids:
        content = _prompt(agent_id)
        normalized = " ".join(content.split())

        lower_normalized = normalized.lower()

        assert "literal" in lower_normalized
        assert "lookup policy" in lower_normalized or "lookup_policy" in lower_normalized
        assert "exact compact" in normalized
        assert "first lookup" in normalized
        assert "Never pass a full evidence sentence" in normalized
        assert "lookup_attempt" in content
        assert "guess" in content.lower()


def test_allele_validator_group_rules_do_not_reintroduce_query_rewrites():
    forbidden_fragments = [
        "Strip Genotype Notation Before Searching",
        "Strip Tissue-Specific Prefixes",
        "Strip the pattern BEFORE searching",
        "systematically stripping notation before searching",
        "→ Search:",
        "force=True",
        "validation_warning",
    ]

    for group_id in ("mgi", "rgd"):
        content = _group_rule("allele", group_id)

        assert "Search the exact compact paper-supported allele mention first" in content
        assert "Do not strip" in content
        assert "before the first lookup" in content
        assert "Record every lookup attempt" in content or "Preserve each lookup attempt" in content
        for fragment in forbidden_fragments:
            assert fragment not in content


def test_agr_curation_search_tools_do_not_block_symbol_shapes_locally():
    agr_curation = (ALLIANCE_TOOLS_PATH / "agr_curation.py").read_text(
        encoding="utf-8"
    )
    search_helpers = (ALLIANCE_TOOLS_PATH / "search_helpers.py").read_text(
        encoding="utf-8"
    )

    assert "validate_search_symbol" not in agr_curation
    assert "_normalize_allele_symbol_for_db" not in agr_curation
    assert "check_force_parameters" not in agr_curation
    assert 'status="validation_warning"' not in agr_curation
    assert "status='validation_warning'" not in agr_curation
    assert "status: validation_warning" not in agr_curation
    assert "status='blocked'" not in agr_curation
    assert "ValidationResult" not in search_helpers
