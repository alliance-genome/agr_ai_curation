"""A sibling docs.yaml populates AgentDefinition.documentation."""
import textwrap
import pytest

from src.lib.config.agent_loader import load_agent_definitions, reset_cache


def _write_agent_bundle(root, folder, agent_yaml, docs_yaml=None):
    bundle = root / folder
    bundle.mkdir(parents=True)
    (bundle / "agent.yaml").write_text(textwrap.dedent(agent_yaml))
    if docs_yaml is not None:
        (bundle / "docs.yaml").write_text(textwrap.dedent(docs_yaml))
    return bundle


def test_docs_yaml_populates_documentation(tmp_path):
    _write_agent_bundle(
        tmp_path,
        "gene",
        agent_yaml="""
            agent_id: gene_validation
            name: "Gene Validation Agent"
            model_config:
              model: "gpt-5.5"
        """,
        docs_yaml="""
            summary: "Checks gene names against the Alliance database."
            capabilities:
              - name: "Gene lookup"
                description: "Find a gene by its symbol, name, or ID"
            tips:
              - "Include the species when you can"
        """,
    )
    reset_cache()
    agents = load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()

    doc = agents["gene_validation"].documentation
    assert doc is not None
    assert doc["summary"] == "Checks gene names against the Alliance database."
    assert doc["capabilities"][0]["name"] == "Gene lookup"
    assert doc["tips"] == ["Include the species when you can"]


def test_inline_documentation_and_docs_yaml_conflict_raises(tmp_path):
    _write_agent_bundle(
        tmp_path,
        "gene",
        agent_yaml="""
            agent_id: gene_validation
            name: "Gene Validation Agent"
            model_config:
              model: "gpt-5.5"
            documentation:
              summary: "inline summary"
        """,
        docs_yaml="""
            summary: "docs.yaml summary"
        """,
    )
    reset_cache()
    with pytest.raises(ValueError, match=r"both an inline 'documentation' block"):
        load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()


def test_empty_docs_yaml_logs_warning_and_no_documentation(tmp_path, caplog):
    import logging
    _write_agent_bundle(
        tmp_path,
        "gene",
        agent_yaml="""
            agent_id: gene_validation
            name: "Gene Validation Agent"
            model_config:
              model: "gpt-5.5"
        """,
        docs_yaml="",  # empty file -> yaml.safe_load returns None
    )
    reset_cache()
    with caplog.at_level(logging.WARNING):
        agents = load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()
    assert agents["gene_validation"].documentation is None
    assert "Empty docs.yaml in gene" in caplog.text
