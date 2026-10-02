"""Every builder extractor can explicitly finalize an empty result.

A builder run that never finalizes fails the whole flow, so a paper with nothing
in scope must end with ``finalize_*(candidate_ids=[])``. That needs both a tool
schema that accepts an empty list and a prompt that tells the model to send it.
"""

from importlib import import_module
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "packages/alliance/agents"

_EMPTY_FINALIZE_FOLLOW_UP = (
    "Known gap: this builder cannot explicitly finalize an empty result yet; "
    "port the gene-expression empty-finalize fix to this domain."
)


def _known_gap(*values, reason=_EMPTY_FINALIZE_FOLLOW_UP):
    return pytest.param(*values, marks=pytest.mark.xfail(strict=True, reason=reason))


# (tools module, finalize tool name, finalize input model, extractor agent folder)
BUILDER_FINALIZERS = [
    ("disease_builder_tools", "finalize_disease_extraction", "DiseaseFinalizeInput", "disease_extractor"),
    ("allele_builder_tools", "finalize_allele_extraction", "AlleleFinalizeInput", "allele_extractor"),
    ("agr_curation", "finalize_gene_expression_extraction", "GeneExpressionFinalizeInput", "gene_expression"),
    ("generic_builder_tools", "finalize_generic_extraction", "GenericFinalizeInput", "pdf"),
]
KNOWN_GAP_FINALIZERS = [
    ("gene_builder_tools", "finalize_gene_extraction", "GeneFinalizeInput", "gene_extractor"),
    ("phenotype_builder_tools", "finalize_phenotype_extraction", "PhenotypeFinalizeInput", "phenotype_extractor"),
    ("go_builder_tools", "finalize_go_extraction", "GOFinalizeInput", "rgd_go_paper_curator"),
]


@pytest.mark.parametrize(
    ("tools_module", "tool_name", "input_model", "agent_dir"),
    [*BUILDER_FINALIZERS, *(_known_gap(*row) for row in KNOWN_GAP_FINALIZERS)],
)
def test_builder_finalizer_accepts_explicit_empty_selection(tools_module, tool_name, input_model, agent_dir):
    tools = import_module(f"agr_ai_curation_alliance.tools.{tools_module}")
    getattr(tools, input_model).model_validate({"candidate_ids": []})
    schema = getattr(tools, tool_name).params_json_schema
    assert schema["properties"]["candidate_ids"].get("minItems", 0) == 0


@pytest.mark.parametrize(
    ("tools_module", "tool_name", "input_model", "agent_dir"),
    [
        *(
            _known_gap(
                *row,
                reason="Known gap: the allele prompt still says to report the empty result instead of finalizing it.",
            )
            if row[0] == "allele_builder_tools"
            else row
            for row in BUILDER_FINALIZERS
        ),
        *(_known_gap(*row) for row in KNOWN_GAP_FINALIZERS),
    ],
)
def test_builder_extractor_prompt_documents_explicit_empty_finalize(tools_module, tool_name, input_model, agent_dir):
    prompt = (AGENTS_DIR / agent_dir / "prompt.yaml").read_text(encoding="utf-8")
    assert f"`{tool_name}(candidate_ids=[])`" in prompt
