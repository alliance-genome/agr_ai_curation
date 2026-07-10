from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_prompt(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(data.get("content") or "")


def test_core_supervisor_prompt_stays_generic():
    content = _load_prompt(
        _repo_root() / "packages" / "core" / "agents" / "supervisor" / "prompt.yaml"
    )

    assert "QUERY REFORMULATION FOR SPECIALIST HANDOFFS" in content
    assert "RUNTIME TOOL AUTHORITY" in content
    assert "<success_criteria>" in content
    assert "prompt-only plain text" in content
    assert "validated or materialized fields" in content
    assert "ask_pdf_extraction_specialist" not in content
    assert "ask_gene_extractor_specialist" not in content
    assert "Alliance Gene Database" not in content
    assert "Ready to prepare these for curation?" not in content


def test_config_supervisor_prompt_keeps_alliance_specific_handoffs():
    content = _load_prompt(
        _repo_root() / "config" / "agents" / "supervisor" / "prompt.yaml"
    )

    assert "QUERY REFORMULATION FOR SPECIALIST HANDOFFS" in content
    assert "<success_criteria>" in content
    assert "prompt-only plain text" in content
    assert "ask_pdf_extraction_specialist" in content
    assert "ask_gene_extractor_specialist" in content
    assert "Ready to prepare these for curation?" in content
    # The gene/allele/disease validators are no longer standalone supervisor
    # handoffs, so the "Alliance Gene Database" display-name row was removed.
    # The supervisor prompt still names alliance-specific handoffs for the kept
    # extractors and lookups.
    assert "Alliance Gene Database" not in content
    assert "Uploaded Document" in content
    assert "Ontology Term Resolver" in content
    assert "Gene Extraction Analysis" in content
    assert "Alliance Chemical Database" in content
    assert "primary_external_id" in content
    assert "validator-materialized scalar fields" in content
    assert "runtime formatter bundle contains extraction results from the active session" in content
    assert "normalized IDs" not in content
    assert "Gene assertions/normalized IDs" not in content
    assert "evidence and normalization" not in content
    assert "normalize Alliance identifiers when possible" not in content
    assert "normalize the retained genes to Alliance identifiers" not in content
