"""Tests for strict package-owned Agent Studio flow recipe contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.packages.flow_recipes import (
    FlowRecipeLoadError,
    build_flow_recipe_catalog,
)
from src.lib.packages.registry import load_package_registry

from . import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))


def _write_package(
    packages_dir: Path,
    package_id: str,
    recipe_yaml: str,
) -> Path:
    package_dir = packages_dir / package_id
    (package_dir / "config").mkdir(parents=True)
    (package_dir / "requirements").mkdir()
    (package_dir / "python" / "src" / package_id.replace(".", "_")).mkdir(
        parents=True
    )
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")
    (package_dir / "package.yaml").write_text(
        "\n".join(
            [
                f"package_id: {package_id}",
                f"display_name: {package_id}",
                "version: 1.0.0",
                "package_api_version: 1.0.0",
                "min_runtime_version: 1.0.0",
                "max_runtime_version: 2.0.0",
                f"python_package_root: python/src/{package_id.replace('.', '_')}",
                "requirements_file: requirements/runtime.txt",
                "exports:",
                "  - kind: flow_recipes",
                "    name: default",
                "    path: config/flow_recipes.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (package_dir / "config" / "flow_recipes.yaml").write_text(
        recipe_yaml,
        encoding="utf-8",
    )
    return package_dir


def test_shipped_alliance_flow_recipe_contract_owns_all_recipes_and_domain_aliases():
    registry = load_package_registry(REPO_ROOT / "packages")

    catalog = build_flow_recipe_catalog(registry)

    assert [recipe.name for recipe in catalog.recipes] == [
        "Gene Curation",
        "Gene Extraction",
        "Disease Annotation",
        "Disease Extraction",
        "Chemical Entity Extraction",
        "Gene Expression Analysis",
        "Phenotype Extraction",
        "Allele/Variant Extraction",
        "Allele Annotation",
        "GO Annotation Pipeline",
    ]
    assert {tuple(group.agent_ids) for group in catalog.equivalence_groups} == {
        ("gene", "gene_validation"),
        ("allele", "allele_validation"),
        ("disease", "disease_validation"),
        ("chemical", "chemical_validation"),
        ("gene_expression", "gene_expression_extraction"),
        ("gene_ontology", "gene_ontology_lookup"),
    }
    assert {suggestion.name for suggestion in catalog.suggestions} == {
        "document_extraction_first",
        "validate_expression_gene_identifiers",
    }


def test_core_package_alone_exposes_no_domain_flow_recipes(tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    core_link = packages_dir / "core"
    core_link.symlink_to(REPO_ROOT / "packages" / "core", target_is_directory=True)

    catalog = build_flow_recipe_catalog(load_package_registry(packages_dir))

    assert catalog.recipes == ()
    assert catalog.equivalence_groups == ()
    assert catalog.suggestions == ()


def test_invalid_recipe_reports_package_path_and_recipe_context(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        "org.invalid",
        """\
flow_recipes_api_version: 1.0.0
recipes:
  - name: Broken Recipe
    description: Has a malformed step
    steps:
      - agent_id: demo_agent
        unexpected: true
""",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(FlowRecipeLoadError) as exc_info:
        build_flow_recipe_catalog(registry)

    message = str(exc_info.value)
    assert "org.invalid" in message
    assert "flow_recipes.yaml" in message
    assert "Broken Recipe" in message
    assert "recipes.0.steps.0.unexpected" in message


def test_recipe_and_equivalence_collisions_report_both_package_sources(tmp_path):
    packages_dir = tmp_path / "packages"
    contract = """\
flow_recipes_api_version: 1.0.0
equivalence_groups:
  - agent_ids: [demo_agent, demo_agent_validation]
recipes:
  - name: Shared Recipe
    description: Demonstrate a collision
    steps:
      - agent_id: demo_agent
"""
    _write_package(packages_dir, "org.one", contract)
    _write_package(packages_dir, "org.two", contract)
    registry = load_package_registry(packages_dir)

    with pytest.raises(FlowRecipeLoadError) as exc_info:
        build_flow_recipe_catalog(registry)

    message = str(exc_info.value)
    assert "Flow recipe name collision 'Shared Recipe'" in message
    assert "org.one" in message
    assert "org.two" in message


def test_package_equivalence_metadata_cannot_redefine_core_formatter_ids(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        "org.invalid",
        """\
flow_recipes_api_version: 1.0.0
equivalence_groups:
  - agent_ids: [chat_output, custom_chat]
""",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(FlowRecipeLoadError) as exc_info:
        build_flow_recipe_catalog(registry)

    message = str(exc_info.value)
    assert "org.invalid" in message
    assert "flow_recipes.yaml" in message
    assert "chat_output" in message
    assert "core-owned" in message


def test_invalid_suggestion_format_spec_reports_package_and_source_path(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        "org.invalid",
        """\
flow_recipes_api_version: 1.0.0
suggestions:
  - name: unsafe_format
    when_present: [record_extractor]
    when_absent: [record_validation]
    suggested_agent_id: record_validation
    placement: after
    message: "{suggested_agent_id:{trigger_agent_id}}"
""",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(FlowRecipeLoadError) as exc_info:
        build_flow_recipe_catalog(registry)

    message = str(exc_info.value)
    assert "org.invalid" in message
    assert "flow_recipes.yaml" in message
    assert "format specifications or conversions" in message
