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
        """,
    )
    reset_cache()
    agents = load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()

    doc = agents["gene_validation"].documentation
    assert doc is not None
    assert doc["summary"] == "Checks gene names against the Alliance database."
    assert doc["capabilities"][0]["name"] == "Gene lookup"


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


def test_merge_keeps_base_docs_yaml_when_override_absent(tmp_path):
    """Regression: a docs-less override dir must not shadow the package docs.yaml.

    Mirrors the agent_dir override quirk where ``config/agents/<folder>`` layers on
    top of the package bundle. Because docs.yaml is now a merged asset (like
    prompt.yaml), the merge must keep the base (package) docs.yaml when the
    override layer supplies none.
    """
    from src.lib.config.agent_sources import (
        AgentConfigSource,
        _merge_agent_config_source,
    )

    base_dir = tmp_path / "pkg" / "gene"
    base_dir.mkdir(parents=True)
    base_docs = base_dir / "docs.yaml"
    base_docs.write_text("summary: base\n")
    base = AgentConfigSource(
        folder_name="gene",
        agent_dir=base_dir,
        agent_yaml=base_dir / "agent.yaml",
        prompt_yaml=base_dir / "prompt.yaml",
        schema_py=None,
        docs_yaml=base_docs,
        group_rule_files=(),
        package_id="alliance",
        package_path=tmp_path / "pkg",
    )
    override_dir = tmp_path / "config" / "gene"
    override_dir.mkdir(parents=True)
    override = AgentConfigSource(
        folder_name="gene",
        agent_dir=override_dir,
        agent_yaml=override_dir / "agent.yaml",
        prompt_yaml=override_dir / "prompt.yaml",
        schema_py=None,
        docs_yaml=None,
        group_rule_files=(),
    )

    merged = _merge_agent_config_source(base, override)

    assert merged.docs_yaml == base_docs


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


from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation


def test_system_agent_docs_has_task_input_and_curation_prep():
    assert get_system_agent_documentation("task_input") is not None
    assert get_system_agent_documentation("curation_prep") is not None
    assert get_system_agent_documentation("task_input")["summary"]


def test_system_agent_docs_unknown_returns_none():
    assert get_system_agent_documentation("does_not_exist") is None
