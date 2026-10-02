"""Every builder extractor can explicitly finalize an empty result.

A builder run that never finalizes fails the whole flow, so a paper with nothing
in scope must end with ``finalize_*(candidate_ids=[])``. That needs both a tool
schema that accepts an empty list and a prompt that tells the model to send it.
"""

import re
from importlib import import_module
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "packages/alliance/agents"

# (tools module, finalize tool name, finalize input model, extractor agent folder)
BUILDER_FINALIZERS = [
    ("disease_builder_tools", "finalize_disease_extraction", "DiseaseFinalizeInput", "disease_extractor"),
    ("allele_builder_tools", "finalize_allele_extraction", "AlleleFinalizeInput", "allele_extractor"),
    ("agr_curation", "finalize_gene_expression_extraction", "GeneExpressionFinalizeInput", "gene_expression"),
    ("gene_builder_tools", "finalize_gene_extraction", "GeneFinalizeInput", "gene_extractor"),
    ("phenotype_builder_tools", "finalize_phenotype_extraction", "PhenotypeFinalizeInput", "phenotype_extractor"),
    ("go_builder_tools", "finalize_go_extraction", "GOFinalizeInput", "rgd_go_paper_curator"),
    ("generic_builder_tools", "finalize_generic_extraction", "GenericFinalizeInput", "pdf"),
]


def test_every_bound_builder_finalizer_is_listed():
    bindings = (REPO_ROOT / "packages/alliance/tools/bindings.yaml").read_text(encoding="utf-8")
    bound = set(re.findall(r"tool_id: (finalize_\w+_extraction)\b", bindings))
    assert bound == {row[1] for row in BUILDER_FINALIZERS}


@pytest.mark.parametrize(("tools_module", "tool_name", "input_model", "agent_dir"), BUILDER_FINALIZERS)
def test_builder_finalizer_accepts_explicit_empty_selection(tools_module, tool_name, input_model, agent_dir):
    tools = import_module(f"agr_ai_curation_alliance.tools.{tools_module}")
    getattr(tools, input_model).model_validate({"candidate_ids": []})
    schema = getattr(tools, tool_name).params_json_schema
    assert schema["properties"]["candidate_ids"].get("minItems", 0) == 0


@pytest.mark.parametrize(("tools_module", "tool_name", "input_model", "agent_dir"), BUILDER_FINALIZERS)
def test_builder_extractor_prompt_documents_explicit_empty_finalize(tools_module, tool_name, input_model, agent_dir):
    prompt = (AGENTS_DIR / agent_dir / "prompt.yaml").read_text(encoding="utf-8")
    assert f"`{tool_name}(candidate_ids=[])`" in prompt
