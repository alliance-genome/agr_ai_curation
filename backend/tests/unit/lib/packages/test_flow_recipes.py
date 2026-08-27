"""Tests for strict package-owned Agent Studio flow recipe contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


def test_shipped_alliance_flow_recipe_contract_uses_canonical_validator_ids():
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
        "RGD GO and Disease Paper Review",
        "RGD GO Paper Review",
    ]
    assert {tuple(group.agent_ids) for group in catalog.equivalence_groups} == {
        ("gene_expression", "gene_expression_extraction"),
        ("gene_ontology", "gene_ontology_lookup"),
    }
    recipe_agent_ids = {
        step.agent_id
        for recipe in catalog.recipes
        for step in recipe.steps
    }
    assert {"gene_validation", "allele_validation", "disease_validation", "chemical_validation"}.issubset(
        recipe_agent_ids
    )
    assert not {"gene", "allele", "disease", "chemical"} & recipe_agent_ids
    assert {suggestion.name for suggestion in catalog.suggestions} == {
        "document_extraction_first",
        "validate_expression_gene_identifiers",
    }


def test_shipped_rgd_paper_review_recipes_keep_go_and_disease_policy_distinct():
    catalog = build_flow_recipe_catalog(load_package_registry(REPO_ROOT / "packages"))
    recipes = {recipe.name: recipe for recipe in catalog.recipes}

    combined = recipes["RGD GO and Disease Paper Review"]
    go_only = recipes["RGD GO Paper Review"]

    assert combined.access.allowed_group_ids == ["RGD"]
    assert go_only.access.allowed_group_ids == ["RGD"]
    assert [step.agent_id for step in combined.steps] == [
        "rgd_go_paper_curator",
        "disease_extractor",
        "chat_output",
    ]
    assert combined.steps[-1].source_steps == [1, 2]
    assert [step.agent_id for step in go_only.steps] == [
        "rgd_go_paper_curator",
        "chat_output",
    ]
    assert go_only.steps[-1].source_steps == [1]
    assert all(
        step.agent_id != "pdf_extraction"
        for step in (*combined.steps, *go_only.steps)
    )
    assert "target_entities" in combined.description
    assert "include_other_genes" in combined.description
    assert "Introduction/Discussion" in combined.description
    assert "evidence-location" in combined.description
    assert "disease" not in (go_only.steps[0].step_goal or "").casefold()


def test_rgd_paper_review_fixture_covers_starter_results_and_follow_up_refs():
    fixture = yaml.safe_load(
        (
            REPO_ROOT
            / "backend/tests/fixtures/flows/rgd_go_disease_paper_review.yaml"
        ).read_text(encoding="utf-8")
    )

    assert fixture["starter_request"] == {
        "target_entities": ["Cttn", "MicroRNA-124-3p"],
        "include_other_genes": True,
        "exclude_sections": ["Introduction", "Discussion"],
        "include_go": True,
        "include_disease": True,
        "require": ["evidence_code", "rationale", "evidence_location"],
    }
    assert fixture["expected_review"]["additional_genes"] == ["Pfn1"]
    assert fixture["expected_review"]["unresolved"][0]["entity"] == "MicroRNA-124-3p"
    assert [result["agent_key"] for result in fixture["expected_review"]["results"]] == [
        "rgd_go_paper_curator",
        "disease_extractor",
    ]
    assert fixture["follow_up"]["reuse_result_refs"] == [
        "extraction-result:go-review-fixture",
        "extraction-result:disease-review-fixture",
    ]
    assert fixture["processing_receipt"] == {
        "schema_version": 1,
        "outcome": "completed",
        "total_stage": "completed",
    }


def test_rgd_paper_review_curator_guide_is_linked_and_covers_release_tasks():
    guide_name = "RGD_GO_DISEASE_PAPER_REVIEW.md"
    guide = (REPO_ROOT / "docs/curator" / guide_name).read_text(encoding="utf-8")
    curator_index = (REPO_ROOT / "docs/curator/README.md").read_text(encoding="utf-8")
    flows_guide = (REPO_ROOT / "docs/curator/CURATION_FLOWS.md").read_text(
        encoding="utf-8"
    )

    assert guide_name in curator_index
    assert guide_name in flows_guide
    for required_text in (
        "Documents → Upload Documents",
        "normal high-accuracy PDF path",
        "target_entities:",
        "include_other_genes: true",
        "Introduction",
        "Discussion",
        "unresolved gene-product identity",
        "result references",
        "default 15-minute deadline",
        "pdf_processing_receipt",
        "Production verification checklist",
        "non-RGD account",
        "Automatic",
    ):
        assert required_text in guide


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


def test_flow_recipe_access_defaults_unrestricted_and_exposes_rgd_restriction(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        "org.access",
        """\
flow_recipes_api_version: 1.0.0
recipes:
  - name: Unrestricted Recipe
    description: Available to every visible curator
    steps:
      - agent_id: demo_agent
  - name: RGD Recipe
    description: Available only to RGD curators
    access:
      allowed_group_ids: [RGD]
    steps:
      - agent_id: demo_agent
""",
    )

    catalog = build_flow_recipe_catalog(load_package_registry(packages_dir))

    assert [recipe.access.allowed_group_ids for recipe in catalog.recipes] == [[], ["RGD"]]


@pytest.mark.parametrize(
    "allowed_group_ids, expected",
    [
        ("[NOT_A_GROUP]", "Unknown group ID"),
        ("RGD", "Input should be a valid list"),
        ("[rgd]", "Unknown group ID"),
    ],
)
def test_flow_recipe_access_rejects_invalid_group_ids(
    tmp_path, allowed_group_ids, expected
):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        "org.invalid_access",
        f"""\
flow_recipes_api_version: 1.0.0
recipes:
  - name: Restricted Recipe
    description: Invalid availability contract
    access:
      allowed_group_ids: {allowed_group_ids}
    steps:
      - agent_id: demo_agent
""",
    )

    with pytest.raises(FlowRecipeLoadError, match=expected):
        build_flow_recipe_catalog(load_package_registry(packages_dir))


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
